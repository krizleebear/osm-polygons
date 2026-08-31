#!/usr/bin/env python3
"""
Filter and structure OSM Facilities GeoJSON stream for osm-facilities.parquet export.

Classifies features into normalized feature classes:
  - motorway (LineString, MultiLineString)
  - junction (Point)
  - service_area (Polygon, MultiPolygon)
  - airport (Polygon, MultiPolygon)
  - shopping_mall (Polygon, MultiPolygon)
  - university (Polygon, MultiPolygon)
  - hospital (Polygon, MultiPolygon)
  - stadium (Polygon, MultiPolygon)

Filters tags to prune metadata (source, created_by, note, fixme, etc.) and retains
domain tags, multilingual names, and references.
"""

import argparse
import json
import os
import sys

# Metadata tags to exclude from the output tags JSON
EXCLUDED_TAG_KEYS = {
    "source", "created_by", "note", "fixme", "FIXME", "check_date",
    "import_uuid", "odbl", "attribution", "source_ref"
}

EXCLUDED_TAG_PREFIXES = (
    "source:", "note:", "tiger:", "yh:", "osak:", "gnis:", "nhd:",
    "naptan:", "ref:bag", "ref:ruian", "addr:country"
)

MOTORWAY_VALUES = {"motorway", "trunk", "motorway_link", "trunk_link"}
SERVICE_AREA_VALUES = {"services", "rest_area"}


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


def normalize_osm_type(raw_type):
    """Normalize raw OSM type string or character to 'N', 'W', or 'R'."""
    if not raw_type:
        return None
    raw_str = str(raw_type).strip().upper()
    if raw_str in ("N", "NODE"):
        return "N"
    if raw_str in ("W", "WAY"):
        return "W"
    if raw_str in ("R", "RELATION", "MULTIPOLYGON"):
        return "R"
    return None


def classify_facility(props, geom_type):
    """
    Classify OSM feature into one of the 8 target feature_classes based on tags and geometry.
    Returns (feature_class, is_valid_geometry) or (None, False).
    """
    if not props or not geom_type:
        return None, False

    highway = str(props.get("highway", "")).strip().lower()
    aeroway = str(props.get("aeroway", "")).strip().lower()
    shop = str(props.get("shop", "")).strip().lower()
    amenity = str(props.get("amenity", "")).strip().lower()
    leisure = str(props.get("leisure", "")).strip().lower()

    # 1. motorway: LineString / MultiLineString
    if highway in MOTORWAY_VALUES:
        if geom_type in ("LineString", "MultiLineString"):
            return "motorway", True
        return "motorway", False

    # 2. junction: Point
    if highway == "motorway_junction":
        if geom_type == "Point":
            return "junction", True
        return "junction", False

    # 3. service_area: Polygon / MultiPolygon
    if highway in SERVICE_AREA_VALUES:
        if geom_type in ("Polygon", "MultiPolygon"):
            return "service_area", True
        return "service_area", False

    # 4. airport: Polygon / MultiPolygon
    if aeroway == "aerodrome":
        if geom_type in ("Polygon", "MultiPolygon"):
            return "airport", True
        return "airport", False

    # 5. shopping_mall: Polygon / MultiPolygon
    if shop == "mall":
        if geom_type in ("Polygon", "MultiPolygon"):
            return "shopping_mall", True
        return "shopping_mall", False

    # 6. university: Polygon / MultiPolygon
    if amenity == "university":
        if geom_type in ("Polygon", "MultiPolygon"):
            return "university", True
        return "university", False

    # 7. hospital: Polygon / MultiPolygon
    if amenity == "hospital":
        if geom_type in ("Polygon", "MultiPolygon"):
            return "hospital", True
        return "hospital", False

    # 8. stadium: Polygon / MultiPolygon
    if leisure == "stadium":
        if geom_type in ("Polygon", "MultiPolygon"):
            return "stadium", True
        return "stadium", False

    return None, False


def process_facility_feature(data, continent="", country_code=""):
    """
    Process a single GeoJSON feature from osmium export stream.
    Returns structured dict or None if invalid/skipped.
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
    if not geom_type:
        return None

    feature_class, is_valid_geom = classify_facility(props, geom_type)
    if not feature_class or not is_valid_geom:
        return None

    raw_osm_id = props.get("@id") or data.get("id")
    if raw_osm_id is None:
        return None

    try:
        osm_id = int(raw_osm_id)
    except (ValueError, TypeError):
        return None

    raw_type = props.get("@type")
    osm_type = normalize_osm_type(raw_type)
    if not osm_type:
        # Fallback heuristic based on geometry type if @type is missing
        if geom_type == "Point":
            osm_type = "N"
        elif geom_type in ("LineString", "MultiLineString"):
            osm_type = "W"
        elif geom_type in ("Polygon", "MultiPolygon"):
            osm_type = "W"  # Most polygon features from osmium export are closed ways or relations
        else:
            osm_type = "W"

    cleaned_tags = clean_tags(props)

    return {
        "continent": continent,
        "country_code": country_code,
        "osm_id": osm_id,
        "osm_type": osm_type,
        "feature_class": feature_class,
        "geom_json": json.dumps(geom),
        "tags": json.dumps(cleaned_tags, ensure_ascii=False),
    }


def main():
    parser = argparse.ArgumentParser(description="Filter and structure OSM facilities GeoJSON stream.")
    parser.add_argument("--country-code", default="", help="Optional country code to inject into records")
    parser.add_argument("--continent", default="", help="Optional continent to inject into records")
    args = parser.parse_args()

    count = 0
    for line in sys.stdin:
        line = line.strip()
        if not line or line.startswith("\x1e"):
            # Skip empty lines or RS (record separator) bytes
            line = line.lstrip("\x1e").strip()
            if not line:
                continue
        try:
            feat = json.loads(line)
            record = process_facility_feature(feat, continent=args.continent, country_code=args.country_code)
            if record:
                sys.stdout.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
        except Exception:
            continue

    sys.stderr.write(f"[FACILITIES] Processed and emitted {count} facility features for CC='{args.country_code}'\n")


if __name__ == "__main__":
    main()
