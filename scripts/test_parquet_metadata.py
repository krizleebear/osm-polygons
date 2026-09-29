#!/usr/bin/env python3
"""
Unit tests for Parquet provenance metadata (AGENTS.md rule 35).

Every GeoParquet file produced by the export pipeline must embed the standard
provenance, copyright and licensing keys in its footer via DuckDB KV_METADATA.
This includes the per-country merges in polygon-export-pipeline.yml, which
rewrite the per-region temp Parquet files and therefore must re-state the
metadata instead of silently dropping it.
"""

import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PIPELINE_YAML = os.path.join(REPO_ROOT, "polygon-export-pipeline.yml")
LANDUSE_CONVERTER = os.path.join(REPO_ROOT, "scripts", "convert_landuse_to_parquet.sh")

SQL_TEMPLATES = (
    "scripts/export_parquet.sql",
    "scripts/export_places.sql",
    "scripts/export_facilities.sql",
    "scripts/export_landuse.sql",
)

REQUIRED_METADATA_KEYS = (
    "source",
    "origin",
    "dataset",
    "attribution",
    "attribution_url",
    "license",
    "license_url",
    "copyright",
    "schema",
    "schema_url",
    "compiler",
    "country_code",
    "exported_at",
)

# Shell variables holding the per-country output paths of the four merges.
MERGE_TARGET_VARS = ("${out}", "${pout}", "${fout}", "${lout}")


def copy_options_for_target(script, target):
    """Return the option list of every ``COPY ... TO '<target>' (...)`` statement."""
    options = []
    for match in re.finditer(r"TO\s+'(%s)'\s*\(" % re.escape(target), script):
        start = match.end()
        depth = 1
        index = start
        quote = None
        while index < len(script) and depth > 0:
            char = script[index]
            if quote:
                if char == quote:
                    quote = None
            elif char in "'\"":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            index += 1
        options.append(script[start:index - 1])
    return options


def read_file(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class TestPipelineMergeMetadata(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.pipeline = read_file(PIPELINE_YAML)

    def test_per_country_merge_statements_embed_kv_metadata(self):
        for target in MERGE_TARGET_VARS:
            options = copy_options_for_target(self.pipeline, target)
            self.assertEqual(
                len(options), 1,
                f"Expected exactly one COPY statement writing to {target}",
            )
            option_block = options[0]
            self.assertIn(
                "KV_METADATA", option_block,
                f"Per-country merge to {target} drops Parquet KV_METADATA",
            )
            for key in REQUIRED_METADATA_KEYS:
                self.assertIn(
                    f"'{key}'", option_block,
                    f"Per-country merge to {target} is missing metadata key '{key}'",
                )

    def test_per_country_merge_exported_at_comes_from_pipeline_variable(self):
        for target in MERGE_TARGET_VARS:
            option_block = copy_options_for_target(self.pipeline, target)[0]
            self.assertIn(
                "'${EXPORTED_AT}'", option_block,
                f"Per-country merge to {target} must stamp the pipeline EXPORTED_AT value",
            )

    def test_parquet_job_defines_exported_at_before_merges(self):
        self.assertIn('EXPORTED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"', self.pipeline)


class TestSqlTemplateMetadata(unittest.TestCase):

    def test_export_sql_templates_embed_required_metadata_keys(self):
        for relative_path in SQL_TEMPLATES:
            template = read_file(os.path.join(REPO_ROOT, relative_path))
            self.assertIn(
                "KV_METADATA", template,
                f"{relative_path} does not write provenance metadata",
            )
            for key in REQUIRED_METADATA_KEYS:
                self.assertIn(
                    f"'{key}'", template,
                    f"{relative_path} is missing metadata key '{key}'",
                )

    def test_landuse_converter_substitutes_exported_at_placeholder(self):
        converter = read_file(LANDUSE_CONVERTER)
        self.assertIn(
            '"s|__EXPORTED_AT__|${EXPORTED_AT}|g"', converter,
            "convert_landuse_to_parquet.sh must substitute __EXPORTED_AT__",
        )


if __name__ == "__main__":
    unittest.main()
