#!/usr/bin/env python3
"""
Unit tests for extract_region.py
"""

import unittest
from unittest.mock import patch, MagicMock
import os
import sys

from extract_region import extract_region, get_pbf_timestamp, ADMIN_FILTER_RULES, PLACES_FILTER_RULES, LANDUSE_FILTER_RULES


class TestExtractRegion(unittest.TestCase):

    def test_filter_rules_present(self):
        self.assertTrue(len(ADMIN_FILTER_RULES) >= 3)
        self.assertTrue(len(PLACES_FILTER_RULES) >= 1)
        self.assertTrue(len(LANDUSE_FILTER_RULES) >= 1)
        self.assertIn("boundary=administrative", ADMIN_FILTER_RULES[0])
        self.assertTrue(any("residential" in r and "farmyard" in r for r in LANDUSE_FILTER_RULES))

    def test_missing_input_pbf_raises(self):
        with self.assertRaises(FileNotFoundError):
            extract_region(
                input_pbf="non_existent.osm.pbf",
                country_code="PT",
                region="portugal",
                polygon_out="out.geojsonseq",
                places_out="out.jsonl",
            )

    @patch("subprocess.run")
    def test_get_pbf_timestamp_from_osmium_header(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "2026-09-16T20:21:42Z\n"
        mock_run.return_value = mock_proc

        ts = get_pbf_timestamp("dummy.osm.pbf")
        self.assertEqual(ts, "2026-09-16T20:21:42Z")

    @patch("subprocess.run")
    def test_get_pbf_timestamp_fallback_to_mtime(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        import tempfile
        with tempfile.NamedTemporaryFile() as tmp:
            ts = get_pbf_timestamp(tmp.name)
            self.assertTrue(ts.endswith("Z"))
            self.assertIn("T", ts)


if __name__ == "__main__":
    unittest.main()
