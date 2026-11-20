#!/usr/bin/env python3
"""
Validate Geofabrik PBF_URL entries in Azure DevOps pipeline YAML files.

Uses only the Python 3 standard library so it runs inside minimal containers
(e.g. docker-osmium-tool and osm2parquet) without extra dependencies.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

DEFAULT_INDEX_URL = "https://download.geofabrik.de/index-v1-nogeom.json"
PAIR_RE = re.compile(r"OSM_REGION:\s*'([^']+)'\s+PBF_URL:\s*'([^']+)'")
ANY_GEOFABRIK_URL_RE = re.compile(r"https://download\.geofabrik\.de/[^\s'\"]+")


def load_geofabrik_catalog(
    index_file=None,
    index_url=DEFAULT_INDEX_URL,
    retries=3,
    timeout=20,
):
    """Return {region_id: canonical_pbf_url} from a local file or Geofabrik live index."""
    if index_file:
        with open(index_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        last_exc = None
        data = None
        for attempt in range(1, retries + 1):
            req = urllib.request.Request(
                index_url,
                headers={"User-Agent": "osm-polygons-preflight/1.0"},
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.load(resp)
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_exc = exc
                if attempt < retries:
                    time.sleep(2)
        if data is None:
            raise RuntimeError(
                f"Failed to fetch Geofabrik index '{index_url}' after {retries} attempts: {last_exc}"
            )

    catalog = {}
    for feat in data.get("features", []):
        props = feat.get("properties") or {}
        fid = props.get("id")
        pbf = (props.get("urls") or {}).get("pbf")
        if fid and pbf:
            catalog[fid] = pbf
    return catalog


def validate_yaml_text(
    content,
    label,
    catalog=None,
    allow_duplicate_regions=False,
):
    """Validate all (OSM_REGION, PBF_URL) entries in pipeline YAML content."""
    errors = []
    pairs = PAIR_RE.findall(content)
    if not pairs:
        return [f"{label}: No (OSM_REGION, PBF_URL) entries found."]

    all_urls = set(ANY_GEOFABRIK_URL_RE.findall(content))
    pair_urls = {url for _, url in pairs}
    stray_urls = sorted(all_urls - pair_urls)
    if stray_urls:
        errors.append(
            f"{label}: Found Geofabrik URL(s) outside standard (OSM_REGION, PBF_URL) pairs: {stray_urls}"
        )

    seen_regions = set()
    for reg, url in pairs:
        if not allow_duplicate_regions and reg in seen_regions:
            errors.append(f"{label}: Duplicate OSM_REGION '{reg}'.")
        seen_regions.add(reg)

        if not url.startswith("https://download.geofabrik.de/"):
            errors.append(
                f"{label}: PBF_URL for '{reg}' must use https://download.geofabrik.de/, got '{url}'."
            )
            continue

        expected_suffix = f"/{reg}/{reg}-latest.osm.pbf"
        if not url.endswith(expected_suffix):
            errors.append(
                f"{label}: Non-canonical PBF_URL for '{reg}' ('{url}'); expected suffix '{expected_suffix}'."
            )

        if catalog is not None:
            expected = catalog.get(reg) or catalog.get(f"us/{reg}")
            if expected is None:
                errors.append(f"{label}: Region '{reg}' not found in Geofabrik catalog.")
            elif url != expected:
                errors.append(
                    f"{label}: URL mismatch for '{reg}': configured '{url}' != Geofabrik catalog '{expected}'."
                )

    return errors


def validate_file(filepath, catalog=None, allow_duplicate_regions=False):
    """Read filepath and validate all Geofabrik PBF URLs. Returns (pair_count, errors)."""
    with open(filepath, "r", encoding="utf-8") as fh:
        content = fh.read()
    pairs = PAIR_RE.findall(content)
    errors = validate_yaml_text(
        content,
        label=filepath,
        catalog=catalog,
        allow_duplicate_regions=allow_duplicate_regions,
    )
    return len(pairs), errors


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate Geofabrik PBF_URL entries in pipeline YAML files."
    )
    parser.add_argument(
        "files",
        nargs="*",
        default=["osm-download-pipeline.yml"],
        help="Pipeline YAML files to validate (default: osm-download-pipeline.yml)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip fetching Geofabrik catalog and validate canonical URL structure only",
    )
    parser.add_argument(
        "--index-file",
        default=None,
        help="Path to local index-v1-nogeom.json catalog file",
    )
    parser.add_argument(
        "--index-url",
        default=DEFAULT_INDEX_URL,
        help=f"Geofabrik catalog URL (default: {DEFAULT_INDEX_URL})",
    )
    parser.add_argument(
        "--allow-duplicate-regions",
        action="store_true",
        help="Allow duplicate OSM_REGION keys (e.g. conditional debug branches)",
    )
    args = parser.parse_args(argv)

    catalog = None
    if not args.offline:
        try:
            catalog = load_geofabrik_catalog(
                index_file=args.index_file,
                index_url=args.index_url,
            )
            print(f"[INFO] Loaded {len(catalog)} Geofabrik catalog entries.")
        except Exception as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1

    all_errors = []
    for fp in args.files:
        if not os.path.exists(fp):
            all_errors.append(f"{fp}: File does not exist.")
            continue
        count, errs = validate_file(
            fp,
            catalog=catalog,
            allow_duplicate_regions=args.allow_duplicate_regions,
        )
        if errs:
            all_errors.extend(errs)
        else:
            mode = "canonical structure" if catalog is None else "live Geofabrik catalog"
            print(f"[OK] {fp}: validated {count} PBF_URL entries against {mode}.")

    if all_errors:
        for err in all_errors:
            print(f"[ERROR] {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
