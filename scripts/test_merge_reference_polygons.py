#!/usr/bin/env python3
import json
import os
import tempfile
import unittest

from merge_reference_polygons import load_candidates, load_reference_features, main


class TestMergeReferencePolygons(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.candidates_file = os.path.join(self.temp_dir.name, "candidates.json")
        self.reference_file = os.path.join(self.temp_dir.name, "reference.geojsonseq")
        self.stream_file = os.path.join(self.temp_dir.name, "stream.geojsonseq")

        # Mock candidates
        candidates_data = {
            "candidates": [
                {"osm_id": 1001, "country": "MC", "admin_level": 2, "name": "Monaco"},
                {"osm_id": 2001, "country": "US", "admin_level": 2, "name": "United States"},
                {"osm_id": 2002, "country": "US", "admin_level": 4, "name": "Alaska"}
            ]
        }
        with open(self.candidates_file, "w") as f:
            json.dump(candidates_data, f)

        # Mock reference polygons
        self.ref_mc = {
            "type": "Feature",
            "id": 1001,
            "properties": {"osm_id": 1001, "name": "Monaco", "admin_level": "2", "ISO3166-1": "MC"},
            "geometry": {"type": "MultiPolygon", "coordinates": [[[[7.4, 43.7], [7.5, 43.7], [7.5, 43.8], [7.4, 43.7]]]]}
        }
        self.ref_us = {
            "type": "Feature",
            "id": 2001,
            "properties": {"osm_id": 2001, "name": "United States", "admin_level": "2", "ISO3166-1": "US"},
            "geometry": {"type": "MultiPolygon", "coordinates": [[[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]]]}
        }
        with open(self.reference_file, "w") as f:
            f.write(json.dumps(self.ref_mc) + "\n")
            f.write(json.dumps(self.ref_us) + "\n")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_candidates_filters_by_country(self):
        mc_candidates = load_candidates(self.candidates_file, "MC")
        self.assertEqual(len(mc_candidates), 1)
        self.assertIn(1001, mc_candidates)

        us_candidates = load_candidates(self.candidates_file, "US")
        self.assertEqual(len(us_candidates), 2)
        self.assertIn(2001, us_candidates)
        self.assertIn(2002, us_candidates)

    def test_insert_missing_candidate(self):
        # Stream has other features, but missing Monaco (1001)
        orig_stream = [
            {"type": "Feature", "id": 5001, "properties": {"osm_id": 5001, "admin_level": "8", "ISO3166-1": "MC"}},
        ]
        with open(self.stream_file, "w") as f:
            for feat in orig_stream:
                f.write(json.dumps(feat) + "\n")

        output_file = os.path.join(self.temp_dir.name, "out.geojsonseq")
        import io, sys
        from contextlib import redirect_stdout
        orig_argv = sys.argv
        try:
            sys.argv = [
                "merge_reference_polygons.py",
                self.stream_file,
                "--reference", self.reference_file,
                "--candidates", self.candidates_file,
                "--country-code", "MC",
                "--out", output_file
            ]
            with redirect_stdout(io.StringIO()):
                main()
        finally:
            sys.argv = orig_argv

        with open(output_file, "r") as f:
            lines = [json.loads(line) for line in f if line.strip()]

        self.assertEqual(len(lines), 2)
        osm_ids = [feat["properties"]["osm_id"] for feat in lines]
        self.assertIn(1001, osm_ids)
        self.assertIn(5001, osm_ids)

    def test_replace_corrupted_candidate(self):
        # Stream has Monaco (1001), but we want to replace it
        orig_stream = [
            {"type": "Feature", "id": 1001, "properties": {"osm_id": 1001, "name": "Broken Monaco", "admin_level": "2", "ISO3166-1": "MC"}},
        ]
        with open(self.stream_file, "w") as f:
            for feat in orig_stream:
                f.write(json.dumps(feat) + "\n")

        output_file = os.path.join(self.temp_dir.name, "out.geojsonseq")
        import io, sys
        from contextlib import redirect_stdout
        orig_argv = sys.argv
        try:
            sys.argv = [
                "merge_reference_polygons.py",
                self.stream_file,
                "--reference", self.reference_file,
                "--candidates", self.candidates_file,
                "--country-code", "MC",
                "--out", output_file
            ]
            with redirect_stdout(io.StringIO()):
                main()
        finally:
            sys.argv = orig_argv

        with open(output_file, "r") as f:
            lines = [json.loads(line) for line in f if line.strip()]

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["properties"]["name"], "Monaco")  # Replaced with reference name



if __name__ == "__main__":
    unittest.main()
