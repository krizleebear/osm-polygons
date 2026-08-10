#!/usr/bin/env python3
import unittest
import json
from filter_polygons import process_feature

class TestFilterPolygons(unittest.TestCase):

    def test_mainland_relation_enforcement(self):
        # FR relation 2202162
        feature = {
            "type": "Feature",
            "properties": {"id": 2202162, "admin_level": "3"}
        }
        res = process_feature(feature)
        self.assertIsNotNone(res)
        self.assertEqual(res["properties"]["admin_level"], "2")
        self.assertEqual(res["properties"]["name"], "France (Métropole)")
        self.assertEqual(res["properties"]["ISO3166-1"], "FR")

    def test_tag_loss_prevention(self):
        # Missing 'name' tag but has 'name:en'
        feature = {
            "type": "Feature",
            "properties": {"admin_level": "4", "name:en": "Bavaria"}
        }
        res = process_feature(feature)
        self.assertIsNotNone(res)
        self.assertEqual(res["properties"]["name"], "Bavaria")

        # Missing all name tags -> should be dropped
        feature_no_name = {
            "type": "Feature",
            "properties": {"admin_level": "4"}
        }
        self.assertIsNone(process_feature(feature_no_name))

    def test_territorial_sea_flagging(self):
        feature = {
            "type": "Feature",
            "properties": {"admin_level": "2", "name": "águas territoriais portuguesas", "border_type": "territorial"}
        }
        res = process_feature(feature)
        self.assertIsNotNone(res)
        self.assertTrue(res["properties"].get("is_territorial_sea"))

    def test_level_4_fallback_mapping(self):
        feature = {
            "type": "Feature",
            "properties": {"admin_level": "6", "name": "Harju maakond", "ISO3166-1": "EE"}
        }
        res = process_feature(feature, country_code="EE")
        self.assertIsNotNone(res)
        self.assertEqual(res["properties"].get("admin_level_mapped"), "4")

if __name__ == "__main__":
    unittest.main()
