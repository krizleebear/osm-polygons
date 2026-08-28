#!/usr/bin/env python3
"""
Merge Overture fallback geometries for genuinely broken admin relations.

Run directly after `filter_polygons.py` in the export stage. Reads the region's
filtered `*.admin-polygons.geojsonseq`, loads the Overture fallback artifacts
(fetched once by `fetch_overture_polygons.py`), and appends features for those
candidate relations that are genuinely absent from the OSM-derived stream.

RULES (see doc/OVERTURE_FALLBACK_PLAN.md):
  * Only candidates listed in `--candidates` whose Geofabrik country matches
    `--country-code` are considered.
  * A candidate is merged ONLY if its osm_id is not present in the input
    stream (insert-only; never overwrites existing OSM geometry).
  * Merged features carry OSM metadata (name, admin_level, ©id) taken from the
    region `*-validation.json`; Overture supplies ONLY geometry.
  * Missing validation entry, missing Overture geometry for an absent
    candidate, or an unresolved candidate is an explicit error (no silent
    fallback, AGENTS.md §5).
"""

import argparse
import json
import os
import sys


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

    present = read_present_osm_ids(args.input_file)

    merged = 0
    skipped_present = 0
    unresolved = []

    for c in candidates:
        try:
            osm_id = int(c["osm_id"])
        except (TypeError, ValueError):
            continue
        if osm_id in present:
            skipped_present += 1
            continue
        if osm_id not in validation:
            unresolved.append((osm_id, "missing in validation.json"))
            continue
        if osm_id not in overture:
            unresolved.append((osm_id, "missing geometry in Overture artifact"))
            continue
        feature = build_feature(osm_id, validation[osm_id], overture[osm_id])
        sys.stdout.write(json.dumps(feature, ensure_ascii=False) + "\n")
        merged += 1

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

    sys.stderr.write(
        f"merge_overture_polygons ({country_code}): {merged} merged, "
        f"{skipped_present} already present (left untouched).\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())