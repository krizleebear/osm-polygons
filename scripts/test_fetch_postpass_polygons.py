#!/usr/bin/env python3
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import fetch_postpass_polygons
from fetch_postpass_polygons import format_feature, load_cached_reference, load_candidates


class TestFetchPostpassPolygons(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.candidates_file = os.path.join(self.temp_dir.name, "candidates.json")
        candidates_data = {
            "candidates": [
                {"osm_id": 1124039, "country": "MC", "admin_level": 2, "name": "Monaco"}
            ]
        }
        with open(self.candidates_file, "w") as f:
            json.dump(candidates_data, f)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_candidates(self):
        candidates = load_candidates(self.candidates_file)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["osm_id"], 1124039)
        self.assertEqual(candidates[0]["country"], "MC")

    def test_format_feature_flattens_tags(self):
        raw_postpass_feature = {
            "type": "Feature",
            "properties": {
                "osm_type": "R",
                "osm_id": 1124039,
                "tags": {
                    "ISO3166-1": "MC",
                    "admin_level": "2",
                    "name": "Monaco",
                    "wikidata": "Q235"
                }
            },
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [[[[7.4, 43.7], [7.5, 43.7], [7.5, 43.8], [7.4, 43.7]]]]
            }
        }
        fallback_info = {"osm_id": 1124039, "country": "MC", "admin_level": 2, "name": "Monaco"}
        formatted = format_feature(raw_postpass_feature, fallback_info)

        self.assertEqual(formatted["type"], "Feature")
        self.assertEqual(formatted["id"], 1124039)
        props = formatted["properties"]
        self.assertEqual(props["osm_id"], 1124039)
        self.assertEqual(props["name"], "Monaco")
        self.assertEqual(props["ISO3166-1"], "MC")
        self.assertEqual(props["wikidata"], "Q235")
        self.assertNotIn("tags", props)

    def test_load_cached_reference(self):
        cache_file = os.path.join(self.temp_dir.name, "cached.geojsonseq")
        cached_feat = {
            "type": "Feature",
            "id": 1124039,
            "properties": {"osm_id": 1124039, "name": "Monaco", "admin_level": "2"},
            "geometry": {"type": "MultiPolygon", "coordinates": [[[[7.4, 43.7], [7.5, 43.7], [7.5, 43.8], [7.4, 43.7]]]]},
        }
        with open(cache_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(cached_feat) + "\n")

        loaded = load_cached_reference(cache_file)
        self.assertIn(1124039, loaded)
        self.assertEqual(loaded[1124039]["properties"]["name"], "Monaco")
        self.assertEqual(load_cached_reference(os.path.join(self.temp_dir.name, "missing.geojsonseq")), {})

    def test_main_falls_back_to_cache_with_warning_on_postpass_error(self):
        cache_file = os.path.join(self.temp_dir.name, "cached.geojsonseq")
        out_dir = os.path.join(self.temp_dir.name, "out")
        cached_feat = {
            "type": "Feature",
            "id": 1124039,
            "properties": {"osm_id": 1124039, "name": "Monaco", "admin_level": "2"},
            "geometry": {"type": "MultiPolygon", "coordinates": [[[[7.4, 43.7], [7.5, 43.7], [7.5, 43.8], [7.4, 43.7]]]]},
        }
        with open(cache_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(cached_feat) + "\n")

        stdout_buf = io.StringIO()
        with patch.object(fetch_postpass_polygons, "query_postpass_polygon", side_effect=RuntimeError("503 Service Unavailable")), \
             patch.object(fetch_postpass_polygons.time, "sleep"), \
             patch.object(sys, "argv", ["fetch_postpass_polygons.py", "--candidates", self.candidates_file, "--out-dir", out_dir, "--retry-count", "2", "--fallback-reference", cache_file]), \
             redirect_stdout(stdout_buf):
            fetch_postpass_polygons.main()

        output = stdout_buf.getvalue()
        self.assertIn("##vso[task.logissue type=warning]", output)
        self.assertIn("falling back to cached reference polygon", output)

        with open(os.path.join(out_dir, "manifest.json"), "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["total_count"], 1)
        self.assertEqual(manifest["fallback_count"], 1)
        self.assertEqual(manifest["candidates"][0]["source"], "cached_fallback")

    def test_main_fails_when_postpass_fails_and_no_cache_entry(self):
        out_dir = os.path.join(self.temp_dir.name, "out_fail")
        with patch.object(fetch_postpass_polygons, "query_postpass_polygon", side_effect=RuntimeError("503 Service Unavailable")), \
             patch.object(fetch_postpass_polygons.time, "sleep"), \
             patch.object(sys, "argv", ["fetch_postpass_polygons.py", "--candidates", self.candidates_file, "--out-dir", out_dir, "--retry-count", "1"]), \
             redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                fetch_postpass_polygons.main()

    def test_reuse_cached_fetches_only_missing_candidates(self):
        candidates_file = os.path.join(self.temp_dir.name, "two_candidates.json")
        with open(candidates_file, "w", encoding="utf-8") as f:
            json.dump({
                "candidates": [
                    {"osm_id": 1124039, "country": "MC", "admin_level": 2, "name": "Monaco"},
                    {"osm_id": 2186665, "country": "TO", "admin_level": 2, "name": "Tonga"},
                ]
            }, f)

        cache_file = os.path.join(self.temp_dir.name, "cached.geojsonseq")
        cached_mc = {
            "type": "Feature",
            "id": 1124039,
            "properties": {"osm_id": 1124039, "name": "Monaco", "admin_level": "2", "ISO3166-1": "MC"},
            "geometry": {"type": "MultiPolygon", "coordinates": [[[[7.4, 43.7], [7.5, 43.7], [7.5, 43.8], [7.4, 43.7]]]]},
        }
        with open(cache_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(cached_mc) + "\n")

        to_raw = {
            "type": "Feature",
            "properties": {
                "osm_type": "R",
                "osm_id": 2186665,
                "tags": {"ISO3166-1": "TO", "admin_level": "2", "name": "Tonga"},
            },
            "geometry": {"type": "MultiPolygon", "coordinates": [[[[-175.3, -21.2], [-175.1, -21.2], [-175.1, -21.1], [-175.3, -21.2]]]]},
        }

        out_dir = os.path.join(self.temp_dir.name, "out_reuse")
        stdout_buf = io.StringIO()
        with patch.object(fetch_postpass_polygons, "query_postpass_polygon", return_value=to_raw) as mock_query, \
             patch.object(sys, "argv", [
                 "fetch_postpass_polygons.py",
                 "--candidates", candidates_file,
                 "--out-dir", out_dir,
                 "--reuse-cached",
                 "--fallback-reference", cache_file,
             ]), \
             redirect_stdout(stdout_buf):
            fetch_postpass_polygons.main()

        mock_query.assert_called_once_with(2186665)
        output = stdout_buf.getvalue()
        self.assertNotIn("##vso[task.logissue type=warning]", output)
        self.assertIn("CACHED (Rel 1124039", output)

        with open(os.path.join(out_dir, "manifest.json"), "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["total_count"], 2)
        self.assertEqual(manifest["reused_count"], 1)
        self.assertEqual(manifest["fetched_count"], 1)
        self.assertEqual(manifest["fallback_count"], 0)

    def test_multiple_fallback_references_priority(self):
        cache1 = os.path.join(self.temp_dir.name, "cache1.geojsonseq")
        cache2 = os.path.join(self.temp_dir.name, "cache2.geojsonseq")
        with open(cache1, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "Feature",
                "id": 1124039,
                "properties": {"osm_id": 1124039, "name": "Monaco Primary"},
                "geometry": {"type": "MultiPolygon", "coordinates": []},
            }) + "\n")
        with open(cache2, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "Feature",
                "id": 1124039,
                "properties": {"osm_id": 1124039, "name": "Monaco Secondary"},
                "geometry": {"type": "MultiPolygon", "coordinates": []},
            }) + "\n")
            f.write(json.dumps({
                "type": "Feature",
                "id": 2186665,
                "properties": {"osm_id": 2186665, "name": "Tonga Secondary"},
                "geometry": {"type": "MultiPolygon", "coordinates": []},
            }) + "\n")

        loaded = fetch_postpass_polygons.load_cached_references([cache1, cache2])
        self.assertEqual(loaded[1124039]["properties"]["name"], "Monaco Primary")
        self.assertEqual(loaded[2186665]["properties"]["name"], "Tonga Secondary")


if __name__ == "__main__":
    unittest.main()
