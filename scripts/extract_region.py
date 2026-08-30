#!/usr/bin/env python3
"""
Orchestrates high-performance PBF filtering and export for a single geographical region.

Uses native C++ osmium-tool binaries with an optimized 1-Pass architecture:
  1. Combined 1-Pass tags-filter over raw PBF (extracts admins + places simultaneously).
  2. Immediate cleanup of the large input PBF to conserve runner disk space.
  3. Fast sub-second split of the compact combined PBF into admins.pbf and places.pbf.
  4. Rapid parent-child hierarchy extraction from admins.pbf via validate_pbf.py.
  5. Streaming polygon export and enrichment (filter_polygons.py + Postpass reference merge).
  6. Streaming place node export and structuring (filter_places.py).

Usage:
    python3 scripts/extract_region.py \
      --input-pbf <path_to_osm_pbf> \
      --country-code <CC> \
      --region <region_name> \
      --polygon-out <out_geojsonseq> \
      --places-out <out_places_jsonl> \
      [--reference-polygons <reference_geojsonseq>] \
      [--keep-input-pbf]
"""

import argparse
import os
import subprocess
import sys
import time

ADMIN_FILTER_RULES = [
    "boundary=administrative,traditional,statistical,cadastral,local_authority,borough",
    "r/place=suburb,quarter,borough,neighbourhood,city_block,hamlet,village,locality",
    "w/place=suburb,quarter,borough,neighbourhood,city_block,hamlet,village,locality",
]

PLACES_FILTER_RULES = [
    "n/place=city,town,municipality,commune,suburb,quarter,neighbourhood,city_block,townlet,village,hamlet,isolated_dwelling,locality",
]


def run_command(cmd, desc, stdin=None, stdout=None):
    """Execute an external command and raise RuntimeError on failure with diagnostic output."""
    t0 = time.time()
    print(f"[EXTRACT] Starting: {desc} ...", flush=True)
    res = subprocess.run(cmd, stdin=stdin, stdout=stdout, capture_output=(stdout is None), text=True)
    dt = time.time() - t0
    if res.returncode != 0:
        err = res.stderr if stdout is None else "(redirected)"
        raise RuntimeError(f"Command '{desc}' failed with exit code {res.returncode}:\n{err}")
    print(f"[EXTRACT] Finished: {desc} ({dt:.2f}s)", flush=True)
    return res


def extract_region(
    input_pbf,
    country_code,
    region,
    polygon_out,
    places_out,
    reference_polygons=None,
    postpass_candidates="scripts/postpass-candidates.json",
    keep_input_pbf=False,
):
    if not os.path.exists(input_pbf):
        raise FileNotFoundError(f"Input PBF file not found: {input_pbf}")

    t_start = time.time()
    print("=" * 60)
    print(f" Region Export Pipeline: {region} ({country_code})")
    print(f" Input PBF:  {input_pbf} ({os.path.getsize(input_pbf) / (1024*1024):.1f} MB)")
    print("=" * 60)

    combined_pbf = f"{region}.combined.pbf"
    admins_pbf = f"{region}.admins.pbf"
    places_pbf = f"{region}.places.pbf"
    parent_mapping_json = "./parent-mapping.json"
    raw_polygon_out = f"{polygon_out}.raw"

    try:
        # Step 1: 1-Pass combined filter over the raw input PBF
        combined_rules = ADMIN_FILTER_RULES + PLACES_FILTER_RULES
        cmd_combined = [
            "osmium", "tags-filter",
            "--output", combined_pbf,
            "--overwrite",
            input_pbf,
        ] + combined_rules
        run_command(cmd_combined, f"1-Pass combined filter for {region}")

        # Step 2: Delete raw input PBF immediately if not requested to keep
        if not keep_input_pbf:
            input_dir = os.path.dirname(input_pbf)
            if input_dir and input_dir != "." and os.path.isdir(input_dir):
                print(f"[EXTRACT] Reclaiming disk space: removing directory {input_dir} ...")
                subprocess.run(["rm", "-rf", input_dir])
            elif os.path.isfile(input_pbf):
                print(f"[EXTRACT] Reclaiming disk space: removing file {input_pbf} ...")
                os.remove(input_pbf)

        # Step 3: Fast sub-second split from combined_pbf
        cmd_split_admins = [
            "osmium", "tags-filter",
            "--output", admins_pbf,
            "--overwrite",
            combined_pbf,
        ] + ADMIN_FILTER_RULES
        run_command(cmd_split_admins, f"Split admins from combined PBF")

        cmd_split_places = [
            "osmium", "tags-filter",
            "--output", places_pbf,
            "--overwrite",
            combined_pbf,
        ] + PLACES_FILTER_RULES
        run_command(cmd_split_places, f"Split places from combined PBF")

        if os.path.exists(combined_pbf):
            os.remove(combined_pbf)

        # Step 4: Rapid parent-mapping extraction on compact admins_pbf
        cmd_validate = [
            sys.executable, "scripts/validate_pbf.py",
            admins_pbf,
            "--country", country_code,
            "--parent-mapping-only",
        ]
        run_command(cmd_validate, f"Extract parent hierarchy mapping")

        # Move generated mapping to ./parent-mapping.json if produced
        generated_mapping = admins_pbf.replace(".osm.pbf", "-parent-mapping.json").replace(".pbf", "-parent-mapping.json")
        if os.path.exists(generated_mapping):
            os.replace(generated_mapping, parent_mapping_json)

        # Ensure validation.json exists for downstream artifact publishing
        with open("./validation.json", "w") as f:
            f.write("{}\n")

        # Step 5: Export polygons directly via streaming pipe
        parent_arg = f"--parent-mapping {parent_mapping_json}" if os.path.exists(parent_mapping_json) else ""
        t_poly = time.time()
        print(f"[EXTRACT] Starting: Stream export & enrich polygons -> {raw_polygon_out} ...", flush=True)
        poly_pipe_cmd = (
            f"osmium export {admins_pbf} --output-format=geojsonseq --overwrite --config=osmium-export-config.json "
            f"| python3 scripts/filter_polygons.py --country-code {country_code} --admin-pbf {admins_pbf} {parent_arg} "
            f"> {raw_polygon_out}"
        )
        res_poly = subprocess.run(["bash", "-c", poly_pipe_cmd], text=True)
        if res_poly.returncode != 0:
            raise RuntimeError(f"Polygon streaming export pipeline failed with exit code {res_poly.returncode}")
        print(f"[EXTRACT] Finished: Polygon export & enrichment ({time.time() - t_poly:.2f}s)", flush=True)

        if os.path.exists(admins_pbf):
            os.remove(admins_pbf)


        # Step 6: Postpass reference merge if reference dataset available
        if reference_polygons and os.path.exists(reference_polygons) and os.path.getsize(reference_polygons) > 0:
            cmd_merge = [
                sys.executable, "scripts/merge_reference_polygons.py",
                raw_polygon_out,
                "--reference", reference_polygons,
                "--candidates", postpass_candidates,
                "--country-code", country_code,
                "--out", polygon_out,
            ]
            run_command(cmd_merge, f"Merge Postpass reference polygons for {country_code}")
            if os.path.exists(raw_polygon_out):
                os.remove(raw_polygon_out)
        else:
            os.replace(raw_polygon_out, polygon_out)

        # Step 7: Export place nodes via streaming pipe
        t_places = time.time()
        print(f"[EXTRACT] Starting: Stream export place nodes -> {places_out} ...", flush=True)
        places_pipe_cmd = (
            f"osmium export {places_pbf} --output-format=geojsonseq --overwrite --config=osmium-export-config.json "
            f"| python3 scripts/filter_places.py --country-code {country_code} "
            f"> {places_out}"
        )
        res_places = subprocess.run(["bash", "-c", places_pipe_cmd], text=True)
        if res_places.returncode != 0:
            raise RuntimeError(f"Places streaming export pipeline failed with exit code {res_places.returncode}")
        print(f"[EXTRACT] Finished: Place nodes export ({time.time() - t_places:.2f}s)", flush=True)

        if os.path.exists(places_pbf):
            os.remove(places_pbf)


    finally:
        # Cleanup any leftover intermediate files
        for tmp in (combined_pbf, admins_pbf, places_pbf, raw_polygon_out):
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    total_time = time.time() - t_start
    print("=" * 60)
    print(f" Region Export Complete: {region} ({country_code}) in {total_time:.2f}s")
    print(f"  - Polygons Output: {polygon_out} ({os.path.getsize(polygon_out) / (1024*1024):.2f} MB)")
    print(f"  - Places Output:   {places_out} ({os.path.getsize(places_out) / (1024*1024):.2f} MB)")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Optimized 1-Pass PBF region extraction & export tool.")
    parser.add_argument("--input-pbf", required=True, help="Path to input .osm.pbf file")
    parser.add_argument("--country-code", required=True, help="ISO 3166-1 alpha-2 country code (e.g. PT, DE)")
    parser.add_argument("--region", required=True, help="Geofabrik region name (e.g. portugal, brazil)")
    parser.add_argument("--polygon-out", required=True, help="Output .admin-polygons.geojsonseq file")
    parser.add_argument("--places-out", required=True, help="Output .places.jsonl file")
    parser.add_argument("--reference-polygons", default=None, help="Optional path to reference-polygons.geojsonseq")
    parser.add_argument("--postpass-candidates", default="scripts/postpass-candidates.json", help="Path to candidates JSON")
    parser.add_argument("--keep-input-pbf", action="store_true", default=False, help="Do not delete input PBF")

    args = parser.parse_args()

    extract_region(
        input_pbf=args.input_pbf,
        country_code=args.country_code,
        region=args.region,
        polygon_out=args.polygon_out,
        places_out=args.places_out,
        reference_polygons=args.reference_polygons,
        postpass_candidates=args.postpass_candidates,
        keep_input_pbf=args.keep_input_pbf,
    )
