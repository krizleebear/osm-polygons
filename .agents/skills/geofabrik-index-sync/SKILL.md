---
name: geofabrik-index-sync
description: >-
  Use when auditing, verifying, or adding Geofabrik regions and country ISO codes
  against Geofabrik's live index-v1-nogeom.json, the GitHub Release index.json,
  and scripts/countries.json.
---

# Geofabrik Index & Region Synchronization

Use this procedure to compare `osm-polygons` against Geofabrik's live extract catalog and keep all pipeline matrices and `scripts/countries.json` in sync.

## 1. Audit Against Live Geofabrik Index
1. Download `https://download.geofabrik.de/index-v1-nogeom.json` to `/tmp/geofabrik-index.json`.
2. Split comma-separated `iso3166-1:alpha2` values (e.g. `HT,DO`, `QA,AE,OM,BH,KW`).
3. Keep known intentional exceptions in mind:
   - `great-britain` has `iso: None` in Geofabrik (`GB` is on `united-kingdom`).
   - French overseas departments (`GP`, `MQ`, `GF`, `RE`, `YT`) are already covered via `europe/france/...` even though Geofabrik omits `iso3166-1:alpha2`.
   - `gcc-states` omits `SA` in Geofabrik's `iso3166-1:alpha2` (`SA` is still in `scripts/countries.json`).
   - `antarctica` (`AQ`) is intentionally omitted unless an `antarctica` simplify partition is added.
4. Cross-check missing ISO codes against the latest GitHub Release `index.json` and DuckDB `httpfs` queries before assuming data is missing or present (e.g. spillover-only `admin_level=2` files).

## 2. Adding Missing Regions (4-Way Sync)
Whenever adding a Geofabrik region:
1. `polygon-export-pipeline.yml` (`export` matrix): add `OSM_REGION`, `PBF_URL` (prefer exact `urls.pbf` from `index-v1-nogeom.json`), and `COUNTRY_CODE`.
2. `polygon-export-pipeline.yml` (`simplify` matrix): add the region to both `REGIONS` and `ARTIFACT_PATTERNS` (`*-<region>/**`) of the matching continent job.
3. `osm-download-pipeline.yml`: add the matching `OSM_REGION` and `PBF_URL` entry.
4. `scripts/countries.json`: add `continent`, `name`, and `levels`. Only include `2` in `levels` for sovereign states; non-sovereign territories (e.g. `PR`, `VI`, `CK`, `NU`, `PN`, `TK`, `WF`) should omit `2` to avoid false-positive L2 errors in `scripts/validate_parquet.py`.

## 3. Verification
Run in one command:
- `python3 scripts/validate_azure_yaml.py polygon-export-pipeline.yml`
- `python3 scripts/validate_azure_yaml.py osm-download-pipeline.yml`
- `PYTHONPATH=scripts python3 -m unittest discover -s scripts -p 'test_*.py'`
- Verify `export` matrix keys == `simplify` `REGIONS` == `simplify` `ARTIFACT_PATTERNS` == `osm-download-pipeline.yml` matrix keys.
