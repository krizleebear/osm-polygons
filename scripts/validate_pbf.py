#!/usr/bin/env python3
"""
Validate a Geofabrik PBF extract for admin boundary completeness.

Uses osmium check-refs (C++, fast) to detect broken references,
then intersects with admin boundary relations found via pyosmium.

Usage:
    python3 scripts/validate_pbf.py <file.osm.pbf> [--country XX]
    python3 scripts/validate_pbf.py portugal-latest.osm.pbf
    python3 scripts/validate_pbf.py portugal-latest.osm.pbf --country PT

The --country flag filters broken relations to those belonging to
the specified country (ISO3166-1 for L2, ISO3166-2 prefix for L3+).
L7+ relations are always shown (rarely have ISO tags).
Auto-detects country from filename if not specified.
"""
import osmium
import sys
import json
import re
import subprocess
import argparse
from collections import defaultdict


COUNTRY_MAP = {
    "portugal": "PT", "spain": "ES", "france": "FR", "germany": "DE",
    "great britain": "GB", "united kingdom": "GB", "ireland": "IE",
    "italy": "IT", "austria": "AT", "switzerland": "CH", "belgium": "BE",
    "netherlands": "NL", "poland": "PL", "czech": "CZ", "czechia": "CZ",
    "romania": "RO", "hungary": "HU", "croatia": "HR", "slovenia": "SI",
    "denmark": "DK", "sweden": "SE", "norway": "NO", "finland": "FI",
    "greece": "GR", "turkey": "TR", "russia": "RU", "ukraine": "UA",
    "brazil": "BR", "argentina": "AR", "chile": "CL", "colombia": "CO",
    "mexico": "MX", "canada": "CA", "united states": "US", "usa": "US",
    "japan": "JP", "china": "CN", "india": "IN", "australia": "AU",
    "new zealand": "NZ", "south africa": "ZA", "egypt": "EG",
    "morocco": "MA", "tunisia": "TN", "algeria": "DZ",
    "island": "IS", "iceland": "IS", "luxembourg": "LU",
    "liechtenstein": "LI", "monaco": "MC", "andorra": "AD",
    "san marino": "SM", "vatican": "VA", "malta": "MT",
    "cyprus": "CY", "estonia": "EE", "latvia": "LV", "lithuania": "LT",
    "slovakia": "SK", "bulgaria": "BG", "serbia": "RS",
    "bosnia": "BA", "montenegro": "ME", "north macedonia": "MK",
    "albania": "AL", "moldova": "MD", "belarus": "BY",
    "georgia": "GE", "armenia": "AM", "azerbaijan": "AZ",
    "kazakhstan": "KZ", "uzbekistan": "UZ",
}


def detect_country_from_filename(pbf_path):
    basename = pbf_path.rsplit("/", 1)[-1].lower()
    m = re.match(r"^([a-z]+?)(?:-latest|-\d{6}|-\d{8})?\.osm\.pbf$", basename)
    if m:
        return COUNTRY_MAP.get(m.group(1))
    return None


def belongs_to_country(tags, country_code):
    cc = country_code.upper()
    if tags.get("ISO3166-1", "").upper() == cc:
        return True
    if tags.get("ISO3166-2", "").upper().startswith(cc + "-"):
        return True
    return False


class AdminBoundaryScanner(osmium.SimpleHandler):
    """Scan PBF for admin boundary relations — metadata only."""

    def __init__(self, target_ids=None):
        super().__init__()
        self.target_ids = target_ids  # if set, only collect these
        self.admin_relations = {}     # id -> {name, level, iso1, iso2, ...}
        self.all_way_ids = set()
        self.node_count = 0
        self.way_count = 0
        self.relation_count = 0

    def node(self, n):
        self.node_count += 1

    def way(self, w):
        self.all_way_ids.add(w.id)
        self.way_count += 1

    def relation(self, r):
        self.relation_count += 1
        tags = dict(r.tags)
        if tags.get("boundary") != "administrative":
            return
        if self.target_ids and r.id not in self.target_ids:
            return

        outer = set()
        for m in r.members:
            mtype = m.type.lower() if m.type else ""
            if mtype in ("w", "way") and m.role == "outer":
                outer.add(m.ref)

        self.admin_relations[r.id] = {
            "name": tags.get("name", ""),
            "admin_level": tags.get("admin_level", ""),
            "iso1": tags.get("ISO3166-1", ""),
            "iso2": tags.get("ISO3166-2", ""),
            "outer_total": len(outer),
            "tags": tags,
        }


def run_check_refs(pbf_path):
    """Run osmium check-refs -r -i and parse output."""
    result = subprocess.run(
        ["osmium", "check-refs", "-r", "-i", pbf_path],
        capture_output=True, text=True
    )
    # Output lines: "w<id> in r<id>"
    broken = defaultdict(lambda: {"missing_ways": 0, "way_ids": []})
    for line in result.stdout.strip().split("\n"):
        if not line.startswith("w"):
            continue
        parts = line.split(" in r")
        if len(parts) != 2:
            continue
        way_id = int(parts[0][1:])  # strip 'w'
        rel_id = int(parts[1])
        broken[rel_id]["missing_ways"] += 1
        broken[rel_id]["way_ids"].append(way_id)
    return dict(broken)


def validate(pbf_path, country_code=None):
    print(f"Scanning {pbf_path} ...")
    if country_code:
        print(f"Filtering for country: {country_code}")
    print()

    # Step 1: osmium check-refs (C++, fast)
    print("Step 1/3: Running osmium check-refs ...", flush=True)
    broken = run_check_refs(pbf_path)
    print(f"  Found {len(broken)} relations with broken references.")

    if not broken:
        print("\nALL references OK — no broken relations.")
        return 0

    # Step 2: Scan PBF for admin boundary metadata (only broken relations)
    print("Step 2/3: Scanning admin boundary metadata ...", flush=True)
    broken_ids = set(broken.keys())
    scanner = AdminBoundaryScanner(target_ids=broken_ids)
    scanner.apply_file(pbf_path, locations=True)
    print(f"  Found {len(scanner.admin_relations)} broken admin boundary relations.")

    # Step 3: Filter and display
    print("Step 3/3: Filtering results ...")
    print()

    # Filter: country-owned L2-L6 + all L7+
    display = []
    for rid, info in scanner.admin_relations.items():
        if country_code:
            if belongs_to_country(info["tags"], country_code) or int(info["admin_level"]) >= 7:
                display.append((rid, info))
        else:
            display.append((rid, info))

    display.sort(key=lambda e: (int(e[1]["admin_level"]) if e[1]["admin_level"].isdigit() else 999, e[0]))

    # Stats
    print(f"PBF statistics:")
    print(f"  Nodes:       {scanner.node_count:>10,}")
    print(f"  Ways:        {scanner.way_count:>10,}")
    print(f"  Relations:   {scanner.relation_count:>10,}")
    print()

    print("=" * 80)
    if country_code:
        label = f" ({country_code} + all L7+)"
    else:
        label = ""
    print(f"BROKEN admin boundary relations{label}: {len(display)}")
    print("=" * 80)
    print()

    if display:
        print(f"{'ID':<12} {'Level':<6} {'Name':<30} {'ISO2':<8} {'Missing':>8}")
        print("-" * 70)
        for rid, info in display:
            missing = broken[rid]["missing_ways"]
            iso2 = info["iso2"][:6] if info["iso2"] else ""
            print(f"{rid:<12} {info['admin_level']:<6} {info['name']:<30} {iso2:<8} {missing:>8}")
    else:
        if broken:
            print(f"All admin boundary relations for {country_code} OK.")
            other = len(broken) - len(display)
            print(f"({other} broken relations belong to other countries)")
        else:
            print("ALL admin boundary relations OK.")

    print()

    # JSON summary
    summary = {
        "pbf_file": pbf_path,
        "country_filter": country_code,
        "total_nodes": scanner.node_count,
        "total_ways": scanner.way_count,
        "total_relations": scanner.relation_count,
        "broken_total": len(broken),
        "broken_admin_boundary": len(display),
        "broken": [
            {
                "id": rid,
                "name": info["name"],
                "admin_level": info["admin_level"],
                "iso2": info["iso2"],
                "missing_ways": broken[rid]["missing_ways"],
            }
            for rid, info in display
        ],
    }

    summary_path = pbf_path.replace(".osm.pbf", "-validation.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"JSON summary written to: {summary_path}")

    return 0 if not display else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate PBF admin boundary completeness")
    parser.add_argument("pbf", help="Path to .osm.pbf file")
    parser.add_argument("--country", "-c", default=None,
                        help="ISO 3166-1 alpha-2 country code to filter (e.g. PT, ES, FR)")
    args = parser.parse_args()

    cc = args.country
    if not cc:
        cc = detect_country_from_filename(args.pbf)
        if cc:
            print(f"(Auto-detected country: {cc})")

    sys.exit(validate(args.pbf, cc))
