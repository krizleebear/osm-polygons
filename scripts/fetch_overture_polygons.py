#!/usr/bin/env python3
"""
Preload missing admin boundary polygon geometries from the Overture Maps `divisions`
theme and cache them as a GeoJSONSeq artifact.

Rationale (see doc/OVERTURE_FALLBACK_PLAN.md):
  * Genuine broken OSM relations (ISO-matched to the Geofabrik country filter) lack
    full geometry in the regional extracts.
  * Overture stores the source OSM relation id in sources[].record_id as
    `r<osm_id>@<version>`, so the division can be resolved by the stable OSM id.
  * Overture provides geometry ONLY; all metadata (name, admin_level, tags) is taken
    from OSM validation artifacts by the consuming merge script.
  * Releases are purged after ~3 months, so the LATEST release is always resolved via
    a cheap S3 bucket listing (ListObjectsV2), never a full data glob.

Usage:
    python3 fetch_overture_polygons.py --candidates overture-candidates.json --out-dir <dir>

Output (per candidate, SubType division lookups batched per country):
    <out-dir>/overture.geojsonseq   -- features with ONLY osm_id + geometry
    <out-dir>/manifest.json         -- osm_id -> division_id, record_id, source release

Fails explicitly on unresolved candidates (no silent fallbacks).
"""

import argparse
import json
import os
import re
import subprocess
import urllib.request

OVERTURE_BUCKET = "s3://overturemaps-us-west-2"
OVERTURE_S3_HTTP = "https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com"
RELEASE_PATTERN = re.compile(r"release/([0-9]{4}-[0-9]{2}-[0-9]{2}\.[0-9]+)/")


def resolve_latest_release():
    """Resolve the most recent Overture release via ListObjectsV2 (prefix=release/, delimiter=/)."""
    url = (
        f"{OVERTURE_S3_HTTP}/?list-type=2&prefix=release/&delimiter=/&max-keys=100"
    )
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            body = resp.read().decode("utf-8")
    except Exception as exc:  # pragma: no cover - network diagnostics
        raise SystemExit(f"ERROR: Failed to list Overture releases: {exc}") from exc

    versions = set()
    for m in RELEASE_PATTERN.finditer(body):
        versions.add(m.group(1))
    if not versions:
        raise SystemExit(
            "ERROR: No Overture releases found in bucket listing. "
            "Bucket listing may be unavailable; open https://overturemaps.org/ "
            "and check the release catalog. Root cause: ListObjectsV2 returned no "
            "release/<YYYY-MM-DD.N>/ prefixes."
        )
    latest = max(versions)
    print(f"Resolved latest Overture release: {latest}")
    return latest


def load_candidates(candidates_path):
    if not os.path.isfile(candidates_path):
        raise SystemExit(f"ERROR: Candidates file '{candidates_path}' not found.")
    with open(candidates_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    candidates = data.get("candidates", [])
    if not candidates:
        raise SystemExit(f"ERROR: No candidates found in '{candidates_path}'.")
    return candidates


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
            "ERROR: 'duckdb' binary not found on PATH. "
            "Run inside the osm2parquet container or install DuckDB."
        ) from None

    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[-5:] if proc.stderr else []
        raise SystemExit(
            f"ERROR: DuckDB query failed for '{label}'.\n"
            + ("\n".join(detail) if detail else proc.stdout[-500:])
        )
    return proc


def build_download_divisions_sql(release_s3dir, output_parquet):
    """Download the divisions part (needed columns) for local, deterministic lookups."""
    return f"""INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';

COPY (
    SELECT id, country, subtype, names, sources
    FROM read_parquet('{release_s3dir}/theme=divisions/type=division/*')
) TO '{output_parquet}' (FORMAT PARQUET, COMPRESSION ZSTD);
"""


def build_lookup_sql(local_divisions_parquet, group, output_json):
    """Resolve division_id for each candidate (single local pass).

    `group` is a list of (country, osm_id, admin_level) tuples where country is
    the Overture-facing code (already normalized to the Overture country when it
    differs, and must match the export matrix job's COUNTRY_CODE, e.g. Kosovo -> XK).
    Matching strategy:
      * admin_level 2 (country level): match on `country` + `subtype='country'`,
        which is unique per country and robust against Overture referencing a
        different OSM relation for the same country (e.g. NO -> r1059668@154
        instead of the candidate's r2978650@).
      * higher levels: match directly on the unnest'ed sources.record_id with
        the exact `r<osm_id>@` prefix, so the result is deterministic even when
        a division has multiple OpenStreetMap sources.
    """
    selects = "\n  UNION ALL\n".join(
        (
            f"""\
SELECT '{osm_id}' AS osm_id_candidate, d.id AS division_id
FROM read_parquet('{local_divisions_parquet}') d
WHERE d.country = '{country}' AND d.subtype = 'country'"""
            if admin_level == 2
            else f"""\
SELECT '{osm_id}' AS osm_id_candidate, d.id AS division_id
FROM read_parquet('{local_divisions_parquet}') d
CROSS JOIN unnest(d.sources) AS t(s)
WHERE s.dataset = 'OpenStreetMap'
  AND s.record_id LIKE 'r{osm_id}@%'
  AND d.country = '{country}'"""
        )
        for country, osm_id, admin_level in group
    )
    return f"""
COPY (
    {selects}
) TO '{output_json}' (FORMAT JSON);
"""


def build_geometry_sql(release_s3dir, division_ids, output_json):
    """Fetch geometry from division_area for the resolved division ids."""
    return f"""INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';
INSTALL spatial; LOAD spatial; SET geometry_always_xy=true;

COPY (
    SELECT DISTINCT division_id, ST_AsGeoJSON(geometry) AS geometry
    FROM read_parquet('{release_s3dir}/theme=divisions/type=division_area/*')
    WHERE division_id IN ({", ".join("'" + i + "'" for i in division_ids)})
) TO '{output_json}' (FORMAT JSON);
"""


def fetch_for_candidates(candidates, out_dir, release):
    release_s3dir = f"{OVERTURE_BUCKET}/release/{release}"
    divisions_parquet = os.path.join(out_dir, "divisions_cached.parquet")

    # Deterministic approach: cache the divisions part locally once, then run
    # all lookups against the local file (remote multi-OR scans are flaky).
    print("Downloading Overture divisions part (cached lookup file) ...")
    sql = build_download_divisions_sql(release_s3dir, divisions_parquet)
    run_duckdb_sql(sql, "divisions download")
    if not os.path.isfile(divisions_parquet) or os.path.getsize(divisions_parquet) == 0:
        raise SystemExit(
            "ERROR: Overture divisions part could not be cached for release "
            f"'{release}'."
        )

    # Single local pass covering every candidate (country, osm_id, admin_level).
    # `country` is the Overture-facing code and must match the export matrix job's
    # COUNTRY_CODE (e.g. Kosovo -> XK, NOT the ISO3166-2 prefix RS-KM).
    group = [
        (c.get("overture_country", c["country"]), c["osm_id"], c["admin_level"])
        for c in candidates
    ]
    print("Looking up all candidates in cached divisions file (single pass) ...")
    lookup_json = os.path.join(out_dir, "tmp_division_lookup.json")
    sql = build_lookup_sql(divisions_parquet, group, lookup_json)
    run_duckdb_sql(sql, "divisions lookup")
    if not os.path.isfile(lookup_json):
        raise SystemExit("ERROR: DuckDB produced no division lookup output.")

    # Map resolved division ids back to candidate osm_ids (deterministic:
    # the SQL matches the exact `r<osm_id>@` record_id prefix per candidate).
    division_to_osm = {}
    with open(lookup_json, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            osm_id = row.get("osm_id_candidate")
            if osm_id is not None:
                division_to_osm[row["division_id"]] = int(osm_id)
    os.remove(lookup_json)

    if not division_to_osm:
        return {}

    # Single division_area pass fetching geometry for all resolved division ids.
    print(f"Fetching geometry for {len(division_to_osm)} resolved divisions ...")
    geom_json = os.path.join(out_dir, "tmp_geometry.json")
    sql = build_geometry_sql(release_s3dir, list(division_to_osm), geom_json)
    run_duckdb_sql(sql, "geometry fetch")
    if not os.path.isfile(geom_json):
        raise SystemExit("ERROR: DuckDB produced no geometry output.")

    found = {}
    with open(geom_json, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            osm_id = division_to_osm.get(row["division_id"])
            if osm_id is None or row.get("geometry") is None:
                continue
            found.setdefault(
                osm_id,
                {"division_id": row["division_id"], "geometry": row["geometry"]},
            )
    os.remove(geom_json)

    # The cached divisions file is only an intermediate for deterministic
    # lookups; it must not be published as part of the artifact.
    if os.path.isfile(divisions_parquet):
        os.remove(divisions_parquet)

    return found


def write_artifacts(out_dir, candidates, found, release):
    geojsonseq_path = os.path.join(out_dir, "overture.geojsonseq")
    manifest = {"release": release, "features": {}}
    written = 0
    resolved = 0
    missing = []

    with open(geojsonseq_path, "w", encoding="utf-8") as fh:
        for c in candidates:
            entry = found.get(c["osm_id"])
            if entry is None or entry.get("geometry") is None:
                missing.append(c)
                continue
            resolved += 1
            feature = {
                "type": "Feature",
                "geometry": entry["geometry"],
                "properties": {"osm_id": c["osm_id"]},
            }
            fh.write(json.dumps(feature, ensure_ascii=False) + "\n")
            written += 1
            manifest["features"][str(c["osm_id"])] = {
                "country": c["country"],
                "admin_level": c["admin_level"],
                "division_id": entry["division_id"],
            }

    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    if missing:
        detail = "; ".join(f"osm_id {c['osm_id']} ({c['country']})" for c in missing)
        raise SystemExit(
            f"ERROR: {len(missing)} candidate(s) could not be resolved in Overture "
            f"release '{release}': {detail}. "
            "Possible causes: relation absent from Overture, schema drift, or an "
            "incomplete country extraction. No silent fallback applied."
        )

    print(f"Resolved {resolved}/{len(candidates)} candidates.")
    print(f"GeoJSONSeq:  {geojsonseq_path} ({written} features)")
    print(f"Manifest:    {os.path.join(out_dir, 'manifest.json')}")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch missing admin polygons from Overture Maps divisions theme."
    )
    parser.add_argument(
        "--candidates",
        required=True,
        help="Path to overture-candidates.json",
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        help="Output directory for overture.geojsonseq and manifest.json",
    )
    parser.add_argument(
        "--release",
        default=None,
        help="Pin a specific Overture release (default: resolve latest)",
    )
    args = parser.parse_args()

    candidates = load_candidates(args.candidates)
    release = args.release or resolve_latest_release()

    os.makedirs(args.out_dir, exist_ok=True)
    found = fetch_for_candidates(candidates, args.out_dir, release)
    write_artifacts(args.out_dir, candidates, found, release)


if __name__ == "__main__":
    main()