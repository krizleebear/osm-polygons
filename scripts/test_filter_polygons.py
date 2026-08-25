#!/usr/bin/env python3
import unittest
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from filter_polygons import process_feature, filter_features

# Minimal valid polygon geometry shared by most fixtures (real osmium export always emits geometry).
POLY_GEOM = {"type": "Polygon", "coordinates": [[[11.0, 48.0], [11.1, 48.0], [11.1, 48.1], [11.0, 48.0]]]}

def feat(properties, geometry=POLY_GEOM):
    return {"type": "Feature", "geometry": geometry, "properties": properties}

class TestFilterPolygons(unittest.TestCase):

    def test_mainland_relation_enforcement_fr(self):
        # FR relation 2202162 (France Métropole)
        feature = feat({"@type": "relation", "id": 2202162, "admin_level": "3"})
        res = process_feature(feature)
        self.assertIsNotNone(res)
        self.assertEqual(res["properties"]["admin_level"], "2")
        self.assertEqual(res["properties"]["name"], "France (Métropole)")
        self.assertEqual(res["properties"]["ISO3166-1"], "FR")

        # FR relation 11980 (France)
        feature_fr = feat({"@type": "relation", "id": 11980, "admin_level": "2", "name": "France"})
        res_fr = process_feature(feature_fr)
        self.assertIsNotNone(res_fr)
        self.assertEqual(res_fr["properties"]["admin_level"], "2")
        self.assertEqual(res_fr["properties"]["ISO3166-1"], "FR")

    def test_mainland_relation_enforcement_nl(self):
        # NL relation 2323309 (European Netherlands)
        feature_nl_main = feat({"@type": "relation", "id": 2323309, "admin_level": "3", "name": "Nederland"})
        res_nl_main = process_feature(feature_nl_main)
        self.assertIsNotNone(res_nl_main)
        self.assertEqual(res_nl_main["properties"]["admin_level"], "2")
        self.assertEqual(res_nl_main["properties"]["ISO3166-1"], "NL")

        # NL relation 47796 (Kingdom of the Netherlands)
        feature_nl = feat({"@type": "relation", "id": 47796, "admin_level": "2", "name": "Koninkrijk der Nederlanden"})
        res_nl = process_feature(feature_nl)
        self.assertIsNotNone(res_nl)
        self.assertEqual(res_nl["properties"]["admin_level"], "2")
        self.assertEqual(res_nl["properties"]["ISO3166-1"], "NL")

    def test_exclude_political_census_and_historic_boundaries(self):
        # German Bundestagswahlkreis / electoral constituency with boundary=political
        bundestag = feat({"@type": "relation", "id": 3146607, "boundary": "political", "admin_level": "9", "name": "Deggendorf"})
        self.assertIsNone(process_feature(bundestag))

        # Boundary with political=election tag
        election_feat = feat({"@type": "relation", "boundary": "administrative", "political": "election", "admin_level": "9", "name": "Wahlkreis 1"})
        self.assertIsNone(process_feature(election_feat))

        # Boundary with election:parliament tag
        parliament_feat = feat({"@type": "relation", "boundary": "administrative", "election:parliament": "yes", "admin_level": "9", "name": "Constituency"})
        self.assertIsNone(process_feature(parliament_feat))

        # Census boundary
        census_feat = feat({"@type": "relation", "boundary": "census", "admin_level": "10", "name": "Census Block"})
        self.assertIsNone(process_feature(census_feat))

        # Historic boundary
        historic_feat = feat({"@type": "relation", "boundary": "historic", "admin_level": "4", "name": "Historical Duchy"})
        self.assertIsNone(process_feature(historic_feat))

        # Feature with end_date
        obsolete_feat = feat({"@type": "relation", "boundary": "administrative", "admin_level": "8", "name": "Alte Gemeinde", "end_date": "1972-01-01"})
        self.assertIsNone(process_feature(obsolete_feat))

    def test_include_traditional_statistical_and_cadastral_boundaries(self):
        # Traditional boundary (e.g. Le Marais, Belgisches Viertel)
        traditional_feat = feat({"@type": "relation", "boundary": "traditional", "name": "Glockenbachviertel"})
        res_trad = process_feature(traditional_feat)
        self.assertIsNotNone(res_trad)
        self.assertEqual(res_trad["properties"]["admin_level"], "10")
        self.assertEqual(res_trad["properties"]["area_type"], "traditional")

        # Statistical boundary (e.g. Statistischer Bezirk in German kreisfreie Städte)
        stat_feat = feat({"@type": "relation", "boundary": "statistical", "name": "Statistischer Bezirk 01"})
        res_stat = process_feature(stat_feat)
        self.assertIsNotNone(res_stat)
        self.assertEqual(res_stat["properties"]["admin_level"], "10")
        self.assertEqual(res_stat["properties"]["area_type"], "statistical")

        # Cadastral boundary (e.g. Katastralgemeinde in Austria)
        cad_feat = feat({"@type": "relation", "boundary": "cadastral", "name": "Katastralgemeinde Graz Stadt"})
        res_cad = process_feature(cad_feat)
        self.assertIsNotNone(res_cad)
        self.assertEqual(res_cad["properties"]["admin_level"], "10")
        self.assertEqual(res_cad["properties"]["area_type"], "cadastral")

        # NUTS statistical macro-regions (e.g. Highlands and Islands, NUTS-2/3) should be excluded
        nuts_feat = feat({"@type": "relation", "boundary": "statistical", "ref:nuts:2": "UKM6", "name": "Highlands and Islands"})
        self.assertIsNone(process_feature(nuts_feat))
        itl_feat = feat({"@type": "relation", "boundary": "statistical", "ref:itl:2": "TLM6", "name": "Highlands and Islands"})
        self.assertIsNone(process_feature(itl_feat))

    def test_filter_l2_border_ways(self):
        # Baarle / Vennbahn joint border way tagged admin_level=2 without ISO3166-1
        border_way = feat({
            "@type": "way",
            "id": 24718735,
            "admin_level": "2",
            "name": "Deutschland - Belgique / België / Belgien"
        })
        self.assertIsNone(process_feature(border_way))

        # Sovereign border way WITH ISO3166-1 code should be kept
        sovereign_way = feat({
            "@type": "way",
            "id": 999999,
            "admin_level": "2",
            "name": "Vatican City",
            "ISO3166-1": "VA"
        })
        res = process_feature(sovereign_way)
        self.assertIsNotNone(res)
        self.assertEqual(res["properties"]["ISO3166-1"], "VA")

        # Relations at admin_level=2 are preserved
        relation_l2 = feat({
            "@type": "relation",
            "id": 51477,
            "admin_level": "2",
            "name": "Deutschland",
            "ISO3166-1": "DE"
        })
        self.assertIsNotNone(process_feature(relation_l2))

    def test_submunicipal_boundary_admin_level_synthesis(self):
        # boundary=local_authority without admin_level -> synthesizes admin_level=10
        feature = feat({"@type": "relation", "type": "boundary", "boundary": "local_authority", "name": "District"})
        res = process_feature(feature)
        self.assertIsNotNone(res)
        self.assertEqual(res["properties"]["admin_level"], "10")

        # boundary=borough without admin_level -> synthesizes admin_level=10
        feature_b = feat({"@type": "relation", "type": "boundary", "boundary": "borough", "name": "Borough of Camden"})
        res_b = process_feature(feature_b)
        self.assertIsNotNone(res_b)
        self.assertEqual(res_b["properties"]["admin_level"], "10")

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

    def test_synthetic_funchal_reconstruction(self):
        # Given child freguesias of Funchal without the parent relation 8421413
        stream = [
            feat({"id": 8427682, "admin_level": "8", "name": "Sé", "wikidata": "Q10860292"}),
            feat({"id": 8427683, "admin_level": "8", "name": "Monte", "wikidata": "Q10860293"}),
            feat({"id": 8427684, "admin_level": "8", "name": "São Martinho", "wikidata": "Q10860294"}),
        ]
        results = list(filter_features(stream))
        # 3 children + 1 synthesized parent
        self.assertEqual(len(results), 4)

        funchal = [f for f in results if f["properties"].get("id") == 8421413][0]
        self.assertEqual(funchal["properties"]["name"], "Funchal")
        self.assertEqual(funchal["properties"]["admin_level"], "7")
        self.assertEqual(funchal["properties"]["wikidata"], "Q25444")
        self.assertEqual(funchal["geometry"]["type"], "MultiPolygon")
        self.assertEqual(len(funchal["geometry"]["coordinates"]), 3)

    def test_synthetic_funchal_not_duplicated_if_present(self):
        # When parent relation is already present, no duplicate is created
        stream = [
            feat({"id": 8421413, "admin_level": "7", "name": "Funchal", "wikidata": "Q25444"}),
            feat({"id": 8427682, "admin_level": "8", "name": "Sé", "wikidata": "Q10860292"}),
        ]
        results = list(filter_features(stream))
        funchal_features = [f for f in results if f["properties"].get("id") == 8421413]
        self.assertEqual(len(funchal_features), 1)

    def test_synthetic_kaohsiung_reconstruction(self):
        # Given child districts of Kaohsiung without the parent relation 2127079
        stream = [
            feat({"admin_level": "7", "name": "三民區", "name:en": "Sanmin District"}),
            feat({"admin_level": "7", "name": "鼓山區", "name:en": "Gushan District"}),
            feat({"admin_level": "7", "name": "苓雅區", "name:en": "Lingya District"}),
        ]
        results = list(filter_features(stream))
        self.assertEqual(len(results), 4)

        kaohsiung = [f for f in results if f["properties"].get("id") == 2127079][0]
        self.assertEqual(kaohsiung["properties"]["name"], "高雄市")
        self.assertEqual(kaohsiung["properties"]["admin_level"], "4")
        self.assertEqual(kaohsiung["properties"]["wikidata"], "Q181557")
        self.assertEqual(kaohsiung["properties"]["ISO3166-2"], "TW-KHH")
        self.assertEqual(kaohsiung["geometry"]["type"], "MultiPolygon")

    def test_synthetic_parent_alaska(self):
        stream = [
            feat({"@type": "relation", "id": 2605259, "admin_level": "6", "name": "Anchorage"}),
            feat({"@type": "relation", "id": 2605273, "admin_level": "6", "name": "Fairbanks North Star Borough"}),
            feat({"@type": "relation", "id": 2605281, "admin_level": "6", "name": "Juneau"}),
        ]
        results = list(filter_features(stream))
        self.assertEqual(len(results), 4)

        alaska = [f for f in results if f["properties"].get("id") == 1116270][0]
        self.assertEqual(alaska["properties"]["name"], "Alaska")
        self.assertEqual(alaska["properties"]["admin_level"], "4")
        self.assertEqual(alaska["properties"]["wikidata"], "Q797")
        self.assertEqual(alaska["properties"]["ISO3166-2"], "US-AK")
        self.assertEqual(alaska["geometry"]["type"], "MultiPolygon")
    def test_admin_centre_enrichment(self):
        # Feature with relation @id = 62428 (Munich)
        feature = feat({"@type": "relation", "id": 62428, "admin_level": "6", "name": "München"})
        relation_centres = {
            62428: {
                "admin_centre": (11.5755, 48.1374),
                "label": (11.5754, 48.1371)
            }
        }
        res = process_feature(feature, relation_centres=relation_centres)
        self.assertIsNotNone(res)
        props = res["properties"]
        self.assertEqual(props["admin_centre:lat"], 48.1374)
        self.assertEqual(props["admin_centre:lon"], 11.5755)
        self.assertEqual(props["label:lat"], 48.1371)
        self.assertEqual(props["label:lon"], 11.5754)
        # Center coordinates should take admin_centre priority
        self.assertEqual(props["center_lat"], 48.1374)
        self.assertEqual(props["center_lon"], 11.5755)

    def test_label_fallback_when_no_admin_centre(self):
        # Feature with only label coordinate
        feature = feat({"@type": "relation", "id": 12345, "admin_level": "8", "name": "Gemeinde"})
        relation_centres = {
            12345: {
                "label": (12.3456, 47.6543)
            }
        }
        res = process_feature(feature, relation_centres=relation_centres)
        self.assertIsNotNone(res)
        props = res["properties"]
        self.assertNotIn("admin_centre:lat", props)
        self.assertNotIn("admin_centre:lon", props)
        self.assertEqual(props["label:lat"], 47.6543)
        self.assertEqual(props["label:lon"], 12.3456)
        self.assertEqual(props["center_lat"], 47.6543)
        self.assertEqual(props["center_lon"], 12.3456)

    def test_filter_features_with_relation_centres(self):
        stream = [
            feat({"id": 100, "admin_level": "8", "name": "Ort A"}),
            feat({"id": 200, "admin_level": "8", "name": "Ort B"}),
        ]
        relation_centres = {
            100: {"admin_centre": (10.1, 50.1)},
            200: {"label": (10.2, 50.2)},
        }
        results = list(filter_features(stream, relation_centres=relation_centres))
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["properties"]["center_lat"], 50.1)
        self.assertEqual(results[0]["properties"]["center_lon"], 10.1)
        self.assertEqual(results[1]["properties"]["center_lat"], 50.2)
        self.assertEqual(results[1]["properties"]["center_lon"], 10.2)

    def test_area_type_derivation_precedence(self):
        # 1. Explicit place tag overrides admin_level
        f1 = feat({"admin_level": "10", "place": "quarter", "name": "Glockenbachviertel"})
        self.assertEqual(process_feature(f1)["properties"]["area_type"], "quarter")

        f2 = feat({"admin_level": "9", "place": "suburb", "name": "Schwabing"})
        self.assertEqual(process_feature(f2)["properties"]["area_type"], "suburb")

        f3 = feat({"admin_level": "10", "place": "hamlet", "name": "Weiler A"})
        self.assertEqual(process_feature(f3)["properties"]["area_type"], "hamlet")

        f3b = feat({"admin_level": "10", "place": "island", "name": "Holm Island"})
        self.assertEqual(process_feature(f3b)["properties"]["area_type"], "island")

        f3c = feat({"admin_level": "10", "place": "islet", "name": "Wart Holm"})
        self.assertEqual(process_feature(f3c)["properties"]["area_type"], "islet")

        # 2. Sub-district tag
        f4 = feat({"admin_level": "10", "subdistrict": "statistischer_bezirk", "name": "Bezirk 02"})
        self.assertEqual(process_feature(f4)["properties"]["area_type"], "statistischer_bezirk")

        # 3. Boundary tag
        f5 = feat({"boundary": "traditional", "name": "Belgisches Viertel"})
        self.assertEqual(process_feature(f5)["properties"]["area_type"], "traditional")

        f6 = feat({"boundary": "cadastral", "name": "Katastralgemeinde X"})
        self.assertEqual(process_feature(f6)["properties"]["area_type"], "cadastral")

        # 4. admin_type:FR
        f7 = feat({"admin_level": "10", "admin_type:FR": "quartier", "name": "Le Marais"})
        self.assertEqual(process_feature(f7)["properties"]["area_type"], "quartier")

        # 5. Level fallbacks
        self.assertEqual(process_feature(feat({"@type": "relation", "admin_level": "2", "name": "Country"}))["properties"]["area_type"], "country")
        self.assertEqual(process_feature(feat({"admin_level": "4", "name": "State"}))["properties"]["area_type"], "state")
        self.assertEqual(process_feature(feat({"admin_level": "6", "name": "County"}))["properties"]["area_type"], "county")
        self.assertEqual(process_feature(feat({"admin_level": "8", "name": "City"}))["properties"]["area_type"], "municipality")
        self.assertEqual(process_feature(feat({"admin_level": "9", "name": "District"}))["properties"]["area_type"], "suburb")
        self.assertEqual(process_feature(feat({"admin_level": "10", "name": "Quarter"}))["properties"]["area_type"], "quarter")
        self.assertEqual(process_feature(feat({"admin_level": "11", "name": "Block"}))["properties"]["area_type"], "neighbourhood")

    def test_place_area_polygons_synthesis(self):
        # Closed way polygon with place=hamlet
        hamlet_way = feat({"@type": "way", "place": "hamlet", "name": "Weiler Kirchberg"})
        res = process_feature(hamlet_way)
        self.assertIsNotNone(res)
        self.assertEqual(res["properties"]["admin_level"], "10")
        self.assertEqual(res["properties"]["area_type"], "hamlet")

        # Multipolygon relation with place=quarter
        quarter_rel = feat({"@type": "relation", "type": "multipolygon", "place": "quarter", "name": "Kreativviertel"})
        res_q = process_feature(quarter_rel)
        self.assertIsNotNone(res_q)
        self.assertEqual(res_q["properties"]["admin_level"], "10")
        self.assertEqual(res_q["properties"]["area_type"], "quarter")

    def test_synthetic_parent_entities(self):
        # 1. Funchal (relation 8421413, admin_level=7) synthesized from child parishes
        parish1 = feat({"@type": "relation", "id": 8427682, "admin_level": "8", "name": "São Martinho"})
        parish2 = feat({"@type": "relation", "id": 8427683, "admin_level": "8", "name": "Santa Maria Maior"})
        
        results = list(filter_features([parish1, parish2]))
        self.assertEqual(len(results), 3)
        funchal = results[-1]
        self.assertEqual(funchal["properties"]["@id"], 8421413)
        self.assertEqual(funchal["properties"]["admin_level"], "7")
        self.assertEqual(funchal["properties"]["name"], "Funchal")
        self.assertEqual(funchal["properties"]["wikidata"], "Q25444")
        self.assertEqual(funchal["properties"]["area_type"], "municipality")
        self.assertEqual(funchal["geometry"]["type"], "MultiPolygon")

        # 2. If parent was already in stream, do not synthesize duplicate
        parent_funchal = feat({"@type": "relation", "id": 8421413, "admin_level": "7", "name": "Funchal"})
        results_with_parent = list(filter_features([parent_funchal, parish1]))
        self.assertEqual(len(results_with_parent), 2)

if __name__ == "__main__":
    unittest.main()

