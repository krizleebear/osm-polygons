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
        )) AS dupe_feature_count
    FROM read_parquet('{file_path}');
    """
    
    cmd = ["duckdb", "-csv", "-c", sql]
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
        }
    
    lines = res.stdout.strip().split("\n")
    if len(lines) < 2:
        return {
            "country_code": cc,
            "path": file_path,
            "error": "Empty duckdb output",
            "total_rows": 0,
            "l2_count": 0,
            "null_geom_count": 0,
            "dupe_feature_count": 0,
        }
    
    parts = lines[1].split(",")
    return {
        "country_code": cc,
        "path": file_path,
        "error": None,
        "total_rows": int(parts[0]) if len(parts) > 0 and parts[0] else 0,
        "l2_count": int(parts[1]) if len(parts) > 1 and parts[1] else 0,
        "null_geom_count": int(parts[2]) if len(parts) > 2 and parts[2] else 0,
        "dupe_feature_count": int(parts[3]) if len(parts) > 3 and parts[3] else 0,
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

    # 2. Inspect all files
    missing_l2 = []
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
            
        if metrics["l2_count"] == 0:
            print(f"##vso[task.logissue type=warning]Country {cc} ({path}) has NO admin_level=2 polygon!")
            missing_l2.append((cc, path))
            
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
