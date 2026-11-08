#!/usr/bin/env python3
"""
Unit tests for scripts/filter_postal_codes.py.
"""

import json
import unittest
from filter_postal_codes import (
    clean_tags,
    normalize_osm_type,
    extract_postal_code,
    process_postal_code_feature,
)


class TestFilterPostalCodes(unittest.TestCase):

    def test_clean_tags_pruning(self):
        props = {
            "@id": 123,
            "boundary": "postal_code",
            "postal_code": "80331",
            "name": "80331 München",
            "source": "survey",
            "note": "fixme",
            "tiger:cfcc": "A41",
        }
        cleaned = clean_tags(props)
        self.assertNotIn("@id", cleaned)
        self.assertNotIn("source", cleaned)
        self.assertNotIn("note", cleaned)
        self.assertNotIn("tiger:cfcc", cleaned)
        self.assertEqual(cleaned["boundary"], "postal_code")
        self.assertEqual(cleaned["postal_code"], "80331")
        self.assertEqual(cleaned["name"], "80331 München")

    def test_normalize_osm_type(self):
        self.assertEqual(normalize_osm_type("W"), "way")
        self.assertEqual(normalize_osm_type("way"), "way")
        self.assertEqual(normalize_osm_type("R"), "relation")
        self.assertEqual(normalize_osm_type("relation"), "relation")
        self.assertEqual(normalize_osm_type("multipolygon"), "relation")
        self.assertEqual(normalize_osm_type(None, geom_type="Polygon"), "way")
        self.assertEqual(normalize_osm_type(None, geom_type="MultiPolygon"), "relation")

    def test_extract_postal_code_priority_chain(self):
        # 1. postal_code takes highest priority
        self.assertEqual(
            extract_postal_code({
                "postal_code": "80331",
                "postcode": "80332",
                "addr:postcode": "80333",
                "ref": "80334",
            }),
            "80331",
        )
        # 2. postcode when postal_code is missing or empty
        self.assertEqual(
            extract_postal_code({
                "postal_code": "   ",
                "postcode": "80332",
                "addr:postcode": "80333",
                "ref": "80334",
            }),
            "80332",
        )
        # 3. addr:postcode when postal_code and postcode are missing
        self.assertEqual(
            extract_postal_code({
                "addr:postcode": "80333",
                "ref": "80334",
            }),
            "80333",
        )
        # 4. ref as last resort
        self.assertEqual(
            extract_postal_code({
                "ref": "80334",
            }),
            "80334",
        )
        # 5. None when all missing or empty
        self.assertIsNone(extract_postal_code({}))
        self.assertIsNone(extract_postal_code({"postal_code": "  ", "ref": " / ; "}))
        self.assertIsNone(extract_postal_code(None))

    def test_extract_postal_code_normalization(self):
        self.assertEqual(extract_postal_code({"postal_code": " 80331 / 80333 "}), "80331;80333")
        self.assertEqual(extract_postal_code({"postal_code": "80331; 80333 ; ; / 80335/"}), "80331;80333;80335")
        self.assertEqual(extract_postal_code({"postcode": "  10115  "}), "10115")

    def test_process_valid_postal_code_relation(self):
        feat = {
            "type": "Feature",
            "id": 1104550,
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [
                    [[[11.56, 48.13], [11.58, 48.13], [11.58, 48.14], [11.56, 48.14], [11.56, 48.13]]]
                ],
            },
            "properties": {
                "@id": 1104550,
                "@type": "relation",
                "boundary": "postal_code",
                "postal_code": " 80331 / 80333 ",
                "name": "80331 München",
                "note": "Relevant PLZ",
            },
        }
        res = process_postal_code_feature(feat, continent="europe", country_code="DE")
        self.assertIsNotNone(res)
        self.assertEqual(res["continent"], "europe")
        self.assertEqual(res["country_code"], "DE")
        self.assertEqual(res["osm_id"], 1104550)
        self.assertEqual(res["osm_type"], "relation")
        self.assertEqual(res["postal_code"], "80331;80333")
        self.assertEqual(res["name"], "80331 München")

        geom = json.loads(res["geom_json"])
        self.assertEqual(geom["type"], "MultiPolygon")
        tags = json.loads(res["tags"])
        self.assertNotIn("note", tags)
        self.assertEqual(tags["boundary"], "postal_code")

    def test_process_valid_postal_code_way(self):
        feat = {
            "type": "Feature",
            "id": 223344,
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[10.0, 48.0], [10.1, 48.0], [10.1, 48.1], [10.0, 48.1], [10.0, 48.0]]
                ],
            },
            "properties": {
                "@id": 223344,
                "@type": "way",
                "boundary": "postal_code",
                "postcode": "1010",
                "official_name": "Innere Stadt",
            },
        }
        res = process_postal_code_feature(feat, continent="europe", country_code="AT")
        self.assertIsNotNone(res)
        self.assertEqual(res["osm_id"], 223344)
        self.assertEqual(res["osm_type"], "way")
        self.assertEqual(res["postal_code"], "1010")
        self.assertEqual(res["name"], "Innere Stadt")

    def test_mandatory_postal_code_gating(self):
        feat = {
            "type": "Feature",
            "id": 999,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
            "properties": {
                "@id": 999,
                "boundary": "postal_code",
                "name": "Missing PLZ",
            },
        }
        self.assertIsNone(process_postal_code_feature(feat))

        feat_empty = {
            "type": "Feature",
            "id": 999,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
            "properties": {
                "@id": 999,
                "boundary": "postal_code",
                "postal_code": "   ",
            },
        }
        self.assertIsNone(process_postal_code_feature(feat_empty))

    def test_blacklist_rejection(self):
        base_geom = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        }

        # 1. end_date present
        feat_end_date = {
            "type": "Feature",
            "geometry": base_geom,
            "properties": {
                "@id": 1,
                "boundary": "postal_code",
                "postal_code": "12345",
                "end_date": "1993-07-01",
            },
        }
        self.assertIsNone(process_postal_code_feature(feat_end_date))

        # 2. historic=yes
        feat_historic_yes = {
            "type": "Feature",
            "geometry": base_geom,
            "properties": {
                "@id": 2,
                "boundary": "postal_code",
                "postal_code": "12345",
                "historic": "yes",
            },
        }
        self.assertIsNone(process_postal_code_feature(feat_historic_yes))

        # 3. historic=postal_code
        feat_historic_pc = {
            "type": "Feature",
            "geometry": base_geom,
            "properties": {
                "@id": 3,
                "boundary": "postal_code",
                "postal_code": "12345",
                "historic": "postal_code",
            },
        }
        self.assertIsNone(process_postal_code_feature(feat_historic_pc))

        # 4. boundary=historic:postal_code
        feat_boundary_historic = {
            "type": "Feature",
            "geometry": base_geom,
            "properties": {
                "@id": 4,
                "boundary": "historic:postal_code",
                "postal_code": "12345",
            },
        }
        self.assertIsNone(process_postal_code_feature(feat_boundary_historic))

        # 5. historic=no / false / 0 is allowed
        for allowed_historic in ("no", "false", "0", ""):
            feat_allowed = {
                "type": "Feature",
                "geometry": base_geom,
                "properties": {
                    "@id": 5,
                    "boundary": "postal_code",
                    "postal_code": "12345",
                    "historic": allowed_historic,
                },
            }
            res = process_postal_code_feature(feat_allowed)
            self.assertIsNotNone(res, f"Expected historic='{allowed_historic}' to be allowed")
            self.assertEqual(res["postal_code"], "12345")

    def test_geometry_validation(self):
        props = {
            "@id": 111,
            "boundary": "postal_code",
            "postal_code": "80331",
        }

        # Point rejected
        self.assertIsNone(process_postal_code_feature({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [11.5, 48.1]},
            "properties": props,
        }))

        # LineString rejected
        self.assertIsNone(process_postal_code_feature({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[11.5, 48.1], [11.6, 48.2]]},
            "properties": props,
        }))

        # Empty coordinates rejected
        self.assertIsNone(process_postal_code_feature({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": []},
            "properties": props,
        }))

        # Malformed features rejected
        self.assertIsNone(process_postal_code_feature({}))
        self.assertIsNone(process_postal_code_feature(None))


if __name__ == "__main__":
    unittest.main()
