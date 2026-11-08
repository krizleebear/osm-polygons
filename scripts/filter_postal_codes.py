#!/usr/bin/env python3
"""
Filter and structure OSM Postal Code GeoJSON stream for osm-postal-codes.parquet export.

Extracts postal code boundary areas (boundary=postal_code) as Polygon and MultiPolygon
geometries, enforces blacklist exclusions (end_date, historic), normalizes postal codes
across fallback tags, cleans metadata tags, and produces a flattened JSONL stream.
"""

import argparse
import json
import re
import sys

EXCLUDED_TAG_KEYS = {
    "source", "created_by", "note", "fixme", "FIXME", "check_date",
    "import_uuid", "odbl", "attribution", "source_ref"
}

EXCLUDED_TAG_PREFIXES = (
    "source:", "note:", "tiger:", "yh:", "osak:", "gnis:", "nhd:",
    "naptan:", "ref:bag", "ref:ruian"
)

POSTAL_CODE_KEYS = ("postal_code", "postcode", "addr:postcode", "ref")


def clean_tags(props):
    """Prune metadata tags and internal osmium attributes from properties dict."""
    cleaned = {}
    for k, v in props.items():
        if k.startswith("@"):
            continue
        k_lower = k.lower()
        if k_lower in EXCLUDED_TAG_KEYS:
            continue
        if any(k_lower.startswith(prefix) for prefix in EXCLUDED_TAG_PREFIXES):
            continue
        cleaned[k] = v
    return cleaned


def normalize_osm_type(raw_type, geom_type=""):
    """Normalize raw OSM type to 'way' or 'relation'."""
    if not raw_type:
        if geom_type == "MultiPolygon":
            return "relation"
        return "way"
    raw_str = str(raw_type).strip().lower()
    if raw_str in ("w", "way"):
        return "way"
    if raw_str in ("r", "rel", "relation", "multipolygon"):
        return "relation"
    return "way"


def extract_postal_code(props):
    """
    Extract and normalize postal code from properties using priority chain:
    COALESCE(postal_code, postcode, addr:postcode, ref).
    Splits on ';' or '/', strips whitespace, drops empty segments, and joins with ';'.
    Returns None if no valid non-empty postal code is found.
    """
    if not props or not isinstance(props, dict):
        return None

    for key in POSTAL_CODE_KEYS:
        raw_val = props.get(key)
        if raw_val is None:
            continue
        val_str = str(raw_val).strip()
        if not val_str or val_str.lower() == "null":
            continue
        segments = [seg.strip() for seg in re.split(r"[;/]", val_str)]
        segments = [seg for seg in segments if seg and seg.lower() != "null"]
        if segments:
            return ";".join(segments)

    return None


def process_postal_code_feature(data, continent="", country_code=""):
    """
    Process a single GeoJSON feature from osmium export.
    Returns a flattened dictionary or None if invalid.
    """
    if not isinstance(data, dict) or data.get("type") != "Feature":
        return None

    props = data.get("properties")
    if not props or not isinstance(props, dict):
        return None

    geom = data.get("geometry")
    if not geom or not isinstance(geom, dict):
        return None

    geom_type = geom.get("type")
    if geom_type not in ("Polygon", "MultiPolygon"):
        return None

    coords = geom.get("coordinates")
    if not coords or len(coords) == 0:
        return None

    # Inclusion check: boundary must strictly be 'postal_code' (rejects 'historic:postal_code', etc.)
    boundary_val = str(props.get("boundary", "")).strip().lower()
    if boundary_val != "postal_code":
        return None

    # Blacklist check: end_date
    end_date = props.get("end_date")
    if end_date is not None and str(end_date).strip() != "":
        return None

    # Blacklist check: historic
    historic = props.get("historic")
    if historic is not None and str(historic).strip().lower() not in ("", "no", "false", "0"):
        return None

    # Postal code extraction & mandatory gating
    postal_code = extract_postal_code(props)
    if not postal_code:
        return None

    raw_osm_id = props.get("@id") or data.get("id")
    if raw_osm_id is None:
        return None

    try:
        osm_id = int(raw_osm_id)
    except (ValueError, TypeError):
        return None

    raw_type = props.get("@type")
    osm_type = normalize_osm_type(raw_type, geom_type)

    # Name extraction
    name = props.get("name")
    if name is not None:
        name = str(name).strip()
        if not name or name.lower() == "null":
            name = None

    if not name:
        name = props.get("name:en") or props.get("official_name")
        if name is not None:
            name = str(name).strip()
            if not name or name.lower() == "null":
                name = None

    cleaned_tags = clean_tags(props)

    return {
        "continent": continent,
        "country_code": country_code,
        "osm_id": osm_id,
        "osm_type": osm_type,
        "postal_code": postal_code,
        "name": name,
        "geom_json": json.dumps(geom),
        "tags": json.dumps(cleaned_tags, ensure_ascii=False),
    }


def main():
    parser = argparse.ArgumentParser(description="Filter and structure OSM postal code GeoJSON stream.")
    parser.add_argument("--country-code", default="", help="Optional country code to inject into records")
    parser.add_argument("--continent", default="", help="Optional continent to inject into records")
    args = parser.parse_args()

    count = 0
    for line in sys.stdin:
        line = line.strip()
        if not line or line.startswith("\x1e"):
            line = line.lstrip("\x1e").strip()
            if not line:
                continue
        try:
            feat = json.loads(line)
            record = process_postal_code_feature(feat, continent=args.continent, country_code=args.country_code)
            if record:
                sys.stdout.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
        except Exception:
            continue

    sys.stderr.write(f"[POSTAL_CODES] Processed and emitted {count} postal code features for CC='{args.country_code}'\n")


if __name__ == "__main__":
    main()
