#!/usr/bin/env python3
"""
Tests for generate_validation_summary.py.

Run: python3 -m pytest scripts/test_generate_validation_summary.py -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from generate_validation_summary import (
    load_validation_files,
    aggregate,
    generate_markdown,
    generate_json,
)

SAMPLE_VALIDATION = {
    "pbf_file": "germany.osm.pbf",
    "country_filter": "DE",
    "total_relations": 12345,
    "broken_total": 5,
    "broken_admin_boundary": 3,
    "broken": [
        {
            "id": 100,
            "name": "Bayern",
            "admin_level": "4",
            "iso1": "DE",
            "iso2": "DE-BY",
            "missing_ways": 3,
            "children": [
                {"id": 200, "name": "Muenchen", "admin_level": "6", "complete": True},
                {"id": 201, "name": "Berlin", "admin_level": "6", "complete": False},
            ],
        },
        {
            "id": 300,
            "name": "Hamburg",
            "admin_level": "8",
            "iso1": "DE",
            "iso2": "DE-HH",
            "missing_ways": 1,
            "children": [],
        },
        {
            "id": 400,
            "name": "Stadtbezirk",
            "admin_level": "9",
            "iso1": "DE",
            "iso2": "DE-HH",
            "missing_ways": 2,
            "children": [
                {"id": 500, "name": "Quarter", "admin_level": "10", "complete": True},
            ],
        },
    ],
}

SAMPLE_VALIDATION_NO_COUNTRY = {
    "pbf_file": "unknown.osm.pbf",
    "country_filter": None,
    "total_relations": 100,
    "broken_total": 1,
    "broken_admin_boundary": 1,
    "broken": [
        {
            "id": 999,
            "name": "Unknown Region",
            "admin_level": "2",
            "iso1": "",
            "iso2": "",
            "missing_ways": 5,
            "children": [],
        }
    ],
}

SAMPLE_VALIDATION_EMPTY = {
    "pbf_file": "clean.osm.pbf",
    "country_filter": "XX",
    "total_relations": 500,
    "broken_total": 0,
    "broken_admin_boundary": 0,
    "broken": [],
}


class TestLoadValidationFiles(unittest.TestCase):
    def test_loads_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for i, label in enumerate(["a", "b"]):
                path = os.path.join(tmpdir, f"{label}-validation.json")
                with open(path, "w") as f:
                    json.dump({"country_filter": label, "broken": []}, f)
            results = load_validation_files(tmpdir)
            self.assertEqual(len(results), 2)

    def test_loads_nested_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = os.path.join(tmpdir, "sub")
            os.makedirs(subdir)
            path = os.path.join(subdir, "xx-validation.json")
            with open(path, "w") as f:
                json.dump({"country_filter": "XX", "broken": []}, f)
            results = load_validation_files(tmpdir)
            self.assertEqual(len(results), 1)

    def test_skips_non_validation_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "something-else.json")
            with open(path, "w") as f:
                json.dump({"foo": "bar"}, f)
            results = load_validation_files(tmpdir)
            self.assertEqual(len(results), 0)

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            results = load_validation_files(tmpdir)
            self.assertEqual(len(results), 0)


class TestAggregate(unittest.TestCase):
    def test_broken_parents_counted(self):
        per_level, _, totals = aggregate([SAMPLE_VALIDATION])
        self.assertEqual(totals["total_broken"], 3)
        self.assertEqual(per_level["4"]["broken_parents"], 1)
        self.assertEqual(per_level["8"]["broken_parents"], 1)
        self.assertEqual(per_level["9"]["broken_parents"], 1)

    def test_children_complete(self):
        per_level, _, _ = aggregate([SAMPLE_VALIDATION])
        self.assertEqual(per_level["4"]["children_complete"], 1)
        self.assertEqual(per_level["9"]["children_complete"], 1)

    def test_children_broken(self):
        per_level, _, _ = aggregate([SAMPLE_VALIDATION])
        self.assertEqual(per_level["4"]["children_broken"], 1)

    def test_per_country(self):
        _, per_country, _ = aggregate([SAMPLE_VALIDATION])
        self.assertIn("DE", per_country)
        self.assertEqual(per_country["DE"]["4"]["broken_parents"], 1)

    def test_none_country_defaults_to_xx(self):
        _, per_country, _ = aggregate([SAMPLE_VALIDATION_NO_COUNTRY])
        self.assertIn("XX", per_country)
        self.assertEqual(per_country["XX"]["2"]["broken_parents"], 1)

    def test_empty_broken_list(self):
        per_level, per_country, totals = aggregate([SAMPLE_VALIDATION_EMPTY])
        self.assertEqual(totals["total_broken"], 0)
        self.assertEqual(len(per_level), 0)
        self.assertEqual(len(per_country), 0)

    def test_multiple_validations_aggregated(self):
        per_level, _, totals = aggregate([SAMPLE_VALIDATION, SAMPLE_VALIDATION])
        self.assertEqual(totals["total_broken"], 6)
        self.assertEqual(per_level["4"]["broken_parents"], 2)


class TestGenerateMarkdown(unittest.TestCase):
    def test_contains_header(self):
        md = generate_markdown({}, {}, {"total_broken": 0, "total_children_complete": 0, "total_children_broken": 0}, 0)
        self.assertIn("Validation Summary", md)

    def test_contains_level_row(self):
        per_level = {"8": {"broken_parents": 10, "children_complete": 3, "children_broken": 2}}
        md = generate_markdown(per_level, {}, {"total_broken": 10, "total_children_complete": 3, "total_children_broken": 2}, 1)
        self.assertIn("admin_level=8", md)
        self.assertIn("`10`", md)

    def test_skips_empty_levels(self):
        per_level = {"8": {"broken_parents": 5, "children_complete": 0, "children_broken": 0}}
        md = generate_markdown(per_level, {}, {"total_broken": 5, "total_children_complete": 0, "total_children_broken": 0}, 1)
        self.assertNotIn("admin_level=2", md)

    def test_per_country_table(self):
        per_country = {"DE": {"8": {"broken_parents": 5, "children_complete": 2, "children_broken": 1}}}
        md = generate_markdown({}, per_country, {"total_broken": 5, "total_children_complete": 2, "total_children_broken": 1}, 1)
        self.assertIn("`DE`", md)

    def test_totals_row(self):
        md = generate_markdown({}, {}, {"total_broken": 42, "total_children_complete": 7, "total_children_broken": 3}, 5)
        self.assertIn("**`42`**", md)
        self.assertIn("**`7`**", md)
        self.assertIn("**`3`**", md)
        self.assertIn("5", md)


class TestGenerateJson(unittest.TestCase):
    def test_structure(self):
        per_level = {"8": {"broken_parents": 1, "children_complete": 2, "children_broken": 3}}
        per_country = {"DE": {"8": {"broken_parents": 1, "children_complete": 2, "children_broken": 3}}}
        totals = {"total_broken": 1, "total_children_complete": 2, "total_children_broken": 3}
        data = generate_json(per_level, per_country, totals, 1)
        self.assertEqual(data["region_count"], 1)
        self.assertIn("per_level", data)
        self.assertIn("per_country", data)
        self.assertEqual(data["per_level"]["8"]["broken_parents"], 1)

    def test_levels_sorted(self):
        per_level = {
            "10": {"broken_parents": 1, "children_complete": 0, "children_broken": 0},
            "2": {"broken_parents": 5, "children_complete": 0, "children_broken": 0},
            "8": {"broken_parents": 3, "children_complete": 0, "children_broken": 0},
        }
        data = generate_json(per_level, {}, {"total_broken": 9, "total_children_complete": 0, "total_children_broken": 0}, 1)
        keys = list(data["per_level"].keys())
        self.assertEqual(keys, ["2", "8", "10"])


if __name__ == "__main__":
    unittest.main()
