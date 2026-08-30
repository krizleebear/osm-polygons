#!/usr/bin/env python3
"""
Aggregates per-region validation.json files into a global validation summary.

Reads all validation.json files from a directory and produces:
  - validation-summary.md   (human-readable Markdown table)
  - validation-summary.json (machine-readable JSON)

Statistics per admin_level:
  - broken_parents:   number of broken parent relations
  - children_complete: children with complete:true (referentially valid)
  - children_broken:  children with complete:false (also broken)
"""

import os
import sys
import json
from collections import defaultdict

LEVEL_COLUMNS = ['2', '3', '4', '5', '6', '7', '8', '9', '10', '11']

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


def load_validation_files(directory):
    """Load all validation.json files from the given directory (recursive)."""
    results = []
    for root, _, files in os.walk(directory):
        for fname in sorted(files):
            if fname.endswith('-validation.json') or fname == 'validation.json':
                path = os.path.join(root, fname)
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if isinstance(data, dict) and data:
                        results.append(data)
                except Exception as e:
                    print(f"Warning: Failed to read {path}: {e}", file=sys.stderr)
    return results



def aggregate(validations):
    """
    Aggregate validation results across all regions.

    Returns:
      per_level: {level_str: {broken_parents, children_complete, children_broken}}
      per_country: {country_filter: {level_str: {broken_parents, ...}}}
      totals: {total_broken, total_children_complete, total_children_broken}
    """
    per_level = defaultdict(lambda: {
        'broken_parents': 0,
        'children_complete': 0,
        'children_broken': 0,
    })
    per_country = defaultdict(lambda: defaultdict(lambda: {
        'broken_parents': 0,
        'children_complete': 0,
        'children_broken': 0,
    }))

    total_broken = 0
    total_children_complete = 0
    total_children_broken = 0

    for v in validations:
        country = v.get('country_filter') or 'XX'
        broken_list = v.get('broken')
        if not isinstance(broken_list, list):
            continue
        for entry in broken_list:
            if not isinstance(entry, dict):
                continue
            lvl = str(entry.get('admin_level', '')).strip()
            if not lvl.isdigit() or int(lvl) < 2 or int(lvl) > 11:
                lvl = 'other'

            per_level[lvl]['broken_parents'] += 1
            per_country[country][lvl]['broken_parents'] += 1
            total_broken += 1

            children_list = entry.get('children')
            if isinstance(children_list, list):
                for child in children_list:
                    if isinstance(child, dict) and child.get('complete', False):
                        per_level[lvl]['children_complete'] += 1
                        per_country[country][lvl]['children_complete'] += 1
                        total_children_complete += 1
                    else:
                        per_level[lvl]['children_broken'] += 1
                        per_country[country][lvl]['children_broken'] += 1
                        total_children_broken += 1


    totals = {
        'total_broken': total_broken,
        'total_children_complete': total_children_complete,
        'total_children_broken': total_children_broken,
    }
    return dict(per_level), dict(per_country), totals


def generate_markdown(per_level, per_country, totals, region_count):
    """Build Markdown summary report."""
    md = []
    md.append("\n## Validation Summary — Broken Admin Boundaries\n")
    md.append(f"- **Regions Validated:** `{region_count}`")
    md.append(f"- **Total Broken Parent Relations:** `{totals['total_broken']:,}`")
    md.append(f"- **Children Referentially Valid (complete):** `{totals['total_children_complete']:,}`")
    md.append(f"- **Children Also Broken (incomplete):** `{totals['total_children_broken']:,}`")
    md.append("")

    # Global per-level table
    md.append("### Broken Relations by Admin Level\n")
    md.append("| Admin Level | Description | Broken Parents | Children (complete) | Children (broken) |")
    md.append("| :--- | :--- | ---: | ---: | ---: |")

    for lvl in LEVEL_COLUMNS:
        stats = per_level.get(lvl, {})
        bp = stats.get('broken_parents', 0)
        cc = stats.get('children_complete', 0)
        cb = stats.get('children_broken', 0)
        if bp == 0 and cc == 0 and cb == 0:
            continue
        desc = ADMIN_LEVEL_DESCRIPTIONS.get(lvl, f"Level {lvl} Boundary")
        md.append(f"| `admin_level={lvl}` | {desc} | `{bp:,}` | `{cc:,}` | `{cb:,}` |")

    other = per_level.get('other', {})
    if other.get('broken_parents', 0) > 0:
        md.append(f"| `other` | Non-standard | `{other['broken_parents']:,}` | `{other['children_complete']:,}` | `{other['children_broken']:,}` |")

    total_row = (
        f"| **Total** | **All Levels** | "
        f"**`{totals['total_broken']:,}`** | "
        f"**`{totals['total_children_complete']:,}`** | "
        f"**`{totals['total_children_broken']:,}`** |"
    )
    md.append(total_row)
    md.append("")

    # Per-country matrix
    if per_country:
        md.append("### Per-Country Breakdown\n")
        header = "| ISO | Broken Parents | Children (complete) | Children (broken) |"
        separator = "| :--- | ---: | ---: | ---: |"
        md.append(header)
        md.append(separator)

        for country in sorted(per_country.keys()):
            country_stats = per_country[country]
            bp = sum(s['broken_parents'] for s in country_stats.values())
            cc = sum(s['children_complete'] for s in country_stats.values())
            cb = sum(s['children_broken'] for s in country_stats.values())
            md.append(f"| `{country}` | `{bp:,}` | `{cc:,}` | `{cb:,}` |")

        total_bp = totals['total_broken']
        total_cc = totals['total_children_complete']
        total_cb = totals['total_children_broken']
        md.append(f"| **Total** | **`{total_bp:,}`** | **`{total_cc:,}`** | **`{total_cb:,}`** |")
        md.append("")

    return "\n".join(md)


def generate_json(per_level, per_country, totals, region_count):
    """Build machine-readable JSON structure."""
    return {
        'region_count': region_count,
        'totals': totals,
        'per_level': {
            lvl: {
                'broken_parents': s['broken_parents'],
                'children_complete': s['children_complete'],
                'children_broken': s['children_broken'],
            }
            for lvl, s in sorted(per_level.items(), key=lambda x: (
                int(x[0]) if x[0].isdigit() else 999, x[0]
            ))
        },
        'per_country': {
            country: {
                lvl: {
                    'broken_parents': s['broken_parents'],
                    'children_complete': s['children_complete'],
                    'children_broken': s['children_broken'],
                }
                for lvl, s in sorted(country_stats.items(), key=lambda x: (
                    int(x[0]) if x[0].isdigit() else 999, x[0]
                ))
            }
            for country, country_stats in sorted(per_country.items())
        },
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Aggregate validation reports.")
    parser.add_argument("directory", nargs="?", default="validation", help="Directory containing validation JSON files")
    args = parser.parse_args()

    directory = args.directory

    if not os.path.isdir(directory):
        print(f"Info: Directory '{directory}' does not exist. Generating empty summary.")
        os.makedirs(directory, exist_ok=True)
        md_path = os.path.join(directory, 'validation-summary.md')
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write("# Administrative Validation Summary\n\nNo validation issues recorded.\n")
        json_path = os.path.join(directory, 'validation-summary.json')
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump({"total_regions": 0, "totals": {"broken_parents": 0}}, f, indent=2)
        return

    validations = load_validation_files(directory)
    if not validations:
        print(f"Info: No populated validation files found in '{directory}'. Generating empty summary.")
        md_path = os.path.join(directory, 'validation-summary.md')
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write("# Administrative Validation Summary\n\nNo validation issues recorded.\n")
        json_path = os.path.join(directory, 'validation-summary.json')
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump({"total_regions": 0, "totals": {"broken_parents": 0}}, f, indent=2)
        return

    print(f"Loaded {len(validations)} validation file(s) from '{directory}'.")



    per_level, per_country, totals = aggregate(validations)
    region_count = len(validations)

    # Markdown
    md_content = generate_markdown(per_level, per_country, totals, region_count)
    md_path = os.path.join(directory, 'validation-summary.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_content)
    print(f"Generated Markdown summary: '{md_path}'")

    # JSON
    json_data = generate_json(per_level, per_country, totals, region_count)
    json_path = os.path.join(directory, 'validation-summary.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    print(f"Generated JSON summary: '{json_path}'")


if __name__ == '__main__':
    main()
