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


def load_cached_reference(reference_path):
    """Load previously cached reference polygons keyed by integer osm_id."""
    cached = {}
    if not reference_path or not os.path.exists(reference_path):
        return cached
    with open(reference_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            feat = json.loads(line)
            osm_id = feat.get("id") or feat.get("properties", {}).get("osm_id")
            if osm_id is not None and feat.get("geometry"):
                cached[int(osm_id)] = feat
    return cached


def load_cached_references(reference_paths):
    """Load cached reference polygons from multiple paths in priority order."""
    cached = {}
    if not reference_paths:
        return cached
    for path in reference_paths:
        if not path:
            continue
        for osm_id, feat in load_cached_reference(path).items():
            if osm_id not in cached:
                cached[osm_id] = feat
    return cached


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
    parser.add_argument(
        "--fallback-reference",
        action="append",
        default=[],
        help="Optional path(s) to previously cached reference-polygons.geojsonseq for reuse or fallback on Postpass failure",
    )
    parser.add_argument(
        "--reuse-cached",
        action="store_true",
        default=False,
        help="Reuse matching polygons directly from --fallback-reference caches and query Postpass only for missing candidates",
    )
    args = parser.parse_args()

    candidates = load_candidates(args.candidates)
    if not candidates:
        sys.exit(f"ERROR: No candidates found in {args.candidates}")

    cached_refs = load_cached_references(args.fallback_reference)
    if cached_refs:
        sources = ", ".join(p for p in args.fallback_reference if p)
        print(f"Loaded {len(cached_refs)} cached reference polygons from: {sources}")

    os.makedirs(args.out_dir, exist_ok=True)
    geojsonseq_path = os.path.join(args.out_dir, "reference-polygons.geojsonseq")
    manifest_path = os.path.join(args.out_dir, "manifest.json")

    print(f"Fetching {len(candidates)} reference boundary relations from Postpass...")

    success_features = []
    manifest = []
    reused_count = 0
    fetched_count = 0
    fallback_count = 0

    for item in candidates:
        rel_id = item["osm_id"]
        name = item.get("name", "Unknown")
        country = item.get("country", "--")
        lvl = item.get("admin_level", "?")

        feature = None
        source = "postpass"
        if args.reuse_cached and int(rel_id) in cached_refs:
            feature = cached_refs[int(rel_id)]
            source = "cached"
            reused_count += 1
            print(
                f"[{country}] CACHED (Rel {rel_id}, Lvl {lvl}, {name}) -> reused from reference cache"
            )
        else:
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

            if feature:
                fetched_count += 1
            elif int(rel_id) in cached_refs:
                feature = cached_refs[int(rel_id)]
                source = "cached_fallback"
                fallback_count += 1
                print(
                    f"##vso[task.logissue type=warning][{country}] Postpass fetch failed for Rel {rel_id} ({name}); falling back to cached reference polygon."
                )
            else:
                sys.exit(
                    f"ERROR: Failed to fetch reference polygon for Rel {rel_id} ({name}, {country}) after {args.retry_count} attempts, and no cached fallback was available."
                )

        success_features.append(feature)
        manifest.append(
            {
                "osm_id": rel_id,
                "name": name,
                "country": country,
                "admin_level": lvl,
                "geometry_type": feature["geometry"].get("type"),
                "source": source,
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
                "reused_count": reused_count,
                "fetched_count": fetched_count,
                "fallback_count": fallback_count,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "candidates": manifest,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    if fallback_count > 0:
        print(
            f"##vso[task.logissue type=warning]Postpass fallback active: {fallback_count}/{len(success_features)} reference polygons were served from the previous cache."
        )

    size_mb = os.path.getsize(geojsonseq_path) / (1024 * 1024)
    print(
        f"\nSUCCESS: Successfully exported {len(success_features)} reference polygons to {geojsonseq_path} "
        f"({size_mb:.2f} MB; reused={reused_count}, fetched={fetched_count}, fallback={fallback_count})"
    )


if __name__ == "__main__":
    main()
