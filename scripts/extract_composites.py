#!/usr/bin/env python3
"""
Extract multiple non-overlapping country/territory PBFs from a composite Geofabrik PBF
in a single `osmium extract` pass.

Uses only the Python 3 standard library.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile


def load_source_extracts(config_path, source_id):
    """Load and validate extracts configuration for source_id."""
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"Composite extracts config not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    sources = data.get("sources") or {}
    if source_id not in sources:
        available = ", ".join(sorted(sources.keys()))
        raise KeyError(f"Source '{source_id}' not found in {config_path}. Available: {available}")

    extracts = sources[source_id].get("extracts") or []
    if not extracts:
        raise ValueError(f"Source '{source_id}' in {config_path} has no extracts configured.")
    return extracts


def build_osmium_config(extracts, out_dir, work_dir):
    """Build osmium extract JSON config and any temporary GeoJSON polygon files."""
    osmium_extracts = []
    expected_outputs = []

    for ext in extracts:
        region = ext.get("region")
        if not region:
            raise ValueError(f"Extract entry missing 'region': {ext}")

        out_path = os.path.abspath(os.path.join(out_dir, f"{region}.osm.pbf"))
        expected_outputs.append((region, out_path))

        entry = {"output": out_path}
        if "bbox" in ext:
            bbox = ext["bbox"]
            if not isinstance(bbox, list) or len(bbox) != 4:
                raise ValueError(f"Invalid bbox for '{region}': {bbox}")
            entry["bbox"] = bbox
        elif "polygon" in ext:
            ring = ext["polygon"]
            if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError(f"Invalid closed polygon ring for '{region}'")
            geojson_path = os.path.abspath(os.path.join(work_dir, f"{region}.geojson"))
            geojson_obj = {
                "type": "Feature",
                "properties": {"region": region},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [ring],
                },
            }
            with open(geojson_path, "w", encoding="utf-8") as gfh:
                json.dump(geojson_obj, gfh)
            entry["polygon"] = {
                "file_name": geojson_path,
                "file_type": "geojson",
            }
        else:
            raise ValueError(f"Extract '{region}' must specify either 'bbox' or 'polygon'.")

        osmium_extracts.append(entry)

    return {"extracts": osmium_extracts}, expected_outputs


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Single-pass osmium extract splitter for composite Geofabrik PBFs."
    )
    parser.add_argument("--config", required=True, help="Path to scripts/composite-extracts.json")
    parser.add_argument("--source", required=True, help="Source key in config (e.g. central-america)")
    parser.add_argument("--input-pbf", required=True, help="Input composite .osm.pbf file")
    parser.add_argument("--out-dir", required=True, help="Output directory for extracted .osm.pbf files")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate osmium extract config and print it as JSON without invoking osmium",
    )
    args = parser.parse_args(argv)

    extracts = load_source_extracts(args.config, args.source)
    os.makedirs(args.out_dir, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="osmium_extract_") as tmp_dir:
        osmium_cfg, expected_outputs = build_osmium_config(extracts, args.out_dir, tmp_dir)

        if args.dry_run:
            print(json.dumps(osmium_cfg, indent=2))
            return 0

        if not os.path.isfile(args.input_pbf) or os.path.getsize(args.input_pbf) == 0:
            print(
                f"ERROR: Input composite PBF '{args.input_pbf}' is missing or empty.",
                file=sys.stderr,
            )
            return 1

        cfg_path = os.path.join(tmp_dir, "osmium-extract.json")
        with open(cfg_path, "w", encoding="utf-8") as cfh:
            json.dump(osmium_cfg, cfh, indent=2)

        cmd = [
            "osmium",
            "extract",
            "--strategy=complete_ways",
            "--overwrite",
            "-c",
            cfg_path,
            args.input_pbf,
        ]
        print(f"[INFO] Running single-pass extraction for source '{args.source}' ({len(extracts)} regions)...")
        proc = subprocess.run(cmd, check=False)
        if proc.returncode != 0:
            print(
                f"ERROR: osmium extract failed with exit code {proc.returncode} for '{args.source}'.",
                file=sys.stderr,
            )
            return proc.returncode

        missing_or_empty = []
        for region, out_path in expected_outputs:
            if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
                missing_or_empty.append((region, out_path))
            else:
                size_kb = os.path.getsize(out_path) / 1024.0
                print(f"[OK] {region}: {out_path} ({size_kb:.1f} KiB)")

        if missing_or_empty:
            for region, out_path in missing_or_empty:
                print(
                    f"ERROR: Expected extracted PBF missing or empty for '{region}': {out_path}",
                    file=sys.stderr,
                )
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
