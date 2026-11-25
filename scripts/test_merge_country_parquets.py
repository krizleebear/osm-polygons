#!/usr/bin/env python3
"""Unit and end-to-end tests for scripts/merge_country_parquets.py."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import merge_country_parquets


def _run_duckdb_sql(sql):
    res = subprocess.run(
        ["duckdb", "-no-init", "-bail"],
        input=sql,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise AssertionError(f"DuckDB SQL failed ({res.returncode}): {res.stderr}\n{res.stdout}")
    return res.stdout


def _query_duckdb_json(sql):
    res = subprocess.run(
        ["duckdb", "-no-init", "-json", "-c", sql],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise AssertionError(f"DuckDB JSON query failed ({res.returncode}): {res.stderr}")
    stdout = res.stdout.strip()
    return json.loads(stdout) if stdout else []


def _read_kv_metadata(parquet_path):
    rows = _query_duckdb_json(
        "SELECT key::VARCHAR AS k, value::VARCHAR AS v "
        f"FROM parquet_kv_metadata({merge_country_parquets.sql_quote(parquet_path)});"
    )
    return {r["k"]: r["v"] for r in rows}


class TestMergeCountryParquetsUnit(unittest.TestCase):

    def test_build_batch_sql_prunes_to_matching_country_files_only(self):
        country_files = {
            "AD": ["/tmp/in/AD_andorra.parquet"],
            "ES": ["/tmp/in/ES_canary-islands.parquet", "/tmp/in/ES_spain.parquet"],
            "SM": ["/tmp/in/IT_italy.parquet"],
        }
        timestamps = {
            "/tmp/in/AD_andorra.parquet": "2026-11-20T01:00:00Z",
            "/tmp/in/ES_spain.parquet": "2026-11-21T02:00:00Z",
            "/tmp/in/ES_canary-islands.parquet": "2026-11-21T03:00:00Z",
            "/tmp/in/IT_italy.parquet": "2026-11-21T04:00:00Z",
        }
        sql = merge_country_parquets.build_batch_sql(
            layer="admin-polygons",
            country_files=country_files,
            output_dir="/tmp/out",
            exported_at="2026-11-23T00:00:00Z",
            file_timestamps=timestamps,
        )

        self.assertNotIn("*.parquet", sql)
        self.assertNotIn("CREATE TABLE", sql)
        self.assertIn("LOAD spatial;", sql)

        # Split by COPY statement to verify exact file isolation per country
        copies = [s for s in sql.split("COPY (") if "TO '/tmp/out/" in s]
        self.assertEqual(len(copies), 3)

        ad_copy, es_copy, sm_copy = copies
        self.assertIn("read_parquet('/tmp/in/AD_andorra.parquet')", ad_copy)
        self.assertNotIn("ES_spain", ad_copy)
        self.assertNotIn("IT_italy", ad_copy)

        self.assertIn(
            "read_parquet(['/tmp/in/ES_canary-islands.parquet', '/tmp/in/ES_spain.parquet'])",
            es_copy,
        )
        self.assertNotIn("AD_andorra", es_copy)

        self.assertIn("read_parquet('/tmp/in/IT_italy.parquet')", sm_copy)
        self.assertNotIn("ES_spain", sm_copy)

    def test_validate_country_code_rejects_traversal_and_empty(self):
        for bad in ("", "   ", "../DE", "DE/FR", "DE\\FR", "DE..FR", "DE\x00FR", "DE;DROP"):
            with self.assertRaises(ValueError, msg=f"Expected rejection for {bad!r}"):
                merge_country_parquets.validate_country_code(bad)
        self.assertEqual(merge_country_parquets.validate_country_code(" DE "), "DE")
        self.assertEqual(merge_country_parquets.validate_country_code("GB-ENG"), "GB-ENG")

    def test_resolve_country_timestamp_prefers_source_max_then_global_then_exported_at(self):
        ts_map = {
            "/tmp/a.parquet": "2026-11-19T10:00:00Z",
            "/tmp/b.parquet": "2026-11-20T12:00:00Z",
        }
        self.assertEqual(
            merge_country_parquets.resolve_country_timestamp(
                ["/tmp/a.parquet", "/tmp/b.parquet"], ts_map, "2026-11-21T00:00:00Z"
            ),
            "2026-11-20T12:00:00Z",
        )
        self.assertEqual(
            merge_country_parquets.resolve_country_timestamp(
                ["/tmp/missing.parquet"], ts_map, "2026-11-21T00:00:00Z"
            ),
            "2026-11-21T00:00:00Z",
        )
        self.assertEqual(
            merge_country_parquets.resolve_country_timestamp(
                ["/tmp/missing.parquet"], {}, "2026-11-23T00:00:00Z"
            ),
            "2026-11-23T00:00:00Z",
        )

    def test_empty_input_dir_require_non_empty_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            in_dir = os.path.join(tmp, "empty_in")
            out_dir = os.path.join(tmp, "out")
            os.makedirs(in_dir)

            rc_fail = merge_country_parquets.main([
                "--layer", "admin-polygons",
                "--input-dir", in_dir,
                "--output-dir", out_dir,
                "--exported-at", "2026-11-23T00:00:00Z",
                "--continent", "europe",
                "--require-non-empty",
            ])
            self.assertEqual(rc_fail, 1)

            rc_ok = merge_country_parquets.main([
                "--layer", "places",
                "--input-dir", in_dir,
                "--output-dir", out_dir,
                "--exported-at", "2026-11-23T00:00:00Z",
                "--continent", "europe",
            ])
            self.assertEqual(rc_ok, 0)


@unittest.skipUnless(shutil.which("duckdb"), "duckdb CLI required for end-to-end merge check")
class TestMergeCountryParquetsEndToEnd(unittest.TestCase):

    def test_admin_polygons_merge_multi_region_tiebreak_spillover_and_metadata(self):
        exported_at = "2026-11-23T12:00:00Z"
        with tempfile.TemporaryDirectory() as tmp:
            in_dir = os.path.join(tmp, "temp_parquet")
            out_dir = os.path.join(tmp, "parquet")
            os.makedirs(in_dir)

            # Build synthetic temp parquets:
            # 1) ES_spain.parquet: ES relation 100 (missing center_lat & parent_osm_id, small area) + ES rel 200
            # 2) ES_canary-islands.parquet: ES relation 100 (has center_lat & parent_osm_id, larger area) + ES rel 300
            # 3) AD_andorra.parquet: AD relation 400
            # 4) IT_italy.parquet: IT relation 500 + spillover SM relation 600
            setup_sql = f"""
            LOAD spatial;
            COPY (
              SELECT * FROM (VALUES
                ('europe', 'ES', 2::TINYINT, 'relation', 100::BIGINT, 'España (truncated)', NULL::DOUBLE, NULL::BIGINT, 0.0, 40.0, 1.0, 41.0, ST_GeomFromText('POLYGON((0 40, 1 40, 1 41, 0 41, 0 40))')),
                ('europe', 'ES', 4::TINYINT, 'relation', 200::BIGINT, 'Madrid', 40.4, 100::BIGINT, -4.0, 40.0, -3.0, 41.0, ST_GeomFromText('POLYGON((-4 40, -3 40, -3 41, -4 41, -4 40))'))
              ) AS t(continent, country_code, admin_level, osm_type, osm_id, name, center_lat, parent_osm_id, bbox_minx, bbox_miny, bbox_maxx, bbox_maxy, geom)
            ) TO '{in_dir}/ES_spain.parquet' (FORMAT PARQUET, KV_METADATA {{'osm_data_timestamp': '2026-11-21T01:00:00Z'}});

            COPY (
              SELECT * FROM (VALUES
                ('europe', 'ES', 2::TINYINT, 'relation', 100::BIGINT, 'España (complete)', 40.0, 0::BIGINT, -18.0, 27.0, 4.0, 43.0, ST_GeomFromText('POLYGON((-18 27, 4 27, 4 43, -18 43, -18 27))')),
                ('europe', 'ES', 4::TINYINT, 'relation', 300::BIGINT, 'Canarias', 28.1, 100::BIGINT, -18.0, 27.5, -13.0, 29.5, ST_GeomFromText('POLYGON((-18 27.5, -13 27.5, -13 29.5, -18 29.5, -18 27.5))'))
              ) AS t(continent, country_code, admin_level, osm_type, osm_id, name, center_lat, parent_osm_id, bbox_minx, bbox_miny, bbox_maxx, bbox_maxy, geom)
            ) TO '{in_dir}/ES_canary-islands.parquet' (FORMAT PARQUET, KV_METADATA {{'osm_data_timestamp': '2026-11-21T03:00:00Z'}});

            COPY (
              SELECT 'europe' AS continent, 'AD' AS country_code, 2::TINYINT AS admin_level, 'relation' AS osm_type,
                     400::BIGINT AS osm_id, 'Andorra' AS name, 42.5 AS center_lat, NULL::BIGINT AS parent_osm_id,
                     1.4 AS bbox_minx, 42.4 AS bbox_miny, 1.8 AS bbox_maxx, 42.7 AS bbox_maxy,
                     ST_GeomFromText('POLYGON((1.4 42.4, 1.8 42.4, 1.8 42.7, 1.4 42.7, 1.4 42.4))') AS geom
            ) TO '{in_dir}/AD_andorra.parquet' (FORMAT PARQUET, KV_METADATA {{'osm_data_timestamp': '2026-11-20T05:00:00Z'}});

            COPY (
              SELECT * FROM (VALUES
                ('europe', 'IT', 2::TINYINT, 'relation', 500::BIGINT, 'Italia', 42.5, NULL::BIGINT, 7.0, 37.0, 18.0, 47.0, ST_GeomFromText('POLYGON((7 37, 18 37, 18 47, 7 47, 7 37))')),
                ('europe', 'SM', 2::TINYINT, 'relation', 600::BIGINT, 'San Marino', 43.9, NULL::BIGINT, 12.4, 43.9, 12.5, 44.0, ST_GeomFromText('POLYGON((12.4 43.9, 12.5 43.9, 12.5 44.0, 12.4 44.0, 12.4 43.9))'))
              ) AS t(continent, country_code, admin_level, osm_type, osm_id, name, center_lat, parent_osm_id, bbox_minx, bbox_miny, bbox_maxx, bbox_maxy, geom)
            ) TO '{in_dir}/IT_italy.parquet' (FORMAT PARQUET, KV_METADATA {{'osm_data_timestamp': '2026-11-21T07:00:00Z'}});
            """
            _run_duckdb_sql(setup_sql)

            rc = merge_country_parquets.main([
                "--layer", "admin-polygons",
                "--input-dir", in_dir,
                "--output-dir", out_dir,
                "--exported-at", exported_at,
                "--continent", "europe",
                "--require-non-empty",
            ])
            self.assertEqual(rc, 0)

            produced = sorted(os.listdir(out_dir))
            self.assertEqual(
                produced,
                [
                    "admin-polygons-AD.parquet",
                    "admin-polygons-ES.parquet",
                    "admin-polygons-IT.parquet",
                    "admin-polygons-SM.parquet",
                ],
            )

            es_rows = _query_duckdb_json(
                f"SELECT osm_id, name, admin_level FROM read_parquet('{out_dir}/admin-polygons-ES.parquet') ORDER BY osm_id"
            )
            self.assertEqual(len(es_rows), 3)
            self.assertEqual(es_rows[0]["osm_id"], 100)
            self.assertEqual(es_rows[0]["name"], "España (complete)")
            self.assertEqual([r["osm_id"] for r in es_rows], [100, 200, 300])

            sm_rows = _query_duckdb_json(
                f"SELECT osm_id, country_code, name FROM read_parquet('{out_dir}/admin-polygons-SM.parquet')"
            )
            self.assertEqual(sm_rows, [{"osm_id": 600, "country_code": "SM", "name": "San Marino"}])

            es_kv = _read_kv_metadata(f"{out_dir}/admin-polygons-ES.parquet")
            ad_kv = _read_kv_metadata(f"{out_dir}/admin-polygons-AD.parquet")
            sm_kv = _read_kv_metadata(f"{out_dir}/admin-polygons-SM.parquet")

            self.assertEqual(es_kv["country_code"], "ES")
            self.assertEqual(es_kv["exported_at"], exported_at)
            self.assertEqual(es_kv["osm_data_timestamp"], "2026-11-21T03:00:00Z")
            self.assertEqual(es_kv["dataset"], "OpenStreetMap Administrative Polygons")
            self.assertEqual(ad_kv["osm_data_timestamp"], "2026-11-20T05:00:00Z")
            self.assertEqual(sm_kv["osm_data_timestamp"], "2026-11-21T07:00:00Z")

    def test_companion_layers_merge_dedup_hilbert_and_metadata(self):
        exported_at = "2026-11-23T15:30:00Z"
        with tempfile.TemporaryDirectory() as tmp:
            # Places
            places_in = os.path.join(tmp, "temp_places")
            places_out = os.path.join(tmp, "out")
            os.makedirs(places_in)
            _run_duckdb_sql(f"""
            LOAD spatial;
            COPY (
              SELECT * FROM (VALUES
                ('europe', 'ES', 1::BIGINT, 'Madrid', 'city', -3.7, 40.4, ST_Point(-3.7, 40.4)),
                ('europe', 'ES', 1::BIGINT, 'Madrid dup', 'town', -3.7, 40.4, ST_Point(-3.7, 40.4)),
                ('europe', 'AD', 2::BIGINT, 'Andorra la Vella', 'city', 1.5, 42.5, ST_Point(1.5, 42.5))
              ) AS t(continent, country_code, osm_id, name, place_type, lon, lat, geom)
            ) TO '{places_in}/ES_spain.places.parquet' (FORMAT PARQUET, KV_METADATA {{'osm_data_timestamp': '2026-11-21T02:00:00Z'}});
            """)
            self.assertEqual(
                merge_country_parquets.main([
                    "--layer", "places",
                    "--input-dir", places_in,
                    "--output-dir", places_out,
                    "--exported-at", exported_at,
                ]),
                0,
            )
            es_places = _query_duckdb_json(f"SELECT osm_id, place_type FROM read_parquet('{places_out}/osm-places-ES.parquet')")
            self.assertEqual(es_places, [{"osm_id": 1, "place_type": "city"}])
            self.assertEqual(_read_kv_metadata(f"{places_out}/osm-places-ES.parquet")["dataset"], "OpenStreetMap Places")

            # Facilities, Landuse, Postal Codes (Hilbert-sorted spatial layers)
            spatial_layers = [
                ("facilities", "osm-facilities", "OpenStreetMap Facilities", "feature_class", "hospital", "parking"),
                ("landuse", "osm-landuse", "OpenStreetMap Landuse", "landuse", "forest", "residential"),
                ("postal-codes", "osm-postal-codes", "OpenStreetMap Postal Codes", "postal_code", "10115", "99999"),
            ]
            for layer, prefix, dataset, rank_col, val1, val2 in spatial_layers:
                in_dir = os.path.join(tmp, f"temp_{layer}")
                os.makedirs(in_dir)
                _run_duckdb_sql(f"""
                LOAD spatial;
                COPY (
                  SELECT * FROM (VALUES
                    ('europe', 'DE', 10::BIGINT, 'way', '{val2}', ST_GeomFromText('POLYGON((13 52, 13.1 52, 13.1 52.1, 13 52.1, 13 52))')),
                    ('europe', 'DE', 10::BIGINT, 'way', '{val1}', ST_GeomFromText('POLYGON((13 52, 13.1 52, 13.1 52.1, 13 52.1, 13 52))')),
                    ('europe', 'DE', 11::BIGINT, 'way', '{val1}', ST_GeomFromText('POLYGON((9 48, 9.1 48, 9.1 48.1, 9 48.1, 9 48))'))
                  ) AS t(continent, country_code, osm_id, osm_type, {rank_col}, geom)
                ) TO '{in_dir}/DE_germany.{layer}.parquet' (FORMAT PARQUET, KV_METADATA {{'osm_data_timestamp': '2026-11-21T08:00:00Z'}});
                """)
                rc = merge_country_parquets.main([
                    "--layer", layer,
                    "--input-dir", in_dir,
                    "--output-dir", places_out,
                    "--exported-at", exported_at,
                ])
                self.assertEqual(rc, 0)
                out_file = f"{places_out}/{prefix}-DE.parquet"
                rows = _query_duckdb_json(
                    f"SELECT osm_id, {rank_col} FROM read_parquet('{out_file}') ORDER BY osm_id"
                )
                self.assertEqual(rows, [
                    {"osm_id": 10, rank_col: val1},
                    {"osm_id": 11, rank_col: val1},
                ])
                kv = _read_kv_metadata(out_file)
                self.assertEqual(kv["dataset"], dataset)
                self.assertEqual(kv["country_code"], "DE")
                self.assertEqual(kv["exported_at"], exported_at)
                self.assertEqual(kv["osm_data_timestamp"], "2026-11-21T08:00:00Z")

    def test_cross_partition_border_bleed_is_filtered_using_countries_json(self):
        """Border-bleed features (e.g. AW/TT in VE, VI in VG) must not emit duplicate country Parquets."""
        exported_at = "2026-11-25T18:00:00Z"
        with tempfile.TemporaryDirectory() as tmp:
            sa_in = os.path.join(tmp, "sa_in")
            sa_out = os.path.join(tmp, "sa_out")
            os.makedirs(sa_in)
            _run_duckdb_sql(f"""
            LOAD spatial;
            COPY (
              SELECT * FROM (VALUES
                ('south-america', 'VE', 100::BIGINT, 'relation', 'Venezuela', 2::TINYINT, -73.0, 1.0, -60.0, 12.0, 10.0, -66.0, 1000::BIGINT, ST_GeomFromText('POLYGON((-67 8, -66 8, -66 9, -67 9, -67 8))')),
                ('central-america', 'AW', 101::BIGINT, 'relation', 'Aruba', 3::TINYINT, -70.1, 12.4, -69.8, 12.6, 12.5, -70.0, NULL::BIGINT, ST_GeomFromText('POLYGON((-70 12.4, -69.9 12.4, -69.9 12.6, -70 12.6, -70 12.4))')),
                ('central-america', 'TT', 102::BIGINT, 'relation', 'Trinidad and Tobago', 2::TINYINT, -61.9, 10.0, -60.5, 11.4, 10.5, -61.2, NULL::BIGINT, ST_GeomFromText('POLYGON((-61.5 10.2, -61.0 10.2, -61.0 10.8, -61.5 10.8, -61.5 10.2))'))
              ) AS t(continent, country_code, osm_id, osm_type, name, admin_level, bbox_minx, bbox_miny, bbox_maxx, bbox_maxy, center_lat, center_lon, parent_osm_id, geom)
            ) TO '{sa_in}/VE_venezuela.admin-polygons.parquet' (FORMAT PARQUET, KV_METADATA {{'osm_data_timestamp': '2026-11-25T01:00:00Z'}});
            """)
            rc = merge_country_parquets.main([
                "--layer", "admin-polygons",
                "--input-dir", sa_in,
                "--output-dir", sa_out,
                "--exported-at", exported_at,
                "--continent", "south-america",
                "--require-non-empty",
            ])
            self.assertEqual(rc, 0)
            self.assertEqual(sorted(os.listdir(sa_out)), ["admin-polygons-VE.parquet"])

            ca_in = os.path.join(tmp, "ca_in")
            ca_out = os.path.join(tmp, "ca_out")
            os.makedirs(ca_in)
            _run_duckdb_sql(f"""
            LOAD spatial;
            COPY (
              SELECT * FROM (VALUES
                ('central-america', 'VG', 200::BIGINT, 'relation', 'British Virgin Islands', 3::TINYINT, -64.7, 18.3, -64.2, 18.8, 18.4, -64.6, NULL::BIGINT, ST_GeomFromText('POLYGON((-64.7 18.4, -64.5 18.4, -64.5 18.5, -64.7 18.5, -64.7 18.4))')),
                ('north-america', 'VI', 201::BIGINT, 'relation', 'US Virgin Islands', 4::TINYINT, -65.1, 17.6, -64.5, 18.4, 18.3, -64.9, NULL::BIGINT, ST_GeomFromText('POLYGON((-65.0 18.3, -64.8 18.3, -64.8 18.4, -65.0 18.4, -65.0 18.3))'))
              ) AS t(continent, country_code, osm_id, osm_type, name, admin_level, bbox_minx, bbox_miny, bbox_maxx, bbox_maxy, center_lat, center_lon, parent_osm_id, geom)
            ) TO '{ca_in}/VG_british-virgin-islands.admin-polygons.parquet' (FORMAT PARQUET, KV_METADATA {{'osm_data_timestamp': '2026-11-25T01:00:00Z'}});
            """)
            rc = merge_country_parquets.main([
                "--layer", "admin-polygons",
                "--input-dir", ca_in,
                "--output-dir", ca_out,
                "--exported-at", exported_at,
                "--continent", "central-america",
                "--require-non-empty",
            ])
            self.assertEqual(rc, 0)
            self.assertEqual(sorted(os.listdir(ca_out)), ["admin-polygons-VG.parquet"])

    def test_partition_ownership_handles_overrides_and_dedicated_partitions(self):
        meta = merge_country_parquets.load_countries_continents(merge_country_parquets.DEFAULT_COUNTRIES_JSON)
        self.assertTrue(merge_country_parquets.country_belongs_to_partition("TR", "asia", meta))
        self.assertFalse(merge_country_parquets.country_belongs_to_partition("TR", "europe", meta))
        self.assertTrue(merge_country_parquets.country_belongs_to_partition("RU", "russia", meta))
        self.assertFalse(merge_country_parquets.country_belongs_to_partition("RU", "europe", meta))
        self.assertFalse(merge_country_parquets.country_belongs_to_partition("UA", "russia", meta))
        self.assertTrue(merge_country_parquets.country_belongs_to_partition("US", "us", meta))
        self.assertFalse(merge_country_parquets.country_belongs_to_partition("US", "north-america", meta))
        self.assertTrue(merge_country_parquets.country_belongs_to_partition("VI", "north-america", meta))
        self.assertFalse(merge_country_parquets.country_belongs_to_partition("VI", "central-america", meta))


if __name__ == "__main__":
    unittest.main()
