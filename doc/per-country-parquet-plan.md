# Per-Country GeoParquet + index.json — Architecture Plan

## Motivation

### Problem 1: GitHub Release 2 GiB Asset Size Limit

Continental GeoParquet files grew dangerously close to the 2 GiB hard limit enforced by GitHub
Releases. The `admin-polygons-europe.parquet` in v2.3.7 reached **1.987 GB**, leaving only ~13 MB
of headroom. Every new region added or every OSM data growth cycle risked a build failure at
release time.

Previous mitigations (externalising Russia, then Turkey into Asia) were workarounds that caused
political continent misclassifications and required repeated manual corrections.

### Problem 2: Continent Misclassification Bugs (Repeated Corrections)

The continental aggregation model required every country to be assigned to exactly one Geofabrik
continental extract path, which did not always match political reality:

- **Canary Islands** (`ES`): Geofabrik serves under `africa/`, causing Spain's national L2 polygon
  (`osm_id=1311341`) to land in `admin-polygons-africa.parquet` instead of Europe. Empirically
  verified via DuckDB against v2.3.7: `SELECT * FROM admin-polygons-europe.parquet WHERE osm_id=1311341`
  returned 0 rows; the relation was found in `admin-polygons-africa.parquet`.
- **Turkey**: Was shuffled between Europe and Asia multiple times purely to manage file size.
- **French Overseas Territories** (Guadeloupe, Martinique, Guyane, Réunion, Mayotte): Not
  represented at all, despite being EU outermost regions.

### Problem 3: No Glob Support for HTTP Range Queries

DuckDB's `httpfs` extension does not support wildcard globbing against GitHub Release asset URLs
(unlike S3). Users wanting to query multiple countries had no way to discover available files
without hardcoding URLs. A machine-readable catalog was missing entirely.

### Problem 4: Northern Ireland / Belfast Geocoding Errors (Repeated Corrections)

The `ireland-and-northern-ireland` Geofabrik extract uses matrix `COUNTRY_CODE=IE`. Because
`country_code` in the Parquet was derived from the matrix value rather than the feature's own
ISO tags, all Northern Ireland features ended up under `country_code=IE`:

- `Belfast` (admin_level 6/7, `iso3166_2=GB-BFS`) → only under `IE`, not `GB`
- `Northern Ireland` (admin_level 4, `iso3166_2=GB-NIR`) → duplicated under both `IE` and `GB`

Empirically verified via DuckDB against v2.3.7:
```sql
SELECT country_code, admin_level, name, iso3166_2
FROM 'admin-polygons-europe.parquet'
WHERE name ILIKE '%Belfast%' OR name ILIKE '%Northern Ireland%';
-- Result: Belfast only under IE; Northern Ireland duplicated IE+GB
```

This required multiple manual post-hoc corrections and was an ongoing source of geocoding errors.

---

## Solution: Per-Country GeoParquet Files + index.json Catalog

### Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Granularity | One Parquet file per country (`admin-polygons-{CC}.parquet`) | Eliminates size limit, political misclassification, and Geofabrik path coupling |
| File naming | Uppercase ISO 3166-1 alpha-2 CC | Unambiguous, consistent with ISO standard |
| `country_code` derivation | `COALESCE(SUBSTRING(iso3166_2,1,2), iso3166_1, matrix-CC)` | Generic fix for NI/Belfast, Ceuta/Melilla, and all multi-country extract cases |
| Continent assignment | `scripts/countries.json` SSOT | Decouples political continent from Geofabrik server path; single place to update |
| Catalog format | Flat `index.json` with `base_url` + `file_pattern` | Globbing workaround for DuckDB httpfs; minimal, machine-readable |
| Multi-region merge | Automatic via `country_code` grouping | `spain`+`canary-islands`→`ES.parquet`; `portugal`+`azores`→`PT.parquet` |
| Multi-country extract split | Full split by derived `country_code` | `gcc-states`→BH/KW/OM/QA/SA/AE; `ireland-and-northern-ireland`→IE+GB |
| French overseas territories | 5 new DOM export jobs (GP/MQ/GF/RE/YT) | Political EU members, previously missing entirely |
| Turkey | Continent `europe` in SSOT | No longer constrained by file size; correct political assignment |
| Simplify stage | Removed entirely | Topology-simplified GeoJSONSeq product discontinued; reduces pipeline complexity |
| Build matrix | Remains static in YAML | Azure DevOps matrix cannot be populated from a file at parse time; SSOT covers the politically variable dimension (continent/name), not the mechanically stable region→URL mapping |
| `osm-places` | Identical per-country restructuring | Consistency with admin-polygons |

---

## index.json Format

Published as a GitHub Release asset alongside the Parquet files.

```json
{
  "version": "2.4.0",
  "generated_at": "2026-08-29T...",
  "base_url": "https://github.com/krizleebear/osm-polygons/releases/download/v2.4.0/",
  "file_pattern": "admin-polygons-{cc}.parquet",
  "countries": [
    { "cc": "DE", "continent": "europe", "name": "Germany" },
    { "cc": "TR", "continent": "europe", "name": "Turkey" },
    { "cc": "MQ", "continent": "europe", "name": "Martinique" },
    { "cc": "ES", "continent": "europe", "name": "Spain" }
  ]
}
```

### DuckDB Globbing Workaround (intended usage pattern)

```python
import duckdb, json, urllib.request

idx = json.load(urllib.request.urlopen(BASE_URL + "index.json"))
ccs  = [c["cc"] for c in idx["countries"] if c["continent"] == "europe"]
urls = [idx["base_url"] + idx["file_pattern"].replace("{cc}", cc) for cc in ccs]

duckdb.sql("SELECT * FROM read_parquet(?)", params=[urls])
```

---

## country_code Derivation Logic

```sql
COALESCE(
  NULLIF(SUBSTRING(json_extract_string(properties, '$.ISO3166-2'), 1, 2), ''),
  json_extract_string(properties, '$.ISO3166-1'),
  '__COUNTRY_CODE__'
) AS country_code
```

### Why iso3166_2 prefix first?

Sub-national features (admin_level 4–11) rarely carry `ISO3166-1` but consistently carry
`ISO3166-2` (e.g. `GB-NIR`, `GB-BFS`, `ES-CE`). The 2-character prefix is always the ISO 3166-1
alpha-2 code of the sovereign state, per the ISO 3166-2 standard.

This generically resolves:
- Northern Ireland (`GB-NIR`) → `GB`
- Belfast (`GB-BFS`) → `GB`
- Ceuta (`ES-CE`), Melilla (`ES-ML`) → `ES` (previously required a hardcoded `FOREIGN_FEATURE_EXCLUSIONS` entry)
- All `gcc-states` features (`BH-*`,`KW-*`, etc.) → correct individual country codes

---

## Files Changed

| File | Change |
|---|---|
| `scripts/countries.json` | **New.** SSOT: `cc → {continent, name}` for all ~175 countries incl. 5 FR-DOM |
| `scripts/export_parquet.sql` | `country_code` derivation + `continent` from `countries.json` JOIN |
| `scripts/export_places.sql` | Identical change |
| `polygon-export-pipeline.yml` | +5 DOM export jobs; per-country parquet/places stage; simplify stage removed; package stage simplified |
| `polygon-release-pipeline.yml` | Per-country assets; `index.json` generation; simplify references removed |

---

## Empirical Evidence Summary

All findings verified via DuckDB directly against the live v2.3.7 release:

```sql
-- Spain L2 missing from Europe (was in Africa)
SELECT * FROM 'admin-polygons-europe.parquet' WHERE osm_id = 1311341;
-- → 0 rows

-- Belfast misclassified under IE
SELECT country_code, name, iso3166_2
FROM 'admin-polygons-europe.parquet'
WHERE name ILIKE '%Belfast%';
-- → country_code=IE, iso3166_2=GB-BFS

-- UK L2 polygon correctly covers Belfast (geometry check)
SELECT ST_Contains(geom, ST_Point(-5.93, 54.597)) AS contains_belfast
FROM 'admin-polygons-europe.parquet'
WHERE osm_id = 62149;
-- → true (UK L2 polygon intact, Belfast geocoding anchor correct)

-- Europe parquet size
-- → 1,986,968,642 bytes (1.987 GB / 2.000 GB limit = 13 MB headroom)
```
