#!/usr/bin/env python3
"""
Comprehensive validation script for per-country GeoParquet datasets.

Performs quality, completeness, and integrity checks across generated
admin-polygons-*.parquet files:
  1. Duplicate Country Files Check (across continents)
  2. Level 2 National Boundary Completeness Check
  3. Feature Deduplication Check (no duplicate osm_type + osm_id in a file)
  4. Empty Datasets & NULL Geometry Integrity Check
  5. Admin-Centre Name Encoding Corruption Check (raw '%' / control characters,
     i.e. OPL escape sequences that were not decoded properly)

Outputs human-readable diagnostic tables and native Azure DevOps warning annotations
(##vso[task.logissue type=warning]...).

Usage:
    python3 scripts/validate_parquet.py <parquet_directory> [--fail-on-error]
"""

import argparse
import glob
import json
import os
import subprocess
import sys



def find_layer_parquet_files(base_dir, pattern):
    """Find all parquet files matching a filename glob pattern recursively."""
    return sorted(glob.glob(os.path.join(base_dir, "**", pattern), recursive=True))


def find_parquet_files(base_dir):
    """Find all admin-polygons-*.parquet files recursively."""
    return find_layer_parquet_files(base_dir, "admin-polygons-*.parquet")


def find_postal_code_parquet_files(base_dir):
    """Find all osm-postal-codes-*.parquet files recursively."""
    return find_layer_parquet_files(base_dir, "osm-postal-codes-*.parquet")


COMPANION_LAYERS = [
    ("osm-places", "osm-places-*.parquet", False),
    ("osm-facilities", "osm-facilities-*.parquet", True),
    ("osm-landuse", "osm-landuse-*.parquet", True),
]


def check_duplicate_files(files):
    """Check if the same country parquet file was emitted multiple times (e.g. across continents)."""
    seen = {}
    duplicates = []
    for path in files:
        basename = os.path.basename(path)
        if basename in seen:
            duplicates.append((basename, seen[basename], path))
        else:
            seen[basename] = path
    return duplicates


def inspect_parquet_file(file_path):
    """Query a single parquet file for metrics using duckdb CLI."""
    cc = os.path.basename(file_path).replace("admin-polygons-", "").replace(".parquet", "")
    
    sql = f"""
    INSTALL spatial; LOAD spatial;
    SELECT 
        count(*) AS total_rows,
        count(*) FILTER (WHERE admin_level = 2) AS l2_count,
        count(*) FILTER (WHERE geom IS NULL) AS null_geom_count,
        count(*) FILTER (
            WHERE admin_centre_name IS NOT NULL
              AND (position('%' IN admin_centre_name) > 0
                   OR regexp_matches(admin_centre_name, '[[:cntrl:]]'))
        ) AS ac_name_corrupt_count,
        count(*) FILTER (
            WHERE admin_centre_names_json IS NOT NULL
              AND (position('%' IN admin_centre_names_json) > 0
                   OR regexp_matches(admin_centre_names_json, '[[:cntrl:]]'))
        ) AS ac_names_corrupt_count,
        (SELECT count(*) FROM (
            SELECT osm_type, osm_id, count(*) 
            FROM read_parquet('{file_path}') 
            GROUP BY osm_type, osm_id HAVING count(*) > 1
        )) AS dupe_feature_count,
        (SELECT list_sort(list(DISTINCT admin_level)) FROM (SELECT admin_level FROM read_parquet('{file_path}') WHERE admin_level IS NOT NULL)) AS populated_levels,
        (SELECT list(k) FROM (SELECT key::VARCHAR AS k FROM parquet_kv_metadata('{file_path}'))) AS meta_keys
    FROM read_parquet('{file_path}');
    """
    
    cmd = ["duckdb", "-json", "-c", sql]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        return {
            "country_code": cc,
            "path": file_path,
            "error": res.stderr.strip(),
            "total_rows": 0,
            "l2_count": 0,
            "null_geom_count": 0,
            "dupe_feature_count": 0,
            "ac_name_corrupt_count": 0,
            "ac_names_corrupt_count": 0,
            "populated_levels": [],
            "meta_keys": [],
        }
    
    try:
        data = json.loads(res.stdout)
        if not data:
            raise ValueError("Empty output")
        row = data[0]
        return {
            "country_code": cc,
            "path": file_path,
            "error": None,
            "total_rows": int(row.get("total_rows", 0)),
            "l2_count": int(row.get("l2_count", 0)),
            "null_geom_count": int(row.get("null_geom_count", 0)),
            "dupe_feature_count": int(row.get("dupe_feature_count", 0)),
            "ac_name_corrupt_count": int(row.get("ac_name_corrupt_count", 0)),
            "ac_names_corrupt_count": int(row.get("ac_names_corrupt_count", 0)),
            "populated_levels": [int(x) for x in row.get("populated_levels", []) if x is not None],
            "meta_keys": [str(x) for x in row.get("meta_keys", []) if x is not None],
        }
    except Exception as e:
        return {
            "country_code": cc,
            "path": file_path,
            "error": f"JSON parse error: {e}",
            "total_rows": 0,
            "l2_count": 0,
            "null_geom_count": 0,
            "dupe_feature_count": 0,
            "ac_name_corrupt_count": 0,
            "ac_names_corrupt_count": 0,
            "populated_levels": [],
            "meta_keys": [],
        }


def inspect_postal_code_parquet_file(file_path):
    """Query a single osm-postal-codes-*.parquet file for quality metrics using duckdb CLI."""
    cc = os.path.basename(file_path).replace("osm-postal-codes-", "").replace(".parquet", "")

    sql = f"""
    INSTALL spatial; LOAD spatial;
    SELECT
        count(*) AS total_rows,
        count(*) FILTER (WHERE geom IS NULL OR ST_IsEmpty(geom)) AS null_or_empty_geom_count,
        count(*) FILTER (WHERE geom IS NOT NULL AND NOT ST_IsEmpty(geom) AND NOT ST_IsValid(geom)) AS invalid_geom_count,
        count(*) FILTER (
            WHERE country_code = 'DE'
              AND (postal_code IS NULL OR NOT regexp_matches(postal_code, '^[0-9]{{5}}(;[0-9]{{5}})*$'))
        ) AS invalid_de_format_count,
        (SELECT count(*) FROM (
            SELECT osm_type, osm_id, count(*)
            FROM read_parquet('{file_path}')
            GROUP BY osm_type, osm_id HAVING count(*) > 1
        )) AS dupe_feature_count,
        (SELECT list(k) FROM (SELECT key::VARCHAR AS k FROM parquet_kv_metadata('{file_path}'))) AS meta_keys
    FROM read_parquet('{file_path}');
    """

    cmd = ["duckdb", "-json", "-c", sql]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        return {
            "country_code": cc,
            "path": file_path,
            "error": res.stderr.strip(),
            "total_rows": 0,
            "null_or_empty_geom_count": 0,
            "invalid_geom_count": 0,
            "invalid_de_format_count": 0,
            "dupe_feature_count": 0,
            "meta_keys": [],
        }

    try:
        data = json.loads(res.stdout)
        if not data:
            raise ValueError("Empty output")
        row = data[0]
        return {
            "country_code": cc,
            "path": file_path,
            "error": None,
            "total_rows": int(row.get("total_rows", 0)),
            "null_or_empty_geom_count": int(row.get("null_or_empty_geom_count", 0)),
            "invalid_geom_count": int(row.get("invalid_geom_count", 0)),
            "invalid_de_format_count": int(row.get("invalid_de_format_count", 0)),
            "dupe_feature_count": int(row.get("dupe_feature_count", 0)),
            "meta_keys": [str(x) for x in row.get("meta_keys", []) if x is not None],
        }
    except Exception as e:
        return {
            "country_code": cc,
            "path": file_path,
            "error": f"JSON parse error: {e}",
            "total_rows": 0,
            "null_or_empty_geom_count": 0,
            "invalid_geom_count": 0,
            "invalid_de_format_count": 0,
            "dupe_feature_count": 0,
            "meta_keys": [],
        }


def inspect_companion_parquet_file(file_path, prefix, has_osm_type=True):
    """Query a companion layer parquet file (places, facilities, landuse) using duckdb CLI."""
    cc = os.path.basename(file_path).replace(f"{prefix}-", "").replace(".parquet", "")
    dupe_cols = "osm_type, osm_id" if has_osm_type else "osm_id"

    sql = f"""
    INSTALL spatial; LOAD spatial;
    SELECT
        count(*) AS total_rows,
        count(*) FILTER (WHERE geom IS NULL OR ST_IsEmpty(geom)) AS null_or_empty_geom_count,
        (SELECT count(*) FROM (
            SELECT {dupe_cols}, count(*)
            FROM read_parquet('{file_path}')
            GROUP BY {dupe_cols} HAVING count(*) > 1
        )) AS dupe_feature_count,
        (SELECT list(k) FROM (SELECT key::VARCHAR AS k FROM parquet_kv_metadata('{file_path}'))) AS meta_keys
    FROM read_parquet('{file_path}');
    """

    cmd = ["duckdb", "-json", "-c", sql]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        return {
            "country_code": cc,
            "path": file_path,
            "error": res.stderr.strip(),
            "total_rows": 0,
            "null_or_empty_geom_count": 0,
            "dupe_feature_count": 0,
            "meta_keys": [],
        }

    try:
        data = json.loads(res.stdout)
        if not data:
            raise ValueError("Empty output")
        row = data[0]
        return {
            "country_code": cc,
            "path": file_path,
            "error": None,
            "total_rows": int(row.get("total_rows", 0)),
            "null_or_empty_geom_count": int(row.get("null_or_empty_geom_count", 0)),
            "dupe_feature_count": int(row.get("dupe_feature_count", 0)),
            "meta_keys": [str(x) for x in row.get("meta_keys", []) if x is not None],
        }
    except Exception as e:
        return {
            "country_code": cc,
            "path": file_path,
            "error": f"JSON parse error: {e}",
            "total_rows": 0,
            "null_or_empty_geom_count": 0,
            "dupe_feature_count": 0,
            "meta_keys": [],
        }


def validate_parquets(base_dir, fail_on_error=False):
    files = find_parquet_files(base_dir)
    print(f"=== GeoParquet Validation Suite ===")
    print(f"Scanning directory: {base_dir}")
    print(f"Found {len(files)} country parquet file(s).\n")
    
    if not files:
        print("ERROR: No parquet files found.")
        return 1 if fail_on_error else 0

    # 1. Duplicate files check
    duplicate_files = check_duplicate_files(files)
    if duplicate_files:
        for basename, path1, path2 in duplicate_files:
            print(f"##vso[task.logissue type=error]Duplicate parquet file '{basename}' emitted in multiple paths: {path1} and {path2}")
        print(f"ERROR: Found {len(duplicate_files)} duplicate parquet file(s) across continents.")
        if fail_on_error:
            return 1
    else:
        print("[OK] No duplicate country parquet files found across continents.")

    # Load ground truth country metadata
    countries_meta = {}
    countries_json_path = os.path.join(os.path.dirname(__file__), "countries.json")
    if os.path.exists(countries_json_path):
        try:
            with open(countries_json_path, "r", encoding="utf-8") as f:
                countries_meta = json.load(f)
        except Exception as e:
            print(f"Warning: Could not load countries.json: {e}")

    # 2. Inspect all files
    missing_l2 = []
    missing_expected_levels = []
    duplicate_features = []
    empty_files = []
    null_geoms = []
    corrupt_names = []
    missing_metadata = []
    errors = []
    
    for path in files:
        metrics = inspect_parquet_file(path)
        cc = metrics["country_code"]
        
        if metrics["error"]:
            print(f"##vso[task.logissue type=error]Failed to inspect {cc} ({path}): {metrics['error']}")
            errors.append((cc, path, metrics["error"]))
            continue
            
        if metrics["total_rows"] == 0:
            print(f"##vso[task.logissue type=warning]Country {cc} ({path}) is EMPTY (0 rows)!")
            empty_files.append((cc, path))
            
        # Check against ground truth expected levels from countries.json
        expected_lvls = countries_meta.get(cc, {}).get("levels", [])
        if metrics["l2_count"] == 0:
            missing_l2.append((cc, path))
            # Only warn if 2 is officially declared in countries.json (autonomous territories like HK, NC, MO start at L3/L4)
            if not expected_lvls or 2 in expected_lvls:
                print(f"##vso[task.logissue type=warning]Country {cc} ({path}) has NO admin_level=2 polygon!")
        if expected_lvls:
            populated = set(metrics.get("populated_levels", []))
            # Flag if top-level sovereign boundary (2) or major sub-entities are missing
            missing_for_cc = [lvl for lvl in expected_lvls if lvl in (2, 4, 5, 6) and lvl not in populated]
            if missing_for_cc:
                missing_str = ", ".join(f"L{lvl}" for lvl in missing_for_cc)
                print(f"##vso[task.logissue type=warning]Country {cc} ({path}) is missing expected administrative level(s): {missing_str}")
                missing_expected_levels.append((cc, path, missing_for_cc))
            
        if metrics["dupe_feature_count"] > 0:
            print(f"##vso[task.logissue type=warning]Country {cc} ({path}) contains {metrics['dupe_feature_count']} duplicate OSM feature(s)!")
            duplicate_features.append((cc, path, metrics["dupe_feature_count"]))
            
        if metrics["null_geom_count"] > 0:
            print(f"##vso[task.logissue type=warning]Country {cc} ({path}) contains {metrics['null_geom_count']} record(s) with NULL geometry!")
            null_geoms.append((cc, path, metrics["null_geom_count"]))

        # 5. Admin-centre name encoding corruption (undecoded OPL escapes)
        ac_name_bad = metrics.get("ac_name_corrupt_count", 0)
        ac_names_bad = metrics.get("ac_names_corrupt_count", 0)
        if ac_name_bad or ac_names_bad:
            print(f"##vso[task.logissue type=warning]Country {cc} ({path}) has {ac_name_bad} admin_centre_name / {ac_names_bad} admin_centre_names_json value(s) with suspected OPL encoding corruption (raw '%' or control characters)!")
            corrupt_names.append((cc, path, ac_name_bad, ac_names_bad))

        # Check required Parquet metadata keys
        required_meta = {"source", "license", "attribution", "country_code", "exported_at", "osm_data_timestamp"}
        present_meta = set(metrics.get("meta_keys", []))
        missing_meta = required_meta - present_meta
        if missing_meta:
            missing_str = ", ".join(sorted(missing_meta))
            print(f"##vso[task.logissue type=warning]Country {cc} ({path}) is missing required metadata key(s): {missing_str}")
            missing_metadata.append((cc, path, missing_str))

    # 3. Validate osm-postal-codes-*.parquet files if present
    postal_code_files = find_postal_code_parquet_files(base_dir)
    postal_code_issues = []
    if postal_code_files:
        print(f"\nFound {len(postal_code_files)} postal codes parquet file(s).")
        duplicate_pc_files = check_duplicate_files(postal_code_files)
        if duplicate_pc_files:
            for basename, path1, path2 in duplicate_pc_files:
                print(f"##vso[task.logissue type=error]Duplicate postal codes parquet file '{basename}' emitted in multiple paths: {path1} and {path2}")
                postal_code_issues.append((basename, f"duplicate file in {path1} and {path2}"))
            if fail_on_error:
                return 1

        required_meta = {"source", "license", "attribution", "country_code", "exported_at", "osm_data_timestamp"}
        for pc_path in postal_code_files:
            pc_metrics = inspect_postal_code_parquet_file(pc_path)
            cc = pc_metrics["country_code"]

            if pc_metrics["error"]:
                print(f"##vso[task.logissue type=error]Failed to inspect postal codes {cc} ({pc_path}): {pc_metrics['error']}")
                errors.append((cc, pc_path, pc_metrics["error"]))
                postal_code_issues.append((cc, pc_path, "error"))
                continue

            if pc_metrics["total_rows"] == 0:
                print(f"##vso[task.logissue type=warning]Postal codes {cc} ({pc_path}) is EMPTY (0 rows)!")
                postal_code_issues.append((cc, pc_path, "empty"))

            if cc == "DE" and pc_metrics["total_rows"] < 8000:
                print(f"##vso[task.logissue type=warning]Postal codes DE has fewer than 8000 polygons ({pc_metrics['total_rows']} rows in {pc_path})!")
                postal_code_issues.append((cc, pc_path, f"DE rows {pc_metrics['total_rows']} < 8000"))

            if pc_metrics["invalid_de_format_count"] > 0:
                print(f"##vso[task.logissue type=warning]Postal codes {cc} ({pc_path}) has {pc_metrics['invalid_de_format_count']} record(s) violating DE 5-digit postal_code format!")
                postal_code_issues.append((cc, pc_path, f"invalid DE format: {pc_metrics['invalid_de_format_count']}"))

            if pc_metrics["null_or_empty_geom_count"] > 0 or pc_metrics["invalid_geom_count"] > 0:
                print(f"##vso[task.logissue type=warning]Postal codes {cc} ({pc_path}) has {pc_metrics['null_or_empty_geom_count']} NULL/empty and {pc_metrics['invalid_geom_count']} invalid geometry record(s)!")
                postal_code_issues.append((cc, pc_path, "geometry validity"))

            if pc_metrics["dupe_feature_count"] > 0:
                print(f"##vso[task.logissue type=warning]Postal codes {cc} ({pc_path}) contains {pc_metrics['dupe_feature_count']} duplicate OSM feature(s)!")
                postal_code_issues.append((cc, pc_path, f"duplicates: {pc_metrics['dupe_feature_count']}"))

            present_meta = set(pc_metrics.get("meta_keys", []))
            missing_meta = required_meta - present_meta
            if missing_meta:
                missing_str = ", ".join(sorted(missing_meta))
                print(f"##vso[task.logissue type=warning]Postal codes {cc} ({pc_path}) is missing required metadata key(s): {missing_str}")
                postal_code_issues.append((cc, pc_path, f"missing metadata: {missing_str}"))

    # 4. Validate companion layers (osm-places, osm-facilities, osm-landuse) if present
    companion_stats = []
    companion_issues = []
    required_meta = {"source", "license", "attribution", "country_code", "exported_at", "osm_data_timestamp"}
    for prefix, pattern, has_osm_type in COMPANION_LAYERS:
        layer_files = find_layer_parquet_files(base_dir, pattern)
        if not layer_files:
            continue
        print(f"\nFound {len(layer_files)} {prefix} parquet file(s).")
        duplicate_layer_files = check_duplicate_files(layer_files)
        if duplicate_layer_files:
            for basename, path1, path2 in duplicate_layer_files:
                print(f"##vso[task.logissue type=error]Duplicate {prefix} parquet file '{basename}' emitted in multiple paths: {path1} and {path2}")
                duplicate_files.append((basename, path1, path2))
                companion_issues.append((prefix, basename, "duplicate file"))
            if fail_on_error:
                return 1

        layer_issue_count = 0
        for l_path in layer_files:
            l_metrics = inspect_companion_parquet_file(l_path, prefix, has_osm_type=has_osm_type)
            cc = l_metrics["country_code"]

            if l_metrics["error"]:
                print(f"##vso[task.logissue type=error]Failed to inspect {prefix} {cc} ({l_path}): {l_metrics['error']}")
                errors.append((cc, l_path, l_metrics["error"]))
                layer_issue_count += 1
                continue

            if l_metrics["total_rows"] == 0:
                print(f"##vso[task.logissue type=warning]{prefix} {cc} ({l_path}) is EMPTY (0 rows)!")
                layer_issue_count += 1

            if l_metrics["null_or_empty_geom_count"] > 0:
                print(f"##vso[task.logissue type=warning]{prefix} {cc} ({l_path}) has {l_metrics['null_or_empty_geom_count']} NULL/empty geometry record(s)!")
                layer_issue_count += 1

            if l_metrics["dupe_feature_count"] > 0:
                print(f"##vso[task.logissue type=warning]{prefix} {cc} ({l_path}) contains {l_metrics['dupe_feature_count']} duplicate OSM feature(s)!")
                layer_issue_count += 1

            present_meta = set(l_metrics.get("meta_keys", []))
            missing_meta = required_meta - present_meta
            if missing_meta:
                missing_str = ", ".join(sorted(missing_meta))
                print(f"##vso[task.logissue type=warning]{prefix} {cc} ({l_path}) is missing required metadata key(s): {missing_str}")
                layer_issue_count += 1

        if layer_issue_count > 0:
            companion_issues.append((prefix, layer_issue_count))
        companion_stats.append((prefix, len(layer_files), layer_issue_count))

    # Summary table
    print("\n" + "=" * 60)
    print(" === GEOPARQUET QUALITY & INTEGRITY SUMMARY ===")
    print("=" * 60)
    print(f" Total Country Parquets Checked: {len(files)}")
    print(f" Complete with Level 2:          {len(files) - len(missing_l2)} / {len(files)}")
    print(f" Missing Level 2:                {len(missing_l2)}")
    print(f" Missing Expected Sub-Levels:    {len(missing_expected_levels)}")
    print(f" Duplicate Features Detected:    {len(duplicate_features)}")
    print(f" Empty Files (0 rows):           {len(empty_files)}")
    print(f" NULL Geometries Detected:       {len(null_geoms)}")
    print(f" Corrupted Admin-Centre Names:   {len(corrupt_names)}")
    print(f" Missing Metadata Headers:       {len(missing_metadata)}")
    if postal_code_files:
        print(f" Postal Code Parquets Checked:   {len(postal_code_files)}")
        print(f" Postal Code Issues Detected:    {len(postal_code_issues)}")
    for prefix, count, issues in companion_stats:
        print(f" {prefix} Parquets Checked:  {count} (Issues: {issues})")
    print(f" Extraction Errors:              {len(errors)}")
    print("=" * 60)


    has_issues = (
        len(duplicate_files) > 0
        or len(missing_l2) > 0
        or len(duplicate_features) > 0
        or len(empty_files) > 0
        or len(null_geoms) > 0
        or len(corrupt_names) > 0
        or len(missing_metadata) > 0
        or len(postal_code_issues) > 0
        or len(companion_issues) > 0
        or len(errors) > 0
    )

    if not has_issues:
        print("RESULT: ALL checks passed with 100% data integrity!")
        return 0
    else:
        print("RESULT: Completed with issues reported above.")
        return 1 if (fail_on_error and (len(duplicate_files) > 0 or len(errors) > 0)) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate GeoParquet datasets for quality and completeness.")
    parser.add_argument("directory", help="Directory containing parquet-check artifacts or parquet files")
    parser.add_argument("--fail-on-error", action="store_true", default=False,
                        help="Exit with non-zero code on critical error")
    args = parser.parse_args()
    
    sys.exit(validate_parquets(args.directory, fail_on_error=args.fail_on_error))
