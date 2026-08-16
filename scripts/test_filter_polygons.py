#!/usr/bin/env python3
import unittest
import json
from filter_polygons import process_feature

# Minimal valid polygon geometry shared by most fixtures (real osmium export always emits geometry).
POLY_GEOM = {"type": "Polygon", "coordinates": [[[11.0, 48.0], [11.1, 48.0], [11.1, 48.1], [11.0, 48.0]]]}

def feat(properties, geometry=POLY_GEOM):
    return {"type": "Feature", "geometry": geometry, "properties": properties}

class TestFilterPolygons(unittest.TestCase):

    def test_mainland_relation_enforcement(self):
        # FR relation 2202162
        feature = feat({"id": 2202162, "admin_level": "3"})
        res = process_feature(feature)
        self.assertIsNotNone(res)
        self.assertEqual(res["properties"]["admin_level"], "2")
        self.assertEqual(res["properties"]["name"], "France (Métropole)")
        self.assertEqual(res["properties"]["ISO3166-1"], "FR")

    def test_tag_loss_prevention(self):
        # Missing 'name' tag but has 'name:en'
        feature = feat({"admin_level": "4", "name:en": "Bavaria"})
        res = process_feature(feature)
        self.assertIsNotNone(res)
        self.assertEqual(res["properties"]["name"], "Bavaria")

        # Missing all name tags -> should be dropped
        feature_no_name = feat({"admin_level": "4"})
        self.assertIsNone(process_feature(feature_no_name))

    def test_territorial_sea_flagging(self):
        feature = feat({"admin_level": "2", "name": "águas territoriais portuguesas", "border_type": "territorial"})
        res = process_feature(feature)
        self.assertIsNotNone(res)
        self.assertTrue(res["properties"].get("is_territorial_sea"))

    def test_level_4_fallback_mapping(self):
        feature = feat({"admin_level": "6", "name": "Harju maakond", "ISO3166-1": "EE"})
        res = process_feature(feature, country_code="EE")
        self.assertIsNotNone(res)
        self.assertEqual(res["properties"].get("admin_level_mapped"), "4")

    def test_statistical_boundary_admin_level_synthesis(self):
        feature = feat({"@type": "relation", "type": "boundary", "boundary": "statistical", "name": "Innenstadt"})
        res = process_feature(feature)
        self.assertIsNotNone(res)
        self.assertEqual(res["properties"]["admin_level"], "10")

    def test_statistical_boundary_keeps_existing_admin_level(self):
        feature = feat({"@type": "relation", "type": "boundary", "boundary": "statistical", "admin_level": "9", "name": "Bezirk 1"})
        res = process_feature(feature)
        self.assertIsNotNone(res)
        self.assertEqual(res["properties"]["admin_level"], "9")

    def test_place_based_boundary_relation(self):
        cases = {"suburb": "9", "quarter": "10", "neighbourhood": "11", "borough": "9"}
        for place, expected in cases.items():
            feature = feat({"@type": "relation", "type": "boundary", "place": place, "name": "Ortsteil"})
            res = process_feature(feature)
            self.assertIsNotNone(res, place)
            self.assertEqual(res["properties"]["admin_level"], expected, place)

    def test_place_tag_on_non_boundary_relation_is_dropped(self):
        feature = feat({"@type": "relation", "type": "route", "place": "suburb", "name": "x"})
        self.assertIsNone(process_feature(feature))

    def test_nameless_level2_with_iso_kept(self):
        feature = feat({"admin_level": "2", "ISO3166-1": "AT"})
        res = process_feature(feature)
        self.assertIsNotNone(res)
        self.assertEqual(res["properties"]["name"], "AT")

    def test_nameless_level2_without_iso_dropped(self):
        feature = feat({"admin_level": "2"})
        self.assertIsNone(process_feature(feature))

    def test_non_polygonal_geometry_is_dropped(self):
        # Spec §4.2: only Polygon/MultiPolygon geometries are emitted; Point/LineString
        # leak through from osmium tags-filter referenced member nodes/ways.
        point = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [11.71, 48.39]},
            "properties": {"admin_level": "10", "name": "Vötting", "place": "suburb"}
        }
        self.assertIsNone(process_feature(point))

        line = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[11.0, 48.0], [11.1, 48.1]]},
            "properties": {"admin_level": "8", "name": "Grenzlinie"}
        }
        self.assertIsNone(process_feature(line))

        polygon = {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[11.0, 48.0], [11.1, 48.0], [11.1, 48.1], [11.0, 48.0]]]},
            "properties": {"admin_level": "8", "name": "Stadt"}
        }
        self.assertIsNotNone(process_feature(polygon))

if __name__ == "__main__":
    unittest.main()
