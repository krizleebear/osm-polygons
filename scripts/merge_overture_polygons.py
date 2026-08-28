#!/usr/bin/env python3
"""
Merge Overture fallback geometries for genuinely broken admin relations.

Reads the region's filtered `*.admin-polygons.geojsonseq`, loads the Overture
fallback artifacts (fetched once by `fetch_overture_polygons.py`), and:

  * INSERTS features for candidate relations that are genuinely absent from
    the OSM-derived stream (Overture supplies geometry only; OSM metadata
    comes from the region `*-validation.json`).
  * REPLACES the geometry of candidate relations that ARE present but whose
    OSM-derived polygon is damaged by the Geofabrik region clipping. Damage is
    measured through DuckDB spatial against the Overture LAND geometry:
      - coverage  = area(present AND land) / area(land)   (truncation)
      - inside    = area(present AND land) / area(present) (maritime overreach)
    A feature is replaced when `coverage < --min-coverage` OR
    `inside < --min-inside` (defaults 0.95 / 0.90). Replaced features keep
    their original OSM properties; only the geometry is swapped.

RULES (see doc/OVERTURE_FALLBACK_PLAN.md):
  * Only candidates listed in `--candidates` whose Geofabrik country matches
    `--country-code` are considered.
  * The health check requires the `duckdb` CLI with the `spatial` extension
    (runs in the `osm2parquet` container). A missing duckdb binary is an
    explicit error, never a silent fallback.
  * Missing validation entry, missing Overture geometry for an absent
    candidate, or an unresolved candidate is an explicit error (no silent
    fallback, AGENTS.md §5).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile


def load_candidates(candidates_path):
    with open(candidates_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("candidates", [])


def load_validation(validation_path):
    """Map osm_id -> broken entry for the region, keyed by int osm_id."""
    with open(validation_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    broken = {}
    for entry in data.get("broken", []):
        try:
            broken[int(entry["id"])] = entry
        except (KeyError, TypeError, ValueError):
            continue
    return broken


def load_overture_geometry(overture_path):
    """Map osm_id -> geometry dict from the Overture .geojsonseq artifact."""
    geometry = {}
    with open(overture_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            feature = json.loads(line)
            props = feature.get("properties", {})
            try:
                osm_id = int(props.get("osm_id"))
            except (TypeError, ValueError):
                continue
            geom = feature.get("geometry")
            if geom and geom.get("type") in ("Polygon", "MultiPolygon"):
                geometry[osm_id] = geom
    return geometry


def run_duckdb_sql(sql, label):
    """Execute a SQL script through the DuckDB CLI (stdin); fail on nonzero exit."""
    try:
        proc = subprocess.run(
            ["duckdb"],
            input=sql,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise SystemExit(
            "ERROR: merge_overture_polygons: 'duckdb' binary not found on PATH.\n"
            "The health check needs DuckDB with the spatial extension; run this step\n"
            "inside the osm2parquet container (python3 + duckdb httpfs/spatial)."
        ) from None

    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[-5:] if proc.stderr else []
        raise SystemExit(
            f"ERROR: DuckDB query failed for '{label}'.\n"
            + ("\n".join(detail) if detail else proc.stdout[-500:])
        )
    return proc


def compute_health(present_geometries, land_geometries):
    """Compare present OSM geometry vs Overture LAND geometry per candidate.

    `present_geometries`: dict osm_id -> geometry dict (or None when the feature
    carries no geometry). `land_geometries`: dict osm_id -> Overture land
    geometry. Returns {osm_id: (coverage, inside)} where
      coverage = area(P & L) / area(L) and inside = area(P & L) / area(P).
    Candidates without a LAND reference geometry are omitted.
    """
    rows = []
    for osm_id, present_geom in present_geometries.items():
        land_geom = land_geometries.get(osm_id)
        if land_geom is None:
            continue
        if present_geom is None:
            rows.append({"osm_id": osm_id, "kind": "PRESENT", "geom": None})
        else:
            rows.append(
                {"osm_id": osm_id, "kind": "PRESENT",
                 "geom": json.dumps(present_geom, separators=(",", ":"))}
            )
        rows.append(
            {"osm_id": osm_id, "kind": "LAND",
             "geom": json.dumps(land_geom, separators=(",", ":"))}
        )

    if not rows:
        return {}

    tmp_ndjson = None
    tmp_result = None
    try:
        fd, tmp_ndjson = tempfile.mkstemp(suffix=".jsonl", prefix="overture_health_")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

        fd2, tmp_result = tempfile.mkstemp(suffix=".json", prefix="overture_health_")
        os.close(fd2)

        sql = f"""INSTALL spatial; LOAD spatial; SET geometry_always_xy=true;
CREATE TABLE g AS
  SELECT CAST(osm_id AS BIGINT) AS osm_id, CAST(kind AS VARCHAR) AS kind,
         ST_GeomFromGeoJSON(geom) AS geo
  FROM read_ndjson('{tmp_ndjson}');
COPY (
  WITH land AS (SELECT osm_id, geo FROM g WHERE kind = 'LAND'),
       pres AS (SELECT osm_id, geo FROM g WHERE kind = 'PRESENT')
  SELECT p.osm_id,
         ST_Area(p.geo)                          AS a_pres,
         ST_Area(l.geo)                          AS a_land,
         COALESCE(ST_Area(ST_Intersection(p.geo, l.geo)), 0.0) AS a_int
  FROM pres p JOIN land l USING (osm_id)
  ORDER BY p.osm_id
) TO '{tmp_result}' (FORMAT JSON);"""
        run_duckdb_sql(sql, "candidate health check")

        health = {}
        with open(tmp_result, "r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                a_land = row.get("a_land") or 0.0
                a_pres = row.get("a_pres") or 0.0
                a_int = row.get("a_int") or 0.0
                coverage = a_int / a_land if a_land > 0 else 0.0
                inside = a_int / a_pres if a_pres > 0 else 0.0
                health[int(row["osm_id"])] = (coverage, inside)
        return health
    finally:
        if tmp_ndjson and os.path.isfile(tmp_ndjson):
            os.remove(tmp_ndjson)
        if tmp_result and os.path.isfile(tmp_result):
            os.remove(tmp_result)


def derive_area_type(admin_level):
    """Consistent mapping with filter_polygons.derive_area_type for our levels."""
    try:
        lvl = int(admin_level)
        if lvl == 2:
            return "country"
        if lvl in (3, 4):
            return "state"
    except (ValueError, TypeError):
        pass
    return "administrative"


def build_feature(osm_id, validation_entry, geometry):
    props = {
        "@type": "relation",
        "@id": osm_id,
        "id": osm_id,
        "boundary": "administrative",
        "admin_level": str(validation_entry.get("admin_level", "")),
        "name": validation_entry.get("name", ""),
        "area_type": derive_area_type(validation_entry.get("admin_level", "")),
    }
    if validation_entry.get("iso1"):
        props["ISO3166-1"] = validation_entry["iso1"]
    if validation_entry.get("iso2"):
        props["ISO3166-2"] = validation_entry["iso2"]
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": props,
    }


def read_present_osm_ids(input_path):
    """Stream the input features through to stdout while collecting present osm_ids.

    The region stream must be reproduced verbatim: the merge output is the full
    stream (input features unchanged) plus, appended, the Overture fallbacks.
    """
    present = set()
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                feature = json.loads(line)
            except json.JSONDecodeError:
                continue
            sys.stdout.write(line + "\n")
            props = feature.get("properties", {})
            for key in ("id", "@id", "osm_id"):
                try:
                    present.add(int(props[key]))
                    break
                except (KeyError, TypeError, ValueError):
                    continue
    return present


def extract_osm_id(props):
    """Return the stable integer OSM id embedded in a feature's properties."""
    for key in ("id", "@id", "osm_id"):
        try:
            return int(props[key])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def collect_present_candidates(input_path, candidate_ids):
    """Map osm_id -> first feature for every candidate present in the stream."""
    present = {}
    with open(input_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                feature = json.loads(line)
            except json.JSONDecodeError:
                continue
            osm_id = extract_osm_id(feature.get("properties", {}))
            if osm_id in candidate_ids and osm_id not in present:
                present[osm_id] = feature
    return present


def replace_feature(feature, geometry):
    """Swap a feature's geometry, preserving all original OSM properties."""
    replaced = dict(feature)
    replaced["geometry"] = geometry
    return replaced


def require_file(path, label):
    if not os.path.isfile(path):
        sys.stderr.write(
            f"ERROR: merge_overture_polygons: required {label} file missing: {path}\n"
            "Cause: the region's validation artifact or the Overture fallback artifact\n"
            "was not produced/duplicated into the workspace.\n"
            "Solution: ensure the 'overture' stage ran and validation.json exists before merge.\n"
        )
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Merge Overture fallback geometries into a region polygon stream."
    )
    parser.add_argument("--validation", required=True,
                        help="Region *-validation.json (broken admin relations with OSM metadata)")
    parser.add_argument("--overture", required=True,
                        help="Overture fallback artifact overture.geojsonseq (osm_id + geometry only)")
    parser.add_argument("--candidates", required=True,
                        help="Curated candidate list scripts/overture-candidates.json")
    parser.add_argument("--country-code", required=True,
                        help="Geofabrik country code of the region being processed")
    parser.add_argument("--min-coverage", type=float, default=0.95,
                        help="Replace present geometry when its land coverage ratio "
                             "(overlap/overture-land area) is below this threshold (default 0.95)")
    parser.add_argument("--min-inside", type=float, default=0.90,
                        help="Replace present geometry when the fraction of its area that "
                             "lies inside the Overture land polygon is below this threshold "
                             "(default 0.90)")
    parser.add_argument("input_file", help="filter_polygons.py output geojsonseq")
    args = parser.parse_args()

    country_code = args.country_code.upper().strip()

    candidates = [
        c for c in load_candidates(args.candidates)
        if c.get("country", "").upper().strip() == country_code
    ]
    if not candidates:
        sys.stderr.write(
            f"merge_overture_polygons: no candidates for country '{country_code}'; "
            f"no Overture merge required for this region.\n"
        )
        read_present_osm_ids(args.input_file)
        return 0

    require_file(args.validation, "validation")
    require_file(args.overture, "overture")

    validation = load_validation(args.validation)
    overture = load_overture_geometry(args.overture)

    candidate_ids = set()
    for c in candidates:
        try:
            candidate_ids.add(int(c["osm_id"]))
        except (TypeError, ValueError):
            continue

    present_features = collect_present_candidates(args.input_file, candidate_ids)

    replacements = {}
    with_land = {
        osm_id: feat.get("geometry")
        for osm_id, feat in present_features.items()
        if osm_id in overture
    }
    if with_land:
        health = compute_health(with_land, overture)
        for osm_id in sorted(with_land):
            coverage, inside = health.get(osm_id, (0.0, 0.0))
            if coverage < args.min_coverage or inside < args.min_inside:
                replacements[osm_id] = (coverage, inside)
                sys.stderr.write(
                    f"merge_overture_polygons ({country_code}): REPLACE osm_id {osm_id} "
                    f"coverage={coverage:.4f} inside={inside:.4f} "
                    f"(thresholds {args.min_coverage}/{args.min_inside}).\n"
                )
            else:
                sys.stderr.write(
                    f"merge_overture_polygons ({country_code}): KEEP osm_id {osm_id} "
                    f"coverage={coverage:.4f} inside={inside:.4f}.\n"
                )

    inserted = 0
    replaced = set()
    with open(args.input_file, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                feature = json.loads(line)
            except json.JSONDecodeError:
                continue
            osm_id = extract_osm_id(feature.get("properties", {}))
            if osm_id in replacements:
                sys.stdout.write(
                    json.dumps(replace_feature(feature, overture[osm_id]), ensure_ascii=False) + "\n"
                )
                replaced.add(osm_id)
            else:
                sys.stdout.write(line + "\n")

    unresolved = []
    for c in candidates:
        try:
            osm_id = int(c["osm_id"])
        except (TypeError, ValueError):
            continue
        if osm_id in present_features:
            continue
        if osm_id not in validation:
            unresolved.append((osm_id, "missing in validation.json"))
            continue
        if osm_id not in overture:
            unresolved.append((osm_id, "missing geometry in Overture artifact"))
            continue
        feature = build_feature(osm_id, validation[osm_id], overture[osm_id])
        sys.stdout.write(json.dumps(feature, ensure_ascii=False) + "\n")
        inserted += 1

    if unresolved:
        details = "; ".join(f"{rid} ({reason})" for rid, reason in unresolved)
        sys.stderr.write(
            "ERROR: merge_overture_polygons: candidate(s) could not be resolved:\n"
            f"  {details}\n"
            "Cause: candidate relation is absent from validation.json or its Overture\n"
            "geometry was not fetched (see fetch_overture_polygons manifest).\n"
            "No silent fallback applied.\n"
        )
        return 1

    present_healthy = len(present_features) - len(replaced)
    sys.stderr.write(
        f"merge_overture_polygons ({country_code}): {inserted} inserted, "
        f"{len(replaced)} replaced, {present_healthy} already present (left untouched).\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())