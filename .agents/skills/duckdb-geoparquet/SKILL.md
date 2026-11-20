---
name: duckdb-geoparquet
description: >-
  Use when writing, editing, or debugging DuckDB SQL exports, GeoParquet schemas,
  spatial sorting, Parquet footer metadata, or remote release diagnostics.
---

# DuckDB & GeoParquet Conventions

## 1. Bounded Hilbert Sorting & BBox Schema
- Sort polygon, linestring, and facility exports by:
  `ORDER BY continent, country_code, ST_Hilbert(ST_PointOnSurface(geom), ST_Extent(ST_Extent_Agg(geom) OVER ()))`
- Never call unbounded `ST_Hilbert(geom)` (forces Float32 universal domain `-3.4e38..+3.4e38`, corrupting bit-interleaving and destroying Row-Group spatial locality).
- Never use planar `ST_Centroid(geom)` for anchors or Hilbert keys (antimeridian-split features like Taveuni, FJ, `relation/3237101` get misplaced centroids); always use `ST_PointOnSurface(geom)`.
- Include uniform 4-column BBox (`bbox_minx`, `bbox_miny`, `bbox_maxx`, `bbox_maxy` as `DOUBLE`) for pushdown pruning.
- Retain `ROW_GROUP_SIZE 5000` (or `50000` for high-volume layers).

## 2. DuckDB SQL Templates & Streaming
- `COPY ... TO` requires string literal paths (`getvariable()` fails). Use `sed` token substitution (`__INPUT_PBF__`, `__OUTPUT_PARQUET__`) on SQL templates before piping into `duckdb`.
- Configure generous `maximum_object_size` in `read_json()` / `read_ndjson()` for sprawling multipolygons exceeding default thresholds.
- Stream `.places.jsonl` and `.facilities.jsonl` directly via DuckDB `read_json()` / `read_json_auto()` without redundant Python filtering passes.
- Use `read_ndjson()` for fast ad-hoc `.geojsonseq` inspection:
  `SELECT json_extract_string(properties, '$.admin_level') AS lvl, count(*) FROM read_ndjson('<file>.geojsonseq') GROUP BY 1`
- Deduplicate multi-extract consolidations (e.g. Spain + Canary Islands):
  - Polygons: `ROW_NUMBER() OVER (PARTITION BY osm_type, osm_id ORDER BY admin_level)`
  - Place nodes: `ROW_NUMBER() OVER (PARTITION BY osm_id ORDER BY place_type)`

## 3. Parquet Provenance & License Metadata (`KV_METADATA`)
All GeoParquet exports (`export_parquet.sql`, `export_places.sql`, `export_facilities.sql`, `export_landuse.sql`) and any post-processing rewrites must embed in `KV_METADATA`:
- `source`: `OpenStreetMap`
- `origin`: `OpenStreetMap (https://www.openstreetmap.org)`
- `dataset`: Canonical dataset title (e.g. `OpenStreetMap Administrative Polygons`, `OpenStreetMap Places`, `OpenStreetMap Facilities`, `OpenStreetMap Landuse`)
- `attribution`: `© OpenStreetMap contributors`
- `attribution_url`: `https://www.openstreetmap.org/copyright`
- `license`: `ODbL-1.0 (https://opendatacommons.org/licenses/odbl/)`
- `license_url`: `https://opendatacommons.org/licenses/odbl/`
- `copyright`: `Data © OpenStreetMap contributors, licensed under Open Data Commons Open Database License 1.0 (ODbL)`
- `schema` / `schema_url`: Canonical schema URL / documentation reference
- `compiler`: `osm-polygons (https://github.com/krizleebear/osm-polygons)`
- `country_code`: 2-letter ISO code or territory identifier
- `exported_at`: ISO-8601 UTC timestamp (`YYYY-MM-DDTHH:MM:SSZ`)
- `osm_data_timestamp`: ISO-8601 UTC timestamp of OSM PBF data cutoff (`YYYY-MM-DDTHH:MM:SSZ`)

## 4. Remote Release Diagnostics
Always verify release anomalies empirically by querying live GitHub Release assets directly with `httpfs`:
`duckdb -c "INSTALL httpfs; LOAD httpfs; SELECT ... FROM 'https://github.com/krizleebear/osm-polygons/releases/download/<tag>/<file>.parquet'"`
