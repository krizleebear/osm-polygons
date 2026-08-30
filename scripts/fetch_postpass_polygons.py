#!/usr/bin/env python3
"""
Pre-fetch intact OpenStreetMap administrative boundary polygon geometries
from Postpass (postpass.geofabrik.de) and cache them as a GeoJSONSeq artifact.

Rationale (see doc/POSTPASS_REFERENCE_FALLBACK.md):
  * Geofabrik country extracts clip boundary ways at extract borders, breaking
    cross-border and coastal multipolygon relations during osmium export.
  * Postpass operates on a complete, unclipped planet database.
  * The indexed `postpass_polygon` table returns pre-assembled multipolygon geometries
    with 100% native OSM tags in <0.3s per relation.

Usage:
    python3 scripts/fetch_postpass_polygons.py \
      --candidates scripts/postpass-candidates.json \
      --out-dir reference-polygons

Output:
    <out-dir>/reference-polygons.geojsonseq   -- Complete GeoJSON features (id, properties, geometry)
    <out-dir>/manifest.json                  -- Metadata list of successfully fetched relations
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request


POSTPASS_URL = "https://postpass.geofabrik.de/api/interpreter"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "osm-polygons-reference/1.0"


def load_candidates(candidates_path):
    with open(candidates_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("candidates", [])


def query_postpass_polygon(rel_id, timeout=20):
    """Query the indexed postpass_polygon table for a relation."""
    sql = f"""
    SELECT osm_type, osm_id, tags, geom 
    FROM postpass_polygon 
    WHERE osm_type = 'R' AND osm_id = {rel_id}
    """
    params = urllib.parse.urlencode({"data": sql.strip()})
    url = f"{POSTPASS_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    features = data.get("features", [])
    if not features:
        return None
    return features[0]


def format_feature(feature, fallback_info):
    """Flatten tags into top-level properties and ensure standard id/osm_id."""
    props = feature.get("properties", {})
    geom = feature.get("geometry")

    tags = props.pop("tags", {})
    flat_props = dict(props)
    if isinstance(tags, dict):
        flat_props = {**tags, **flat_props}

    osm_id = flat_props.get("osm_id") or fallback_info.get("osm_id")
    if osm_id is not None:
        flat_props["osm_id"] = int(abs(osm_id))
        flat_props["id"] = int(abs(osm_id))

    if "admin_level" not in flat_props and "admin_level" in fallback_info:
        flat_props["admin_level"] = str(fallback_info["admin_level"])

    if "ISO3166-1" not in flat_props and "country" in fallback_info:
        flat_props["ISO3166-1"] = fallback_info["country"]

    return {
        "type": "Feature",
        "id": flat_props.get("id"),
        "properties": flat_props,
        "geometry": geom,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Pre-fetch reference polygons from Postpass API"
    )
    parser.add_argument(
        "--candidates",
        required=True,
        help="Path to postpass-candidates.json",
    )
    parser.add_argument(
        "--out-dir",
        default="reference-polygons",
        help="Output directory for reference polygons",
    )
    parser.add_argument(
        "--retry-count",
        type=int,
        default=3,
        help="Number of retries per relation on failure",
    )
    args = parser.parse_args()

    candidates = load_candidates(args.candidates)
    if not candidates:
        sys.exit(f"ERROR: No candidates found in {args.candidates}")

    os.makedirs(args.out_dir, exist_ok=True)
    geojsonseq_path = os.path.join(args.out_dir, "reference-polygons.geojsonseq")
    manifest_path = os.path.join(args.out_dir, "manifest.json")

    print(f"Fetching {len(candidates)} reference boundary relations from Postpass...")

    success_features = []
    manifest = []

    for item in candidates:
        rel_id = item["osm_id"]
        name = item.get("name", "Unknown")
        country = item.get("country", "--")
        lvl = item.get("admin_level", "?")

        feature = None
        for attempt in range(1, args.retry_count + 1):
            try:
                t0 = time.time()
                raw_feat = query_postpass_polygon(rel_id)
                dt = time.time() - t0
                if raw_feat and raw_feat.get("geometry"):
                    feature = format_feature(raw_feat, item)
                    geom_type = feature["geometry"].get("type")
                    print(
                        f"[{country}] OK (Rel {rel_id}, Lvl {lvl}, {name}) -> {geom_type} ({dt:.2f}s)"
                    )
                    break
                else:
                    print(
                        f"[{country}] Warn: Rel {rel_id} returned empty from postpass_polygon (attempt {attempt}/{args.retry_count})"
                    )
            except Exception as e:
                print(
                    f"[{country}] Attempt {attempt}/{args.retry_count} failed for Rel {rel_id}: {e}"
                )
                time.sleep(2)

        if not feature:
            sys.exit(
                f"ERROR: Failed to fetch reference polygon for Rel {rel_id} ({name}, {country}) after {args.retry_count} attempts."
            )

        success_features.append(feature)
        manifest.append(
            {
                "osm_id": rel_id,
                "name": name,
                "country": country,
                "admin_level": lvl,
                "geometry_type": feature["geometry"].get("type"),
            }
        )

    # Write .geojsonseq
    with open(geojsonseq_path, "w", encoding="utf-8") as f:
        for feat in success_features:
            f.write(json.dumps(feat, ensure_ascii=False) + "\n")

    # Write manifest.json
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "total_count": len(manifest),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "candidates": manifest,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    size_mb = os.path.getsize(geojsonseq_path) / (1024 * 1024)
    print(
        f"\nSUCCESS: Successfully exported {len(success_features)} reference polygons to {geojsonseq_path} ({size_mb:.2f} MB)"
    )


if __name__ == "__main__":
    main()
