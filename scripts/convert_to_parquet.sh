#!/bin/bash
set -euo pipefail

# Convert OSM administrative polygon GeoJSON sequence stream to GeoParquet using DuckDB
# Usage: ./scripts/convert_to_parquet.sh <CONTINENT> <COUNTRY_CODE> <INPUT_GEOJSONSEQ> <OUTPUT_PARQUET>

if [ "$#" -ne 4 ]; then
    echo "Usage: $0 <CONTINENT> <COUNTRY_CODE> <INPUT_GEOJSONSEQ> <OUTPUT_PARQUET>" >&2
    exit 1
fi

CONTINENT="$1"
COUNTRY_CODE="$2"
INPUT_GEOJSONSEQ="$3"
OUTPUT_PARQUET="$4"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_TEMPLATE="${SCRIPT_DIR}/export_parquet.sql"

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

echo "============================================================"
echo " Converting GeoJSON to GeoParquet"
echo " Continent:    $CONTINENT"
echo " Country Code: $COUNTRY_CODE"
echo " Input:        $INPUT_GEOJSONSEQ ($(wc -l < "$INPUT_GEOJSONSEQ") features, $(du -h "$INPUT_GEOJSONSEQ" | cut -f1))"
echo " Output:       $OUTPUT_PARQUET"
echo "============================================================"

TMP_SQL="$(mktemp /tmp/export_parquet_XXXXXX.sql)"
trap 'rm -f "$TMP_SQL"' EXIT

sed -e "s|__CONTINENT__|${CONTINENT}|g" \
    -e "s|__COUNTRY_CODE__|${COUNTRY_CODE}|g" \
    -e "s|__INPUT_GEOJSONSEQ__|${INPUT_GEOJSONSEQ}|g" \
    -e "s|__OUTPUT_PARQUET__|${OUTPUT_PARQUET}|g" \
    "$SQL_TEMPLATE" > "$TMP_SQL"

duckdb < "$TMP_SQL"

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
