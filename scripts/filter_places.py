#!/usr/bin/env python3
"""
Filter and structure OSM Place Nodes GeoJSON stream for osm-places.parquet export.

Extracts place nodes (hamlet, village, quarter, suburb, neighbourhood, isolated_dwelling,
locality, city, town, municipality, commune, city_block, townlet) with exact coordinates,
names, multilingual alt_names, wikidata, and population.
"""

import sys
import os
import json
import re
import argparse

PLACE_WHITELIST = {
    "hamlet", "village", "quarter", "suburb", "neighbourhood", "neighborhood",
    "isolated_dwelling", "locality", "city", "town", "municipality", "commune",
    "city_block", "townlet", "island", "islet", "island_group"
}

EXCLUDED_PLACES = {
    "country", "state", "province", "region", "county", "district",
    "sea", "ocean", "water", "glacier", "continent"
}

ALT_NAME_TAG_PREFIXES = ("name:", "alt_name", "official_name", "loc_name", "int_name", "short_name", "reg_name", "old_name")

_POP_PLAIN_RE = re.compile(r"^\d+$")
_POP_GROUPED_RE = re.compile(r"^\d{1,3}(?:[,.\s\u00a0\u202f'\u2019_]\d{3})+$")
MAX_PLAUSIBLE_POPULATION = 2_000_000_000


_POP_RANGE_RE = re.compile(r"^(.+?)\s*[-\u2013\u2014]\s*(.+)$")


def _parse_single_population(text):
    """Parse one plain or thousands-grouped integer; return None otherwise."""
    text = text.strip().lstrip("~\u2248").strip()
    if _POP_PLAIN_RE.match(text):
        return int(text)
    if _POP_GROUPED_RE.match(text):
        return int(re.sub(r"\D", "", text))
    return None


def parse_population(raw):
    """Parse an OSM population tag strictly into an integer, or None.

    Accepts plain integers ("42"), thousands-grouped integers ("303,419",
    "1.234.567", "22 000") and ranges ("22000-30000"), for which the rounded
    midpoint is returned. Lists ("100;200"), decimals, free text, inverted
    ranges and implausible values yield None. Digits are never concatenated
    across separators, which previously turned "22000-30000" into 2200030000.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    value = _parse_single_population(text)
    if value is None:
        m = _POP_RANGE_RE.match(text)
        if m:
            low = _parse_single_population(m.group(1))
            high = _parse_single_population(m.group(2))
            if low is not None and high is not None and low <= high:
                value = (low + high) // 2
    if value is None or value > MAX_PLAUSIBLE_POPULATION:
        return None
    return value


def process_place_feature(data, continent="", country_code=""):
    if not isinstance(data, dict) or data.get("type") != "Feature":
        return None

    props = data.get("properties")
    if not props or not isinstance(props, dict):
        return None

    geom = data.get("geometry")
    if not geom or not isinstance(geom, dict) or geom.get("type") != "Point":
        return None

    coords = geom.get("coordinates")
    if not coords or len(coords) < 2:
        return None

    lon, lat = coords[0], coords[1]
    if lon is None or lat is None or not (-180.0 <= lon <= 180.0) or not (-90.0 <= lat <= 90.0):
        return None

    place_val = str(props.get("place", "")).strip().lower()
    if not place_val or place_val in EXCLUDED_PLACES or place_val not in PLACE_WHITELIST:
        return None

    if place_val == "neighborhood":
        place_val = "neighbourhood"

    # Name extraction with fallback
    name = props.get("name")
    if not name or str(name).strip() == "" or str(name).lower() == "null":
        fallback = (
            props.get("name:en") or
            props.get("official_name") or
            props.get("name:de") or
            props.get("loc_name") or
            props.get("short_name")
        )
        if fallback and str(fallback).strip() != "":
            name = str(fallback).strip()
        else:
            return None
    else:
        name = str(name).strip()

    osm_id = props.get("id") or props.get("@id") or props.get("osm_id") or data.get("id")
    try:
        if isinstance(osm_id, str):
            cleaned = "".join(c for c in osm_id if c.isdigit())
            osm_id_num = int(cleaned) if cleaned else 0
        else:
            osm_id_num = int(osm_id)
    except (ValueError, TypeError):
        osm_id_num = 0

    wikidata = props.get("wikidata")
    if wikidata:
        wikidata = str(wikidata).strip()
        if wikidata.lower() in ("none", "null", ""):
            wikidata = None

    population = parse_population(props.get("population"))

    # Collect multilingual and alternative names
    alt_names = {}
    for k, v in props.items():
        if not v or not isinstance(v, str) or str(v).strip() == "":
            continue
        if any(k.startswith(prefix) for prefix in ALT_NAME_TAG_PREFIXES):
            if k != "name":
                alt_names[k] = str(v).strip()

    alt_names_json = json.dumps(alt_names, ensure_ascii=False) if alt_names else "{}"

    return {
        "continent": continent,
        "country_code": country_code,
        "osm_id": osm_id_num,
        "name": name,
        "place_type": place_val,
        "lon": float(lon),
        "lat": float(lat),
        "wikidata": wikidata,
        "population": population,
        "alt_names_json": alt_names_json,
        "tags": json.dumps(props, ensure_ascii=False),
        "geometry": geom
    }

def main():
    parser = argparse.ArgumentParser(description="Filter and structure OSM place nodes stream for GeoParquet export.")
    parser.add_argument("--continent", type=str, nargs="?", default="", help="Continental group (e.g. europe)")
    parser.add_argument("--country-code", type=str, nargs="?", default="", help="ISO Country code (e.g. DE)")
    parser.add_argument("input_file", nargs="?", help="Input geojsonseq file (or stdin if omitted)")
    args = parser.parse_args()

    input_stream = open(args.input_file, "r", encoding="utf-8") if args.input_file else sys.stdin

    count = 0
    for line in input_stream:
        line_str = line.strip()
        if not line_str:
            continue
        try:
            data = json.loads(line_str)
        except json.JSONDecodeError:
            continue

        res = process_place_feature(data, continent=args.continent, country_code=args.country_code)
        if res:
            sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
            count += 1

    if args.input_file:
        input_stream.close()

    sys.stderr.write(f"filter_places: Processed {count:,} place nodes for {args.country_code or 'input'}\n")
    sys.stderr.flush()

if __name__ == "__main__":
    main()
