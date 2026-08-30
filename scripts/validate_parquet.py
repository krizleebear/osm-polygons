#!/usr/bin/env python3
"""
Comprehensive validation script for per-country GeoParquet datasets.

Performs quality, completeness, and integrity checks across generated
admin-polygons-*.parquet files:
  1. Duplicate Country Files Check (across continents)
  2. Level 2 National Boundary Completeness Check
  3. Feature Deduplication Check (no duplicate osm_type + osm_id in a file)
  4. Empty Datasets & NULL Geometry Integrity Check

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



def find_parquet_files(base_dir):
    """Find all admin-polygons-*.parquet files recursively."""
    pattern = os.path.join(base_dir, "**", "admin-polygons-*.parquet")
    return sorted(glob.glob(pattern, recursive=True))


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
        (SELECT count(*) FROM (
            SELECT osm_type, osm_id, count(*) 
            FROM read_parquet('{file_path}') 
            GROUP BY osm_type, osm_id HAVING count(*) > 1
        )) AS dupe_feature_count,
        (SELECT list_sort(list(DISTINCT admin_level)) FROM (SELECT admin_level FROM read_parquet('{file_path}') WHERE admin_level IS NOT NULL)) AS populated_levels
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
            "populated_levels": [],
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
            "populated_levels": [int(x) for x in row.get("populated_levels", []) if x is not None],
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
            "populated_levels": [],
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
    print(f" Extraction Errors:              {len(errors)}")
    print("=" * 60)


    has_issues = (
        len(duplicate_files) > 0
        or len(missing_l2) > 0
        or len(duplicate_features) > 0
        or len(empty_files) > 0
        or len(null_geoms) > 0
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
