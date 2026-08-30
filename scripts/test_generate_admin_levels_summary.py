#!/usr/bin/env python3
"""
Unit tests for generate_admin_levels_summary.py
"""

import json
import os
import tempfile
import unittest
from generate_admin_levels_summary import analyze_parquet_files, generate_markdown, load_country_names



class TestGenerateAdminLevelsSummary(unittest.TestCase):

    def test_load_country_names(self):
        names = load_country_names()
        self.assertIn("DE", names)
        self.assertEqual(names["DE"], "Germany")
        self.assertIn("PT", names)
        self.assertEqual(names["PT"], "Portugal")

    def test_empty_directory_handling(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stats = analyze_parquet_files(tmpdir)
            self.assertEqual(stats, [])
            md = generate_markdown(stats)
            self.assertIn("Total Countries / Territories:** 0", md)


if __name__ == "__main__":
    unittest.main()
