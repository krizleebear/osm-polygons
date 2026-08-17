#!/usr/bin/env python3
"""
Generates Markdown dataset statistics and continental breakdowns for GitHub Release notes in osm-polygons.
"""

import os
import sys
import glob
import json
import tarfile
from collections import defaultdict

ADMIN_LEVEL_DESCRIPTIONS = {
    '2': 'National Boundaries / Countries',
    '3': 'Statistical / Autonomous Regions',
    '4': 'States / Provinces / Regions',
    '5': 'Districts / Departments',
    '6': 'Counties / Landkreise / Districts',
    '7': 'Arrondissements / Intercommunal',
    '8': 'Municipalities / Cities / Gemeinden',
    '9': 'Sub-municipalities / Wards',
    '10': 'Sub-localities / Quarters',
    '11': 'Neighborhoods / Localities',
}

def analyze_archive(tar_path):
    """Analyzes a continental simplified-*.tar.gz archive."""
    stats = {
        'region_count': 0,
        'total_polygons': 0,
        'wikidata_count': 0,
        'admin_levels': defaultdict(int)
    }
    
    try:
        with tarfile.open(tar_path, 'r:gz') as tar:
            members = [m for m in tar.getmembers() if m.name.endswith('.geojsonseq')]
            stats['region_count'] = len(members)
            
            for m in members:
                f = tar.extractfile(m)
                if not f:
                    continue
                for line in f:
                    line_str = line.decode('utf-8', errors='ignore').strip()
                    if not line_str:
                        continue
                    stats['total_polygons'] += 1
                    if '"wikidata"' in line_str:
                        stats['wikidata_count'] += 1
                    
                    try:
                        data = json.loads(line_str)
                        raw_lvl = str(data.get('properties', {}).get('admin_level', '')).strip()
                        if raw_lvl.isdigit() and 2 <= int(raw_lvl) <= 11:
                            lvl = raw_lvl
                        else:
                            lvl = 'other'
                        stats['admin_levels'][lvl] += 1
                    except Exception:
                        stats['admin_levels']['other'] += 1
    except Exception as e:
        print(f"Warning: Failed to process archive {tar_path}: {e}", file=sys.stderr)
        
    return stats

def main():
    assets_dir = sys.argv[1] if len(sys.argv) > 1 else 'release-assets'
    notes_path = sys.argv[2] if len(sys.argv) > 2 else 'release-notes.md'

    print(f"Analyzing polygon assets in '{assets_dir}'...")

    # Find continental archives (excluding simplified-all.tar.gz)
    archive_paths = glob.glob(os.path.join(assets_dir, 'simplified-*.tar.gz'))
    archive_paths = [p for p in archive_paths if not os.path.basename(p) == 'simplified-all.tar.gz']
    archive_paths.sort()

    global_total = 0
    global_wikidata = 0
    global_admin_levels = defaultdict(int)
    global_admin_wikidata = defaultdict(int)
    global_regions = 0

    continent_rows = []

    for path in archive_paths:
        fname = os.path.basename(path)
        # Extract continent name from simplified-<continent>.tar.gz
        continent_name = fname.replace('simplified-', '').replace('.tar.gz', '').replace('-', ' ').title()
        
        stats = analyze_archive(path)
        if stats['total_polygons'] == 0:
            continue

        global_regions += stats['region_count']
        global_total += stats['total_polygons']
        global_wikidata += stats['wikidata_count']
        
        for lvl, cnt in stats['admin_levels'].items():
            global_admin_levels[lvl] += cnt

        # Format admin levels summary string for continent (e.g., L2: 54, L4: 310, L6: 2100, L8: 45000)
        sorted_lvls = sorted([l for l in stats['admin_levels'].keys() if l.isdigit()], key=int)
        lvl_str_parts = []
        for l in sorted_lvls:
            if l in ('2', '4', '6', '8', '10'):
                lvl_str_parts.append(f"L{l}: {stats['admin_levels'][l]:,}")
        lvl_summary = ", ".join(lvl_str_parts) if lvl_str_parts else "N/A"

        wiki_pct = (stats['wikidata_count'] / stats['total_polygons'] * 100) if stats['total_polygons'] > 0 else 0.0
        
        continent_rows.append({
            'name': continent_name,
            'archive': fname,
            'regions': stats['region_count'],
            'polygons': stats['total_polygons'],
            'wikidata_pct': f"{wiki_pct:.1f}%",
            'levels': lvl_summary
        })

    if global_total == 0:
        print("ERROR: No polygon data found to summarize.", file=sys.stderr)
        sys.exit(1)

    global_wiki_pct = (global_wikidata / global_total * 100) if global_total > 0 else 0.0

    # Generate Markdown Content
    md = []
    md.append("\n## Dataset Statistics\n")
    md.append(f"- **Total Administrative Polygons:** `{global_total:,}`")
    md.append(f"- **Regions / Countries Covered:** `{global_regions}`")
    md.append(f"- **Wikidata Linking Rate:** `{global_wikidata:,} / {global_total:,} ({global_wiki_pct:.1f}%)`\n")

    md.append("### Global Administrative Level Breakdown\n")
    md.append("| Admin Level | Description | Polygon Count | % of Total |")
    md.append("| :--- | :--- | :--- | :--- |")

    for lvl in sorted([l for l in global_admin_levels.keys() if l.isdigit()], key=int):
        cnt = global_admin_levels[lvl]
        pct = (cnt / global_total * 100) if global_total > 0 else 0.0
        desc = ADMIN_LEVEL_DESCRIPTIONS.get(lvl, f"Level {lvl} Boundary")
        md.append(f"| `admin_level={lvl}` | {desc} | `{cnt:,}` | `{pct:.1f}%` |")

    other_cnt = sum(cnt for lvl, cnt in global_admin_levels.items() if not lvl.isdigit())
    if other_cnt > 0:
        pct = (other_cnt / global_total * 100) if global_total > 0 else 0.0
        md.append(f"| `other` | Minor / Non-standard Localities | `{other_cnt:,}` | `{pct:.1f}%` |")

    md.append("\n### Continental & Regional Breakdown\n")
    md.append("| Region / Continent | Countries / Regions | Total Polygons | Wikidata % | Admin Levels Present |")
    md.append("| :--- | :--- | :--- | :--- | :--- |")

    for row in continent_rows:
        md.append(f"| **{row['name']}** | `{row['regions']}` | `{row['polygons']:,}` | `{row['wikidata_pct']}` | {row['levels']} |")

    md.append("\n")

    stats_markdown = "\n".join(md)

    # Append to release-notes.md if file exists
    if os.path.exists(notes_path):
        with open(notes_path, 'a') as f:
            f.write(stats_markdown)
        print(f"Successfully appended polygon statistics to '{notes_path}'.")
    else:
        print(stats_markdown)

if __name__ == '__main__':
    main()
