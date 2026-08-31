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

    def test_train_station_point(self):
        feat = make_feature({
            "@type": "node",
            "@id": 10001,
            "railway": "station",
            "name": "München Hauptbahnhof",
            "uic_ref": "8000261"
        }, geom_type="Point")
        res = process_facility_feature(feat)
        self.assertIsNotNone(res)
        self.assertEqual(res["feature_class"], "train_station")
        self.assertEqual(res["osm_type"], "N")

    def test_train_station_building_polygon(self):
        feat = make_feature({
            "@type": "way",
            "@id": 10002,
            "building": "train_station",
            "name": "Empfangsgebäude"
        }, geom_type="Polygon")
        res = process_facility_feature(feat)
        self.assertIsNotNone(res)
        self.assertEqual(res["feature_class"], "train_station")
        self.assertEqual(res["osm_type"], "W")

    def test_train_station_rejects_underground_and_tunnel_polygons(self):
        # Undergound tunnel polygon should be rejected to prevent POI bleeding
        feat_tunnel = make_feature({
            "@type": "relation",
            "@id": 10003,
            "railway": "station",
            "tunnel": "yes"
        }, geom_type="Polygon")
        self.assertIsNone(process_facility_feature(feat_tunnel))

        feat_underground = make_feature({
            "@type": "way",
            "@id": 10004,
            "railway": "station",
            "location": "underground"
        }, geom_type="Polygon")
        self.assertIsNone(process_facility_feature(feat_underground))

        feat_tracks = make_feature({
            "@type": "relation",
            "@id": 10005,
            "railway": "station",
            "landuse": "railway"
        }, geom_type="Polygon")
        self.assertIsNone(process_facility_feature(feat_tracks))

    def test_train_station_rejects_pure_subway_halts(self):
        feat_subway = make_feature({
            "@type": "node",
            "@id": 10006,
            "railway": "station",
            "station": "subway"
        }, geom_type="Point")
        self.assertIsNone(process_facility_feature(feat_subway))

    def test_exhibition_centre(self):
        feat = make_feature({
            "@type": "relation",
            "@id": 11001,
            "amenity": "exhibition_centre",
            "name": "Messe München"
        }, geom_type="MultiPolygon")
        res = process_facility_feature(feat)
        self.assertIsNotNone(res)
        self.assertEqual(res["feature_class"], "exhibition_centre")

        feat_conf = make_feature({
            "@type": "way",
            "@id": 11002,
            "amenity": "conference_centre",
            "name": "ICM – Internationales Congress Center München"
        }, geom_type="Polygon")
        res2 = process_facility_feature(feat_conf)
        self.assertIsNotNone(res2)
        self.assertEqual(res2["feature_class"], "exhibition_centre")

    def test_theme_park(self):
        feat = make_feature({
            "@type": "relation",
            "@id": 12001,
            "tourism": "theme_park",
            "name": "Europa-Park"
        }, geom_type="Polygon")
        res = process_facility_feature(feat)
        self.assertIsNotNone(res)
        self.assertEqual(res["feature_class"], "theme_park")

        feat_water = make_feature({
            "@type": "way",
            "@id": 12002,
            "leisure": "water_park",
            "name": "Therme Erding"
        }, geom_type="Polygon")
        res2 = process_facility_feature(feat_water)
        self.assertIsNotNone(res2)
        self.assertEqual(res2["feature_class"], "theme_park")

    def test_zoo_and_aquarium(self):
        feat_zoo = make_feature({
            "@type": "relation",
            "@id": 13001,
            "tourism": "zoo",
            "name": "Tierpark Hellabrunn"
        }, geom_type="Polygon")
        res = process_facility_feature(feat_zoo)
        self.assertIsNotNone(res)
        self.assertEqual(res["feature_class"], "zoo")

        feat_aqua = make_feature({
            "@type": "way",
            "@id": 13002,
            "tourism": "aquarium",
            "name": "Sea Life München"
        }, geom_type="Polygon")
        res2 = process_facility_feature(feat_aqua)
        self.assertIsNotNone(res2)
        self.assertEqual(res2["feature_class"], "zoo")

    def test_ferry_terminal(self):
        feat_point = make_feature({
            "@type": "node",
            "@id": 14001,
            "amenity": "ferry_terminal",
            "name": "Fährhafen Puttgarden"
        }, geom_type="Point")
        res = process_facility_feature(feat_point)
        self.assertIsNotNone(res)
        self.assertEqual(res["feature_class"], "ferry_terminal")

        feat_poly = make_feature({
            "@type": "way",
            "@id": 14002,
            "building": "ferry_terminal",
            "name": "Terminalgebäude"
        }, geom_type="Polygon")
        res2 = process_facility_feature(feat_poly)
        self.assertIsNotNone(res2)
        self.assertEqual(res2["feature_class"], "ferry_terminal")

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
