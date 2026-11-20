#!/usr/bin/env python3
"""
Unit tests for 4-way pipeline synchronization and canonical Geofabrik URLs
across osm-download-pipeline.yml, polygon-export-pipeline.yml, and countries.json.

Uses only the Python 3 standard library so it runs inside minimal containers.
"""

import json
import os
import re
import unittest

import validate_geofabrik_urls

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOWNLOAD_YAML = os.path.join(REPO_ROOT, "osm-download-pipeline.yml")
EXPORT_YAML = os.path.join(REPO_ROOT, "polygon-export-pipeline.yml")
COUNTRIES_JSON = os.path.join(REPO_ROOT, "scripts", "countries.json")

PAIR_RE = re.compile(r"OSM_REGION:\s*'([^']+)'\s+PBF_URL:\s*'([^']+)'")
TRIPLE_RE = re.compile(
    r"OSM_REGION:\s*'([^']+)'\s+PBF_URL:\s*'([^']+)'\s+COUNTRY_CODE:\s*'([^']+)'"
)
PARTITION_KEY_RE = re.compile(r"^ {8}([a-z_]+):\s*$", re.MULTILINE)
REGIONS_LINE_RE = re.compile(r"REGIONS:\s*'([^']+)'")
ARTIFACT_PATTERN_RE = re.compile(r"\*-([a-z0-9-]+)/\*\*")


def read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def parse_region_url_pairs(text):
    return PAIR_RE.findall(text)


def parse_export_triples(text):
    return TRIPLE_RE.findall(text)


def parse_parquet_partitions(text):
    """Return {partition_name: (regions_list, artifact_pattern_regions_list)}."""
    after_job = text.split("- job: parquet", 1)[1]
    matrix_block = after_job.split("\n    steps:", 1)[0]
    matches = list(PARTITION_KEY_RE.finditer(matrix_block))
    partitions = {}
    for idx, match in enumerate(matches):
        name = match.group(1)
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(matrix_block)
        chunk = matrix_block[start:end]
        reg_match = REGIONS_LINE_RE.search(chunk)
        regions = reg_match.group(1).split() if reg_match else []
        patterns = ARTIFACT_PATTERN_RE.findall(chunk)
        partitions[name] = (regions, patterns)
    return partitions


class TestPipelineSyncAndUrls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.download_text = read_text(DOWNLOAD_YAML)
        cls.export_text = read_text(EXPORT_YAML)
        cls.countries = json.loads(read_text(COUNTRIES_JSON))

    def test_download_and_export_matrices_match_regions_and_urls(self):
        dl_pairs = parse_region_url_pairs(self.download_text)
        ex_pairs = parse_region_url_pairs(self.export_text)

        self.assertGreaterEqual(len(dl_pairs), 197)
        self.assertEqual(len(dl_pairs), len({r for r, _ in dl_pairs}), "Duplicate region in osm-download-pipeline.yml")
        self.assertEqual(len(ex_pairs), len({r for r, _ in ex_pairs}), "Duplicate region in polygon-export-pipeline.yml")

        dl_map = dict(dl_pairs)
        ex_map = dict(ex_pairs)
        self.assertEqual(set(dl_map.keys()), set(ex_map.keys()))

        for reg, dl_url in dl_map.items():
            self.assertEqual(
                dl_url,
                ex_map[reg],
                f"PBF_URL mismatch between download and export pipelines for '{reg}'",
            )

    def test_all_pbf_urls_are_canonical_geofabrik_format(self):
        for path in (DOWNLOAD_YAML, EXPORT_YAML):
            count, errs = validate_geofabrik_urls.validate_file(path, catalog=None)
            self.assertGreaterEqual(count, 197)
            self.assertEqual(errs, [], f"Canonical URL errors in {path}: {errs}")

    def test_parquet_partitions_match_export_matrix_and_artifact_patterns(self):
        ex_regions = [r for r, _, _ in parse_export_triples(self.export_text)]
        partitions = parse_parquet_partitions(self.export_text)
        self.assertGreaterEqual(len(partitions), 10)

        seen_union = set()
        for part_name, (regions, patterns) in partitions.items():
            self.assertTrue(regions, f"Partition '{part_name}' has empty REGIONS")
            self.assertEqual(
                len(regions),
                len(set(regions)),
                f"Duplicate region inside REGIONS of partition '{part_name}'",
            )
            self.assertEqual(
                len(patterns),
                len(set(patterns)),
                f"Duplicate ARTIFACT_PATTERNS in partition '{part_name}'",
            )
            self.assertEqual(
                set(regions),
                set(patterns),
                f"REGIONS vs ARTIFACT_PATTERNS mismatch in partition '{part_name}'",
            )
            overlap = seen_union & set(regions)
            self.assertEqual(
                overlap,
                set(),
                f"Region(s) assigned to multiple simplify partitions: {sorted(overlap)}",
            )
            seen_union.update(regions)

        self.assertEqual(seen_union, set(ex_regions))

    def test_every_export_country_code_exists_in_countries_json(self):
        triples = parse_export_triples(self.export_text)
        self.assertGreaterEqual(len(triples), 197)
        for reg, _, cc in triples:
            self.assertIn(
                cc,
                self.countries,
                f"COUNTRY_CODE '{cc}' for region '{reg}' missing from scripts/countries.json",
            )
            entry = self.countries[cc]
            self.assertTrue(entry.get("continent"), f"countries.json[{cc}] missing continent")
            self.assertTrue(entry.get("name"), f"countries.json[{cc}] missing name")

    def test_osm_download_pipeline_has_preflight_guard(self):
        self.assertIn("- job: preflight", self.download_text)
        self.assertIn("scripts/validate_geofabrik_urls.py", self.download_text)
        self.assertRegex(
            self.download_text,
            r"- job: download\s+dependsOn: preflight",
        )

    def test_validate_geofabrik_urls_catches_flat_legacy_dom_url_and_catalog_mismatch(self):
        flat_legacy_yaml = (
            "      guadeloupe:\n"
            "        OSM_REGION: 'guadeloupe'\n"
            "        PBF_URL: 'https://download.geofabrik.de/europe/france/guadeloupe-latest.osm.pbf'\n"
        )
        errs_offline = validate_geofabrik_urls.validate_yaml_text(
            flat_legacy_yaml, label="legacy.yml", catalog=None
        )
        self.assertTrue(errs_offline, "Expected flat legacy DOM URL to be rejected offline")

        canonical_yaml = (
            "      guadeloupe:\n"
            "        OSM_REGION: 'guadeloupe'\n"
            "        PBF_URL: 'https://download.geofabrik.de/europe/france/guadeloupe/guadeloupe-latest.osm.pbf'\n"
        )
        good_catalog = {
            "guadeloupe": "https://download.geofabrik.de/europe/france/guadeloupe/guadeloupe-latest.osm.pbf"
        }
        self.assertEqual(
            validate_geofabrik_urls.validate_yaml_text(
                canonical_yaml, label="ok.yml", catalog=good_catalog
            ),
            [],
        )

        mismatched_catalog = {
            "guadeloupe": "https://download.geofabrik.de/central-america/guadeloupe/guadeloupe-latest.osm.pbf"
        }
        errs_mismatch = validate_geofabrik_urls.validate_yaml_text(
            canonical_yaml, label="mismatch.yml", catalog=mismatched_catalog
        )
        self.assertEqual(len(errs_mismatch), 1)
        self.assertIn("URL mismatch", errs_mismatch[0])


if __name__ == "__main__":
    unittest.main()
