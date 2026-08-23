#!/usr/bin/env python3
import unittest
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from filter_places import process_place_feature, PLACE_WHITELIST, EXCLUDED_PLACES

def place_feat(properties, coordinates=[11.5755, 48.1374]):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": coordinates},
        "properties": properties
    }

class TestExportPlaces(unittest.TestCase):

    def test_valid_hamlet_node(self):
        feat = place_feat({
            "@type": "node",
            "@id": 1234567,
            "name": "Mooshäusl",
            "place": "hamlet",
            "wikidata": "Q99999",
            "population": "42",
            "name:en": "Mooshausl",
            "alt_name": "Moos"
        })
        res = process_place_feature(feat, continent="europe", country_code="DE")
        self.assertIsNotNone(res)
        self.assertEqual(res["continent"], "europe")
        self.assertEqual(res["country_code"], "DE")
        self.assertEqual(res["osm_id"], 1234567)
        self.assertEqual(res["name"], "Mooshäusl")
        self.assertEqual(res["place_type"], "hamlet")
        self.assertEqual(res["lon"], 11.5755)
        self.assertEqual(res["lat"], 48.1374)
        self.assertEqual(res["wikidata"], "Q99999")
        self.assertEqual(res["population"], 42)

        alt_names = json.loads(res["alt_names_json"])
        self.assertEqual(alt_names.get("name:en"), "Mooshausl")
        self.assertEqual(alt_names.get("alt_name"), "Moos")

    def test_all_whitelist_place_types(self):
        for place_type in PLACE_WHITELIST:
            feat = place_feat({"name": f"Test {place_type}", "place": place_type})
            res = process_place_feature(feat)
            self.assertIsNotNone(res, f"Failed for place={place_type}")
            expected_type = "neighbourhood" if place_type == "neighborhood" else place_type
            self.assertEqual(res["place_type"], expected_type)

    def test_excluded_places(self):
        for excluded in EXCLUDED_PLACES:
            feat = place_feat({"name": f"Test {excluded}", "place": excluded})
            self.assertIsNone(process_place_feature(feat), f"Should exclude place={excluded}")

    def test_unnamed_place_node_is_dropped(self):
        feat = place_feat({"place": "hamlet"})
        self.assertIsNone(process_place_feature(feat))

        feat_empty = place_feat({"place": "hamlet", "name": "   "})
        self.assertIsNone(process_place_feature(feat_empty))

    def test_name_fallback(self):
        feat = place_feat({"place": "village", "name:en": "English Name"})
        res = process_place_feature(feat)
        self.assertIsNotNone(res)
        self.assertEqual(res["name"], "English Name")

    def test_non_point_geometry_is_dropped(self):
        polygon_feat = {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[11.0, 48.0], [11.1, 48.0], [11.1, 48.1], [11.0, 48.0]]]},
            "properties": {"place": "hamlet", "name": "Hamlet Area"}
        }
        self.assertIsNone(process_place_feature(polygon_feat))

    def test_invalid_coordinates(self):
        feat = place_feat({"place": "hamlet", "name": "Test"}, coordinates=[200.0, 48.0])
        self.assertIsNone(process_place_feature(feat))

        feat_lat = place_feat({"place": "hamlet", "name": "Test"}, coordinates=[11.0, 95.0])
        self.assertIsNone(process_place_feature(feat_lat))

    def test_population_parsing(self):
        feat = place_feat({"place": "city", "name": "Graz", "population": "303,419"})
        res = process_place_feature(feat)
        self.assertEqual(res["population"], 303419)

        feat_invalid = place_feat({"place": "city", "name": "Unknown", "population": "many"})
        res2 = process_place_feature(feat_invalid)
        self.assertIsNone(res2["population"])

if __name__ == "__main__":
    unittest.main()
