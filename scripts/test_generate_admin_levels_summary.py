#!/usr/bin/env python3
import unittest
import os
import json
import tempfile
from generate_admin_levels_summary import generate_markdown

class TestGenerateAdminLevelsSummary(unittest.TestCase):

    def test_generate_markdown_formatting(self):
        stats = [
            {
                "country_code": "DE",
                "country_name": "Germany",
                "levels": {"2": 1, "4": 16, "6": 400, "8": 11054},
                "total_features": 11471
            },
            {
                "country_code": "MC",
                "country_name": "Monaco",
                "levels": {"2": 1, "10": 1},
                "total_features": 2
            }
        ]
        md = generate_markdown(stats)
        self.assertIn("# Administrative Levels Coverage Summary", md)
        self.assertIn("**Total Countries / Territories:** 2", md)
        self.assertIn("**Total Administrative Polygons:** 11,473", md)
        self.assertIn("| **DE** | Germany | 1 | - | 16 | - | 400 | - | 11,054 | - | - | - | **11,471** |", md)
        self.assertIn("| **MC** | Monaco | 1 | - | - | - | - | - | - | - | 1 | - | **2** |", md)

if __name__ == '__main__':
    unittest.main()
