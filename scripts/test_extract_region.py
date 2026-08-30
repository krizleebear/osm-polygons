#!/usr/bin/env python3
"""
Unit tests for extract_region.py
"""

import unittest
from unittest.mock import patch, MagicMock
import os
import sys

from extract_region import extract_region, ADMIN_FILTER_RULES, PLACES_FILTER_RULES


class TestExtractRegion(unittest.TestCase):

    def test_filter_rules_present(self):
        self.assertTrue(len(ADMIN_FILTER_RULES) >= 3)
        self.assertTrue(len(PLACES_FILTER_RULES) >= 1)
        self.assertIn("boundary=administrative", ADMIN_FILTER_RULES[0])

    def test_missing_input_pbf_raises(self):
        with self.assertRaises(FileNotFoundError):
            extract_region(
                input_pbf="non_existent.osm.pbf",
                country_code="PT",
                region="portugal",
                polygon_out="out.geojsonseq",
                places_out="out.jsonl",
            )


if __name__ == "__main__":
    unittest.main()
