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


if __name__ == "__main__":
    unittest.main()
