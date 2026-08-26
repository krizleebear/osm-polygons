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
    """Scan PBF for admin boundary relations — metadata, membership, completeness."""

    def __init__(self):
        super().__init__()
        self.admin_relations = {}     # id -> {name, level, iso1, iso2, ...}
        self.parent_children = defaultdict(set)  # parent_id -> {child_id, ...}
        self.child_parents = defaultdict(set)    # child_id -> {parent_id, ...}
        self.relation_count = 0

    def relation(self, r):
        self.relation_count += 1
        tags = dict(r.tags)
        if tags.get("boundary") != "administrative":
            return

        outer = set()
        member_relations = set()
        for m in r.members:
            mtype = m.type.lower() if m.type else ""
            if mtype in ("w", "way") and m.role == "outer":
                outer.add(m.ref)
            elif mtype in ("r", "relation"):
                member_relations.add(m.ref)

        self.admin_relations[r.id] = {
            "name": tags.get("name", ""),
            "admin_level": tags.get("admin_level", ""),
            "iso1": tags.get("ISO3166-1", ""),
            "iso2": tags.get("ISO3166-2", ""),
            "outer_total": len(outer),
            "member_relations": sorted(member_relations),
            "tags": tags,
        }

        for child_id in member_relations:
            self.parent_children[r.id].add(child_id)
            self.child_parents[child_id].add(r.id)

    def infer_missing_children(self):
        """For L2+ relations with no relation-members, find children by ISO prefix.

        Some country boundaries (e.g. US) use direct Ways instead of nesting
        child relations. This method infers parent-child relationships by
        matching ISO3166-2 prefix of potential children against ISO3166-1 of
        the parent.
        """
        for rid, info in self.admin_relations.items():
            if self.parent_children[rid]:
                continue
            level = info["admin_level"]
            if not level.isdigit() or int(level) > 2:
                continue
            iso1 = info["iso1"]
            if not iso1:
                continue

            prefix = iso1 + "-"
            for cid, cinfo in self.admin_relations.items():
                if cid == rid:
                    continue
                clevel = cinfo["admin_level"]
                if not clevel.isdigit() or int(clevel) <= int(level):
                    continue
                if cinfo["iso2"].startswith(prefix):
                    self.parent_children[rid].add(cid)
                    self.child_parents[cid].add(rid)


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


def generate_synthetic_defs(enriched, scanner, broken_ids):
    """Generate SYNTHETIC_PARENT_DEFINITIONS from enriched broken relations."""
    defs = {}
    for entry in enriched:
        rid = entry["id"]
        info = scanner.admin_relations[rid]
        tags = info["tags"]

        # Country code: ISO3166-1 or prefix of ISO3166-2
        country_code = ""
        if info["iso1"]:
            country_code = info["iso1"]
        elif info["iso2"]:
            country_code = info["iso2"].split("-")[0]

        # Properties: all OSM tags plus computed fields
        properties = dict(tags)
        properties["@id"] = rid
        properties["@type"] = "relation"
        properties["id"] = rid

        # Child completeness and force_collect
        complete_children = [c for c in entry["children"] if c["complete"]]
        child_ids_set = {c["id"] for c in entry["children"]}
        force_collect = any(cid in broken_ids for cid in child_ids_set)

        # If force_collect, include ALL children (not just complete ones)
        if force_collect:
            all_children = entry["children"]
        else:
            all_children = complete_children

        child_relation_ids = sorted(c["id"] for c in all_children)
        child_names = sorted(c["name"] for c in all_children if c["name"])

        # Child admin level: level of the first complete child (or first child if force_collect)
        child_admin_level = ""
        if complete_children:
            child_admin_level = complete_children[0]["admin_level"]
        elif all_children:
            child_admin_level = all_children[0]["admin_level"]

        defs[rid] = {
            "country_code": country_code,
            "properties": properties,
            "child_relation_ids": child_relation_ids,
            "child_names": child_names,
            "child_admin_level": child_admin_level,
        }
        if force_collect:
            defs[rid]["force_collect"] = True

    return defs


def generate_parent_mapping(scanner, country_code=None):
    """Generate a child_id -> parent metadata mapping from the scanner's admin boundary data."""
    mapping = {}
    for child_id, parent_ids in scanner.child_parents.items():
        for parent_id in parent_ids:
            if parent_id not in scanner.admin_relations:
                continue
            pinfo = scanner.admin_relations[parent_id]
            # Filter by country if specified
            if country_code:
                iso1 = pinfo.get("iso1", "")
                iso2 = pinfo.get("iso2", "")
                cc_from_iso1 = iso1.upper().strip()
                cc_from_iso2 = iso2.split("-")[0].upper().strip() if iso2 else ""
                if cc_from_iso1 != country_code.upper() and cc_from_iso2 != country_code.upper():
                    continue
            mapping[str(child_id)] = {
                "parent_osm_id": parent_id,
                "parent_name": pinfo.get("name", ""),
                "parent_iso3166_2": pinfo.get("iso2", ""),
            }
    return mapping


def validate(pbf_path, country_code=None):
    print(f"Scanning {pbf_path} ...")
    if country_code:
        print(f"Filtering for country: {country_code}")
    print()

    # Step 1: osmium check-refs (C++, fast)
    print("Step 1/3: Running osmium check-refs ...", flush=True)
    broken = run_check_refs(pbf_path)
    print(f"  Found {len(broken)} relations with broken references.")

    # Step 2: Scan ALL admin boundary metadata (need membership for child detection)
    print("Step 2/3: Scanning admin boundary metadata ...", flush=True)
    scanner = AdminBoundaryScanner()
    scanner.apply_file(pbf_path)
    scanner.infer_missing_children()
    print(f"  Found {len(scanner.admin_relations)} admin boundary relations.")

    # Always generate parent mapping (needed for child-to-parent enrichment)
    parent_mapping = generate_parent_mapping(scanner, country_code)
    mapping_path = pbf_path.replace(".osm.pbf", "-parent-mapping.json")
    with open(mapping_path, "w") as f:
        json.dump(parent_mapping, f, indent=2, ensure_ascii=False)
    print(f"Parent mapping written to: {mapping_path} ({len(parent_mapping)} child entries)")

    if not broken:
        print("\nALL references OK — no broken relations.")
        # Write empty validation summary
        summary = {
            "pbf_file": pbf_path,
            "country_filter": country_code,
            "total_relations": scanner.relation_count,
            "broken_total": 0,
            "broken_admin_boundary": 0,
            "broken": [],
        }
        summary_path = pbf_path.replace(".osm.pbf", "-validation.json")
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"JSON summary written to: {summary_path}")
        return 0

    # Build child completeness
    broken_ids = set(broken.keys())
    child_complete = {cid: cid not in broken_ids for cid in scanner.admin_relations}

    # Step 3: Filter and enrich broken relations with children
    print("Step 3/3: Analyzing child relations ...")
    print()

    broken_ids = set(broken.keys())

    # Filter: country-owned L2-L6 + all L7+
    display = []
    for rid, info in scanner.admin_relations.items():
        if rid not in broken_ids:
            continue
        if country_code:
            lvl = info["admin_level"]
            is_high_level = lvl.isdigit() and int(lvl) >= 7
            if belongs_to_country(info["tags"], country_code) or is_high_level:
                display.append((rid, info))
        else:
            display.append((rid, info))

    display.sort(key=lambda e: (int(e[1]["admin_level"]) if e[1]["admin_level"].isdigit() else 999, e[0]))

    # Enrich with children
    enriched = []
    for rid, info in display:
        child_ids = scanner.parent_children.get(rid, set())
        children = []
        for cid in sorted(child_ids):
            if cid in scanner.admin_relations:
                cinfo = scanner.admin_relations[cid]
                children.append({
                    "id": cid,
                    "name": cinfo["name"],
                    "admin_level": cinfo["admin_level"],
                    "complete": child_complete.get(cid, False),
                })

        enriched.append({
            "id": rid,
            "name": info["name"],
            "admin_level": info["admin_level"],
            "iso1": info["iso1"],
            "iso2": info["iso2"],
            "missing_ways": broken[rid]["missing_ways"],
            "children": children,
        })

    # Stats
    print(f"PBF statistics:")
    print(f"  Relations:   {scanner.relation_count:>10,}")
    print()

    print("=" * 80)
    if country_code:
        label = f" ({country_code} + all L7+)"
    else:
        label = ""
    print(f"BROKEN admin boundary relations{label}: {len(enriched)}")
    print("=" * 80)
    print()

    if enriched:
        print(f"{'ID':<12} {'Level':<6} {'Name':<30} {'ISO2':<8} {'Missing':>8} {'Children':>9}")
        print("-" * 80)
        for entry in enriched:
            iso2 = entry["iso2"][:6] if entry["iso2"] else ""
            n_complete = sum(1 for c in entry["children"] if c["complete"])
            n_total = len(entry["children"])
            children_str = f"{n_complete}/{n_total}" if n_total else "-"
            print(f"{entry['id']:<12} {entry['admin_level']:<6} {entry['name']:<30} {iso2:<8} {entry['missing_ways']:>8} {children_str:>9}")
    else:
        if broken:
            print(f"All admin boundary relations for {country_code} OK.")
            other = len(broken) - len(enriched)
            print(f"({other} broken relations belong to other countries)")
        else:
            print("ALL admin boundary relations OK.")

    print()

    # JSON summary
    summary = {
        "pbf_file": pbf_path,
        "country_filter": country_code,
        "total_relations": scanner.relation_count,
        "broken_total": len(broken),
        "broken_admin_boundary": len(enriched),
        "broken": enriched,
    }

    summary_path = pbf_path.replace(".osm.pbf", "-validation.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"JSON summary written to: {summary_path}")

    # Synthetic parent definitions for filter_polygons.py
    synthetic_defs = generate_synthetic_defs(enriched, scanner, broken_ids)
    defs_path = pbf_path.replace(".osm.pbf", "-synthetic-defs.json")
    with open(defs_path, "w") as f:
        json.dump(synthetic_defs, f, indent=2, ensure_ascii=False)
    print(f"Synthetic defs written to: {defs_path}")

    return 0 if not enriched else 1


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
