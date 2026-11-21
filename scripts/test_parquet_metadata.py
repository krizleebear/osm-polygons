#!/usr/bin/env python3
"""
Unit tests for Parquet provenance metadata (AGENTS.md rule 35).

Every GeoParquet file produced by the export pipeline must embed the standard
provenance, copyright and licensing keys in its footer via DuckDB KV_METADATA.
This includes the per-country merges in polygon-export-pipeline.yml, which
rewrite the per-region temp Parquet files and therefore must re-state the
metadata instead of silently dropping it.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PIPELINE_YAML = os.path.join(REPO_ROOT, "polygon-export-pipeline.yml")
LANDUSE_CONVERTER = os.path.join(REPO_ROOT, "scripts", "convert_landuse_to_parquet.sh")

SQL_TEMPLATES = (
    "scripts/export_parquet.sql",
    "scripts/export_places.sql",
    "scripts/export_facilities.sql",
    "scripts/export_landuse.sql",
    "scripts/export_postal_codes.sql",
)

REQUIRED_METADATA_KEYS = (
    "source",
    "origin",
    "dataset",
    "attribution",
    "attribution_url",
    "license",
    "license_url",
    "copyright",
    "schema",
    "schema_url",
    "compiler",
    "country_code",
    "exported_at",
    "osm_data_timestamp",
)

# Shell variables holding the per-country output paths of the five merges.
MERGE_TARGET_VARS = ("${out}", "${pout}", "${fout}", "${lout}", "${pcout}")


def copy_options_for_target(script, target):
    """Return the option list of every ``COPY ... TO '<target>' (...)`` statement."""
    options = []
    for match in re.finditer(r"TO\s+'(%s)'\s*\(" % re.escape(target), script):
        start = match.end()
        depth = 1
        index = start
        quote = None
        while index < len(script) and depth > 0:
            char = script[index]
            if quote:
                if char == quote:
                    quote = None
            elif char in "'\"":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            index += 1
        options.append(script[start:index - 1])
    return options


def read_file(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class TestPipelineMergeMetadata(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.pipeline = read_file(PIPELINE_YAML)

    def test_per_country_merge_statements_embed_kv_metadata(self):
        for target in MERGE_TARGET_VARS:
            options = copy_options_for_target(self.pipeline, target)
            self.assertEqual(
                len(options), 1,
                f"Expected exactly one COPY statement writing to {target}",
            )
            option_block = options[0]
            self.assertIn(
                "KV_METADATA", option_block,
                f"Per-country merge to {target} drops Parquet KV_METADATA",
            )
            for key in REQUIRED_METADATA_KEYS:
                self.assertIn(
                    f"'{key}'", option_block,
                    f"Per-country merge to {target} is missing metadata key '{key}'",
                )

    def test_per_country_merge_exported_at_comes_from_pipeline_variable(self):
        for target in MERGE_TARGET_VARS:
            option_block = copy_options_for_target(self.pipeline, target)[0]
            self.assertIn(
                "'${EXPORTED_AT}'", option_block,
                f"Per-country merge to {target} must stamp the pipeline EXPORTED_AT value",
            )

    def test_parquet_job_defines_exported_at_before_merges(self):
        self.assertIn('EXPORTED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"', self.pipeline)


class TestSqlTemplateMetadata(unittest.TestCase):

    def test_export_sql_templates_embed_required_metadata_keys(self):
        for relative_path in SQL_TEMPLATES:
            template = read_file(os.path.join(REPO_ROOT, relative_path))
            self.assertIn(
                "KV_METADATA", template,
                f"{relative_path} does not write provenance metadata",
            )
            for key in REQUIRED_METADATA_KEYS:
                self.assertIn(
                    f"'{key}'", template,
                    f"{relative_path} is missing metadata key '{key}'",
                )

    def test_landuse_converter_substitutes_exported_at_placeholder(self):
        converter = read_file(LANDUSE_CONVERTER)
        self.assertIn(
            '"s|__EXPORTED_AT__|${EXPORTED_AT}|g"', converter,
            "convert_landuse_to_parquet.sh must substitute __EXPORTED_AT__",
        )

    def test_converters_substitute_osm_data_timestamp_placeholder(self):
        converters = (
            "scripts/convert_to_parquet.sh",
            "scripts/convert_places_to_parquet.sh",
            "scripts/convert_facilities_to_parquet.sh",
            "scripts/convert_landuse_to_parquet.sh",
            "scripts/convert_postal_codes_to_parquet.sh",
        )
        for rel in converters:
            content = read_file(os.path.join(REPO_ROOT, rel))
            self.assertIn(
                "__OSM_DATA_TIMESTAMP__", content,
                f"{rel} must substitute __OSM_DATA_TIMESTAMP__",
            )

    def test_facilities_and_landuse_project_bbox_columns(self):
        for sql_rel in ("scripts/export_facilities.sql", "scripts/export_landuse.sql", "scripts/export_postal_codes.sql"):
            content = read_file(os.path.join(REPO_ROOT, sql_rel))
            for col in ("bbox_minx", "bbox_miny", "bbox_maxx", "bbox_maxy"):
                self.assertIn(
                    col, content,
                    f"{sql_rel} must project '{col}' column for Parquet row group pruning",
                )

    def test_facilities_and_landuse_sort_by_hilbert(self):
        for sql_rel in ("scripts/export_facilities.sql", "scripts/export_landuse.sql", "scripts/export_postal_codes.sql"):
            content = read_file(os.path.join(REPO_ROOT, sql_rel))
            self.assertRegex(
                content, r"ORDER BY .*ST_Hilbert\(.*ST_Extent\(ST_Extent_Agg\(",
                f"{sql_rel} must sort by bounded ST_Hilbert with ST_Extent(ST_Extent_Agg(...)) for accurate spatial clustering",
            )

    def test_hilbert_sort_uses_point_on_surface_not_centroid(self):
        # A planar ST_Centroid of antimeridian-split geometries (e.g. Taveuni, FJ) lands far from the feature.
        for sql_rel in ("scripts/export_facilities.sql", "scripts/export_landuse.sql", "scripts/export_postal_codes.sql"):
            content = read_file(os.path.join(REPO_ROOT, sql_rel))
            self.assertRegex(content, r"ST_Hilbert\(ST_PointOnSurface\(geom\)")
            self.assertNotRegex(content, r"ST_Hilbert\(ST_Centroid\(")


class TestExportParquetNormalization(unittest.TestCase):

    @unittest.skipUnless(shutil.which("duckdb"), "duckdb CLI required for end-to-end SQL check")
    def test_export_parquet_filters_non_numeric_levels_non_admin_boundaries_and_normalizes_osm_type(self):
        geom = {"type": "Polygon", "coordinates": [[[-87.2, 13.2], [-87.1, 13.2], [-87.1, 13.3], [-87.2, 13.2]]]}
        features = [
            {
                "type": "Feature",
                "id": 287670,
                "geometry": geom,
                "properties": {"osm_type": "R", "osm_id": 287670, "admin_level": "2", "area_type": "country", "ISO3166-1": "HN", "name": "Honduras"},
            },
            {
                "type": "Feature",
                "id": 4627314,
                "geometry": geom,
                "properties": {
                    "osm_type": "R", "osm_id": 4627314, "admin_level": "4", "area_type": "state",
                    "ISO3166-2": "HN-CH", "name": "Choluteca",
                    "center_lat": 13.3061, "center_lon": -87.1955, "parent_osm_id": 287670, "parent_name": "Honduras",
                },
            },
            # HN way/224755729 (non-numeric admin_level="suburb") -> must be dropped
            {
                "type": "Feature",
                "geometry": geom,
                "properties": {"@type": "way", "@id": 224755729, "admin_level": "suburb", "boundary": "administrative", "name": "La San Miguel"},
            },
            # PR boundary=protected_area -> must be dropped
            {
                "type": "Feature",
                "geometry": geom,
                "properties": {"@type": "way", "@id": 44106736, "admin_level": "7", "boundary": "protected_area", "place": "island", "name": "Isla Caja de Muertos"},
            },
            # CR boundary=place -> must be dropped
            {
                "type": "Feature",
                "geometry": geom,
                "properties": {"@type": "relation", "@id": 21261577, "admin_level": "11", "boundary": "place", "place": "neighbourhood", "name": "Finca Marañon"},
            },
            # Valid island without explicit boundary tag -> must be kept as boundary='administrative'
            {
                "type": "Feature",
                "geometry": geom,
                "properties": {"@type": "way", "@id": 777777, "admin_level": "7", "area_type": "island", "place": "island", "name": "Sample Island"},
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            in_seq = os.path.join(tmp, "HN_test.admin-polygons.geojsonseq")
            out_pq = os.path.join(tmp, "HN_test.parquet")
            with open(in_seq, "w", encoding="utf-8") as f:
                for feat in features:
                    f.write(json.dumps(feat) + "\n")

            converter = os.path.join(REPO_ROOT, "scripts", "convert_to_parquet.sh")
            res = subprocess.run([converter, "HN", in_seq, out_pq], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

            q = (
                f"SELECT osm_id, osm_type, admin_level, area_type, boundary, iso3166_1, center_lat, parent_osm_id "
                f"FROM read_parquet('{out_pq}') ORDER BY osm_id"
            )
            qres = subprocess.run(["duckdb", "-json", "-c", q], capture_output=True, text=True)
            self.assertEqual(qres.returncode, 0, qres.stderr)
            rows = {r["osm_id"]: r for r in json.loads(qres.stdout)}

        self.assertEqual(set(rows.keys()), {287670, 777777, 4627314})
        for osm_id, row in rows.items():
            self.assertEqual(row["osm_type"], "relation" if osm_id != 777777 else "way")
        self.assertEqual(rows[287670]["iso3166_1"], "HN")
        self.assertIsNone(rows[4627314]["iso3166_1"])
        self.assertEqual(rows[4627314]["area_type"], "state")
        self.assertAlmostEqual(rows[4627314]["center_lat"], 13.3061, places=4)
        self.assertEqual(rows[4627314]["parent_osm_id"], 287670)
        self.assertEqual(rows[777777]["boundary"], "administrative")


if __name__ == "__main__":
    unittest.main()
