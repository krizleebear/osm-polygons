#!/usr/bin/env python3
"""
Unit tests for validate_parquet.py
"""

import unittest
from unittest.mock import patch, MagicMock
import os
import sys

from validate_parquet import (
    find_parquet_files,
    check_duplicate_files,
    inspect_parquet_file,
    validate_parquets
)


class TestValidateParquet(unittest.TestCase):

    def test_check_duplicate_files(self):
        files = [
            "europe/admin-polygons-DE.parquet",
            "europe/admin-polygons-FR.parquet",
            "africa/admin-polygons-DE.parquet",
        ]
        dupes = check_duplicate_files(files)
        self.assertEqual(len(dupes), 1)
        self.assertEqual(dupes[0][0], "admin-polygons-DE.parquet")

    @patch("subprocess.run")
    def test_inspect_parquet_file_ok(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "total_rows,l2_count,null_geom_count,dupe_feature_count\n150,1,0,0\n"
        mock_run.return_value = mock_proc

        res = inspect_parquet_file("path/to/admin-polygons-DE.parquet")
        self.assertEqual(res["country_code"], "DE")
        self.assertEqual(res["total_rows"], 150)
        self.assertEqual(res["l2_count"], 1)
        self.assertEqual(res["null_geom_count"], 0)
        self.assertEqual(res["dupe_feature_count"], 0)
        self.assertIsNone(res["error"])

    @patch("subprocess.run")
    def test_inspect_parquet_file_with_issues(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "total_rows,l2_count,null_geom_count,dupe_feature_count\n50,0,2,3\n"
        mock_run.return_value = mock_proc

        res = inspect_parquet_file("path/to/admin-polygons-US.parquet")
        self.assertEqual(res["country_code"], "US")
        self.assertEqual(res["total_rows"], 50)
        self.assertEqual(res["l2_count"], 0)
        self.assertEqual(res["null_geom_count"], 2)
        self.assertEqual(res["dupe_feature_count"], 3)

    @patch("validate_parquet.find_parquet_files")
    @patch("validate_parquet.inspect_parquet_file")
    def test_validate_parquets_summary(self, mock_inspect, mock_find):
        mock_find.return_value = ["admin-polygons-DE.parquet", "admin-polygons-FR.parquet"]
        mock_inspect.side_effect = [
            {"country_code": "DE", "path": "admin-polygons-DE.parquet", "error": None, "total_rows": 100, "l2_count": 1, "null_geom_count": 0, "dupe_feature_count": 0},
            {"country_code": "FR", "path": "admin-polygons-FR.parquet", "error": None, "total_rows": 200, "l2_count": 0, "null_geom_count": 0, "dupe_feature_count": 0},
        ]
        
        exit_code = validate_parquets("some/dir", fail_on_error=False)
        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
