#!/usr/bin/env python3
"""
Unit tests for generate_validation_summary.py
"""

import json
import os
import tempfile
import unittest
from generate_validation_summary import load_validation_files, aggregate, generate_json, generate_markdown, main


class TestGenerateValidationSummary(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_empty_directory_handling(self):
        """Must handle empty directory without crashing."""
        files = load_validation_files(self.tmpdir.name)
        self.assertEqual(len(files), 0)

    def test_placeholder_json_files_skipped(self):
        """Empty placeholder dicts '{}' from --parent-mapping-only runs must be skipped."""
        p1 = os.path.join(self.tmpdir.name, "validation-DE-validation.json")
        with open(p1, "w") as f:
            f.write("{}\n")

        p2 = os.path.join(self.tmpdir.name, "validation-FR-validation.json")
        with open(p2, "w") as f:
            f.write("{}\n")

        files = load_validation_files(self.tmpdir.name)
        self.assertEqual(len(files), 0)

    def test_populated_validation_aggregation(self):
        """Populated validation results must be aggregated accurately."""
        p = os.path.join(self.tmpdir.name, "validation-IT-validation.json")
        sample_data = {
            "country_filter": "IT",
            "broken": [
                {
                    "osm_id": 12345,
                    "admin_level": "4",
                    "children": [
                        {"osm_id": 101, "complete": True},
                        {"osm_id": 102, "complete": False},
                    ],
                }
            ],
        }
        with open(p, "w") as f:
            json.dump(sample_data, f)

        files = load_validation_files(self.tmpdir.name)
        self.assertEqual(len(files), 1)

        per_level, per_country, totals = aggregate(files)
        self.assertEqual(totals["total_broken"], 1)
        self.assertEqual(totals["total_children_complete"], 1)
        self.assertEqual(totals["total_children_broken"], 1)
        self.assertEqual(per_level["4"]["broken_parents"], 1)
        self.assertEqual(per_country["IT"]["4"]["broken_parents"], 1)

    def test_main_cli_with_empty_and_placeholder(self):
        """CLI invocation on empty directory must exit cleanly with code 0 and write empty report."""
        p1 = os.path.join(self.tmpdir.name, "validation-DE-validation.json")
        with open(p1, "w") as f:
            f.write("{}\n")

        orig_argv = os.sys.argv
        try:
            os.sys.argv = ["generate_validation_summary.py", self.tmpdir.name]
            main()
            self.assertTrue(os.path.exists(os.path.join(self.tmpdir.name, "validation-summary.md")))
            self.assertTrue(os.path.exists(os.path.join(self.tmpdir.name, "validation-summary.json")))
        finally:
            os.sys.argv = orig_argv


if __name__ == "__main__":
    unittest.main()
