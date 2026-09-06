#!/usr/bin/env python3
"""
Unit tests for scripts/filter_landuse.py.
"""

import json
import unittest
from filter_landuse import clean_tags, normalize_osm_type, process_landuse_feature


class TestFilterLanduse(unittest.TestCase):

    def test_clean_tags_pruning(self):
        props = {
            "@id": 123,
            "landuse": "residential",
            "name": "Stätzling",
            "source": "survey",
            "note": "fixme",
            "tiger:cfcc": "A41",
            "building": "yes",
        }
        cleaned = clean_tags(props)
        self.assertNotIn("@id", cleaned)
        self.assertNotIn("source", cleaned)
        self.assertNotIn("note", cleaned)
        self.assertNotIn("tiger:cfcc", cleaned)
        self.assertEqual(cleaned["landuse"], "residential")
        self.assertEqual(cleaned["name"], "Stätzling")
        self.assertEqual(cleaned["building"], "yes")

    def test_normalize_osm_type(self):
        self.assertEqual(normalize_osm_type("W"), "way")
        self.assertEqual(normalize_osm_type("way"), "way")
        self.assertEqual(normalize_osm_type("R"), "relation")
        self.assertEqual(normalize_osm_type("relation"), "relation")
        self.assertEqual(normalize_osm_type("multipolygon"), "relation")
        self.assertEqual(normalize_osm_type(None, geom_type="Polygon"), "way")
        self.assertEqual(normalize_osm_type(None, geom_type="MultiPolygon"), "relation")

    def test_process_valid_residential_way(self):
        feat = {
            "type": "Feature",
            "id": 89960316,
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[10.9, 48.4], [10.95, 48.4], [10.95, 48.45], [10.9, 48.45], [10.9, 48.4]]
                ]
            },
            "properties": {
                "@id": 89960316,
                "@type": "way",
                "landuse": "residential",
                "name": "Stätzling",
            }
        }
        res = process_landuse_feature(feat, continent="europe", country_code="DE")
        self.assertIsNotNone(res)
        self.assertEqual(res["continent"], "europe")
        self.assertEqual(res["country_code"], "DE")
        self.assertEqual(res["osm_id"], 89960316)
        self.assertEqual(res["osm_type"], "way")
        self.assertEqual(res["landuse"], "residential")
        self.assertEqual(res["name"], "Stätzling")
        self.assertIsNone(res["name_en"])

        geom = json.loads(res["geom_json"])
        self.assertEqual(geom["type"], "Polygon")

    def test_process_valid_commercial_multipolygon(self):
        feat = {
            "type": "Feature",
            "id": 123456,
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [
                    [[[[10.1, 48.1], [10.2, 48.1], [10.2, 48.2], [10.1, 48.2], [10.1, 48.1]]]]
                ]
            },
            "properties": {
                "@id": 123456,
                "@type": "relation",
                "landuse": "commercial",
                "name:en": "Commercial Center",
            }
        }
        res = process_landuse_feature(feat, continent="europe", country_code="DE")
        self.assertIsNotNone(res)
        self.assertEqual(res["osm_type"], "relation")
        self.assertEqual(res["landuse"], "commercial")
        self.assertEqual(res["name"], "Commercial Center")
        self.assertEqual(res["name_en"], "Commercial Center")

    def test_reject_non_whitelisted_landuse(self):
        feat = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
            },
            "properties": {
                "@id": 999,
                "landuse": "forest"
            }
        }
        self.assertIsNone(process_landuse_feature(feat))

    def test_reject_non_polygonal_geometry(self):
        point_feat = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [10.0, 48.0]
            },
            "properties": {
                "@id": 111,
                "landuse": "residential"
            }
        }
        self.assertIsNone(process_landuse_feature(point_feat))

        line_feat = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[10.0, 48.0], [10.1, 48.1]]
            },
            "properties": {
                "@id": 222,
                "landuse": "residential"
            }
        }
        self.assertIsNone(process_landuse_feature(line_feat))

    def test_reject_empty_or_malformed_feature(self):
        self.assertIsNone(process_landuse_feature({}))
        self.assertIsNone(process_landuse_feature(None))
        self.assertIsNone(process_landuse_feature({"type": "Feature", "properties": None}))
        self.assertIsNone(process_landuse_feature({
            "type": "Feature",
            "properties": {"landuse": "residential"},
            "geometry": {"type": "Polygon", "coordinates": []}
        }))


if __name__ == "__main__":
    unittest.main()
