#!/usr/bin/env python3
import unittest
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from fetch_overture_polygons import (
    build_geometry_sql,
    build_download_divisions_sql,
    load_candidates,
    resolve_latest_release,
    run_duckdb_sql,
)

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
CANDIDATES = os.path.join(MODULE_DIR, "overture-candidates.json")

SQL_CREATE_FAKE_AREA = """\
INSTALL spatial; LOAD spatial; SET geometry_always_xy=true;
COPY (
    SELECT * FROM (VALUES
        ('A', ST_GeomFromText('POLYGON((0 0,1 0,1 1,0 1,0 0))'), true,  false),
        ('A', ST_GeomFromText('POLYGON((0 0,2 0,2 2,0 2,0 0))'), false, true),
        ('C', ST_GeomFromText('POLYGON((0 0,1 0,1 1,0 1,0 0))'), true,  false),
        ('C', ST_GeomFromText('POLYGON((5 5,6 5,6 6,5 6,5 5))'), true,  false)
    ) AS t(division_id, geometry, is_land, is_territorial)
) TO '__AREA_PARQUET__' (FORMAT PARQUET);
"""


class TestGeometrySQLSelection(unittest.TestCase):
    """division_area carries land + territorial rows per division; the geometry SQL
    must keep ONLY is_land=true rows and union them into ONE deterministic row."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="overture-fetch-test-")
        area_dir = os.path.join(cls.tmp, "theme=divisions", "type=division_area")
        os.makedirs(area_dir)
        cls.area_parquet = os.path.join(area_dir, "part-0.parquet")
        sql = SQL_CREATE_FAKE_AREA.replace("__AREA_PARQUET__", cls.area_parquet)
        run_duckdb_sql(sql, "fake area fixture")

    @classmethod
    def tearDownClass(cls):
        if os.path.isdir(cls.tmp):
            shutil.rmtree(cls.tmp)

    def _fetch_geometry(self):
        out = os.path.join(self.tmp, "geometry.json")
        sql = build_geometry_sql(self.tmp, ["A", "C"], out)
        run_duckdb_sql(sql, "geometry fetch")
        self.assertTrue(os.path.isfile(out))
        rows = {}
        with open(out, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                rows[row["division_id"]] = row["geometry"]
        return rows, out

    def test_only_land_rows_selected_no_territorial_hull(self):
        rows, out = self._fetch_geometry()
        # Territorial hull (BOX 0-2) must not win; land square 0-1 must be returned.
        self.assertEqual(len(rows), 2, "one row per division, territorial excluded")
        self.assertEqual(rows["A"]["type"], "Polygon")
        coords = [c for ring in rows["A"]["coordinates"] for c in ring]
        maxv = max(max(x, y) for x, y in coords)
        self.assertEqual(maxv, 1, "land square only, territorial hull (max 2) excluded")

    def test_multiple_land_rows_unioned_into_single_geometry(self):
        rows, out = self._fetch_geometry()
        self.assertEqual(len(rows), 2)
        # C has two disjoint land squares -> union must contain both parts.
        self.assertEqual(rows["C"]["type"], "MultiPolygon")
        self.assertEqual(len(rows["C"]["coordinates"]), 2)

    def test_result_deterministic_across_runs(self):
        first, out = self._fetch_geometry()
        second, out2 = self._fetch_geometry()
        self.assertEqual(first, second)

    def test_sql_regression_guard(self):
        sql = build_geometry_sql("s3://bucket/release/2000-01-01.0", ["D"], "o.json")
        self.assertIn("a.is_land = true", sql)
        self.assertIn("ST_Union_Agg(a.geometry)", sql)
        self.assertIn("GROUP BY a.division_id", sql)


class TestHelpers(unittest.TestCase):
    def test_load_candidates(self):
        cands = load_candidates(CANDIDATES)
        self.assertEqual(len(cands), 29)

    def test_resolve_latest_release_regex(self):
        import re as _re
        from fetch_overture_polygons import RELEASE_PATTERN
        xml = (
            "<Contents><Key>release/2026-07-22.0/theme=divisions/boundary=all/</Key></Contents>"
            "<CommonPrefixes><Prefix>release/2026-08-19.0/</Prefix></CommonPrefixes>"
        )
        versions = set(_re.findall(RELEASE_PATTERN.pattern, xml))
        self.assertEqual(versions, {"2026-07-22.0", "2026-08-19.0"})

    def test_run_duckdb_sql_fails_explicitly(self):
        try:
            run_duckdb_sql("THIS IS NOT SQL;", "bogus")
            self.fail("expected SystemExit for failing SQL")
        except SystemExit as exc:
            self.assertIn("DuckDB query failed", str(exc))

    def test_download_divisions_sql_prunes_columns(self):
        sql = build_download_divisions_sql("s3://bucket/release/2000-01-01.0", "d.parquet")
        self.assertIn("theme=divisions/type=division/*", sql)
        self.assertIn("SELECT id, country, subtype, names, sources", sql)


if __name__ == "__main__":
    unittest.main(verbosity=2)