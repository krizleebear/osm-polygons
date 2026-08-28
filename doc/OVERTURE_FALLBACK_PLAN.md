# Overture Fallback for Genuine Broken Parent Polygons

## 1. Context & Motivation

During validation (`validate_pbf.py`), 12,063 relations are flagged as **broken** (missing ways in the regional extract). Analysis shows two fundamentally different groups:

| Group | Count | Meaning |
|---|---|---|
| Genuine broken (ISO-matched) | **29** | 24 × admin_level=2, 5 × admin_level=4. Real in-country boundary relations damaged by Geofabrik bbox clipping (e.g. US 148838, GB 62149, CN 270056). |
| Neighbour spillover | **12,033** | Foreign relations from adjacent countries (Surrey/BC, CZ okresy in DE extract). Exported correctly in their *actual* country's parquet. Must NOT be rescued here. |

Several broken relations are nevertheless present in output (FR 2202162, NO 2978650, PT 295480, Kosovo 2088990, Madeira 1629145, NI 156393) so **broken ≠ missing**. Only the truly missing subset needs external geometry.

Since the affected OSM relation IDs are stable and consistently re-missing, the missing geometries are fetched **once** from the [Overture Maps](https://overturemaps.org/) `divisions` theme, cached as a pipeline artifact (same pattern as the preloaded PBFs from the separate download build), and consumed by the export pipeline — no live Overture dependency at runtime.

## 2. Verified Findings

**Lookup by OSM relation id.** Overture stores the source OSM id in `sources[].record_id` as `r<osm_id>@<version>`; a wildcard match works:

```sql
WHERE country = '<CC>'
  AND CAST(CAST(sources AS JSON) AS VARCHAR) LIKE '%"record_id":"r<OSM_ID>@%'
```

- Verified: US L2 → `r148838@1043` (29 sub-polygons, complete geometry); DE L8 example → `r62428@215`.
- The `country =` predicate is required for pruning — global scans of the single 4.6M-row part file time out.

**Release layout.** `release/<ver>/theme=divisions/type=division/*` is a single zstd parquet per version (all subtypes). `type=division_area/*` is keyed by `division_id` and holds the `geometry` column.

**Deterministic id resolution (verified 29/29 on 2026-08-19.0 / 4.6M-row part).** Naive `unnest(sources) ... LIMIT 1` is **non-deterministic**: divisions carry multiple OpenStreetMap source entries (e.g. US country references both a node `n424317935@128` and the relation `r148838@1043`), and a batched multi-OR remote scan across the full part file is flaky (20 vs 18 hits across runs). Two fixes are verified:
  1. The `division` part is downloaded once locally (column-pruned `id, country, subtype, names, sources` → ~203 MB) and all lookups run as a single local pass.
  2. Matching targets the exact expected OSM object instead of sampling the first source:
     - **admin_level = 2** → match `country` + `subtype = 'country'` (unique per country, robust against Overture referencing a different OSM relation than our validation data, e.g. NO country → `r1059668@154` vs. our candidate `r2978650@`).
     - **admin_level ≥ 3** → match the unnest'ed `sources[].record_id` with the exact `r<osm_id>@%` prefix per candidate (`dataset = 'OpenStreetMap'`).

**Overture country code vs. export matrix.** Kosovo r2088990 is exported by the `kosovo` matrix job under `COUNTRY_CODE=XK` and Overture stores it under `country='XK'`, so the candidate's `country` field is `XK` consistently. Do NOT set it to the ISO3166-2 prefix (`RS-KM`): the merge filters candidates by the matrix job's `COUNTRY_CODE`, so a `RS` tag would make the Kosovo candidate match the `serbia` job and insert Kosovo geometry into the Serbien stream (verified: live artifact carries r2088990 only once, under `country_code=XK`).

**Overture `admin_level` is NOT OSM-compatible** (Overture's own scheme: country=0, region=1, county=2, rest NULL). Therefore all metadata (`name`, `admin_level`, `tags`, `©id`, `osm_id`) is taken exclusively from our OSM data (`*-validation.json`); Overture supplies **only geometry**.

**Overture retention.** Old releases are purged from S3 (~3 months). Pinning a version would break the pipeline, so **always resolve the latest release** at fetch time (verified: bucket glob over `release/*/` lists e.g. 2026-07-22.0 and 2026-08-19.0).

## 3. Architecture & Decisions

Confirmed by the project owner (interview 2026-08-28):

| Decision | Choice | Rationale |
|---|---|---|
| Execution | Optional **stage in `polygon-export-pipeline.yml`**, gated by pipeline parameter `fetchOverturePolygons` (default `false`) | No separate pipeline; runs only when enabled; mirrors the PBF-preload pattern |
| Candidate list | Curated `scripts/overture-candidates.json` in repo | Stable, versioned, reviewable; extended only on evidence |
| Merge point | Export stage, directly into the region `.geojsonseq` | simplify / parquet / package stages remain untouched |
| Overwrite behavior | Insert **only if missing** | Never replace an existing (even imperfect) OSM geometry |
| Release pinning | **Always "latest"** | Overture retention purges pinned versions after ~3 months |
| Script execution | Python stdlib + DuckDB **CLI** | Identical local and CI paths (DuckDB CLI v1.5.5 with `spatial` + `httpfs` verified locally; container `osm2parquet:v1.0.6` is CLI-based and ships Python 3) |

## 4. Implementation

### 4.1 Candidate list — `scripts/overture-candidates.json` (new)

Each entry carries the OSM relation id, the target country code (matching Geofabrik `country_filter`) and the OSM admin level:

```json
{
  "covers_release": "latest",
  "candidates": [
    { "osm_id": 148838, "country": "US", "admin_level": 2 },
    { "osm_id": 62149,  "country": "GB", "admin_level": 2 },
    { "osm_id": 270056, "country": "CN", "admin_level": 2 },
    { "osm_id": 2088990, "country": "XK", "admin_level": 2 }
  ]
}
```

Seeded from the 29 verified genuine broken relations (ISO matched against `country_filter`).

### 4.2 Preload — `scripts/fetch_overture_polygons.py` (new)

Container `osm2parquet:v1.0.6` (DuckDB CLI + spatial + httpfs **and** Python 3 for the fetch/merge scripts — v1.0.5 lacks Python; must run on linux/amd64). Steps:

1. **Resolve latest release**: cheap bucket listing of `release/*/` (directory-level listing, NOT a full data glob per version), take lexicographically greatest version. Regex extracts `2026-08-19.0` from paths.
2. **Cache the division part locally**: single column-pruned download (`id, country, subtype, names, sources`) of the resolved release; the cached part is deleted after the run.
3. **Lookup per candidate** `(country, osm_id, admin_level)` against the cached file in **one local SQL pass** (deterministic strategy above → `division_id`).
4. **Geometry**: `division_area` fetched remotely per resolved `division_id IN (...)` with `is_land = true` and `ST_Union_Agg` aggregation per division → exactly one deterministic geometry per division. `division_area` stores up to two area rows per division (land area vs. territorial hull); the territorial row is a maritime envelope (e.g. Norway `BOX(4.09..31.76)` vs. land `BOX(4.51..31.17)`) and is **excluded** — administrative boundary polygons must follow the coastline, not the EEZ hull. Naive `SELECT DISTINCT ... geometry` + keep-first row order was non-deterministic across runs (pipeline build 328 accidentally shipped territorial polygons for 10 countries).
5. **Artifact**: `overture-polygons/<COUNTRY>/<osm_id>.geojsonseq` features containing **only** `osm_id` + `geometry`, plus a manifest `manifest.json` (release + osm_id → country, admin_level, division_id).
6. **Verified end-to-end (fixed)**: 29/29 candidates resolved deterministically on release 2026-08-19.0; `is_land = true` + `ST_Union_Agg` selects the land area (every division carries exactly one land row + one territorial row; the territorial row is a maritime hull and is excluded, e.g. Indonesia `POLYGON@24,811` territorial vs `MULTIPOLYGON@62,703` land).

SQL is issued through the DuckDB CLI via a template with `sed` token substitution, following the existing pattern in `convert_to_parquet.sh:46-52` (DuckDB `COPY ... TO` requires literal paths; no `getvariable`).

Errors fail explicitly with diagnostics (AGENTS.md §5); the optional stage is isolated so a fetch outage cannot block export/package.

### 4.3 Consumption — `scripts/merge_overture_polygons.py` (new)

Run in the `export` job directly after `filter_polygons.py`:

1. Load region `*-validation.json` → genuine broken set (ISO matched to `country_filter`).
2. Load Overture artifact(s) → geometry map by `osm_id`.
3. Read `filter_polygons.py` output → determine which broken relations are **actually absent**.
4. For exactly those: build a feature from Overture geometry + OSM metadata (name, admin_level, `©id`, tags) from validation.json; append to the stream.
5. Missing artifact / unresolved lookup → explicit failure (no silent fallback).

Downstream stages (simplify / parquet / package): **no changes**.

### 4.4 Pipeline — `polygon-export-pipeline.yml`

1. New boolean parameter `fetchOverturePolygons` (default `false`).
2. New stage `overture` (container `osm2parquet:v1.0.6`, `condition: eq(parameters.fetchOverturePolygons, true)`), produces `overture-polygons` artifact.
3. Download step in the `export` job (same pattern as PBF preload, e.g. `itemPattern: 'overture-polygons/**'`).
4. Invoke `merge_overture_polygons.py` after `filter_polygons.py`.

## 5. Tests

1. **Unit:** candidate ISO filter logic (exactly 29, no spillover); `merge_overture_polygons.py` schema mapping; lookup SQL determinism (division with multiple OSM sources); `fetch_overture_polygons.py` geometry SQL selects `is_land = true` land rows only, unions split/duplicate land rows deterministically, and never falls back to the territorial hull (tested end-to-end against a local fake `division_area` table via the DuckDB CLI, AGENTS.md §9/§18).
2. **Integration (US):** preload yields 1 feature (osm_id 148838, complete geometry incl. islands) from latest release; after merge US L2 present in `admin-polygons-north-america.parquet` with OSM properties.
3. **Regression:** FR/NO/PT/Kosovo broken-but-present remain unmodified (no overwrite).

All tests run locally with the DuckDB CLI and mirror the CI execution path (AGENTS.md §9).

Deterministic full-run evidence (2026-08-19.0):
```sql
-- resolution: all 29 candidates map to a division_id (single local pass)
-- NO + Kosovo are the two canonical L2 edge cases:
--   NO country      -> Overture record_id r1059668@154  (not r2978650@) ; resolved via subtype='country'
--   Kosovo (XK/2088990) -> Overture country='XK', record_id r2088990@513 ; country tag matches the kosovo matrix job (COUNTRY_CODE=XK)
```

## 6. Risks & Mitigations

- **Latest-release detection must not scan all old releases** — use directory-level listing, not data globs.
- **Fragile lookups** — resolved by caching the division part locally and matching the exact expected OSM object (`subtype='country'` for L2, `r<id>@` prefix for regions); multi-source divisions (e.g. US node+relation) no longer cause variance.
- **Ambiguous area rows** — `division_area` has land and territorial rows per division; selection is pinned to `is_land = true` with `ST_Union` aggregation so runs are deterministic and landlocked/coastal geometry is never replaced by an EEZ hull.
- **Lookup latency** — country-pruning verified; one local pass over the cached part ≈ seconds.
- **Overture schema drift** — new releases could alter `sources` structure; manifest + explicit failure surfaces this.
- **Release transition between preload and consumption** — manifest records the source release for full traceability.