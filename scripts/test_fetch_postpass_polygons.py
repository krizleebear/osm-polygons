#!/usr/bin/env python3
import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from fetch_postpass_polygons import format_feature, load_candidates


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


if __name__ == "__main__":
    unittest.main()
