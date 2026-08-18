#!/usr/bin/env python3
"""
Generates dataset statistics, country-by-country admin_level matrix breakdowns,
and release notes for osm-polygons releases and CI packaging stages.
"""

import os
import sys
import glob
import json
import csv
import tarfile
from collections import defaultdict

ADMIN_LEVEL_DESCRIPTIONS = {
    '2': 'National Boundaries / Countries',
    '3': 'Statistical / Autonomous Regions',
    '4': 'States / Provinces / Regions',
    '5': 'Districts / Departments (Macro)',
    '6': 'Counties / Landkreise / Départements',
    '7': 'Arrondissements / Concelhos / Districts',
    '8': 'Municipalities / Cities / Gemeinden',
    '9': 'Sub-municipalities / Stadtbezirke / Wards',
    '10': 'Sub-localities / Stadtteile / Quarters',
    '11': 'Neighborhoods / Ortsteile / Siedlungen',
}

LEVEL_COLUMNS = ['2', '3', '4', '5', '6', '7', '8', '9', '10', '11']

def parse_country_info(filename):
    """Extracts (iso, region_name) from filename like 'DE_germany.admin-polygons.simplified.geojsonseq'."""
    base = os.path.basename(filename)
    clean = base.replace('.admin-polygons.simplified.geojsonseq', '').replace('.admin-polygons.geojsonseq', '').replace('.geojsonseq', '')
    if '_' in clean:
        parts = clean.split('_', 1)
        return parts[0].upper(), parts[1].replace('-', ' ').title()
    return 'XX', clean.replace('-', ' ').title()

def analyze_geojson_lines(line_iterable, filename):
    """Analyzes lines of a single GeoJSON sequence file."""
    iso, region_name = parse_country_info(filename)
    stats = {
        'iso': iso,
        'region': region_name,
        'filename': os.path.basename(filename),
        'total': 0,
        'wikidata': 0,
        'levels': defaultdict(int)
    }

    for line in line_iterable:
        if isinstance(line, bytes):
            line_str = line.decode('utf-8', errors='ignore').strip()
        else:
            line_str = line.strip()
        if not line_str:
            continue
        stats['total'] += 1
        if '"wikidata"' in line_str:
            stats['wikidata'] += 1

        try:
            data = json.loads(line_str)
            raw_lvl = str(data.get('properties', {}).get('admin_level', '')).strip()
            if raw_lvl.isdigit() and 2 <= int(raw_lvl) <= 11:
                lvl = raw_lvl
            else:
                lvl = 'other'
            stats['levels'][lvl] += 1
        except Exception:
            stats['levels']['other'] += 1

    return stats

def analyze_assets_directory(target_dir):
    """
    Finds and analyzes all polygon data in target_dir, checking both individual
    .geojsonseq files and continental tar.gz archives.
    """
    country_stats = []
    continental_stats = defaultdict(lambda: {
        'regions': 0,
        'total': 0,
        'wikidata': 0,
        'levels': defaultdict(int)
    })

    # 1. First check if loose .geojsonseq files exist in directory
    geojson_files = sorted(glob.glob(os.path.join(target_dir, '*.geojsonseq')) +
                           glob.glob(os.path.join(target_dir, '**', '*.geojsonseq'), recursive=True))

    if geojson_files:
        for path in geojson_files:
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    c_stat = analyze_geojson_lines(f, path)
                    if c_stat['total'] > 0:
                        country_stats.append(c_stat)
            except Exception as e:
                print(f"Warning: Failed to read {path}: {e}", file=sys.stderr)

    # 2. If no loose files found, analyze continental tar.gz archives
    if not country_stats:
        archive_paths = sorted(glob.glob(os.path.join(target_dir, 'simplified-*.tar.gz')) +
                               glob.glob(os.path.join(target_dir, '**', 'simplified-*.tar.gz'), recursive=True))
        archive_paths = [p for p in archive_paths if os.path.basename(p) != 'simplified-all.tar.gz']

        for tar_path in archive_paths:
            fname = os.path.basename(tar_path)
            continent_name = fname.replace('simplified-', '').replace('.tar.gz', '').replace('-', ' ').title()
            try:
                with tarfile.open(tar_path, 'r:gz') as tar:
                    members = [m for m in tar.getmembers() if m.name.endswith('.geojsonseq')]
                    for m in members:
                        f = tar.extractfile(m)
                        if not f:
                            continue
                        c_stat = analyze_geojson_lines(f, m.name)
                        if c_stat['total'] > 0:
                            country_stats.append(c_stat)
                            cont = continental_stats[continent_name]
                            cont['regions'] += 1
                            cont['total'] += c_stat['total']
                            cont['wikidata'] += c_stat['wikidata']
                            for lvl, cnt in c_stat['levels'].items():
                                cont['levels'][lvl] += cnt
            except Exception as e:
                print(f"Warning: Failed to process archive {tar_path}: {e}", file=sys.stderr)

    return country_stats, continental_stats

def generate_markdown_report(country_stats, continental_stats):
    """Builds a comprehensive Markdown statistics document with global, continental, and country tables."""
    # Deduplicate country_stats by filename if any
    unique_countries = {}
    for c in country_stats:
        unique_countries[c['filename']] = c
    sorted_countries = sorted(unique_countries.values(), key=lambda x: (x['iso'], x['region']))

    global_total = sum(c['total'] for c in sorted_countries)
    global_wikidata = sum(c['wikidata'] for c in sorted_countries)
    global_regions = len(sorted_countries)
    global_levels = defaultdict(int)
    for c in sorted_countries:
        for lvl, cnt in c['levels'].items():
            global_levels[lvl] += cnt

    global_wiki_pct = (global_wikidata / global_total * 100) if global_total > 0 else 0.0

    md = []
    md.append("\n## Dataset & Administrative Level Statistics\n")
    md.append(f"- **Total Administrative Polygons:** `{global_total:,}`")
    md.append(f"- **Countries & Regions Covered:** `{global_regions}`")
    md.append(f"- **Wikidata Linking Rate:** `{global_wikidata:,} / {global_total:,} ({global_wiki_pct:.1f}%)`\n")

    # Global summary
    md.append("### Global Summary by Administrative Level\n")
    md.append("| Admin Level | Description | Polygon Count | % of Total |")
    md.append("| :--- | :--- | ---: | ---: |")
    for lvl in LEVEL_COLUMNS:
        cnt = global_levels.get(lvl, 0)
        pct = (cnt / global_total * 100) if global_total > 0 else 0.0
        desc = ADMIN_LEVEL_DESCRIPTIONS.get(lvl, f"Level {lvl} Boundary")
        md.append(f"| `admin_level={lvl}` | {desc} | `{cnt:,}` | `{pct:.1f}%` |")
    
    other_cnt = global_levels.get('other', 0)
    if other_cnt > 0:
        pct = (other_cnt / global_total * 100) if global_total > 0 else 0.0
        md.append(f"| `other` | Minor / Non-standard Localities | `{other_cnt:,}` | `{pct:.1f}%` |")
    md.append(f"| **Total** | **All Administrative Polygons** | **`{global_total:,}`** | **`100.0%`** |\n")

    # Per-Country Matrix Table
    md.append("### Country Administrative Level Breakdown\n")
    md.append("| ISO | Country / Region | Total | L2 | L3 | L4 | L5 | L6 | L7 | L8 | L9 | L10 | L11 | Wikidata % |")
    md.append("| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")

    for c in sorted_countries:
        w_pct = (c['wikidata'] / c['total'] * 100) if c['total'] > 0 else 0.0
        row = [
            f"`{c['iso']}`",
            f"**{c['region']}**",
            f"`{c['total']:,}`"
        ]
        for lvl in LEVEL_COLUMNS:
            cnt = c['levels'].get(lvl, 0)
            row.append(f"`{cnt:,}`" if cnt > 0 else "-")
        row.append(f"`{w_pct:.1f}%`")
        md.append("| " + " | ".join(row) + " |")

    # Total row
    total_row = ["**--**", f"**TOTAL ({global_regions} Regions)**", f"**`{global_total:,}`**"]
    for lvl in LEVEL_COLUMNS:
        total_row.append(f"**`{global_levels.get(lvl, 0):,}`**")
    total_row.append(f"**`{global_wiki_pct:.1f}%`**")
    md.append("| " + " | ".join(total_row) + " |\n")

    return "\n".join(md), sorted_countries, global_levels, global_total

def export_csv_and_json(sorted_countries, global_levels, global_total, output_dir):
    """Exports machine-readable CSV and JSON files with admin_level statistics."""
    csv_path = os.path.join(output_dir, 'admin-levels-summary.csv')
    json_path = os.path.join(output_dir, 'admin-levels-summary.json')

    # Export CSV
    try:
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            header = ['iso', 'region', 'total_polygons', 'wikidata_count', 'wikidata_pct'] + [f'level_{l}' for l in LEVEL_COLUMNS] + ['other']
            writer.writerow(header)

            for c in sorted_countries:
                w_pct = round((c['wikidata'] / c['total'] * 100), 2) if c['total'] > 0 else 0.0
                row = [c['iso'], c['region'], c['total'], c['wikidata'], f"{w_pct:.2f}"]
                for lvl in LEVEL_COLUMNS:
                    row.append(c['levels'].get(lvl, 0))
                row.append(c['levels'].get('other', 0))
                writer.writerow(row)
        print(f"Generated CSV statistics: '{csv_path}'")
    except Exception as e:
        print(f"Warning: Failed to write CSV statistics: {e}", file=sys.stderr)

    # Export JSON
    try:
        data = {
            'total_polygons': global_total,
            'total_regions': len(sorted_countries),
            'global_levels': dict(global_levels),
            'regions': []
        }
        for c in sorted_countries:
            data['regions'].append({
                'iso': c['iso'],
                'region': c['region'],
                'filename': c['filename'],
                'total': c['total'],
                'wikidata': c['wikidata'],
                'wikidata_pct': round((c['wikidata'] / c['total'] * 100), 2) if c['total'] > 0 else 0.0,
                'levels': dict(c['levels'])
            })
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Generated JSON statistics: '{json_path}'")
    except Exception as e:
        print(f"Warning: Failed to write JSON statistics: {e}", file=sys.stderr)

def main():
    assets_dir = sys.argv[1] if len(sys.argv) > 1 else 'release-assets'
    notes_path = sys.argv[2] if len(sys.argv) > 2 else 'release-notes.md'

    print(f"Analyzing administrative polygons in '{assets_dir}'...")

    country_stats, continental_stats = analyze_assets_directory(assets_dir)
    if not country_stats:
        print("ERROR: No polygon data found to summarize in", assets_dir, file=sys.stderr)
        sys.exit(1)

    stats_markdown, sorted_countries, global_levels, global_total = generate_markdown_report(country_stats, continental_stats)

    # Export CSV & JSON artifacts
    export_csv_and_json(sorted_countries, global_levels, global_total, assets_dir)

    # Standalone Markdown summary
    summary_md_path = os.path.join(assets_dir, 'admin-levels-summary.md')
    try:
        with open(summary_md_path, 'w', encoding='utf-8') as f:
            f.write(stats_markdown)
        print(f"Generated Markdown summary: '{summary_md_path}'")
    except Exception as e:
        print(f"Warning: Failed to write standalone Markdown summary: {e}", file=sys.stderr)

    # Append to release-notes.md if requested
    if os.path.exists(notes_path):
        with open(notes_path, 'a', encoding='utf-8') as f:
            f.write(stats_markdown)
        print(f"Successfully appended polygon statistics to '{notes_path}'.")
    else:
        print(stats_markdown)

if __name__ == '__main__':
    main()
