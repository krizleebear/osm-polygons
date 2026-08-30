#!/usr/bin/env python3
"""
Aggregates per-country GeoParquet files into an administrative level coverage summary.

Reads all `admin-polygons-*.parquet` files from a directory and produces:
  - admin-levels-summary.md   (human-readable Markdown matrix table)
  - admin-levels-summary.json (machine-readable JSON summary)

Columns: Country Code, Country Name (from L2 or prominent feature), L2..L11 counts, Total Features.
"""

import os
import sys
import glob
import json
import argparse
from collections import defaultdict

LEVEL_COLUMNS = [str(lvl) for lvl in range(2, 12)]


def analyze_parquet_files(parquet_dir):
    """
    Query all parquet files using DuckDB to extract admin_level counts.
    Returns: list of dicts with statistics per country.
    """
    parquet_files = sorted(glob.glob(os.path.join(parquet_dir, "**", "admin-polygons-*.parquet"), recursive=True))
    if not parquet_files:
        print(f"Warning: No admin-polygons-*.parquet files found in {parquet_dir}", file=sys.stderr)
        return []

    try:
        import duckdb
    except ImportError:
        print("Error: duckdb Python package is required. Run 'pip install duckdb'.", file=sys.stderr)
        sys.exit(1)

    con = duckdb.connect(database=":memory:")
    con.execute("INSTALL spatial; LOAD spatial;")

    country_stats = []

    for pq_path in parquet_files:
        base = os.path.basename(pq_path)
        cc = base.replace("admin-polygons-", "").replace(".parquet", "").upper()

        try:
            query = f"""
                SELECT
                    admin_level,
                    count(*) AS feature_count
                FROM read_parquet('{pq_path}')
                GROUP BY admin_level
            """
            rows = con.execute(query).fetchall()
            level_counts = {str(row[0]): row[1] for row in rows if row[0] is not None}

            name_query = f"""
                SELECT name FROM read_parquet('{pq_path}')
                WHERE admin_level = 2 AND name IS NOT NULL AND name != ''
                LIMIT 1
            """
            name_row = con.execute(name_query).fetchone()
            if name_row and name_row[0]:
                country_name = name_row[0]
            else:
                fallback_name = con.execute(f"SELECT name FROM read_parquet('{pq_path}') WHERE name IS NOT NULL LIMIT 1").fetchone()
                country_name = fallback_name[0] if fallback_name else cc

            total_features = sum(level_counts.values())

            country_stats.append({
                "country_code": cc,
                "country_name": country_name,
                "levels": level_counts,
                "total_features": total_features,
            })
        except Exception as e:
            print(f"Error reading {pq_path}: {e}", file=sys.stderr)

    return sorted(country_stats, key=lambda x: x["country_code"])


def generate_markdown(stats):
    """Generate GitHub-flavored Markdown report."""
    lines = []
    lines.append("# Administrative Levels Coverage Summary\n")
    lines.append("This document summarizes the administrative level hierarchy and feature counts per country.\n")

    total_countries = len(stats)
    total_all_features = sum(s["total_features"] for s in stats)
    global_level_counts = defaultdict(int)
    for s in stats:
        for lvl, cnt in s["levels"].items():
            global_level_counts[lvl] += cnt

    lines.append(f"**Total Countries / Territories:** {total_countries:,}  ")
    lines.append(f"**Total Administrative Polygons:** {total_all_features:,}\n")

    lines.append("### Global Feature Distribution by Admin Level\n")
    lines.append("| Admin Level | Description | Feature Count | % of Total |")
    lines.append("| :--- | :--- | :---: | :---: |")

    descriptions = {
        "2": "National Boundaries / Sovereign States",
        "3": "Statistical / Macro Regions",
        "4": "States / Provinces / Regions",
        "5": "Districts / Macro Departments",
        "6": "Counties / Landkreise / Départements",
        "7": "Arrondissements / Concelhos / Districts",
        "8": "Municipalities / Cities / Gemeinden",
        "9": "Sub-municipalities / Stadtbezirke / Wards",
        "10": "Sub-localities / Quarters / Neighborhoods",
        "11": "Sub-divisions / Micro Localities",
    }

    for lvl in LEVEL_COLUMNS:
        cnt = global_level_counts.get(lvl, 0)
        pct = (cnt / total_all_features * 100) if total_all_features > 0 else 0
        desc = descriptions.get(lvl, f"Level {lvl}")
        lines.append(f"| **Level {lvl}** | {desc} | {cnt:,} | {pct:.1f}% |")
    lines.append("")

    lines.append("### Per-Country Administrative Level Breakdown\n")
    header = "| CC | Country / Territory | " + " | ".join(f"L{lvl}" for lvl in LEVEL_COLUMNS) + " | Total |"
    sep = "| :--- | :--- | " + " | ".join([":---:"] * len(LEVEL_COLUMNS)) + " | :---: |"
    lines.append(header)
    lines.append(sep)

    for s in stats:
        cc = s["country_code"]
        name = s["country_name"]
        cols = []
        for lvl in LEVEL_COLUMNS:
            cnt = s["levels"].get(lvl, 0)
            cols.append(f"{cnt:,}" if cnt > 0 else "-")
        total = f"{s['total_features']:,}"
        row = f"| **{cc}** | {name} | " + " | ".join(cols) + f" | **{total}** |"
        lines.append(row)

    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate admin levels coverage summary.")
    parser.add_argument("parquet_dir", help="Directory containing admin-polygons-*.parquet files")
    parser.add_argument("--out-md", default="admin-levels-summary.md", help="Output Markdown path")
    parser.add_argument("--out-json", default="admin-levels-summary.json", help="Output JSON path")

    args = parser.parse_args()

    stats = analyze_parquet_files(args.parquet_dir)
    md_content = generate_markdown(stats)

    with open(args.out_md, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"Generated Markdown summary: {args.out_md}")

    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump({
            "total_countries": len(stats),
            "total_features": sum(s["total_features"] for s in stats),
            "countries": stats
        }, f, indent=2, ensure_ascii=False)
    print(f"Generated JSON summary: {args.out_json}")


if __name__ == "__main__":
    main()
