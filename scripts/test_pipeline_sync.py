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

PAIR_RE = re.compile(r"OSM_REGION:\s*'([^']+)'\s+PBF_URL:\s*'([^']*)'")
TRIPLE_RE = re.compile(
    r"OSM_REGION:\s*'([^']+)'\s+PBF_URL:\s*'([^']*)'\s+COUNTRY_CODE:\s*'([^']+)'"
)
PARTITION_KEY_RE = re.compile(r"^ {8}([a-z_]+):\s*$", re.MULTILINE)
REGIONS_LINE_RE = re.compile(r"REGIONS:\s*'([^']+)'")
ARTIFACT_PATTERN_RE = re.compile(r"\*-([a-z0-9-]+)/\*\*")
COMPOSITES_JSON = os.path.join(REPO_ROOT, "scripts", "composite-extracts.json")


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
        derived_regions, sources = validate_geofabrik_urls.load_composite_config(COMPOSITES_JSON)

        self.assertGreaterEqual(len(dl_pairs), 197)
        self.assertEqual(len(derived_regions), 23)
        self.assertEqual(len(dl_pairs), len({r for r, _ in dl_pairs}), "Duplicate region in osm-download-pipeline.yml")
        self.assertEqual(len(ex_pairs), len({r for r, _ in ex_pairs}), "Duplicate region in polygon-export-pipeline.yml")

        dl_map = dict(dl_pairs)
        ex_map = dict(ex_pairs)
        self.assertEqual(set(dl_map.keys()) & derived_regions, set())
        self.assertEqual(set(dl_map.keys()) | derived_regions, set(ex_map.keys()))
        self.assertGreaterEqual(len(ex_map), 220)

        for reg, dl_url in dl_map.items():
            self.assertEqual(
                dl_url,
                ex_map[reg],
                f"PBF_URL mismatch between download and export pipelines for '{reg}'",
            )

        for reg in derived_regions:
            self.assertEqual(ex_map[reg], "", f"Derived region '{reg}' must have empty PBF_URL in export matrix")
            self.assertIn(
                f"    - {reg}\n",
                self.download_text,
                f"Derived region '{reg}' must be published in osm-download-pipeline.yml",
            )
        for source_id, src_url in sources.items():
            self.assertIn(
                f"pbfUrl: '{src_url}'",
                self.download_text,
                f"Composite source '{source_id}' pbfUrl missing from osm-download-pipeline.yml",
            )

    def test_all_pbf_urls_are_canonical_geofabrik_format(self):
        dl_count, dl_errs = validate_geofabrik_urls.validate_file(DOWNLOAD_YAML, catalog=None)
        self.assertGreaterEqual(dl_count, 197)
        self.assertEqual(dl_errs, [], f"Canonical URL errors in {DOWNLOAD_YAML}: {dl_errs}")

        ex_count, ex_errs = validate_geofabrik_urls.validate_file(EXPORT_YAML, catalog=None)
        self.assertGreaterEqual(ex_count, 220)
        self.assertEqual(ex_errs, [], f"Canonical URL errors in {EXPORT_YAML}: {ex_errs}")

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
        self.assertIn("templates/extract-composite-job.yml", self.download_text)
        self.assertIn("extract_guernsey_jersey", self.download_text)
        self.assertIn("extract_american_oceania", self.download_text)
        self.assertIn("extract_caribbean", self.download_text)

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

    def test_parquet_stage_fetches_missing_postpass_candidates_and_merges(self):
        parquet_stage = self.export_text.split("- stage: parquet", 1)[1].split("- stage: release", 1)[0]
        fetch_job = parquet_stage.split("- job: fetch_reference", 1)[1].split("- job: parquet", 1)[0]
        self.assertIn("- job: fetch_reference", parquet_stage)
        self.assertIn("scripts/resolve_reference_cache_build.py", fetch_job)
        self.assertIn("SYSTEM_ACCESSTOKEN: $(System.AccessToken)", fetch_job)
        self.assertIn("ne(variables['DL_REF_BUILD_ID'], '')", fetch_job)
        self.assertIn("ne(variables['POLY_REF_BUILD_ID'], '')", fetch_job)
        self.assertIn("pipelineId: '$(DL_REF_BUILD_ID)'", fetch_job)
        self.assertIn("pipelineId: '$(POLY_REF_BUILD_ID)'", fetch_job)
        self.assertIn("artifact: 'admin-polygons-reference'", fetch_job)
        self.assertNotIn("tags: 'has-reference-polygons'", fetch_job)
        self.assertIn("scripts/fetch_postpass_polygons.py", parquet_stage)
        self.assertIn("--reuse-cached", parquet_stage)
        self.assertRegex(parquet_stage, r"- job: parquet\s+dependsOn: fetch_reference")
        self.assertIn("targetPath: '$(System.DefaultWorkingDirectory)/reference-polygons'", parquet_stage)
        self.assertIn("scripts/merge_reference_polygons.py", parquet_stage)
        self.assertLess(
            parquet_stage.index("scripts/merge_reference_polygons.py"),
            parquet_stage.index("./scripts/convert_to_parquet.sh"),
        )

    def test_download_pipeline_reference_polygons_resolves_cached_build(self):
        ref_job = self.download_text.split("- job: reference_polygons", 1)[1]
        self.assertIn("- checkout: self", ref_job)
        self.assertIn("scripts/resolve_reference_cache_build.py", ref_job)
        self.assertIn("SYSTEM_ACCESSTOKEN: $(System.AccessToken)", ref_job)
        self.assertIn("ne(variables['DL_REF_BUILD_ID'], '')", ref_job)
        self.assertIn("pipelineId: '$(DL_REF_BUILD_ID)'", ref_job)
        self.assertIn("artifact: 'admin-polygons-reference'", ref_job)


if __name__ == "__main__":
    unittest.main()
