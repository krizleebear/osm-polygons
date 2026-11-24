#!/bin/bash
set -euo pipefail

# Convert OSM administrative polygon GeoJSON sequence stream to GeoParquet using DuckDB
# Usage: ./scripts/convert_to_parquet.sh <COUNTRY_CODE> <INPUT_GEOJSONSEQ> <OUTPUT_PARQUET>
# continent is resolved at runtime via scripts/countries.json (SSOT)

if [ "$#" -ne 3 ]; then
    echo "Usage: $0 <COUNTRY_CODE> <INPUT_GEOJSONSEQ> <OUTPUT_PARQUET>" >&2
    exit 1
fi

COUNTRY_CODE="$1"
INPUT_GEOJSONSEQ="$2"
OUTPUT_PARQUET="$3"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_TEMPLATE="${SCRIPT_DIR}/export_parquet.sql"
COUNTRIES_JSON="${SCRIPT_DIR}/countries.json"

if [ ! -f "$INPUT_GEOJSONSEQ" ]; then
    echo "ERROR: Input file '$INPUT_GEOJSONSEQ' not found." >&2
    exit 1
fi

if [ ! -s "$INPUT_GEOJSONSEQ" ]; then
    echo "WARNING: Input file '$INPUT_GEOJSONSEQ' is empty (0 bytes). Skipping Parquet conversion." >&2
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
echo " Converting GeoJSON to GeoParquet"
echo " Country Code: $COUNTRY_CODE"
echo " Input:        $INPUT_GEOJSONSEQ ($(wc -l < "$INPUT_GEOJSONSEQ") features, $(du -h "$INPUT_GEOJSONSEQ" | cut -f1))"
echo " Output:       $OUTPUT_PARQUET"
echo " SSOT:         $COUNTRIES_JSON"
echo "============================================================"

TMP_SQL="$(mktemp /tmp/export_parquet_XXXXXX.sql)"
trap 'rm -f "$TMP_SQL"' EXIT

EXPORTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

DIRNAME="$(dirname "$INPUT_GEOJSONSEQ")"
BASENAME="$(basename "$INPUT_GEOJSONSEQ")"
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
    -e "s|__INPUT_GEOJSONSEQ__|${INPUT_GEOJSONSEQ}|g" \
    -e "s|__OUTPUT_PARQUET__|${OUTPUT_PARQUET}|g" \
    -e "s|__COUNTRIES_JSON__|${COUNTRIES_JSON}|g" \
    -e "s|__EXPORTED_AT__|${EXPORTED_AT}|g" \
    -e "s|__OSM_DATA_TIMESTAMP__|${OSM_DATA_TIMESTAMP}|g" \
    "$SQL_TEMPLATE" > "$TMP_SQL"

duckdb < "$TMP_SQL"

# Per-extract CC remapping: features tagged with a parent country's ISO3166-1
# (e.g. FR for Nouvelle-Calédonie, DK for Greenland) are remapped to the
# extract's own matrix CC. This prevents parquet collisions when the same
# country_code appears in multiple continents.
# Format: MATRIX_CC:OLD_CC=NEW_CC (multiple entries separated by spaces)
CC_REMAP=""
case "$COUNTRY_CODE" in
  NC) CC_REMAP="NC:FR=NC" ;;        # Nouvelle-Calédonie (FR-NC → NC)
  PF) CC_REMAP="PF:FR=PF" ;;        # French Polynesia (FR-PF → PF)
  WF) CC_REMAP="WF:FR=WF" ;;        # Wallis and Futuna (FR-WF → WF)
  GL) CC_REMAP="GL:DK=GL" ;;        # Greenland (DK-GL → GL)
  FO) CC_REMAP="FO:DK=FO" ;;        # Faroe Islands (DK-FO → FO)
  HK) CC_REMAP="HK:CN=HK" ;;        # Hong Kong (CN-HK → HK)
  MO) CC_REMAP="MO:CN=MO" ;;        # Macao (CN-MO → MO)
  GG) CC_REMAP="GG:GB=GG" ;;        # Guernsey (GB-GG → GG)
  JE) CC_REMAP="JE:GB=JE" ;;        # Jersey (GB-JE → JE)
  AS) CC_REMAP="AS:US=AS" ;;        # American Samoa (US-AS → AS)
  GU) CC_REMAP="GU:US=GU" ;;        # Guam (US-GU → GU)
  MP) CC_REMAP="MP:US=MP" ;;        # Northern Mariana Islands (US-MP → MP)
  AW) CC_REMAP="AW:NL=AW" ;;        # Aruba (NL-AW → AW)
  CW) CC_REMAP="CW:NL=CW" ;;        # Curaçao (NL-CW → CW)
  SX) CC_REMAP="SX:NL=SX" ;;        # Sint Maarten (NL-SX → SX)
  BL) CC_REMAP="BL:FR=BL" ;;        # Saint-Barthélemy (FR-BL → BL)
  MF) CC_REMAP="MF:FR=MF" ;;        # Saint-Martin (FR-MF → MF)
  KY) CC_REMAP="KY:GB=KY" ;;        # Cayman Islands (GB-KY → KY)
  TC) CC_REMAP="TC:GB=TC" ;;        # Turks and Caicos Islands (GB-TC → TC)
  VG) CC_REMAP="VG:GB=VG" ;;        # British Virgin Islands (GB-VG → VG)
  AI) CC_REMAP="AI:GB=AI" ;;        # Anguilla (GB-AI → AI)
  MS) CC_REMAP="MS:GB=MS" ;;        # Montserrat (GB-MS → MS)
esac

if [ -n "$CC_REMAP" ]; then
  echo "Applying CC remap: $CC_REMAP"
  OLD_CC=$(echo "$CC_REMAP" | cut -d= -f1 | cut -d: -f2)
  NEW_CC=$(echo "$CC_REMAP" | cut -d= -f2)
  duckdb -c "
  LOAD spatial;
  CREATE TABLE t AS SELECT * FROM read_parquet('${OUTPUT_PARQUET}');
  COPY (
    SELECT * REPLACE(
      CASE WHEN country_code = '${OLD_CC}' THEN '${NEW_CC}' ELSE country_code END AS country_code
    )
    FROM t
  ) TO '${OUTPUT_PARQUET}' (
    FORMAT PARQUET,
    COMPRESSION ZSTD,
    ROW_GROUP_SIZE 5000,
    KV_METADATA {
        'source': 'OpenStreetMap',
        'origin': 'OpenStreetMap (https://www.openstreetmap.org)',
        'dataset': 'OpenStreetMap Administrative Polygons',
        'attribution': '© OpenStreetMap contributors',
        'attribution_url': 'https://www.openstreetmap.org/copyright',
        'license': 'ODbL-1.0 (https://opendatacommons.org/licenses/odbl/)',
        'license_url': 'https://opendatacommons.org/licenses/odbl/',
        'copyright': 'Data © OpenStreetMap contributors, licensed under Open Data Commons Open Database License 1.0 (ODbL)',
        'schema': 'https://github.com/krizleebear/osm-polygons',
        'schema_url': 'https://github.com/krizleebear/osm-polygons',
        'compiler': 'osm-polygons (https://github.com/krizleebear/osm-polygons)',
        'country_code': '${NEW_CC}',
        'exported_at': '${EXPORTED_AT}',
        'osm_data_timestamp': '${OSM_DATA_TIMESTAMP}'
    }
  );
  "
  echo "Remapped country_code '${OLD_CC}' -> '${NEW_CC}' in ${OUTPUT_PARQUET}"
fi

# Multi-country composite extract splitting: assign sub-national polygons (L3+)
# to their respective sovereign country via ISO3166-2 prefix or L2 spatial containment.
MULTI_CCS=""
case "$COUNTRY_CODE" in
  AE) MULTI_CCS="'AE','SA','QA','KW','OM','BH'" ;;
  MY) MULTI_CCS="'MY','SG','BN'" ;;
  HT) MULTI_CCS="'HT','DO'" ;;
  SN) MULTI_CCS="'SN','GM'" ;;
  IL) MULTI_CCS="'IL','PS'" ;;
esac

if [ -n "$MULTI_CCS" ]; then
  echo "Applying multi-country L2 spatial splitting for host ${COUNTRY_CODE} (${MULTI_CCS})..."
  duckdb -c "
  LOAD spatial;
  LOAD json;
  CREATE TEMP TABLE countries_meta AS
      SELECT key AS cc, value->>'continent' AS continent
      FROM (
          SELECT unnest(json_keys(doc)) AS key, doc->(unnest(json_keys(doc))) AS value
          FROM (SELECT json(content) AS doc FROM read_text('${COUNTRIES_JSON}'))
      );
  CREATE TABLE t AS SELECT *, ST_PointOnSurface(geom) AS _pt FROM read_parquet('${OUTPUT_PARQUET}');
  CREATE TABLE l2_bounds AS
      SELECT
          cc AS l2_cc,
          ST_Union_Agg(geom) AS l2_geom,
          CASE WHEN cc != '${COUNTRY_CODE}' THEN 0 ELSE 1 END AS prio
      FROM (
          SELECT country_code AS cc, geom FROM t
          WHERE admin_level = 2 AND country_code IN (${MULTI_CCS})
          UNION ALL
          SELECT 'PS' AS cc, geom FROM t
          WHERE '${COUNTRY_CODE}' = 'IL'
            AND (UPPER(COALESCE(iso3166_2, '')) LIKE 'PS-%' OR (osm_type = 'relation' AND osm_id IN (1473938, 1613659)))
      )
      GROUP BY cc;
  CREATE TABLE resolved AS
      SELECT
          t.* EXCLUDE (_pt),
          CASE
              WHEN t.admin_level = 2 AND t.country_code IN (${MULTI_CCS}) THEN t.country_code
              WHEN regexp_matches(COALESCE(t.iso3166_2, ''), '^[A-Za-z]{2}-')
                   AND UPPER(split_part(t.iso3166_2, '-', 1)) IN (${MULTI_CCS})
              THEN UPPER(split_part(t.iso3166_2, '-', 1))
              WHEN '${COUNTRY_CODE}' = 'IL' AND t.osm_type = 'relation' AND t.osm_id IN (1473938, 1613659, 1803010) THEN 'PS'
              ELSE COALESCE(
                  (SELECT b.l2_cc FROM l2_bounds b WHERE ST_Contains(b.l2_geom, t._pt) ORDER BY b.prio ASC LIMIT 1),
                  (SELECT b.l2_cc FROM l2_bounds b ORDER BY ST_Distance(b.l2_geom, t._pt) ASC, b.prio ASC LIMIT 1),
                  t.country_code
              )
          END AS new_cc
      FROM t;
  COPY (
      SELECT
          r.* EXCLUDE (new_cc) REPLACE (
              COALESCE(m.continent, r.continent) AS continent,
              r.new_cc AS country_code
          )
      FROM resolved r
      LEFT JOIN countries_meta m ON m.cc = r.new_cc
      ORDER BY continent, country_code, admin_level, bbox_miny, bbox_minx
  ) TO '${OUTPUT_PARQUET}' (
      FORMAT PARQUET,
      COMPRESSION ZSTD,
      ROW_GROUP_SIZE 5000,
      KV_METADATA {
          'source': 'OpenStreetMap',
          'origin': 'OpenStreetMap (https://www.openstreetmap.org)',
          'dataset': 'OpenStreetMap Administrative Polygons',
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
  echo "Multi-country split complete for ${OUTPUT_PARQUET}"
fi

if [ ! -s "$OUTPUT_PARQUET" ]; then
    echo "ERROR: Parquet conversion failed or output '$OUTPUT_PARQUET' is empty." >&2
    exit 1
fi

echo "============================================================"
echo " GeoParquet Artifact Summary: $OUTPUT_PARQUET"
duckdb -c "
SELECT
    COUNT(*) AS total_rows,
    COUNT(center_lat) AS with_center,
    COUNT(admin_centre_lat) AS with_admin_centre,
    COUNT(admin_centre_name) AS with_admin_centre_name,
    COUNT(label_lat) AS with_label,
    ROUND(COUNT(center_lat) * 100.0 / COUNT(*), 1) || '%' AS center_coverage
FROM read_parquet('${OUTPUT_PARQUET}');
"
echo " Size: $(du -h "$OUTPUT_PARQUET" | cut -f1) (Compressed ZSTD)"
echo "============================================================"
