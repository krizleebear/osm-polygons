#!/usr/bin/env python3
"""
Filter and structure OSM Landuse GeoJSON stream for osm-landuse.parquet export.

Extracts built-up landuse areas (residential, commercial, retail) as Polygon and
MultiPolygon geometries, cleans metadata tags, extracts names and produces a
flattened JSONL stream.
"""

import argparse
import json
import sys

LANDUSE_WHITELIST = {"residential", "commercial", "retail"}

EXCLUDED_TAG_KEYS = {
    "source", "created_by", "note", "fixme", "FIXME", "check_date",
    "import_uuid", "odbl", "attribution", "source_ref"
}

EXCLUDED_TAG_PREFIXES = (
    "source:", "note:", "tiger:", "yh:", "osak:", "gnis:", "nhd:",
    "naptan:", "ref:bag", "ref:ruian"
)


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


def process_landuse_feature(data, continent="", country_code=""):
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

    landuse_val = str(props.get("landuse", "")).strip().lower()
    if not landuse_val or landuse_val not in LANDUSE_WHITELIST:
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

    name_en = props.get("name:en")
    if name_en is not None:
        name_en = str(name_en).strip()
        if not name_en or name_en.lower() == "null":
            name_en = None

    cleaned_tags = clean_tags(props)

    return {
        "continent": continent,
        "country_code": country_code,
        "osm_id": osm_id,
        "osm_type": osm_type,
        "landuse": landuse_val,
        "name": name,
        "name_en": name_en,
        "geom_json": json.dumps(geom),
        "tags": json.dumps(cleaned_tags, ensure_ascii=False),
    }


def main():
    parser = argparse.ArgumentParser(description="Filter and structure OSM landuse GeoJSON stream.")
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
            record = process_landuse_feature(feat, continent=args.continent, country_code=args.country_code)
            if record:
                sys.stdout.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
        except Exception:
            continue

    sys.stderr.write(f"[LANDUSE] Processed and emitted {count} landuse features for CC='{args.country_code}'\n")


if __name__ == "__main__":
    main()
