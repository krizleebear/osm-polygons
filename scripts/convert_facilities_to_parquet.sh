#!/bin/bash
set -euo pipefail

# Convert OSM Facilities stream to GeoParquet using DuckDB
# Usage: ./scripts/convert_facilities_to_parquet.sh <COUNTRY_CODE> <INPUT_STREAM> <OUTPUT_PARQUET>
# continent is resolved at runtime via scripts/countries.json (SSOT)

if [ "$#" -ne 3 ]; then
    echo "Usage: $0 <COUNTRY_CODE> <INPUT_STREAM> <OUTPUT_PARQUET>" >&2
    exit 1
fi

COUNTRY_CODE="$1"
INPUT_STREAM="$2"
OUTPUT_PARQUET="$3"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_TEMPLATE="${SCRIPT_DIR}/export_facilities.sql"
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
echo " Converting Facilities to GeoParquet"
echo " Country Code: $COUNTRY_CODE"
echo " Input:        $INPUT_STREAM ($(wc -l < "$INPUT_STREAM") features, $(du -h "$INPUT_STREAM" | cut -f1))"
echo " Output:       $OUTPUT_PARQUET"
echo " SSOT:         $COUNTRIES_JSON"
echo "============================================================"

TMP_SQL="$(mktemp /tmp/export_facilities_XXXXXX.sql)"
trap 'rm -f "$TMP_SQL"' EXIT

sed -e "s|__COUNTRY_CODE__|${COUNTRY_CODE}|g" \
    -e "s|__INPUT_JSONL__|${INPUT_STREAM}|g" \
    -e "s|__OUTPUT_PARQUET__|${OUTPUT_PARQUET}|g" \
    -e "s|__COUNTRIES_JSON__|${COUNTRIES_JSON}|g" \
    "$SQL_TEMPLATE" > "$TMP_SQL"

duckdb < "$TMP_SQL"

if [ ! -s "$OUTPUT_PARQUET" ]; then
    echo "ERROR: Parquet conversion failed or output '$OUTPUT_PARQUET' is empty." >&2
    exit 1
fi

echo "============================================================"
echo " OSM Facilities GeoParquet Summary: $OUTPUT_PARQUET"
duckdb -c "
SELECT
    COUNT(*) AS total_facilities,
    COUNT(DISTINCT feature_class) AS distinct_feature_classes,
    COUNT(DISTINCT osm_type) AS distinct_osm_types
FROM read_parquet('${OUTPUT_PARQUET}');
"
echo " Size: $(du -h "$OUTPUT_PARQUET" | cut -f1) (Compressed ZSTD)"
echo "============================================================"
