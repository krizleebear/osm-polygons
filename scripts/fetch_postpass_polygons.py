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

try:
    from filter_polygons import derive_area_type
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from filter_polygons import derive_area_type


POSTPASS_URL = "https://postpass.geofabrik.de/api/interpreter"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "osm-polygons-reference/1.0"

_OSM_TYPE_MAP = {
    "r": "relation",
    "relation": "relation",
    "w": "way",
    "way": "way",
    "n": "node",
    "node": "node",
}


def normalize_osm_type(raw_type, default="relation"):
    """Normalize short or long OSM element type codes to 'relation', 'way', or 'node'."""
    val = str(raw_type or "").strip().lower()
    if not val:
        return default
    return _OSM_TYPE_MAP.get(val, val)


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
            props = feat.get("properties") or {}
            osm_id = feat.get("id") or props.get("osm_id") or props.get("@id")
            if osm_id is not None and feat.get("geometry"):
                cached[int(abs(int(osm_id)))] = feat
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


def _query_postpass_table(sql, timeout=30):
    """Execute a non-GeoJSON Postpass SQL query and return decoded result rows."""
    params = urllib.parse.urlencode({"data": sql.strip(), "options[geojson]": "false"})
    url = f"{POSTPASS_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("result") or []


def query_postpass_centres(rel_ids, timeout=30):
    """Batch-fetch admin_centre and label node metadata for a list of relation IDs."""
    ids = sorted({int(abs(int(r))) for r in (rel_ids or [])})
    if not ids:
        return {}

    id_list = ", ".join(str(r) for r in ids)
    sql = f"""
    SELECT r.id AS rel_id,
           (m->>'ref')::bigint AS node_id,
           m->>'role' AS role,
           ST_X(p.geom) AS lon,
           ST_Y(p.geom) AS lat,
           p.tags AS node_tags
    FROM planet_osm_rels r
    CROSS JOIN LATERAL jsonb_array_elements(r.members) AS m
    JOIN postpass_point p ON p.osm_type = 'N' AND p.osm_id = (m->>'ref')::bigint
    WHERE r.id IN ({id_list})
      AND m->>'type' = 'N'
      AND m->>'role' IN ('admin_centre', 'admin_center', 'label')
    ORDER BY r.id, (m->>'ref')::bigint
    """
    rows = _query_postpass_table(sql, timeout=timeout)

    resolved = {}
    for row in rows:
        raw_rel = row.get("rel_id")
        raw_node = row.get("node_id")
        lon = row.get("lon")
        lat = row.get("lat")
        if raw_rel is None or raw_node is None or lon is None or lat is None:
            continue

        rel_id = int(raw_rel)
        node_id = int(raw_node)
        role = str(row.get("role") or "").strip().lower()
        role_key = "label" if role == "label" else "admin_centre"

        rel_entry = resolved.setdefault(rel_id, {})
        if role_key in rel_entry:
            continue

        tags = row.get("node_tags") or {}
        if isinstance(tags, str):
            tags = json.loads(tags) if tags.strip() else {}

        name = tags.get("name")
        if not name or str(name).strip() == "" or str(name).lower() == "null":
            fallback = tags.get("name:en") or tags.get("official_name")
            name = str(fallback).strip() if fallback and str(fallback).strip() else None
        else:
            name = str(name).strip()

        names = {
            k: str(v).strip()
            for k, v in tags.items()
            if k.startswith("name:") and v and str(v).strip()
        }
        names_json = json.dumps(names, ensure_ascii=False) if names else None

        wikidata = tags.get("wikidata")
        if wikidata:
            wikidata = str(wikidata).strip()
            if wikidata.lower() in ("none", "null", ""):
                wikidata = None

        rel_entry[role_key] = {
            "lon": float(lon),
            "lat": float(lat),
            "osm_type": "node",
            "osm_id": node_id,
            "source_id": f"osm:node/{node_id}",
            "name": name,
            "names_json": names_json,
            "wikidata": wikidata,
        }
    return resolved


def query_postpass_parents(rel_ids, candidates_by_id=None, timeout=30):
    """Batch-fetch direct parent relation metadata among candidate relations."""
    ids = sorted({int(abs(int(r))) for r in (rel_ids or [])})
    if not ids:
        return {}

    id_list = ", ".join(str(r) for r in ids)
    sql = f"""
    SELECT r.id AS parent_id,
           r.tags->>'name' AS parent_name,
           r.tags->>'name:en' AS parent_name_en,
           r.tags->>'admin_level' AS parent_lvl,
           r.tags->>'ISO3166-2' AS parent_iso2,
           (m->>'ref')::bigint AS child_id
    FROM planet_osm_rels r
    CROSS JOIN LATERAL jsonb_array_elements(r.members) AS m
    WHERE r.id IN ({id_list})
      AND m->>'type' = 'R'
      AND (m->>'ref')::bigint IN ({id_list})
    """
    rows = _query_postpass_table(sql, timeout=timeout)

    best_by_child = {}
    for row in rows:
        raw_parent = row.get("parent_id")
        raw_child = row.get("child_id")
        if raw_parent is None or raw_child is None:
            continue
        parent_id = int(raw_parent)
        child_id = int(raw_child)
        if parent_id == child_id:
            continue

        raw_plvl = str(row.get("parent_lvl") or "").strip()
        parent_lvl_num = int(raw_plvl) if raw_plvl.isdigit() else None

        if candidates_by_id and child_id in candidates_by_id:
            raw_clvl = str(candidates_by_id[child_id].get("admin_level") or "").strip()
            if raw_clvl.isdigit() and parent_lvl_num is not None:
                if parent_lvl_num >= int(raw_clvl):
                    continue

        score = parent_lvl_num if parent_lvl_num is not None else -1
        prev = best_by_child.get(child_id)
        if prev is not None and score <= prev[0]:
            continue

        parent_name = str(row.get("parent_name") or row.get("parent_name_en") or "").strip()
        parent_iso2 = str(row.get("parent_iso2") or "").strip()
        best_by_child[child_id] = (
            score,
            {
                "parent_osm_id": parent_id,
                "parent_name": parent_name,
                "parent_iso3166_2": parent_iso2,
            },
        )

    return {child_id: info for child_id, (_, info) in best_by_child.items()}


def infer_candidate_parents(candidates, features_by_id, explicit_parents):
    """Fill missing non-L2 parent links from L2 candidates via ISO3166-2 prefix or country code."""
    parents = {int(k): dict(v) for k, v in (explicit_parents or {}).items()}
    l2_by_cc = {}

    for cand in candidates:
        cid = int(cand["osm_id"])
        feat = (features_by_id or {}).get(cid) or {}
        props = feat.get("properties") or {}
        tags = props.get("tags") if isinstance(props.get("tags"), dict) else {}
        lvl_str = str(props.get("admin_level") or tags.get("admin_level") or cand.get("admin_level") or "").strip()
        if lvl_str != "2":
            continue

        l2_name = str(props.get("name") or tags.get("name") or cand.get("name") or "").strip()
        for raw_cc in (cand.get("country"), props.get("ISO3166-1"), tags.get("ISO3166-1")):
            cc = str(raw_cc or "").strip().upper()
            if cc and cc not in l2_by_cc:
                l2_by_cc[cc] = (cid, l2_name)

    for cand in candidates:
        cid = int(cand["osm_id"])
        if cid in parents:
            continue
        lvl_str = str(cand.get("admin_level") or "").strip()
        if lvl_str == "2":
            continue

        feat = (features_by_id or {}).get(cid) or {}
        props = feat.get("properties") or {}
        tags = props.get("tags") if isinstance(props.get("tags"), dict) else {}
        iso2 = str(props.get("ISO3166-2") or tags.get("ISO3166-2") or "").strip().upper()
        cc = iso2.split("-", 1)[0] if "-" in iso2 else ""
        if not cc and lvl_str == "4":
            cc = str(cand.get("country") or "").strip().upper()
        if cc and cc in l2_by_cc:
            l2_id, l2_name = l2_by_cc[cc]
            if l2_id != cid:
                parents[cid] = {
                    "parent_osm_id": l2_id,
                    "parent_name": l2_name,
                    "parent_iso3166_2": "",
                }

    return parents


def format_feature(feature, fallback_info, *, relation_centres=None, parent_info=None):
    """Flatten tags, normalize OSM identity/country fields, and apply centres/parent enrichment."""
    raw_props = feature.get("properties") or {}
    props = dict(raw_props)
    geom = feature.get("geometry")

    tags = props.pop("tags", None)
    if isinstance(tags, dict):
        flat_props = {**dict(tags), **props}
    else:
        flat_props = dict(props)

    raw_id = (
        flat_props.get("osm_id")
        or flat_props.get("@id")
        or flat_props.get("id")
        or feature.get("id")
        or fallback_info.get("osm_id")
    )
    osm_id = int(abs(int(raw_id))) if raw_id is not None else None
    if osm_id is not None:
        flat_props["osm_id"] = osm_id
        flat_props["id"] = osm_id
        flat_props["@id"] = osm_id

    osm_type = normalize_osm_type(
        flat_props.get("@type") or flat_props.get("osm_type"),
        default="relation",
    )
    flat_props["osm_type"] = osm_type
    flat_props["@type"] = osm_type

    if not flat_props.get("admin_level") and "admin_level" in fallback_info:
        flat_props["admin_level"] = str(fallback_info["admin_level"])
    level_str = str(flat_props.get("admin_level") or "").strip()

    fallback_country = fallback_info.get("country")
    if level_str == "2":
        if not flat_props.get("ISO3166-1") and fallback_country:
            flat_props["ISO3166-1"] = fallback_country
    else:
        if fallback_country and flat_props.get("ISO3166-1") == fallback_country:
            flat_props.pop("ISO3166-1", None)

    flat_props["area_type"] = derive_area_type(flat_props, level_str)

    raw_name = flat_props.get("name")
    if not raw_name or str(raw_name).strip() == "" or str(raw_name).lower() == "null":
        fallback_name = (
            flat_props.get("name:en")
            or flat_props.get("official_name")
            or fallback_info.get("name")
            or flat_props.get("ISO3166-2")
            or flat_props.get("ISO3166-1")
        )
        if fallback_name and str(fallback_name).strip():
            flat_props["name"] = str(fallback_name).strip()

    if osm_id is not None and relation_centres and osm_id in relation_centres:
        centres = relation_centres[osm_id]
        if "admin_centre" in centres:
            c = centres["admin_centre"]
            flat_props["admin_centre:lat"] = c["lat"]
            flat_props["admin_centre:lon"] = c["lon"]
            flat_props["admin_centre:osm_type"] = c.get("osm_type", "node")
            if c.get("osm_id") is not None:
                flat_props["admin_centre:osm_id"] = c["osm_id"]
            if c.get("source_id"):
                flat_props["admin_centre:source_id"] = c["source_id"]
            if c.get("name"):
                flat_props["admin_centre:name"] = c["name"]
            if c.get("names_json"):
                flat_props["admin_centre:names_json"] = c["names_json"]
            if c.get("wikidata"):
                flat_props["admin_centre:wikidata"] = c["wikidata"]

        if "label" in centres:
            l = centres["label"]
            flat_props["label:lat"] = l["lat"]
            flat_props["label:lon"] = l["lon"]

        if "admin_centre" in centres:
            c = centres["admin_centre"]
            flat_props["center_lat"] = c["lat"]
            flat_props["center_lon"] = c["lon"]
        elif "label" in centres:
            l = centres["label"]
            flat_props["center_lat"] = l["lat"]
            flat_props["center_lon"] = l["lon"]

    if parent_info and level_str != "2":
        flat_props["parent_osm_id"] = int(parent_info["parent_osm_id"])
        if parent_info.get("parent_iso3166_2"):
            flat_props["parent_iso3166_2"] = parent_info["parent_iso3166_2"]
        if parent_info.get("parent_name"):
            flat_props["parent_name"] = parent_info["parent_name"]

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

    raw_records = []
    features_by_id = {}
    reused_count = 0
    fetched_count = 0
    fallback_count = 0

    for item in candidates:
        rel_id = int(item["osm_id"])
        name = item.get("name", "Unknown")
        country = item.get("country", "--")
        lvl = item.get("admin_level", "?")

        feature = None
        source = "postpass"
        if args.reuse_cached and rel_id in cached_refs:
            feature = cached_refs[rel_id]
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
                        feature = raw_feat
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
            elif rel_id in cached_refs:
                feature = cached_refs[rel_id]
                source = "cached_fallback"
                fallback_count += 1
                print(
                    f"##vso[task.logissue type=warning][{country}] Postpass fetch failed for Rel {rel_id} ({name}); falling back to cached reference polygon."
                )
            else:
                sys.exit(
                    f"ERROR: Failed to fetch reference polygon for Rel {rel_id} ({name}, {country}) after {args.retry_count} attempts, and no cached fallback was available."
                )

        raw_records.append((item, feature, source))
        features_by_id[rel_id] = feature

    all_ids = [int(c["osm_id"]) for c in candidates]
    candidates_by_id = {int(c["osm_id"]): c for c in candidates}

    if args.reuse_cached:
        need_centres = [
            rid for rid, feat in features_by_id.items()
            if (feat.get("properties") or {}).get("center_lat") is None
        ]
        need_parents = [
            rid for rid, feat in features_by_id.items()
            if candidates_by_id[rid].get("admin_level") != 2
            and (feat.get("properties") or {}).get("parent_osm_id") is None
        ]
    else:
        need_centres = all_ids
        need_parents = [rid for rid in all_ids if candidates_by_id[rid].get("admin_level") != 2]

    relation_centres = {}
    if need_centres:
        try:
            relation_centres = query_postpass_centres(need_centres)
            print(
                f"Enriched admin_centre/label metadata for {len(relation_centres)}/{len(need_centres)} relation(s) via Postpass."
            )
        except Exception as exc:
            print(
                f"##vso[task.logissue type=warning]Failed to batch-fetch admin_centre/label metadata from Postpass: {exc}"
            )

    explicit_parents = {}
    if need_parents:
        try:
            explicit_parents = query_postpass_parents(all_ids, candidates_by_id=candidates_by_id)
        except Exception as exc:
            print(
                f"##vso[task.logissue type=warning]Failed to batch-fetch parent hierarchy from Postpass: {exc}"
            )
    parents = infer_candidate_parents(candidates, features_by_id, explicit_parents)

    success_features = []
    manifest = []
    for item, raw_feat, source in raw_records:
        rel_id = int(item["osm_id"])
        formatted = format_feature(
            raw_feat,
            item,
            relation_centres=relation_centres,
            parent_info=parents.get(rel_id),
        )
        success_features.append(formatted)
        manifest.append(
            {
                "osm_id": item["osm_id"],
                "name": item.get("name", "Unknown"),
                "country": item.get("country", "--"),
                "admin_level": item.get("admin_level", "?"),
                "geometry_type": (formatted.get("geometry") or {}).get("type"),
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
