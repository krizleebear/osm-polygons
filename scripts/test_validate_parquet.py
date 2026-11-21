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
    find_postal_code_parquet_files,
    find_layer_parquet_files,
    check_duplicate_files,
    inspect_parquet_file,
    inspect_postal_code_parquet_file,
    inspect_companion_parquet_file,
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
        mock_proc.stdout = '[{"total_rows": 150, "l2_count": 1, "null_geom_count": 0, "dupe_feature_count": 0, "populated_levels": [2, 4, 6, 8], "meta_keys": ["source", "license", "attribution", "country_code", "exported_at", "osm_data_timestamp"]}]'
        mock_run.return_value = mock_proc

        res = inspect_parquet_file("path/to/admin-polygons-DE.parquet")
        self.assertEqual(res["country_code"], "DE")
        self.assertEqual(res["total_rows"], 150)
        self.assertEqual(res["l2_count"], 1)
        self.assertEqual(res["null_geom_count"], 0)
        self.assertEqual(res["dupe_feature_count"], 0)
        self.assertEqual(res["populated_levels"], [2, 4, 6, 8])
        self.assertEqual(res["meta_keys"], ["source", "license", "attribution", "country_code", "exported_at", "osm_data_timestamp"])
        self.assertIsNone(res["error"])

    @patch("subprocess.run")
    def test_inspect_parquet_file_with_issues(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '[{"total_rows": 50, "l2_count": 0, "null_geom_count": 2, "dupe_feature_count": 3, "populated_levels": [4, 6], "meta_keys": ["source"]}]'
        mock_run.return_value = mock_proc

        res = inspect_parquet_file("path/to/admin-polygons-US.parquet")
        self.assertEqual(res["country_code"], "US")
        self.assertEqual(res["total_rows"], 50)
        self.assertEqual(res["l2_count"], 0)
        self.assertEqual(res["null_geom_count"], 2)
        self.assertEqual(res["dupe_feature_count"], 3)
        self.assertEqual(res["populated_levels"], [4, 6])
        self.assertEqual(res["meta_keys"], ["source"])


    @patch("validate_parquet.find_postal_code_parquet_files", return_value=[])
    @patch("validate_parquet.find_parquet_files")
    @patch("validate_parquet.inspect_parquet_file")
    def test_validate_parquets_summary(self, mock_inspect, mock_find, _mock_find_pc):
        mock_find.return_value = ["admin-polygons-DE.parquet", "admin-polygons-FR.parquet"]
        mock_inspect.side_effect = [
            {"country_code": "DE", "path": "admin-polygons-DE.parquet", "error": None, "total_rows": 100, "l2_count": 1, "null_geom_count": 0, "dupe_feature_count": 0, "populated_levels": [2, 4, 6], "meta_keys": ["source", "license", "attribution", "country_code", "exported_at", "osm_data_timestamp"]},
            {"country_code": "FR", "path": "admin-polygons-FR.parquet", "error": None, "total_rows": 200, "l2_count": 0, "null_geom_count": 0, "dupe_feature_count": 0, "populated_levels": [4, 6], "meta_keys": ["source", "license", "attribution", "country_code", "exported_at", "osm_data_timestamp"]},
        ]
        
        # Suppress stdout during test to prevent Azure DevOps runner from picking up mocked ##vso issue commands
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            exit_code = validate_parquets("some/dir", fail_on_error=False)
        self.assertEqual(exit_code, 0)


    @patch("subprocess.run")
    def test_inspect_parquet_file_corrupt_names(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ('[{"total_rows": 80, "l2_count": 1, "null_geom_count": 0, "dupe_feature_count": 0, '
                            '"ac_name_corrupt_count": 3, "ac_names_corrupt_count": 4, '
                            '"populated_levels": [2, 4], "meta_keys": ["source"]}]')
        mock_run.return_value = mock_proc

        res = inspect_parquet_file("path/to/admin-polygons-JP.parquet")
        self.assertEqual(res["ac_name_corrupt_count"], 3)
        self.assertEqual(res["ac_names_corrupt_count"], 4)

    @patch("validate_parquet.find_postal_code_parquet_files", return_value=[])
    @patch("validate_parquet.find_parquet_files")
    @patch("validate_parquet.inspect_parquet_file")
    def test_validate_parquets_warns_on_corrupt_admin_centre_names(self, mock_inspect, mock_find, _mock_find_pc):
        mock_find.return_value = ["admin-polygons-JP.parquet"]
        mock_inspect.side_effect = [
            {"country_code": "JP", "path": "admin-polygons-JP.parquet", "error": None,
             "total_rows": 100, "l2_count": 1, "null_geom_count": 0, "dupe_feature_count": 0,
             "ac_name_corrupt_count": 7, "ac_names_corrupt_count": 5,
             "populated_levels": [2, 4], "meta_keys": ["source", "license", "attribution", "country_code", "exported_at"]},
        ]

        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = validate_parquets("some/dir", fail_on_error=True)
        output = buf.getvalue()

        # Encoding corruption must be reported as a non-breaking warning (rule: transparent audits).
        self.assertIn("suspected OPL encoding corruption", output)
        self.assertIn("Corrupted Admin-Centre Names:   1", output)
        self.assertEqual(exit_code, 0)

    @patch("subprocess.run")
    def test_inspect_postal_code_parquet_file_ok(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = (
            '[{"total_rows": 8250, "null_or_empty_geom_count": 0, "invalid_geom_count": 0, '
            '"invalid_de_format_count": 0, "dupe_feature_count": 0, '
            '"meta_keys": ["source", "license", "attribution", "country_code", "exported_at", "osm_data_timestamp"]}]'
        )
        mock_run.return_value = mock_proc

        res = inspect_postal_code_parquet_file("path/to/osm-postal-codes-DE.parquet")
        self.assertEqual(res["country_code"], "DE")
        self.assertEqual(res["total_rows"], 8250)
        self.assertEqual(res["null_or_empty_geom_count"], 0)
        self.assertEqual(res["invalid_geom_count"], 0)
        self.assertEqual(res["invalid_de_format_count"], 0)
        self.assertEqual(res["dupe_feature_count"], 0)
        self.assertIsNone(res["error"])

    @patch("validate_parquet.find_postal_code_parquet_files")
    @patch("validate_parquet.inspect_postal_code_parquet_file")
    @patch("validate_parquet.find_parquet_files")
    @patch("validate_parquet.inspect_parquet_file")
    def test_validate_parquets_postal_codes_checks(self, mock_inspect, mock_find, mock_inspect_pc, mock_find_pc):
        mock_find.return_value = ["admin-polygons-DE.parquet"]
        mock_inspect.return_value = {
            "country_code": "DE",
            "path": "admin-polygons-DE.parquet",
            "error": None,
            "total_rows": 100,
            "l2_count": 1,
            "null_geom_count": 0,
            "dupe_feature_count": 0,
            "populated_levels": [2, 4, 6],
            "meta_keys": ["source", "license", "attribution", "country_code", "exported_at", "osm_data_timestamp"],
        }
        mock_find_pc.return_value = ["osm-postal-codes-DE.parquet"]
        mock_inspect_pc.return_value = {
            "country_code": "DE",
            "path": "osm-postal-codes-DE.parquet",
            "error": None,
            "total_rows": 7500,
            "null_or_empty_geom_count": 1,
            "invalid_geom_count": 2,
            "invalid_de_format_count": 3,
            "dupe_feature_count": 1,
            "meta_keys": ["source"],
        }

        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = validate_parquets("some/dir", fail_on_error=True)
        output = buf.getvalue()

        self.assertIn("Postal codes DE has fewer than 8000 polygons", output)
        self.assertIn("violating DE 5-digit postal_code format", output)
        self.assertIn("NULL/empty and 2 invalid geometry record(s)", output)
        self.assertIn("duplicate OSM feature(s)", output)
        self.assertIn("missing required metadata key(s)", output)
        self.assertEqual(exit_code, 0)

    @patch("subprocess.run")
    def test_inspect_companion_parquet_file_ok(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = (
            '[{"total_rows": 420, "null_or_empty_geom_count": 0, "dupe_feature_count": 0, '
            '"meta_keys": ["source", "license", "attribution", "country_code", "exported_at", "osm_data_timestamp"]}]'
        )
        mock_run.return_value = mock_proc

        res = inspect_companion_parquet_file("path/to/osm-places-DE.parquet", "osm-places", has_osm_type=False)
        self.assertEqual(res["country_code"], "DE")
        self.assertEqual(res["total_rows"], 420)
        self.assertEqual(res["null_or_empty_geom_count"], 0)
        self.assertEqual(res["dupe_feature_count"], 0)
        self.assertIsNone(res["error"])
        # Places table groups duplicates by osm_id only
        sql_arg = mock_run.call_args[0][0][3]
        self.assertIn("GROUP BY osm_id HAVING", sql_arg)

    @patch("validate_parquet.find_layer_parquet_files")
    @patch("validate_parquet.inspect_companion_parquet_file")
    @patch("validate_parquet.find_postal_code_parquet_files", return_value=[])
    @patch("validate_parquet.find_parquet_files")
    @patch("validate_parquet.inspect_parquet_file")
    def test_validate_parquets_companion_layers(
        self, mock_inspect, mock_find, _mock_find_pc, mock_inspect_comp, mock_find_layer
    ):
        mock_find.return_value = ["admin-polygons-DE.parquet"]
        mock_inspect.return_value = {
            "country_code": "DE",
            "path": "admin-polygons-DE.parquet",
            "error": None,
            "total_rows": 100,
            "l2_count": 1,
            "null_geom_count": 0,
            "dupe_feature_count": 0,
            "populated_levels": [2, 4, 6],
            "meta_keys": ["source", "license", "attribution", "country_code", "exported_at", "osm_data_timestamp"],
        }
        # Called for admin-polygons, postal-codes (mocked separately), then places, facilities, landuse
        mock_find_layer.side_effect = [
            ["osm-places-DE.parquet"],
            ["osm-facilities-DE.parquet"],
            [],
        ]
        mock_inspect_comp.side_effect = [
            {
                "country_code": "DE",
                "path": "osm-places-DE.parquet",
                "error": None,
                "total_rows": 500,
                "null_or_empty_geom_count": 0,
                "dupe_feature_count": 0,
                "meta_keys": ["source", "license", "attribution", "country_code", "exported_at", "osm_data_timestamp"],
            },
            {
                "country_code": "DE",
                "path": "osm-facilities-DE.parquet",
                "error": None,
                "total_rows": 0,
                "null_or_empty_geom_count": 2,
                "dupe_feature_count": 1,
                "meta_keys": ["source"],
            },
        ]

        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = validate_parquets("some/dir", fail_on_error=True)
        output = buf.getvalue()

        self.assertIn("osm-places Parquets Checked:  1 (Issues: 0)", output)
        self.assertIn("osm-facilities Parquets Checked:  1 (Issues: 4)", output)
        self.assertEqual(exit_code, 0)

    @patch("validate_parquet.find_layer_parquet_files", return_value=[])
    @patch("validate_parquet.find_postal_code_parquet_files", return_value=[])
    @patch("validate_parquet.find_parquet_files")
    @patch("validate_parquet.inspect_parquet_file")
    def test_validate_parquets_fails_on_missing_expected_l2(self, mock_inspect, mock_find, _mock_pc, _mock_layer):
        mock_find.return_value = ["admin-polygons-BE.parquet"]
        mock_inspect.return_value = {
            "country_code": "BE",
            "path": "admin-polygons-BE.parquet",
            "error": None,
            "total_rows": 100,
            "l2_count": 0,
            "null_geom_count": 0,
            "dupe_feature_count": 0,
            "populated_levels": [4, 6],
            "meta_keys": ["source", "license", "attribution", "country_code", "exported_at", "osm_data_timestamp"],
        }

        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = validate_parquets("some/dir", fail_on_error=True)
        output = buf.getvalue()

        self.assertIn("type=error]Country BE", output)
        self.assertEqual(exit_code, 1)

    @patch("validate_parquet.find_layer_parquet_files", return_value=[])
    @patch("validate_parquet.find_postal_code_parquet_files", return_value=[])
    @patch("validate_parquet.find_parquet_files")
    @patch("validate_parquet.inspect_parquet_file")
    def test_validate_parquets_allows_territories_without_expected_l2(self, mock_inspect, mock_find, _mock_pc, _mock_layer):
        territories = [
            ("GF", [3, 4, 8]),
            ("HK", [3]),
            ("MO", [3]),
            ("CC", [4]),
            ("CX", [4]),
            ("HM", [4]),
            ("NF", [4]),
            ("AW", [3]),
            ("AX", [3]),
            ("PM", [3]),
        ]
        required_meta = ["source", "license", "attribution", "country_code", "exported_at", "osm_data_timestamp"]
        mock_find.return_value = [f"admin-polygons-{cc}.parquet" for cc, _ in territories]
        mock_inspect.side_effect = [
            {
                "country_code": cc,
                "path": f"admin-polygons-{cc}.parquet",
                "error": None,
                "total_rows": 20,
                "l2_count": 0,
                "null_geom_count": 0,
                "dupe_feature_count": 0,
                "populated_levels": levels,
                "meta_keys": required_meta,
            }
            for cc, levels in territories
        ]

        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = validate_parquets("some/dir", fail_on_error=True)
        output = buf.getvalue()

        self.assertNotIn("type=error]", output)
        self.assertNotIn("type=warning]", output)
        self.assertEqual(exit_code, 0)

    @patch("validate_parquet.find_layer_parquet_files", return_value=[])
    @patch("validate_parquet.find_postal_code_parquet_files", return_value=[])
    @patch("validate_parquet.find_parquet_files")
    @patch("validate_parquet.inspect_parquet_file")
    def test_validate_parquets_warns_for_unlisted_country_without_l2(self, mock_inspect, mock_find, _mock_pc, _mock_layer):
        mock_find.return_value = ["admin-polygons-ZZ.parquet"]
        mock_inspect.return_value = {
            "country_code": "ZZ",
            "path": "admin-polygons-ZZ.parquet",
            "error": None,
            "total_rows": 10,
            "l2_count": 0,
            "null_geom_count": 0,
            "dupe_feature_count": 0,
            "populated_levels": [4],
            "meta_keys": ["source", "license", "attribution", "country_code", "exported_at", "osm_data_timestamp"],
        }

        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = validate_parquets("some/dir", fail_on_error=True)
        output = buf.getvalue()

        self.assertIn("type=warning]Country ZZ", output)
        self.assertNotIn("type=error]", output)
        self.assertEqual(exit_code, 0)

    @patch("validate_parquet.find_layer_parquet_files", return_value=[])
    @patch("validate_parquet.find_postal_code_parquet_files", return_value=[])
    @patch("validate_parquet.find_parquet_files")
    @patch("validate_parquet.inspect_parquet_file")
    def test_validate_parquets_fails_on_missing_l2_for_new_sovereign_regions(self, mock_inspect, mock_find, _mock_pc, _mock_layer):
        required_meta = ["source", "license", "attribution", "country_code", "exported_at", "osm_data_timestamp"]
        ccs = ["TO", "HN", "PA"]
        mock_find.return_value = [f"admin-polygons-{cc}.parquet" for cc in ccs]
        mock_inspect.side_effect = [
            {
                "country_code": cc,
                "path": f"admin-polygons-{cc}.parquet",
                "error": None,
                "total_rows": 50,
                "l2_count": 0,
                "null_geom_count": 0,
                "dupe_feature_count": 0,
                "populated_levels": [4, 6],
                "meta_keys": required_meta,
            }
            for cc in ccs
        ]

        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = validate_parquets("some/dir", fail_on_error=True)
        output = buf.getvalue()

        for cc in ccs:
            self.assertIn(f"type=error]Country {cc}", output)
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()

