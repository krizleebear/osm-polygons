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

sed -e "s|__COUNTRY_CODE__|${COUNTRY_CODE}|g" \
    -e "s|__INPUT_GEOJSONSEQ__|${INPUT_GEOJSONSEQ}|g" \
    -e "s|__OUTPUT_PARQUET__|${OUTPUT_PARQUET}|g" \
    -e "s|__COUNTRIES_JSON__|${COUNTRIES_JSON}|g" \
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
  MO)  CC_REMAP="MO:CN=MO" ;;        # Macao (CN-MO → MO)
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
  ) TO '${OUTPUT_PARQUET}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 5000);
  "
  echo "Remapped country_code '${OLD_CC}' -> '${NEW_CC}' in ${OUTPUT_PARQUET}"
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
    COUNT(label_lat) AS with_label,
    ROUND(COUNT(center_lat) * 100.0 / COUNT(*), 1) || '%' AS center_coverage
FROM read_parquet('${OUTPUT_PARQUET}');
"
echo " Size: $(du -h "$OUTPUT_PARQUET" | cut -f1) (Compressed ZSTD)"
echo "============================================================"
