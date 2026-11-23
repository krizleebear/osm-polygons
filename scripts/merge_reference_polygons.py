#!/usr/bin/env python3
"""
Merge intact Postpass reference polygons into a region's GeoJSON stream.

Rationale (see doc/POSTPASS_REFERENCE_FALLBACK.md):
  * For a given region (e.g. US, Spain, China), some major relations are either:
    - COMPLETELY MISSING from the osmium export stream (because cross-border ways broke the multipolygon), or
    - DAMAGED / TRUNCATED by the Geofabrik regional bounding box.
  * This script reads the region's `*.admin-polygons.geojsonseq` and the global `reference-polygons.geojsonseq`,
    filters candidates by `--country-code`, and:
    - INSERTS the candidate if it is missing from the stream.
    - REPLACES the candidate's geometry if the stream version is truncated/damaged.

Usage:
    python3 scripts/merge_reference_polygons.py <input-stream.geojsonseq> \
      --reference reference-polygons/reference-polygons.geojsonseq \
      --candidates scripts/postpass-candidates.json \
      --country-code <CC> \
      --out <output-stream.geojsonseq>
"""

import argparse
import json
import os
import sys

try:
    from filter_polygons import derive_area_type
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from filter_polygons import derive_area_type


ENRICHMENT_KEYS = (
    "area_type",
    "center_lat",
    "center_lon",
    "admin_centre:lat",
    "admin_centre:lon",
    "admin_centre:osm_type",
    "admin_centre:osm_id",
    "admin_centre:source_id",
    "admin_centre:name",
    "admin_centre:names_json",
    "admin_centre:wikidata",
    "label:lat",
    "label:lon",
    "parent_osm_id",
    "parent_iso3166_2",
    "parent_name",
    "admin_level_mapped",
    "is_territorial_sea",
)

_OSM_TYPE_MAP = {
    "r": "relation",
    "relation": "relation",
    "w": "way",
    "way": "way",
    "n": "node",
    "node": "node",
}


MULTI_COUNTRY_HOSTS = {
    "AE": ("AE", "SA", "QA", "KW", "OM", "BH"),
    "MY": ("MY", "SG", "BN"),
    "HT": ("HT", "DO"),
    "SN": ("SN", "GM"),
    "IL": ("IL", "PS"),
}


def load_candidates(candidates_path, country_code):
    with open(candidates_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    candidates = data.get("candidates", [])
    raw_codes = [c.strip().upper() for c in str(country_code).split(",") if c.strip()]
    target_codes = set()
    for code in raw_codes:
        target_codes.update(MULTI_COUNTRY_HOSTS.get(code, (code,)))
    # Return mapping of int osm_id -> candidate metadata matching target country codes
    return {
        int(c["osm_id"]): c
        for c in candidates
        if c.get("country", "").upper() in target_codes
    }


def extract_osm_id(feat):
    """Extract integer OSM ID from Postpass or osmium-export GeoJSON features."""
    props = feat.get("properties") or {}
    for raw in (feat.get("id"), props.get("osm_id"), props.get("@id"), props.get("id")):
        if raw is not None and raw != "":
            try:
                return int(abs(int(raw)))
            except (ValueError, TypeError):
                continue
    return None


def is_relation_feature(feat):
    """Return True when feature represents an OSM relation (or omits element type)."""
    props = feat.get("properties") or {}
    raw_type = props.get("@type")
    if raw_type is None:
        raw_type = props.get("osm_type")
    elem_type = str(raw_type or "").strip().lower()
    return elem_type in ("", "relation", "r")


def normalize_and_enrich_reference(ref_feat, candidate_meta, existing_feat=None, stream_l2_by_cc=None):
    """Defensively normalize OSM identity/ISO fields and preserve stream/L2 enrichment."""
    out = dict(ref_feat)
    props = dict(ref_feat.get("properties") or {})
    out["properties"] = props

    osm_id = extract_osm_id(ref_feat)
    if osm_id is None and candidate_meta and candidate_meta.get("osm_id") is not None:
        osm_id = int(abs(int(candidate_meta["osm_id"])))
    if osm_id is not None:
        props["osm_id"] = osm_id
        props["id"] = osm_id
        props["@id"] = osm_id
        out["id"] = osm_id

    raw_type = str(props.get("@type") or props.get("osm_type") or "").strip().lower()
    norm_type = _OSM_TYPE_MAP.get(raw_type, raw_type) if raw_type else "relation"
    props["osm_type"] = norm_type
    props["@type"] = norm_type

    if not props.get("admin_level") and candidate_meta and "admin_level" in candidate_meta:
        props["admin_level"] = str(candidate_meta["admin_level"])
    level_str = str(props.get("admin_level") or "").strip()

    raw_boundary = str(props.get("boundary") or "").strip().lower()
    if not raw_boundary or raw_boundary == "disputed":
        props["boundary"] = "administrative"

    cand_country = candidate_meta.get("country") if candidate_meta else None
    if level_str != "2" and cand_country and props.get("ISO3166-1") == cand_country:
        props.pop("ISO3166-1", None)

    if existing_feat:
        existing_props = existing_feat.get("properties") or {}
        for key in ENRICHMENT_KEYS:
            existing_val = existing_props.get(key)
            if existing_val is None or existing_val == "":
                continue
            if key == "area_type":
                if not props.get("area_type") or (
                    props.get("area_type") == "administrative" and existing_val != "administrative"
                ):
                    props["area_type"] = existing_val
            elif props.get(key) is None or props.get(key) == "":
                props[key] = existing_val

    if not props.get("area_type") or props.get("area_type") == "administrative":
        props["area_type"] = derive_area_type(props, level_str)

    if level_str != "2" and not props.get("parent_osm_id") and stream_l2_by_cc:
        iso2 = str(props.get("ISO3166-2") or "").strip().upper()
        cc = iso2.split("-", 1)[0] if "-" in iso2 else ""
        if not cc and level_str == "4" and cand_country:
            cc = str(cand_country).strip().upper()
        if cc and cc in stream_l2_by_cc:
            l2_id, l2_name = stream_l2_by_cc[cc]
            if l2_id != osm_id:
                props["parent_osm_id"] = l2_id
                if l2_name and not props.get("parent_name"):
                    props["parent_name"] = l2_name

    return out


def has_valid_polygonal_geometry(feat):
    """Return True if feat carries a GeoJSON Polygon or MultiPolygon geometry."""
    if not isinstance(feat, dict):
        return False
    geom = feat.get("geometry")
    if not isinstance(geom, dict):
        return False
    return geom.get("type") in ("Polygon", "MultiPolygon") and geom.get("coordinates") is not None


def load_reference_features(reference_path, candidate_ids):
    """Load matching reference GeoJSON features from reference-polygons.geojsonseq."""
    ref_map = {}
    if not os.path.exists(reference_path):
        sys.exit(f"ERROR: Reference polygon file not found: {reference_path}")

    with open(reference_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            feat = json.loads(line)
            osm_id = extract_osm_id(feat)
            if osm_id is not None and osm_id in candidate_ids and has_valid_polygonal_geometry(feat):
                ref_map[osm_id] = feat
    return ref_map


def main():
    parser = argparse.ArgumentParser(
        description="Merge intact Postpass reference polygons into a region stream"
    )
    parser.add_argument(
        "stream",
        help="Input region .geojsonseq file",
    )
    parser.add_argument(
        "--reference",
        required=True,
        help="Path to reference-polygons.geojsonseq",
    )
    parser.add_argument(
        "--candidates",
        required=True,
        help="Path to postpass-candidates.json",
    )
    parser.add_argument(
        "--country-code",
        required=True,
        help="ISO 2-letter country code for the current region (e.g. US, ES, FR)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path (default: overwrites input stream)",
    )
    args = parser.parse_args()

    candidates = load_candidates(args.candidates, args.country_code)
    if not candidates:
        print(f"[{args.country_code}] No reference candidates configured for this country. Skipping merge.")
        if args.out and args.out != args.stream:
            import shutil
            shutil.copyfile(args.stream, args.out)
        return

    candidate_ids = set(candidates.keys())
    ref_features = load_reference_features(args.reference, candidate_ids)

    missing_refs = candidate_ids - set(ref_features.keys())
    if missing_refs:
        sys.exit(
            f"ERROR: Candidates configured for {args.country_code} ({missing_refs}) but missing from {args.reference}"
        )

    out_path = args.out if args.out else args.stream + ".tmp"

    seen_ids = set()
    stream_l2_by_cc = {}
    replaced_count = 0
    inserted_count = 0
    total_stream_count = 0

    with open(args.stream, "r", encoding="utf-8") as in_fp, open(
        out_path, "w", encoding="utf-8"
    ) as out_fp:
        for line in in_fp:
            line = line.strip()
            if not line:
                continue
            feat = json.loads(line)
            props = feat.get("properties") or {}
            osm_id = extract_osm_id(feat)

            if is_relation_feature(feat) and osm_id is not None and str(props.get("admin_level") or "").strip() == "2":
                iso1 = str(props.get("ISO3166-1") or "").strip().upper()
                if iso1:
                    stream_l2_by_cc[iso1] = (osm_id, str(props.get("name") or "").strip())

            if is_relation_feature(feat) and osm_id is not None and osm_id in ref_features:
                total_stream_count += 1
                rel_id = osm_id
                seen_ids.add(rel_id)
                ref_feat = normalize_and_enrich_reference(
                    ref_features[rel_id],
                    candidates[rel_id],
                    existing_feat=feat,
                    stream_l2_by_cc=stream_l2_by_cc,
                )
                out_fp.write(json.dumps(ref_feat, ensure_ascii=False) + "\n")
                replaced_count += 1
                print(
                    f"[{args.country_code}] REPLACED relation {rel_id} ({candidates[rel_id].get('name')}) with intact reference geometry."
                )
            elif has_valid_polygonal_geometry(feat):
                total_stream_count += 1
                out_fp.write(line + "\n")

        # Insert candidates that were completely absent from the stream
        for rel_id in candidate_ids:
            if rel_id not in seen_ids:
                ref_feat = normalize_and_enrich_reference(
                    ref_features[rel_id],
                    candidates[rel_id],
                    stream_l2_by_cc=stream_l2_by_cc,
                )
                out_fp.write(json.dumps(ref_feat, ensure_ascii=False) + "\n")
                inserted_count += 1
                print(
                    f"[{args.country_code}] INSERTED missing relation {rel_id} ({candidates[rel_id].get('name')}) from reference dataset."
                )

    if not args.out:
        os.replace(out_path, args.stream)

    print(
        f"[{args.country_code}] Reference merge complete: {replaced_count} replaced, {inserted_count} inserted (total output features: {total_stream_count + inserted_count})"
    )


if __name__ == "__main__":
    main()
