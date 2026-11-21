#!/usr/bin/env python3
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import fetch_postpass_polygons
from fetch_postpass_polygons import (
    format_feature,
    infer_candidate_parents,
    load_cached_reference,
    load_candidates,
    normalize_osm_type,
    query_postpass_centres,
    query_postpass_parents,
)


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

    def test_normalize_osm_type(self):
        self.assertEqual(normalize_osm_type("R"), "relation")
        self.assertEqual(normalize_osm_type("relation"), "relation")
        self.assertEqual(normalize_osm_type("w"), "way")
        self.assertEqual(normalize_osm_type("way"), "way")
        self.assertEqual(normalize_osm_type("N"), "node")
        self.assertEqual(normalize_osm_type(""), "relation")
        self.assertEqual(normalize_osm_type(None), "relation")

    def test_format_feature_flattens_tags_and_normalizes_l2(self):
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
        self.assertEqual(props["@id"], 1124039)
        self.assertEqual(props["osm_type"], "relation")
        self.assertEqual(props["@type"], "relation")
        self.assertEqual(props["area_type"], "country")
        self.assertEqual(props["name"], "Monaco")
        self.assertEqual(props["ISO3166-1"], "MC")
        self.assertEqual(props["wikidata"], "Q235")
        self.assertNotIn("tags", props)
        # Ensure original raw feature dict was not mutated
        self.assertIn("tags", raw_postpass_feature["properties"])

    def test_format_feature_l4_and_l7_omit_iso3166_1_and_strip_stale(self):
        raw_l4 = {
            "type": "Feature",
            "properties": {
                "osm_type": "R",
                "osm_id": 4627314,
                "tags": {
                    "admin_level": "4",
                    "boundary": "administrative",
                    "name": "Choluteca",
                    "ISO3166-2": "HN-CH",
                },
            },
            "geometry": {"type": "MultiPolygon", "coordinates": []},
        }
        cand_l4 = {"osm_id": 4627314, "country": "HN", "admin_level": 4, "name": "Choluteca"}
        formatted_l4 = format_feature(raw_l4, cand_l4)
        self.assertEqual(formatted_l4["properties"]["osm_type"], "relation")
        self.assertEqual(formatted_l4["properties"]["area_type"], "state")
        self.assertEqual(formatted_l4["properties"]["ISO3166-2"], "HN-CH")
        self.assertNotIn("ISO3166-1", formatted_l4["properties"])

        # Old cached L4/L7 features that mistakenly carried ISO3166-1 == country are healed
        stale_cached_l7 = {
            "type": "Feature",
            "id": 8421413,
            "properties": {
                "osm_type": "R",
                "osm_id": 8421413,
                "admin_level": "7",
                "name": "Funchal",
                "ISO3166-1": "PT",
            },
            "geometry": {"type": "MultiPolygon", "coordinates": []},
        }
        cand_l7 = {"osm_id": 8421413, "country": "PT", "admin_level": 7, "name": "Funchal"}
        healed_l7 = format_feature(stale_cached_l7, cand_l7)
        self.assertEqual(healed_l7["properties"]["osm_type"], "relation")
        self.assertEqual(healed_l7["properties"]["area_type"], "municipality")
        self.assertNotIn("ISO3166-1", healed_l7["properties"])

    def test_format_feature_applies_centres_and_parent_enrichment(self):
        raw_l4 = {
            "type": "Feature",
            "properties": {
                "osm_type": "R",
                "osm_id": 4627314,
                "tags": {
                    "admin_level": "4",
                    "boundary": "administrative",
                    "name": "Choluteca",
                    "ISO3166-2": "HN-CH",
                },
            },
            "geometry": {"type": "MultiPolygon", "coordinates": []},
        }
        centres = {
            4627314: {
                "admin_centre": {
                    "lon": -87.1955,
                    "lat": 13.3061,
                    "osm_type": "node",
                    "osm_id": 13234275683,
                    "source_id": "osm:node/13234275683",
                    "name": "Choluteca",
                    "names_json": '{"name:es": "Choluteca"}',
                    "wikidata": "Q1773148",
                },
                "label": {"lon": -87.1, "lat": 13.2},
            }
        }
        parent = {"parent_osm_id": 287670, "parent_name": "Honduras", "parent_iso3166_2": ""}
        formatted = format_feature(
            raw_l4,
            {"osm_id": 4627314, "country": "HN", "admin_level": 4, "name": "Choluteca"},
            relation_centres=centres,
            parent_info=parent,
        )
        props = formatted["properties"]
        self.assertEqual(props["center_lat"], 13.3061)
        self.assertEqual(props["center_lon"], -87.1955)
        self.assertEqual(props["admin_centre:lat"], 13.3061)
        self.assertEqual(props["admin_centre:lon"], -87.1955)
        self.assertEqual(props["admin_centre:osm_id"], 13234275683)
        self.assertEqual(props["admin_centre:source_id"], "osm:node/13234275683")
        self.assertEqual(props["admin_centre:name"], "Choluteca")
        self.assertEqual(props["admin_centre:wikidata"], "Q1773148")
        self.assertEqual(props["label:lat"], 13.2)
        self.assertEqual(props["label:lon"], -87.1)
        self.assertEqual(props["parent_osm_id"], 287670)
        self.assertEqual(props["parent_name"], "Honduras")

        # L2 should ignore parent_info even if provided
        l2_formatted = format_feature(
            {
                "type": "Feature",
                "properties": {"osm_type": "R", "osm_id": 287670, "tags": {"admin_level": "2", "name": "Honduras"}},
                "geometry": {"type": "MultiPolygon", "coordinates": []},
            },
            {"osm_id": 287670, "country": "HN", "admin_level": 2, "name": "Honduras"},
            parent_info={"parent_osm_id": 999, "parent_name": "Bad"},
        )
        self.assertNotIn("parent_osm_id", l2_formatted["properties"])

    def test_query_postpass_centres_and_parents_and_infer_fallback(self):
        centres_payload = {
            "result": [
                {
                    "rel_id": 287670,
                    "node_id": 13230187123,
                    "role": "admin_centre",
                    "lon": -87.2068,
                    "lat": 14.1057,
                    "node_tags": {"name": "Tegucigalpa", "name:en": "Tegucigalpa", "wikidata": "Q3238"},
                },
                {
                    "rel_id": 287670,
                    "node_id": 424297629,
                    "role": "label",
                    "lon": -86.0755,
                    "lat": 15.2572,
                    "node_tags": {"name": "Honduras"},
                },
            ]
        }
        parents_payload = {
            "result": [
                # Both L2 (PT) and L4 (Madeira) list Funchal (8421413); L4 (direct) must win
                {"parent_id": 295480, "parent_name": "Portugal", "parent_lvl": "2", "parent_iso2": "", "child_id": 8421413},
                {"parent_id": 1629145, "parent_name": "Madeira", "parent_lvl": "4", "parent_iso2": "PT-30", "child_id": 8421413},
            ]
        }

        def fake_urlopen(req, timeout=30):
            url = req.full_url
            resp = MagicMock()
            resp.__enter__.return_value = resp
            if "postpass_point" in url:
                resp.read.return_value = json.dumps(centres_payload).encode("utf-8")
            else:
                resp.read.return_value = json.dumps(parents_payload).encode("utf-8")
            return resp

        with patch.object(fetch_postpass_polygons.urllib.request, "urlopen", side_effect=fake_urlopen):
            centres = query_postpass_centres([287670])
            parents = query_postpass_parents([295480, 1629145, 8421413], {
                295480: {"admin_level": 2},
                1629145: {"admin_level": 4},
                8421413: {"admin_level": 7},
            })

        self.assertEqual(centres[287670]["admin_centre"]["name"], "Tegucigalpa")
        self.assertEqual(centres[287670]["label"]["lat"], 15.2572)
        self.assertEqual(parents[8421413]["parent_osm_id"], 1629145)
        self.assertEqual(parents[8421413]["parent_iso3166_2"], "PT-30")

        # ISO3166-2 prefix fallback fills Alaska (1116270 -> US 148838) when not in subarea members
        inferred = infer_candidate_parents(
            [
                {"osm_id": 148838, "country": "US", "admin_level": 2, "name": "United States"},
                {"osm_id": 1116270, "country": "US", "admin_level": 4, "name": "Alaska"},
            ],
            {
                148838: {"properties": {"admin_level": "2", "name": "United States", "ISO3166-1": "US"}},
                1116270: {"properties": {"admin_level": "4", "name": "Alaska", "ISO3166-2": "US-AK"}},
            },
            {},
        )
        self.assertEqual(inferred[1116270]["parent_osm_id"], 148838)
        self.assertEqual(inferred[1116270]["parent_name"], "United States")

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
             patch.object(fetch_postpass_polygons, "query_postpass_centres", return_value={}), \
             patch.object(fetch_postpass_polygons, "query_postpass_parents", return_value={}), \
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

    def test_reuse_cached_fetches_only_missing_and_heals_old_cache(self):
        candidates_file = os.path.join(self.temp_dir.name, "three_candidates.json")
        with open(candidates_file, "w", encoding="utf-8") as f:
            json.dump({
                "candidates": [
                    {"osm_id": 1124039, "country": "MC", "admin_level": 2, "name": "Monaco"},
                    {"osm_id": 4627314, "country": "HN", "admin_level": 4, "name": "Choluteca"},
                    {"osm_id": 2186665, "country": "TO", "admin_level": 2, "name": "Tonga"},
                ]
            }, f)

        # Unnormalized legacy caches: osm_type="R", bogus L4 ISO3166-1="HN", missing center/parent/area_type
        cache_file = os.path.join(self.temp_dir.name, "cached.geojsonseq")
        with open(cache_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "Feature",
                "id": 1124039,
                "properties": {"osm_type": "R", "osm_id": 1124039, "name": "Monaco", "admin_level": "2", "ISO3166-1": "MC"},
                "geometry": {"type": "MultiPolygon", "coordinates": [[[[7.4, 43.7], [7.5, 43.7], [7.5, 43.8], [7.4, 43.7]]]]},
            }) + "\n")
            f.write(json.dumps({
                "type": "Feature",
                "id": 4627314,
                "properties": {"osm_type": "R", "osm_id": 4627314, "name": "Choluteca", "admin_level": "4", "ISO3166-1": "HN", "ISO3166-2": "HN-CH"},
                "geometry": {"type": "MultiPolygon", "coordinates": [[[[-87.3, 13.0], [-87.0, 13.0], [-87.0, 13.5], [-87.3, 13.0]]]]},
            }) + "\n")

        to_raw = {
            "type": "Feature",
            "properties": {
                "osm_type": "R",
                "osm_id": 2186665,
                "tags": {"ISO3166-1": "TO", "admin_level": "2", "name": "Tonga"},
            },
            "geometry": {"type": "MultiPolygon", "coordinates": [[[[-175.3, -21.2], [-175.1, -21.2], [-175.1, -21.1], [-175.3, -21.2]]]]},
        }
        mock_centres = {
            1124039: {"admin_centre": {"lon": 7.42, "lat": 43.73, "osm_type": "node", "osm_id": 1, "source_id": "osm:node/1", "name": "Monaco", "names_json": None, "wikidata": "Q235"}},
            4627314: {"admin_centre": {"lon": -87.19, "lat": 13.30, "osm_type": "node", "osm_id": 2, "source_id": "osm:node/2", "name": "Choluteca", "names_json": None, "wikidata": None}},
            2186665: {"admin_centre": {"lon": -175.2, "lat": -21.13, "osm_type": "node", "osm_id": 3, "source_id": "osm:node/3", "name": "Nukuʻalofa", "names_json": None, "wikidata": None}},
        }
        mock_parents = {
            4627314: {"parent_osm_id": 287670, "parent_name": "Honduras", "parent_iso3166_2": ""}
        }

        out_dir = os.path.join(self.temp_dir.name, "out_reuse")
        stdout_buf = io.StringIO()
        with patch.object(fetch_postpass_polygons, "query_postpass_polygon", return_value=to_raw) as mock_query, \
             patch.object(fetch_postpass_polygons, "query_postpass_centres", return_value=mock_centres), \
             patch.object(fetch_postpass_polygons, "query_postpass_parents", return_value=mock_parents), \
             patch.object(sys, "argv", [
                 "fetch_postpass_polygons.py",
                 "--candidates", candidates_file,
                 "--out-dir", out_dir,
                 "--reuse-cached",
                 "--fallback-reference", cache_file,
             ]), \
             redirect_stdout(stdout_buf):
            fetch_postpass_polygons.main()

        mock_query.assert_called_once_with(2186665)
        output = stdout_buf.getvalue()
        self.assertNotIn("##vso[task.logissue type=warning]", output)
        self.assertIn("CACHED (Rel 1124039", output)

        with open(os.path.join(out_dir, "reference-polygons.geojsonseq"), "r", encoding="utf-8") as f:
            feats = {feat["id"]: feat["properties"] for feat in (json.loads(line) for line in f if line.strip())}

        self.assertEqual(feats[1124039]["osm_type"], "relation")
        self.assertEqual(feats[1124039]["area_type"], "country")
        self.assertEqual(feats[1124039]["center_lat"], 43.73)
        self.assertEqual(feats[4627314]["osm_type"], "relation")
        self.assertEqual(feats[4627314]["area_type"], "state")
        self.assertNotIn("ISO3166-1", feats[4627314])
        self.assertEqual(feats[4627314]["parent_osm_id"], 287670)
        self.assertEqual(feats[4627314]["parent_name"], "Honduras")
        self.assertEqual(feats[2186665]["osm_type"], "relation")
        self.assertEqual(feats[2186665]["area_type"], "country")

        with open(os.path.join(out_dir, "manifest.json"), "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["total_count"], 3)
        self.assertEqual(manifest["reused_count"], 2)
        self.assertEqual(manifest["fetched_count"], 1)
        self.assertEqual(manifest["fallback_count"], 0)

    def test_multiple_fallback_references_priority(self):
        cache1 = os.path.join(self.temp_dir.name, "cache1.geojsonseq")
        cache2 = os.path.join(self.temp_dir.name, "cache2.geojsonseq")
        with open(cache1, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "Feature",
                "id": 1124039,
                "properties": {"osm_id": 1124039, "name": "Monaco Primary"},
                "geometry": {"type": "MultiPolygon", "coordinates": []},
            }) + "\n")
        with open(cache2, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "Feature",
                "id": 1124039,
                "properties": {"osm_id": 1124039, "name": "Monaco Secondary"},
                "geometry": {"type": "MultiPolygon", "coordinates": []},
            }) + "\n")
            f.write(json.dumps({
                "type": "Feature",
                "id": 2186665,
                "properties": {"osm_id": 2186665, "name": "Tonga Secondary"},
                "geometry": {"type": "MultiPolygon", "coordinates": []},
            }) + "\n")

        loaded = fetch_postpass_polygons.load_cached_references([cache1, cache2])
        self.assertEqual(loaded[1124039]["properties"]["name"], "Monaco Primary")
        self.assertEqual(loaded[2186665]["properties"]["name"], "Tonga Secondary")


if __name__ == "__main__":
    unittest.main()
