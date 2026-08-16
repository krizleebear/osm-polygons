#!/usr/bin/env python3
"""
Filter and enhance OSM admin polygon GeoJSON sequence streams for osm-polygons pipeline.

Main tasks:
1. Enforce admin_level=2 for key national mainland relations (FR 2202162, GB 62149, NO 2978650, PT 295438).
2. Prevent tag loss: fallback missing or null 'name' tags to name:en, official_name, ISO3166-1, or default.
3. Flag maritime / territorial sea polygons (border_type=territorial, maritime=yes) to prioritize landmasses.
4. Provide mapped level-4 fallback properties for countries lacking native admin_level=4 relations.
5. Synthesize admin_level for sub-municipal boundaries per SPEC_OSM_POLYGONS_SUBDIVISIONS.md:
   - Rule 2: boundary=statistical/local_authority/political/borough without admin_level -> default "10".
   - Rule 3: type=boundary/multipolygon relations with place=suburb/quarter/neighbourhood/borough -> default 9/10/11/9.
6. Keep nameless admin_level=2 land borders that carry ISO3166-1 (instead of dropping them).
"""

import sys
import json
import argparse

# National mainland relations that must be preserved as admin_level=2
MAINLAND_RELATION_IDS = {
    2202162: {"country": "FR", "default_name": "France (Métropole)"},
    62149:   {"country": "GB", "default_name": "Great Britain"},
    2978650: {"country": "NO", "default_name": "Norway (Mainland)"},
    295438:  {"country": "PT", "default_name": "Portugal (Continental)"},
}

# Countries known to lack native admin_level=4 relations, mapping fallback levels (e.g., level 6 -> 4)
LEVEL_4_FALLBACK_COUNTRIES = {
    "EE", "HR", "ME", "SI", "XK", "CY", "IS", "LV", "MK"
}

# Sub-municipal boundary tag values (Rule 2 of SPEC_OSM_POLYGONS_SUBDIVISIONS.md):
# boundary IN (statistical, local_authority, political, borough) without admin_level -> default admin_level.
SUBMUNICIPAL_BOUNDARY_VALUES = ("statistical", "local_authority", "political", "borough")
DEFAULT_SUBMUNICIPAL_ADMIN_LEVEL = "10"

# Place-based boundary relations (Rule 3 of SPEC_OSM_POLYGONS_SUBDIVISIONS.md):
# type=boundary OR type=multipolygon combined with place -> default admin_level.
PLACE_TO_ADMIN_LEVEL = {
    "suburb": "9",
    "quarter": "10",
    "neighbourhood": "11",
    "borough": "9",
}
PLACE_RELATION_TYPES = ("boundary", "multipolygon")

def process_feature(data, require_wikidata=False, country_code=None):
    if data.get("type") != "Feature":
        return None

    props = data.get("properties")
    if not props or not isinstance(props, dict):
        return None

    # Spec §4.2: only Polygon / MultiPolygon geometries are emitted.
    # osmium tags-filter pulls in referenced member nodes/ways which osmium export
    # would otherwise leak through as Point/LineString features.
    geometry_type = (data.get("geometry") or {}).get("type")
    if geometry_type not in ("Polygon", "MultiPolygon"):
        return None

    # Check admin_level presence
    raw_level = str(props.get("admin_level", "")).strip()
    osm_id = props.get("id") or props.get("@id") or props.get("osm_id")
    try:
        osm_id_num = int(osm_id) if osm_id else None
    except (ValueError, TypeError):
        osm_id_num = None

    # Task 1: Enforce admin_level=2 for key mainland relations
    if osm_id_num in MAINLAND_RELATION_IDS:
        info = MAINLAND_RELATION_IDS[osm_id_num]
        props["admin_level"] = "2"
        raw_level = "2"
        if not props.get("name"):
            props["name"] = info["default_name"]
        if not props.get("ISO3166-1"):
            props["ISO3166-1"] = info["country"]

    # Task 5: Synthesize admin_level for sub-municipal boundaries (Rule 3 takes precedence over Rule 2)
    element_type = str(props.get("@type", "")).strip().lower()
    rel_type = str(props.get("type", "")).strip().lower()
    place_val = str(props.get("place", "")).strip().lower()
    if (element_type == "relation" and rel_type in PLACE_RELATION_TYPES
            and place_val in PLACE_TO_ADMIN_LEVEL
            and (not raw_level or raw_level == "None")):
        props["admin_level"] = PLACE_TO_ADMIN_LEVEL[place_val]
        raw_level = PLACE_TO_ADMIN_LEVEL[place_val]

    boundary_val = str(props.get("boundary", "")).strip().lower()
    if (boundary_val in SUBMUNICIPAL_BOUNDARY_VALUES
            and (not raw_level or raw_level == "None")):
        props["admin_level"] = DEFAULT_SUBMUNICIPAL_ADMIN_LEVEL
        raw_level = DEFAULT_SUBMUNICIPAL_ADMIN_LEVEL

    if not raw_level or raw_level == "None":
        return None

    # Task 2: Prevent tag-loss by falling back missing/null name tags
    name = props.get("name")
    if not name or str(name).strip() == "" or str(name).lower() == "null":
        fallback_name = (
            props.get("name:en") or
            props.get("official_name") or
            props.get("name:de") or
            props.get("short_name") or
            props.get("ref") or
            props.get("ISO3166-2") or
            props.get("ISO3166-1")
        )
        if fallback_name and str(fallback_name).strip() != "":
            props["name"] = str(fallback_name).strip()
        else:
            # Drop features without any identifiable name, EXCEPT national land borders
            # (admin_level=2 with ISO3166-1, per SPEC_OSM_POLYGONS_SUBDIVISIONS.md §3).
            if raw_level == "2" and props.get("ISO3166-1"):
                props["name"] = str(props.get("ISO3166-1")).strip()
            else:
                return None

    if require_wikidata and not props.get("wikidata"):
        return None

    # Task 3: Flag maritime / territorial sea polygons
    border_type = str(props.get("border_type", "")).lower()
    maritime_tag = str(props.get("maritime", "")).lower()
    name_lower = str(props.get("name", "")).lower()
    
    is_territorial = (
        border_type in ("territorial", "baseline", "maritime") or
        maritime_tag in ("yes", "true", "1") or
        "águas territoriais" in name_lower or
        "territorial sea" in name_lower or
        "maritime border" in name_lower
    )
    if is_territorial:
        props["is_territorial_sea"] = True

    # Task 4: Provide mapped level-4 fallback for countries lacking admin_level=4
    c_code = country_code or props.get("ISO3166-1")
    if c_code and str(c_code).upper() in LEVEL_4_FALLBACK_COUNTRIES:
        if raw_level in ("5", "6", "7") and "admin_level_mapped" not in props:
            props["admin_level_mapped"] = "4"

    return data

def main():
    parser = argparse.ArgumentParser(description="Filter and enhance GeoJSON sequence stream for osm-polygons exporter.")
    parser.add_argument("--require-wikidata", action="store_true", help="Require wikidata property")
    parser.add_argument("--country-code", type=str, help="Optional country code ISO override")
    parser.add_argument("input_file", nargs="?", help="Input geojsonseq file (or stdin if omitted)")
    args = parser.parse_args()

    if args.input_file:
        input_stream = open(args.input_file, "r", encoding="utf-8")
    else:
        input_stream = sys.stdin

    for line in input_stream:
        line_str = line.strip()
        if not line_str:
            continue
        try:
            data = json.loads(line_str)
            processed = process_feature(data, require_wikidata=args.require_wikidata, country_code=args.country_code)
            if processed:
                sys.stdout.write(json.dumps(processed, ensure_ascii=False) + "\n")
        except json.JSONDecodeError:
            continue

    if args.input_file:
        input_stream.close()

if __name__ == "__main__":
    main()
