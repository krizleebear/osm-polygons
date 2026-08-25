#!/usr/bin/env python3
"""
Tests for validate_pbf.py.

Uses the local madeira-latest.osm.pbf as test fixture.
Run: python3 -m pytest scripts/test_validate_pbf.py -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from validate_pbf import (
    detect_country_from_filename,
    belongs_to_country,
    run_check_refs,
    AdminBoundaryScanner,
    generate_synthetic_defs,
    validate,
)

MADEIRA_PBF = os.path.join(os.path.dirname(__file__), "..", "madeira-latest.osm.pbf")
HAS_PBF = os.path.exists(MADEIRA_PBF)


class TestDetectCountry(unittest.TestCase):
    def test_portugal(self):
        self.assertEqual(detect_country_from_filename("portugal-latest.osm.pbf"), "PT")

    def test_spain(self):
        self.assertEqual(detect_country_from_filename("spain-latest.osm.pbf"), "ES")

    def test_with_date(self):
        self.assertEqual(detect_country_from_filename("germany-20240101.osm.pbf"), "DE")

    def test_unknown(self):
        self.assertIsNone(detect_country_from_filename("madeira-latest.osm.pbf"))

    def test_case_insensitive(self):
        self.assertEqual(detect_country_from_filename("Portugal-latest.osm.pbf"), "PT")

    def test_path_prefix(self):
        self.assertEqual(detect_country_from_filename("/data/france-latest.osm.pbf"), "FR")


class TestBelongsToCountry(unittest.TestCase):
    def test_iso1_match(self):
        tags = {"ISO3166-1": "PT"}
        self.assertTrue(belongs_to_country(tags, "PT"))

    def test_iso2_match(self):
        tags = {"ISO3166-2": "PT-30"}
        self.assertTrue(belongs_to_country(tags, "PT"))

    def test_no_match(self):
        tags = {"ISO3166-1": "ES"}
        self.assertFalse(belongs_to_country(tags, "PT"))

    def test_case_insensitive(self):
        tags = {"ISO3166-1": "pt"}
        self.assertTrue(belongs_to_country(tags, "PT"))

    def test_iso2_prefix(self):
        tags = {"ISO3166-2": "PT-08"}
        self.assertTrue(belongs_to_country(tags, "PT"))

    def test_wrong_country_iso2(self):
        tags = {"ISO3166-2": "ES-BA"}
        self.assertFalse(belongs_to_country(tags, "PT"))

    def test_empty_tags(self):
        self.assertFalse(belongs_to_country({}, "PT"))


@unittest.skipUnless(HAS_PBF, "madeira-latest.osm.pbf not found")
class TestRunCheckRefs(unittest.TestCase):
    def setUp(self):
        self.broken = run_check_refs(MADEIRA_PBF)

    def test_returns_dict(self):
        self.assertIsInstance(self.broken, dict)

    def test_madeira_is_broken(self):
        self.assertIn(1629145, self.broken)

    def test_portugal_is_broken(self):
        self.assertIn(295480, self.broken)

    def test_missing_ways_count(self):
        self.assertEqual(self.broken[1629145]["missing_ways"], 237)

    def test_way_ids_present(self):
        self.assertEqual(len(self.broken[1629145]["way_ids"]), 237)

    def test_funchal_not_broken(self):
        # Funchal (8421413) is complete in the PBF
        self.assertNotIn(8421413, self.broken)


@unittest.skipUnless(HAS_PBF, "madeira-latest.osm.pbf not found")
class TestAdminBoundaryScanner(unittest.TestCase):
    def setUp(self):
        self.scanner = AdminBoundaryScanner()
        self.scanner.apply_file(MADEIRA_PBF, locations=True)

    def test_scans_all_admin_relations(self):
        self.assertGreater(len(self.scanner.admin_relations), 0)

    def test_madeira_exists(self):
        self.assertIn(1629145, self.scanner.admin_relations)

    def test_madeira_metadata(self):
        info = self.scanner.admin_relations[1629145]
        self.assertEqual(info["name"], "Madeira")
        self.assertEqual(info["admin_level"], "4")
        self.assertEqual(info["iso2"], "PT-30")

    def test_funchal_children(self):
        info = self.scanner.admin_relations[8421413]
        self.assertEqual(info["name"], "Funchal")
        self.assertEqual(info["admin_level"], "7")
        self.assertGreater(len(info["member_relations"]), 0)

    def test_madeira_has_children(self):
        children = self.scanner.parent_children.get(1629145, set())
        self.assertEqual(len(children), 11)

    def test_child_parent_mapping(self):
        parents = self.scanner.child_parents.get(8421413, set())
        self.assertIn(1629145, parents)

    def test_way_count(self):
        self.assertGreater(self.scanner.way_count, 100000)

    def test_relation_count(self):
        self.assertGreater(self.scanner.relation_count, 2000)


@unittest.skipUnless(HAS_PBF, "madeira-latest.osm.pbf not found")
class TestValidate(unittest.TestCase):
    def setUp(self):
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            self.exit_code = validate(MADEIRA_PBF)
        self.output = f.getvalue()

    def test_exit_code_is_1(self):
        self.assertEqual(self.exit_code, 1)

    def test_output_shows_madeira(self):
        self.assertIn("Madeira", self.output)

    def test_output_shows_children(self):
        self.assertIn("11/11", self.output)

    def test_json_written(self):
        json_path = MADEIRA_PBF.replace(".osm.pbf", "-validation.json")
        self.assertTrue(os.path.exists(json_path))

    def test_json_structure(self):
        json_path = MADEIRA_PBF.replace(".osm.pbf", "-validation.json")
        with open(json_path) as f:
            data = json.load(f)
        self.assertIn("broken", data)
        self.assertIn("broken_admin_boundary", data)
        self.assertGreater(len(data["broken"]), 0)

    def test_json_children(self):
        json_path = MADEIRA_PBF.replace(".osm.pbf", "-validation.json")
        with open(json_path) as f:
            data = json.load(f)
        madeira = next(b for b in data["broken"] if b["id"] == 1629145)
        self.assertEqual(len(madeira["children"]), 11)
        self.assertTrue(all(c["complete"] for c in madeira["children"]))


@unittest.skipUnless(HAS_PBF, "madeira-latest.osm.pbf not found")
class TestSyntheticDefs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.defs_path = MADEIRA_PBF.replace(".osm.pbf", "-synthetic-defs.json")

    def test_synthetic_defs_written(self):
        self.assertTrue(os.path.exists(self.defs_path))

    def test_synthetic_defs_structure(self):
        with open(self.defs_path) as f:
            defs = json.load(f)
        self.assertIn("1629145", defs)
        self.assertIn("295480", defs)

    def test_madeira_children(self):
        with open(self.defs_path) as f:
            defs = json.load(f)
        madeira = defs["1629145"]
        self.assertEqual(len(madeira["child_relation_ids"]), 11)
        self.assertEqual(len(madeira["child_names"]), 11)
        self.assertEqual(madeira["child_admin_level"], "7")

    def test_madeira_force_collect_false(self):
        with open(self.defs_path) as f:
            defs = json.load(f)
        madeira = defs["1629145"]
        self.assertFalse(madeira.get("force_collect", False))

    def test_portugal_force_collect_true(self):
        with open(self.defs_path) as f:
            defs = json.load(f)
        pt = defs["295480"]
        self.assertTrue(pt["force_collect"])
        self.assertIn(1629145, pt["child_relation_ids"])

    def test_properties_include_osm_tags(self):
        with open(self.defs_path) as f:
            defs = json.load(f)
        madeira = defs["1629145"]
        props = madeira["properties"]
        self.assertEqual(props["@id"], 1629145)
        self.assertEqual(props["admin_level"], "4")
        self.assertEqual(props["name"], "Madeira")

    def test_lists_converted_to_sets(self):
        import importlib
        import filter_polygons
        importlib.reload(filter_polygons)
        filter_polygons.load_synthetic_defs(self.defs_path)
        d = filter_polygons.SYNTHETIC_PARENT_DEFINITIONS[1629145]
        self.assertIsInstance(d["child_relation_ids"], set)
        self.assertIsInstance(d["child_names"], set)


if __name__ == "__main__":
    unittest.main()
