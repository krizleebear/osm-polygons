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


def load_candidates(candidates_path, country_code):
    with open(candidates_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    candidates = data.get("candidates", [])
    # Return mapping of int osm_id -> candidate metadata matching country_code
    return {
        int(c["osm_id"]): c
        for c in candidates
        if c.get("country", "").upper() == country_code.upper()
    }


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
            osm_id = feat.get("id") or feat.get("properties", {}).get("osm_id")
            if osm_id is not None and int(osm_id) in candidate_ids:
                ref_map[int(osm_id)] = feat
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
            total_stream_count += 1
            feat = json.loads(line)
            osm_id = feat.get("id") or feat.get("properties", {}).get("osm_id")
            if osm_id is not None and int(osm_id) in ref_features:
                rel_id = int(osm_id)
                seen_ids.add(rel_id)
                # Replace with the intact reference feature (preserving any extra stream tags if present)
                ref_feat = ref_features[rel_id]
                out_fp.write(json.dumps(ref_feat, ensure_ascii=False) + "\n")
                replaced_count += 1
                print(
                    f"[{args.country_code}] REPLACED relation {rel_id} ({candidates[rel_id].get('name')}) with intact reference geometry."
                )
            else:
                out_fp.write(line + "\n")

        # Insert candidates that were completely absent from the stream
        for rel_id in candidate_ids:
            if rel_id not in seen_ids:
                ref_feat = ref_features[rel_id]
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
