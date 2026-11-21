#!/usr/bin/env python3
import json
import os
import tempfile
import unittest

from merge_reference_polygons import load_candidates, load_reference_features, main


class TestMergeReferencePolygons(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.candidates_file = os.path.join(self.temp_dir.name, "candidates.json")
        self.reference_file = os.path.join(self.temp_dir.name, "reference.geojsonseq")
        self.stream_file = os.path.join(self.temp_dir.name, "stream.geojsonseq")

        # Mock candidates
        candidates_data = {
            "candidates": [
                {"osm_id": 1001, "country": "MC", "admin_level": 2, "name": "Monaco"},
                {"osm_id": 2001, "country": "US", "admin_level": 2, "name": "United States"},
                {"osm_id": 2002, "country": "US", "admin_level": 4, "name": "Alaska"}
            ]
        }
        with open(self.candidates_file, "w") as f:
            json.dump(candidates_data, f)

        # Mock reference polygons
        self.ref_mc = {
            "type": "Feature",
            "id": 1001,
            "properties": {"osm_id": 1001, "name": "Monaco", "admin_level": "2", "ISO3166-1": "MC"},
            "geometry": {"type": "MultiPolygon", "coordinates": [[[[7.4, 43.7], [7.5, 43.7], [7.5, 43.8], [7.4, 43.7]]]]}
        }
        self.ref_us = {
            "type": "Feature",
            "id": 2001,
            "properties": {"osm_id": 2001, "name": "United States", "admin_level": "2", "ISO3166-1": "US"},
            "geometry": {"type": "MultiPolygon", "coordinates": [[[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]]]}
        }
        with open(self.reference_file, "w") as f:
            f.write(json.dumps(self.ref_mc) + "\n")
            f.write(json.dumps(self.ref_us) + "\n")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_candidates_filters_by_country(self):
        mc_candidates = load_candidates(self.candidates_file, "MC")
        self.assertEqual(len(mc_candidates), 1)
        self.assertIn(1001, mc_candidates)

        us_candidates = load_candidates(self.candidates_file, "US")
        self.assertEqual(len(us_candidates), 2)
        self.assertIn(2001, us_candidates)
        self.assertIn(2002, us_candidates)

    def test_insert_missing_candidate(self):
        # Stream has other features, but missing Monaco (1001)
        orig_stream = [
            {"type": "Feature", "id": 5001, "properties": {"osm_id": 5001, "admin_level": "8", "ISO3166-1": "MC"}},
        ]
        with open(self.stream_file, "w") as f:
            for feat in orig_stream:
                f.write(json.dumps(feat) + "\n")

        output_file = os.path.join(self.temp_dir.name, "out.geojsonseq")
        import io, sys
        from contextlib import redirect_stdout
        orig_argv = sys.argv
        try:
            sys.argv = [
                "merge_reference_polygons.py",
                self.stream_file,
                "--reference", self.reference_file,
                "--candidates", self.candidates_file,
                "--country-code", "MC",
                "--out", output_file
            ]
            with redirect_stdout(io.StringIO()):
                main()
        finally:
            sys.argv = orig_argv

        with open(output_file, "r") as f:
            lines = [json.loads(line) for line in f if line.strip()]

        self.assertEqual(len(lines), 2)
        osm_ids = [feat["properties"]["osm_id"] for feat in lines]
        self.assertIn(1001, osm_ids)
        self.assertIn(5001, osm_ids)

    def test_replace_corrupted_candidate(self):
        # Stream has Monaco (1001), but we want to replace it
        orig_stream = [
            {"type": "Feature", "id": 1001, "properties": {"osm_id": 1001, "name": "Broken Monaco", "admin_level": "2", "ISO3166-1": "MC"}},
        ]
        with open(self.stream_file, "w") as f:
            for feat in orig_stream:
                f.write(json.dumps(feat) + "\n")

        output_file = os.path.join(self.temp_dir.name, "out.geojsonseq")
        import io, sys
        from contextlib import redirect_stdout
        orig_argv = sys.argv
        try:
            sys.argv = [
                "merge_reference_polygons.py",
                self.stream_file,
                "--reference", self.reference_file,
                "--candidates", self.candidates_file,
                "--country-code", "MC",
                "--out", output_file
            ]
            with redirect_stdout(io.StringIO()):
                main()
        finally:
            sys.argv = orig_argv

        with open(output_file, "r") as f:
            lines = [json.loads(line) for line in f if line.strip()]

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["properties"]["name"], "Monaco")  # Replaced with reference name

    def test_replace_osmium_at_id_relation_and_ignore_way_collision(self):
        orig_stream = [
            {
                "type": "Feature",
                "properties": {"@type": "relation", "@id": 1001, "name": "Old Monaco", "admin_level": "2"},
                "geometry": {"type": "Polygon", "coordinates": []},
            },
            {
                "type": "Feature",
                "properties": {"@type": "way", "@id": 1001, "name": "Border Way", "admin_level": "2"},
                "geometry": {"type": "Polygon", "coordinates": []},
            },
        ]
        with open(self.stream_file, "w") as f:
            for feat in orig_stream:
                f.write(json.dumps(feat) + "\n")

        output_file = os.path.join(self.temp_dir.name, "out_at_id.geojsonseq")
        import io, sys
        from contextlib import redirect_stdout
        orig_argv = sys.argv
        try:
            sys.argv = [
                "merge_reference_polygons.py",
                self.stream_file,
                "--reference", self.reference_file,
                "--candidates", self.candidates_file,
                "--country-code", "MC",
                "--out", output_file,
            ]
            with redirect_stdout(io.StringIO()):
                main()
        finally:
            sys.argv = orig_argv

        with open(output_file, "r") as f:
            lines = [json.loads(line) for line in f if line.strip()]

        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["properties"]["name"], "Monaco")
        self.assertEqual(lines[1]["properties"]["@type"], "way")
        self.assertEqual(lines[1]["properties"]["name"], "Border Way")

    def test_merge_is_idempotent(self):
        with open(self.stream_file, "w") as f:
            f.write(json.dumps({"type": "Feature", "id": 5001, "properties": {"osm_id": 5001, "admin_level": "8"}}) + "\n")

        import io, sys
        from contextlib import redirect_stdout
        orig_argv = sys.argv
        try:
            for _ in range(2):
                sys.argv = [
                    "merge_reference_polygons.py",
                    self.stream_file,
                    "--reference", self.reference_file,
                    "--candidates", self.candidates_file,
                    "--country-code", "MC",
                ]
                with redirect_stdout(io.StringIO()):
                    main()
        finally:
            sys.argv = orig_argv

        with open(self.stream_file, "r") as f:
            lines = [json.loads(line) for line in f if line.strip()]

        self.assertEqual(len(lines), 2)
        self.assertEqual([feat["properties"]["osm_id"] for feat in lines], [5001, 1001])

    def test_replace_preserves_stream_enrichment_and_normalizes_r_type(self):
        cands = os.path.join(self.temp_dir.name, "hn_cands.json")
        refs = os.path.join(self.temp_dir.name, "hn_refs.geojsonseq")
        stream = os.path.join(self.temp_dir.name, "hn_stream.geojsonseq")
        with open(cands, "w") as f:
            json.dump({
                "candidates": [
                    {"osm_id": 4627314, "country": "HN", "admin_level": 4, "name": "Choluteca"}
                ]
            }, f)
        with open(refs, "w") as f:
            f.write(json.dumps({
                "type": "Feature",
                "id": 4627314,
                "properties": {
                    "osm_type": "R",
                    "osm_id": 4627314,
                    "admin_level": "4",
                    "ISO3166-2": "HN-CH",
                    "name": "Choluteca",
                },
                "geometry": {"type": "MultiPolygon", "coordinates": []},
            }) + "\n")
        with open(stream, "w") as f:
            f.write(json.dumps({
                "type": "Feature",
                "properties": {
                    "@type": "relation",
                    "@id": 4627314,
                    "admin_level": "4",
                    "area_type": "state",
                    "name": "Choluteca",
                    "center_lat": 13.3061,
                    "center_lon": -87.1955,
                    "admin_centre:name": "Choluteca",
                    "parent_osm_id": 287670,
                    "parent_name": "Honduras",
                },
                "geometry": {"type": "Polygon", "coordinates": []},
            }) + "\n")

        out = os.path.join(self.temp_dir.name, "hn_out.geojsonseq")
        import io, sys
        from contextlib import redirect_stdout
        orig_argv = sys.argv
        try:
            sys.argv = [
                "merge_reference_polygons.py", stream,
                "--reference", refs, "--candidates", cands,
                "--country-code", "HN", "--out", out,
            ]
            with redirect_stdout(io.StringIO()):
                main()
        finally:
            sys.argv = orig_argv

        with open(out, "r") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(lines), 1)
        props = lines[0]["properties"]
        self.assertEqual(props["osm_type"], "relation")
        self.assertEqual(props["@type"], "relation")
        self.assertEqual(props["area_type"], "state")
        self.assertEqual(props["center_lat"], 13.3061)
        self.assertEqual(props["admin_centre:name"], "Choluteca")
        self.assertEqual(props["parent_osm_id"], 287670)
        self.assertEqual(props["parent_name"], "Honduras")

    def test_insert_missing_l4_strips_bogus_iso1_and_infers_parent_from_stream_l2(self):
        cands = os.path.join(self.temp_dir.name, "hn_insert_cands.json")
        refs = os.path.join(self.temp_dir.name, "hn_insert_refs.geojsonseq")
        stream = os.path.join(self.temp_dir.name, "hn_insert_stream.geojsonseq")
        with open(cands, "w") as f:
            json.dump({
                "candidates": [
                    {"osm_id": 4627314, "country": "HN", "admin_level": 4, "name": "Choluteca"}
                ]
            }, f)
        with open(refs, "w") as f:
            f.write(json.dumps({
                "type": "Feature",
                "id": 4627314,
                "properties": {
                    "osm_type": "R",
                    "osm_id": 4627314,
                    "admin_level": "4",
                    "area_type": "administrative",
                    "ISO3166-1": "HN",
                    "ISO3166-2": "HN-CH",
                    "name": "Choluteca",
                },
                "geometry": {"type": "MultiPolygon", "coordinates": []},
            }) + "\n")
        with open(stream, "w") as f:
            f.write(json.dumps({
                "type": "Feature",
                "properties": {
                    "@type": "relation",
                    "@id": 287670,
                    "admin_level": "2",
                    "ISO3166-1": "HN",
                    "name": "Honduras",
                },
                "geometry": {"type": "MultiPolygon", "coordinates": []},
            }) + "\n")

        out = os.path.join(self.temp_dir.name, "hn_insert_out.geojsonseq")
        import io, sys
        from contextlib import redirect_stdout
        orig_argv = sys.argv
        try:
            sys.argv = [
                "merge_reference_polygons.py", stream,
                "--reference", refs, "--candidates", cands,
                "--country-code", "HN", "--out", out,
            ]
            with redirect_stdout(io.StringIO()):
                main()
        finally:
            sys.argv = orig_argv

        with open(out, "r") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(lines), 2)
        l4_props = lines[1]["properties"]
        self.assertEqual(l4_props["osm_type"], "relation")
        self.assertEqual(l4_props["area_type"], "state")
        self.assertNotIn("ISO3166-1", l4_props)
        self.assertEqual(l4_props["parent_osm_id"], 287670)
        self.assertEqual(l4_props["parent_name"], "Honduras")



if __name__ == "__main__":
    unittest.main()
