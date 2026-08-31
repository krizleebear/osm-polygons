#!/usr/bin/env python3
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from filter_facilities import (
    classify_facility,
    clean_tags,
    normalize_osm_type,
    process_facility_feature,
)


def make_feature(properties, geom_type="LineString", coordinates=None, feat_id=123):
    if coordinates is None:
        if geom_type == "Point":
            coordinates = [11.5755, 48.1374]
        elif geom_type == "LineString":
            coordinates = [[11.5755, 48.1374], [11.5760, 48.1380], [11.5770, 48.1390]]
        elif geom_type == "Polygon":
            coordinates = [[[11.5, 48.1], [11.6, 48.1], [11.6, 48.2], [11.5, 48.2], [11.5, 48.1]]]
        elif geom_type == "MultiPolygon":
            coordinates = [[[[11.5, 48.1], [11.6, 48.1], [11.6, 48.2], [11.5, 48.2], [11.5, 48.1]]]]

    return {
        "type": "Feature",
        "id": feat_id,
        "geometry": {
            "type": geom_type,
            "coordinates": coordinates
        },
        "properties": properties
    }


class TestFilterFacilities(unittest.TestCase):

    def test_motorway_linestring_preserves_node_order(self):
        coords = [[11.1, 48.1], [11.2, 48.2], [11.3, 48.3]]
        feat = make_feature({
            "@type": "way",
            "@id": 1001,
            "highway": "motorway",
            "ref": "A 8",
            "name": "Bundesautobahn 8",
            "oneway": "yes",
            "source": "survey",
            "fixme": "check lanes"
        }, geom_type="LineString", coordinates=coords)

        res = process_facility_feature(feat, continent="europe", country_code="DE")
        self.assertIsNotNone(res)
        self.assertEqual(res["continent"], "europe")
        self.assertEqual(res["country_code"], "DE")
        self.assertEqual(res["osm_id"], 1001)
        self.assertEqual(res["osm_type"], "W")
        self.assertEqual(res["feature_class"], "motorway")

        # Verify geometry is exact LineString with untouched coordinate sequence
        geom = json.loads(res["geom_json"])
        self.assertEqual(geom["type"], "LineString")
        self.assertEqual(geom["coordinates"], coords)

        # Verify tag pruning
        tags = json.loads(res["tags"])
        self.assertEqual(tags.get("ref"), "A 8")
        self.assertEqual(tags.get("highway"), "motorway")
        self.assertEqual(tags.get("oneway"), "yes")
        self.assertNotIn("source", tags)
        self.assertNotIn("fixme", tags)
        self.assertNotIn("@type", tags)
        self.assertNotIn("@id", tags)

    def test_trunk_and_links(self):
        for hw in ["trunk", "motorway_link", "trunk_link"]:
            feat = make_feature({"@type": "way", "@id": 2000, "highway": hw}, geom_type="LineString")
            res = process_facility_feature(feat)
            self.assertIsNotNone(res, f"Failed for highway={hw}")
            self.assertEqual(res["feature_class"], "motorway")

    def test_motorway_rejects_polygon_or_point(self):
        feat_point = make_feature({"highway": "motorway"}, geom_type="Point")
        self.assertIsNone(process_facility_feature(feat_point))

        feat_poly = make_feature({"highway": "motorway"}, geom_type="Polygon")
        self.assertIsNone(process_facility_feature(feat_poly))

    def test_junction_point(self):
        feat = make_feature({
            "@type": "node",
            "@id": 3001,
            "highway": "motorway_junction",
            "ref": "72",
            "name": "Kreuz München-Süd"
        }, geom_type="Point")

        res = process_facility_feature(feat, continent="europe", country_code="DE")
        self.assertIsNotNone(res)
        self.assertEqual(res["osm_id"], 3001)
        self.assertEqual(res["osm_type"], "N")
        self.assertEqual(res["feature_class"], "junction")

        geom = json.loads(res["geom_json"])
        self.assertEqual(geom["type"], "Point")

    def test_junction_rejects_linestring(self):
        feat = make_feature({"highway": "motorway_junction"}, geom_type="LineString")
        self.assertIsNone(process_facility_feature(feat))

    def test_service_area_polygon(self):
        for hw in ["services", "rest_area"]:
            feat = make_feature({
                "@type": "way",
                "@id": 4001,
                "highway": hw,
                "name": "Raststätte Holzkirchen"
            }, geom_type="Polygon")
            res = process_facility_feature(feat)
            self.assertIsNotNone(res, f"Failed for highway={hw}")
            self.assertEqual(res["feature_class"], "service_area")

    def test_service_area_rejects_point(self):
        feat = make_feature({"highway": "services"}, geom_type="Point")
        self.assertIsNone(process_facility_feature(feat))

    def test_airport_polygon(self):
        feat = make_feature({
            "@type": "relation",
            "@id": 5001,
            "aeroway": "aerodrome",
            "name": "Flughafen München Franz Josef Strauß",
            "iata": "MUC",
            "icao": "EDDM"
        }, geom_type="Polygon")
        res = process_facility_feature(feat)
        self.assertIsNotNone(res)
        self.assertEqual(res["osm_type"], "R")
        self.assertEqual(res["feature_class"], "airport")
        tags = json.loads(res["tags"])
        self.assertEqual(tags.get("iata"), "MUC")
        self.assertEqual(tags.get("icao"), "EDDM")

    def test_shopping_mall(self):
        feat = make_feature({
            "@type": "way",
            "@id": 6001,
            "shop": "mall",
            "name": "Olympia-Einkaufszentrum"
        }, geom_type="Polygon")
        res = process_facility_feature(feat)
        self.assertIsNotNone(res)
        self.assertEqual(res["feature_class"], "shopping_mall")

    def test_university(self):
        feat = make_feature({
            "@type": "relation",
            "@id": 7001,
            "amenity": "university",
            "name": "Ludwig-Maximilians-Universität München"
        }, geom_type="MultiPolygon")
        res = process_facility_feature(feat)
        self.assertIsNotNone(res)
        self.assertEqual(res["feature_class"], "university")

    def test_hospital(self):
        feat = make_feature({
            "@type": "way",
            "@id": 8001,
            "amenity": "hospital",
            "name": "Klinikum Großhadern"
        }, geom_type="Polygon")
        res = process_facility_feature(feat)
        self.assertIsNotNone(res)
        self.assertEqual(res["feature_class"], "hospital")

    def test_stadium(self):
        feat = make_feature({
            "@type": "way",
            "@id": 9001,
            "leisure": "stadium",
            "name": "Allianz Arena"
        }, geom_type="Polygon")
        res = process_facility_feature(feat)
        self.assertIsNotNone(res)
        self.assertEqual(res["feature_class"], "stadium")

    def test_osm_type_normalization(self):
        self.assertEqual(normalize_osm_type("node"), "N")
        self.assertEqual(normalize_osm_type("NODE"), "N")
        self.assertEqual(normalize_osm_type("n"), "N")
        self.assertEqual(normalize_osm_type("way"), "W")
        self.assertEqual(normalize_osm_type("WAY"), "W")
        self.assertEqual(normalize_osm_type("w"), "W")
        self.assertEqual(normalize_osm_type("relation"), "R")
        self.assertEqual(normalize_osm_type("RELATION"), "R")
        self.assertEqual(normalize_osm_type("multipolygon"), "R")
        self.assertEqual(normalize_osm_type("r"), "R")
        self.assertIsNone(normalize_osm_type(None))
        self.assertIsNone(normalize_osm_type("unknown"))

    def test_irrelevant_feature_is_dropped(self):
        feat = make_feature({"amenity": "restaurant", "name": "Pizzeria"}, geom_type="Point")
        self.assertIsNone(process_facility_feature(feat))


if __name__ == "__main__":
    unittest.main()
