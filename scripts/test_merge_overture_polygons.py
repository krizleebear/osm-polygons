#!/usr/bin/env python3
import unittest
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from merge_overture_polygons import (
    load_candidates,
    load_validation,
    load_overture_geometry,
    derive_area_type,
    build_feature,
    read_present_osm_ids,
    compute_health,
)

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
CANDIDATES = os.path.join(MODULE_DIR, "overture-candidates.json")

POLY = {"type": "Polygon", "coordinates": [[[20.0, 41.0], [21.0, 41.0], [21.0, 42.0], [20.0, 42.0], [20.0, 41.0]]]}


def line(feature):
    return json.dumps(feature, ensure_ascii=False) + "\n"


class TestLoaders(unittest.TestCase):
    def test_load_candidates(self):
        cands = load_candidates(CANDIDATES)
        self.assertEqual(len(cands), 29)
        kosovo = [c for c in cands if c["osm_id"] == 2088990][0]
        self.assertEqual(kosovo["country"], "XK")
        self.assertNotIn("overture_country", kosovo)

    def test_load_validation(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"broken": [{"id": 2088990, "admin_level": "2", "name": "Kosova / Kosovo", "iso1": "XK", "iso2": "RS-KM"}]}, fh)
            path = fh.name
        try:
            v = load_validation(path)
            self.assertIn(2088990, v)
            self.assertEqual(v[2088990]["iso2"], "RS-KM")
        finally:
            os.unlink(path)

    def test_load_overture_geometry(self):
        with tempfile.NamedTemporaryFile("w", suffix=".geojsonseq", delete=False) as fh:
            fh.write(line({"type": "Feature", "geometry": POLY, "properties": {"osm_id": 2088990}}))
            fh.write(line({"type": "Feature", "geometry": None, "properties": {"osm_id": 999}}))
            path = fh.name
        try:
            g = load_overture_geometry(path)
            self.assertIn(2088990, g)
            self.assertNotIn(999, g)
        finally:
            os.unlink(path)


class TestFeatureBuilding(unittest.TestCase):
    def test_derive_area_type(self):
        self.assertEqual(derive_area_type("2"), "country")
        self.assertEqual(derive_area_type("3"), "state")
        self.assertEqual(derive_area_type("4"), "state")
        self.assertEqual(derive_area_type("9"), "administrative")
        self.assertEqual(derive_area_type(None), "administrative")

    def test_build_feature(self):
        entry = {"id": 2088990, "admin_level": "2", "name": "Kosova / Kosovo", "iso1": "XK", "iso2": "RS-KM"}
        f = build_feature(2088990, entry, POLY)
        self.assertEqual(f["properties"]["@id"], 2088990)
        self.assertEqual(f["properties"]["admin_level"], "2")
        self.assertEqual(f["properties"]["name"], "Kosova / Kosovo")
        self.assertEqual(f["properties"]["ISO3166-1"], "XK")
        self.assertEqual(f["properties"]["ISO3166-2"], "RS-KM")
        self.assertEqual(f["properties"]["area_type"], "country")
        self.assertEqual(f["geometry"], POLY)


class TestReadPresentOsmIds(unittest.TestCase):
    def test_pass_through_and_collect(self):
        with tempfile.NamedTemporaryFile("w", suffix=".geojsonseq", delete=False) as fh:
            fh.write(line({"type": "Feature", "geometry": POLY, "properties": {"@id": 100, "id": 100}}))
            fh.write(line({"type": "Feature", "geometry": POLY, "properties": {"id": 200}}))
            fh.write(line({"type": "Feature", "geometry": POLY, "properties": {"osm_id": 300}}))
            fh.write(line({"type": "Feature", "geometry": POLY, "properties": {"name": "no id"}}))
            fh.write("garbage\n")
            path = fh.name
        try:
            passthrough = []
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                present = read_present_osm_ids(path)
            self.assertEqual(present, {100, 200, 300})
            out = [json.loads(l) for l in buf.getvalue().splitlines() if l]
            # 4 features pass through (incl. the id-less one); garbage line is dropped
            self.assertEqual(len(out), 4)
        finally:
            os.unlink(path)


class TestCliIntegration(unittest.TestCase):
    def _stream(self, tmpdir, features):
        path = os.path.join(tmpdir, "in.geojsonseq")
        with open(path, "w") as fh:
            for ft in features:
                fh.write(line(ft))
        return path

    def _artifacts(self, tmpdir, validation, overture):
        val = os.path.join(tmpdir, "validation.json")
        with open(val, "w") as fh:
            json.dump(validation, fh)
        ov = os.path.join(tmpdir, "overture.geojsonseq")
        with open(ov, "w") as fh:
            for osm_id, geom in overture.items():
                fh.write(line({"type": "Feature", "geometry": geom, "properties": {"osm_id": osm_id}}))
        return val, ov

    def _run(self, tmpdir, features, validation, overture, country="XK", candidates=CANDIDATES):
        import subprocess
        stream = self._stream(tmpdir, features)
        val, ov = self._artifacts(tmpdir, validation, overture)
        proc = subprocess.run(
            ["python3", os.path.join(MODULE_DIR, "merge_overture_polygons.py"),
             "--validation", val, "--overture", ov, "--candidates", candidates,
             "--country-code", country, stream],
            capture_output=True, text=True,
        )
        return proc

    def test_kosovo_present_skipped(self):
        # Kosovo 2088990 present in XK stream -> skip; validation/overture not consulted
        with tempfile.TemporaryDirectory() as tmp:
            ft = {"type": "Feature", "geometry": POLY,
                  "properties": {"@type": "relation", "@id": 2088990, "id": 2088990, "name": "Kosova / Kosovo", "admin_level": "2"}}
            proc = self._run(tmp, [ft], {"broken": []}, {}, country="XK")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("1 already present", proc.stderr)
            out = [json.loads(l) for l in proc.stdout.splitlines() if l]
            self.assertEqual([p["properties"]["@id"] for p in out], [2088990])

    def test_absent_candidate_merged(self):
        # Kosovo absent from XK stream + in validation + overture geometry -> appended
        with tempfile.TemporaryDirectory() as tmp:
            present_ft = {"type": "Feature", "geometry": POLY,
                          "properties": {"@id": 100, "id": 100, "name": "D", "admin_level": "8"}}
            val = {"broken": [{"id": 2088990, "admin_level": "2", "name": "Kosova / Kosovo", "iso1": "XK"}]}
            ov = {2088990: POLY}
            proc = self._run(tmp, [present_ft], val, ov, country="XK")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("1 inserted", proc.stderr)
            out = [json.loads(l) for l in proc.stdout.splitlines() if l]
            self.assertEqual(len(out), 2)
            merged = out[1]["properties"]
            self.assertEqual(merged["@id"], 2088990)
            self.assertEqual(merged["name"], "Kosova / Kosovo")
            self.assertEqual(merged["ISO3166-1"], "XK")

    def test_no_candidates_region(self):
        # Country without candidates: stream passes through untouched, exit 0
        with tempfile.TemporaryDirectory() as tmp:
            ft = {"type": "Feature", "geometry": POLY, "properties": {"@id": 5, "id": 5}}
            proc = self._run(tmp, [ft], {"broken": []}, {}, country="DE")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("no candidates for country 'DE'", proc.stderr)
            out = [json.loads(l) for l in proc.stdout.splitlines() if l]
            self.assertEqual([p["properties"]["@id"] for p in out], [5])

    def test_absent_candidate_missing_validation_fails(self):
        # Genuinely absent candidate not in validation -> explicit error, no silent fallback
        with tempfile.TemporaryDirectory() as tmp:
            ft = {"type": "Feature", "geometry": POLY, "properties": {"@id": 100, "id": 100}}
            proc = self._run(tmp, [ft], {"broken": []}, {2088990: POLY}, country="XK")
            self.assertEqual(proc.returncode, 1)
            self.assertIn("missing in validation.json", proc.stderr)

    def test_absent_candidate_missing_geometry_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            ft = {"type": "Feature", "geometry": POLY, "properties": {"@id": 100, "id": 100}}
            val = {"broken": [{"id": 2088990, "admin_level": "2", "name": "Kosova / Kosovo"}]}
            proc = self._run(tmp, [ft], val, {}, country="XK")
            self.assertEqual(proc.returncode, 1)
            self.assertIn("missing geometry in Overture artifact", proc.stderr)

    def test_kosovo_not_matched_by_serbia_job(self):
        # serbia job (COUNTRY_CODE=RS) must NOT consider the Kosovo candidate
        with tempfile.TemporaryDirectory() as tmp:
            ft = {"type": "Feature", "geometry": POLY, "properties": {"@id": 100, "id": 100}}
            proc = self._run(tmp, [ft], {"broken": []}, {}, country="RS")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = [json.loads(l) for l in proc.stdout.splitlines() if l]
            self.assertEqual(len(out), 1)


class TestHealthCheckReplace(unittest.TestCase):
    """Present-but-damaged candidates must be replaced by the Overture LAND
    geometry when coverage (land covered) or inside (overreach) falls below
    the health thresholds. Requires the duckdb CLI + spatial extension."""

    HALF = {"type": "Polygon", "coordinates": [[[20.0, 41.0], [20.5, 41.0], [20.5, 42.0], [20.0, 42.0], [20.0, 41.0]]]}
    BIG = {"type": "Polygon", "coordinates": [[[19.0, 40.0], [22.0, 40.0], [22.0, 43.0], [19.0, 43.0], [19.0, 40.0]]]}

    def _present(self, tmpdir, geom):
        path = os.path.join(tmpdir, "in.geojsonseq")
        ft = {"type": "Feature", "geometry": geom,
              "properties": {"@type": "relation", "@id": 2088990, "id": 2088990,
                             "name": "Kosova / Kosovo", "admin_level": "2", "ISO3166-1": "XK"}}
        with open(path, "w") as fh:
            fh.write(line(ft))
        return path

    def _run(self, tmpdir, present_geom, overture_geom, extra_args=None):
        import subprocess
        stream = self._present(tmpdir, present_geom)
        val = os.path.join(tmpdir, "validation.json")
        with open(val, "w") as fh:
            json.dump({"broken": [{"id": 2088990, "admin_level": "2", "name": "Kosova / Kosovo", "iso1": "XK"}]}, fh)
        ov = os.path.join(tmpdir, "overture.geojsonseq")
        with open(ov, "w") as fh:
            fh.write(line({"type": "Feature", "geometry": overture_geom, "properties": {"osm_id": 2088990}}))
        cmd = ["python3", os.path.join(MODULE_DIR, "merge_overture_polygons.py"),
               "--validation", val, "--overture", ov, "--candidates", CANDIDATES,
               "--country-code", "XK"] + (extra_args or []) + [stream]
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_present_healthy_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run(tmp, POLY, POLY)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("KEEP osm_id 2088990", proc.stderr)
            self.assertIn("0 replaced", proc.stderr)
            out = [json.loads(l) for l in proc.stdout.splitlines() if l]
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["geometry"], POLY)
            self.assertEqual(out[0]["properties"]["name"], "Kosova / Kosovo")

    def test_present_truncated_replaced(self):
        # Present polygon covers only ~50% of the land -> coverage < 0.95
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run(tmp, self.HALF, POLY)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("REPLACE osm_id 2088990", proc.stderr)
            self.assertIn("1 replaced", proc.stderr)
            out = [json.loads(l) for l in proc.stdout.splitlines() if l]
            self.assertEqual(len(out), 1)
            # Geometry swapped to the Overture LAND geometry; metadata preserved
            self.assertEqual(out[0]["geometry"], POLY)
            self.assertEqual(out[0]["properties"]["name"], "Kosova / Kosovo")
            self.assertEqual(out[0]["properties"]["admin_level"], "2")

    def test_present_overreach_replaced(self):
        # Present polygon extends far beyond the land -> inside < 0.90
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run(tmp, self.BIG, POLY)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("REPLACE osm_id 2088990", proc.stderr)
            self.assertIn("1 replaced", proc.stderr)
            out = [json.loads(l) for l in proc.stdout.splitlines() if l]
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["geometry"], POLY)

    def test_present_loose_thresholds_keep(self):
        # A lenient threshold must keep the truncated polygon untouched
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run(tmp, self.HALF, POLY, extra_args=["--min-coverage", "0.4", "--min-inside", "0.4"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("KEEP osm_id 2088990", proc.stderr)
            self.assertIn("0 replaced", proc.stderr)
            out = [json.loads(l) for l in proc.stdout.splitlines() if l]
            self.assertEqual(out[0]["geometry"], self.HALF)


class TestComputeHealth(unittest.TestCase):
    def test_identical_geometry_is_healthy(self):
        health = compute_health({2088990: POLY}, {2088990: POLY})
        coverage, inside = health[2088990]
        self.assertAlmostEqual(coverage, 1.0, delta=0.02)
        self.assertAlmostEqual(inside, 1.0, delta=0.02)

    def test_missing_land_omitted(self):
        self.assertEqual(compute_health({2088990: POLY}, {}), {})

    def test_null_present_geometry_unhealthy(self):
        health = compute_health({2088990: None}, {2088990: POLY})
        coverage, inside = health[2088990]
        self.assertEqual(coverage, 0.0)
        self.assertEqual(inside, 0.0)


if __name__ == "__main__":
    unittest.main()