#!/usr/bin/env python3
"""
Aggregates per-country GeoParquet files into an administrative level coverage summary.

Reads all `admin-polygons-*.parquet` files from a directory and produces:
  - admin-levels-summary.md   (human-readable Markdown matrix table)
  - admin-levels-summary.json (machine-readable JSON summary)

Columns: Country Code, Country Name (from countries.json or parquet), L2..L11 counts, Total Features.
"""

import os
import sys
import glob
import json
import argparse
import subprocess
from collections import defaultdict

LEVEL_COLUMNS = [str(lvl) for lvl in range(2, 12)]


def load_country_names():
    """Load country code -> name mapping from scripts/countries.json if present."""
    cpath = os.path.join(os.path.dirname(__file__), "countries.json")
    if os.path.exists(cpath):
        try:
            with open(cpath, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {cc.upper(): info.get("name", cc) for cc, info in data.items()}
        except Exception:
            pass
    return {}


def analyze_parquet_files(parquet_dir):
    """
    Query all parquet files using DuckDB CLI to extract admin_level counts.
    Returns: list of dicts with statistics per country.
    """
    parquet_files = sorted(glob.glob(os.path.join(parquet_dir, "**", "admin-polygons-*.parquet"), recursive=True))
    if not parquet_files:
        print(f"Warning: No admin-polygons-*.parquet files found in {parquet_dir}", file=sys.stderr)
        return []

    country_names = load_country_names()
    country_stats = []

    for pq_path in parquet_files:
        base = os.path.basename(pq_path)
        cc = base.replace("admin-polygons-", "").replace(".parquet", "").upper()
        country_name = country_names.get(cc, cc)

        # Single lightweight aggregate query per file
        query = f"SELECT admin_level, count(*) AS cnt FROM read_parquet('{pq_path}') GROUP BY admin_level"
        cmd = ["duckdb", "-json", "-c", query]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                print(f"Warning: DuckDB failed on {pq_path}: {res.stderr.strip()}", file=sys.stderr)
                rows = []
            else:
                rows = json.loads(res.stdout) if res.stdout.strip() else []

            level_counts = {}
            for r in rows:
                lvl = str(r.get("admin_level", ""))
                cnt = int(r.get("cnt", 0))
                if lvl and lvl != "None" and lvl != "null":
                    level_counts[lvl] = cnt

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

    lines.append("### Global Level Distribution\n")
    lines.append("| Admin Level | Feature Count | Percentage |")
    lines.append("| :--- | :---: | :---: |")
    for lvl in LEVEL_COLUMNS:
        cnt = global_level_counts.get(lvl, 0)
        pct = (cnt / total_all_features * 100) if total_all_features > 0 else 0.0
        lines.append(f"| Level {lvl} | {cnt:,} | {pct:.1f}% |")

    lines.append("\n---\n")

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
