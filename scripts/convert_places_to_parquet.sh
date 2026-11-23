#!/bin/bash
set -euo pipefail

# Convert OSM Place Nodes stream to GeoParquet using DuckDB
# Usage: ./scripts/convert_places_to_parquet.sh <COUNTRY_CODE> <INPUT_STREAM> <OUTPUT_PARQUET>
# continent is resolved at runtime via scripts/countries.json (SSOT)

if [ "$#" -ne 3 ]; then
    echo "Usage: $0 <COUNTRY_CODE> <INPUT_STREAM> <OUTPUT_PARQUET>" >&2
    exit 1
fi

COUNTRY_CODE="$1"
INPUT_STREAM="$2"
OUTPUT_PARQUET="$3"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_TEMPLATE="${SCRIPT_DIR}/export_places.sql"
COUNTRIES_JSON="${SCRIPT_DIR}/countries.json"

if [ ! -f "$INPUT_STREAM" ]; then
    echo "ERROR: Input file '$INPUT_STREAM' not found." >&2
    exit 1
fi

if [ ! -s "$INPUT_STREAM" ]; then
    echo "WARNING: Input file '$INPUT_STREAM' is empty. Skipping Parquet conversion." >&2
    exit 0
fi

if [ ! -f "$SQL_TEMPLATE" ]; then
    echo "ERROR: SQL template '$SQL_TEMPLATE' not found." >&2
    exit 1
fi

if [ ! -f "$COUNTRIES_JSON" ]; then
    echo "ERROR: SSOT file '$COUNTRIES_JSON' not found." >&2
    exit 1
fi

echo "============================================================"
echo " Converting Place Nodes to GeoParquet"
echo " Country Code: $COUNTRY_CODE"
echo " Input:        $INPUT_STREAM ($(wc -l < "$INPUT_STREAM") features, $(du -h "$INPUT_STREAM" | cut -f1))"
echo " Output:       $OUTPUT_PARQUET"
echo " SSOT:         $COUNTRIES_JSON"
echo "============================================================"

TMP_SQL="$(mktemp /tmp/export_places_XXXXXX.sql)"
trap 'rm -f "$TMP_SQL"' EXIT

EXPORTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

DIRNAME="$(dirname "$INPUT_STREAM")"
BASENAME="$(basename "$INPUT_STREAM")"
PREFIX="${BASENAME%%.*}"
META_FILE="${DIRNAME}/${PREFIX}.meta.json"

OSM_DATA_TIMESTAMP="${4:-${OSM_DATA_TIMESTAMP:-}}"
if [ -z "$OSM_DATA_TIMESTAMP" ] && [ -f "$META_FILE" ]; then
    OSM_DATA_TIMESTAMP=$(python3 -c "import json, sys; print(json.load(open(sys.argv[1])).get('osm_data_timestamp', ''))" "$META_FILE" 2>/dev/null || true)
fi
if [ -z "$OSM_DATA_TIMESTAMP" ]; then
    OSM_DATA_TIMESTAMP="$EXPORTED_AT"
fi

sed -e "s|__COUNTRY_CODE__|${COUNTRY_CODE}|g" \
    -e "s|__INPUT_JSONL__|${INPUT_STREAM}|g" \
    -e "s|__OUTPUT_PARQUET__|${OUTPUT_PARQUET}|g" \
    -e "s|__COUNTRIES_JSON__|${COUNTRIES_JSON}|g" \
    -e "s|__EXPORTED_AT__|${EXPORTED_AT}|g" \
    -e "s|__OSM_DATA_TIMESTAMP__|${OSM_DATA_TIMESTAMP}|g" \
    "$SQL_TEMPLATE" > "$TMP_SQL"

duckdb < "$TMP_SQL"

MULTI_CCS=""
case "$COUNTRY_CODE" in
  AE) MULTI_CCS="'AE','SA','QA','KW','OM','BH'" ;;
  MY) MULTI_CCS="'MY','SG','BN'" ;;
  HT) MULTI_CCS="'HT','DO'" ;;
  SN) MULTI_CCS="'SN','GM'" ;;
  IL) MULTI_CCS="'IL','PS'" ;;
esac

ADMIN_PARQUET="${ADMIN_PARQUET:-$(dirname "$OUTPUT_PARQUET")/../temp_parquet/${PREFIX}.admin-polygons.parquet}"
if [ -n "$MULTI_CCS" ] && [ -f "$ADMIN_PARQUET" ] && [ -s "$ADMIN_PARQUET" ]; then
  echo "Applying multi-country L2 spatial splitting for places in ${OUTPUT_PARQUET} using ${ADMIN_PARQUET}..."
  duckdb -c "
  LOAD spatial;
  LOAD json;
  CREATE TEMP TABLE countries_meta AS
      SELECT key AS cc, value->>'continent' AS continent
      FROM (
          SELECT unnest(json_keys(doc)) AS key, doc->(unnest(json_keys(doc))) AS value
          FROM (SELECT json(content) AS doc FROM read_text('${COUNTRIES_JSON}'))
      );
  CREATE TABLE a AS SELECT * FROM read_parquet('${ADMIN_PARQUET}');
  CREATE TABLE l2_bounds AS
      SELECT
          cc AS l2_cc,
          ST_Union_Agg(geom) AS l2_geom,
          CASE WHEN cc != '${COUNTRY_CODE}' THEN 0 ELSE 1 END AS prio
      FROM (
          SELECT country_code AS cc, geom FROM a
          WHERE admin_level = 2 AND country_code IN (${MULTI_CCS})
          UNION ALL
          SELECT 'PS' AS cc, geom FROM a
          WHERE '${COUNTRY_CODE}' = 'IL'
            AND (UPPER(COALESCE(iso3166_2, '')) LIKE 'PS-%' OR (osm_type = 'relation' AND osm_id IN (1473938, 1613659)))
      )
      GROUP BY cc;
  CREATE TABLE t AS SELECT * FROM read_parquet('${OUTPUT_PARQUET}');
  CREATE TABLE resolved AS
      SELECT
          t.*,
          COALESCE(
              (SELECT b.l2_cc FROM l2_bounds b WHERE ST_Contains(b.l2_geom, t.geom) ORDER BY b.prio ASC LIMIT 1),
              (SELECT b.l2_cc FROM l2_bounds b ORDER BY ST_Distance(b.l2_geom, t.geom) ASC, b.prio ASC LIMIT 1),
              t.country_code
          ) AS new_cc
      FROM t;
  COPY (
      SELECT
          r.* EXCLUDE (new_cc) REPLACE (
              COALESCE(m.continent, r.continent) AS continent,
              r.new_cc AS country_code
          )
      FROM resolved r
      LEFT JOIN countries_meta m ON m.cc = r.new_cc
      ORDER BY continent, country_code, place_type, lat, lon
  ) TO '${OUTPUT_PARQUET}' (
      FORMAT PARQUET,
      COMPRESSION ZSTD,
      ROW_GROUP_SIZE 5000,
      KV_METADATA {
          'source': 'OpenStreetMap',
          'origin': 'OpenStreetMap (https://www.openstreetmap.org)',
          'dataset': 'OpenStreetMap Places',
          'attribution': '© OpenStreetMap contributors',
          'attribution_url': 'https://www.openstreetmap.org/copyright',
          'license': 'ODbL-1.0 (https://opendatacommons.org/licenses/odbl/)',
          'license_url': 'https://opendatacommons.org/licenses/odbl/',
          'copyright': 'Data © OpenStreetMap contributors, licensed under Open Data Commons Open Database License 1.0 (ODbL)',
          'schema': 'https://github.com/krizleebear/osm-polygons',
          'schema_url': 'https://github.com/krizleebear/osm-polygons',
          'compiler': 'osm-polygons (https://github.com/krizleebear/osm-polygons)',
          'country_code': '${COUNTRY_CODE}',
          'exported_at': '${EXPORTED_AT}',
          'osm_data_timestamp': '${OSM_DATA_TIMESTAMP}'
      }
  );
  "
fi

if [ ! -s "$OUTPUT_PARQUET" ]; then
    echo "ERROR: Parquet conversion failed or output '$OUTPUT_PARQUET' is empty." >&2
    exit 1
fi

echo "============================================================"
echo " OSM Place Nodes GeoParquet Summary: $OUTPUT_PARQUET"
duckdb -c "
SELECT
    COUNT(*) AS total_places,
    COUNT(wikidata) AS with_wikidata,
    COUNT(population) AS with_population,
    COUNT(DISTINCT place_type) AS distinct_place_types
FROM read_parquet('${OUTPUT_PARQUET}');
"
echo " Size: $(du -h "$OUTPUT_PARQUET" | cut -f1) (Compressed ZSTD)"
echo "============================================================"
