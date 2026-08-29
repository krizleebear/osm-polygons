-- DuckDB SQL script to export filtered OSM place nodes into OGC GeoParquet 1.1 format.
-- Placeholders (__COUNTRY_CODE__, __INPUT_JSONL__, __OUTPUT_PARQUET__, __COUNTRIES_JSON__) are replaced via sed before execution.
-- Note: continent is resolved via __COUNTRIES_JSON__ lookup per country_code (not a static token anymore).

INSTALL spatial;
LOAD spatial;

-- Load country -> continent/name mapping from SSOT (scripts/countries.json)
CREATE TEMP TABLE countries_meta AS
    SELECT key AS cc, value->>'continent' AS continent
    FROM (
        SELECT unnest(json_keys(doc)) AS key, doc->(unnest(json_keys(doc))) AS value
        FROM (SELECT parse_json(read_text('__COUNTRIES_JSON__')) AS doc)
    );

COPY (
    SELECT
        COALESCE(m.continent, p.continent, 'unknown') AS continent,
        COALESCE(NULLIF('__COUNTRY_CODE__', ''), p.country_code) AS country_code,
        TRY_CAST(p.osm_id AS BIGINT) AS osm_id,
        p.name,
        p.place_type,
        TRY_CAST(p.lon AS DOUBLE) AS lon,
        TRY_CAST(p.lat AS DOUBLE) AS lat,
        p.wikidata,
        TRY_CAST(p.population AS BIGINT) AS population,
        p.alt_names_json,
        p.tags,
        ST_Point(TRY_CAST(p.lon AS DOUBLE), TRY_CAST(p.lat AS DOUBLE)) AS geom
    FROM read_json('__INPUT_JSONL__',
        format='newline_delimited',
        columns={
            'continent': 'VARCHAR',
            'country_code': 'VARCHAR',
            'osm_id': 'BIGINT',
            'name': 'VARCHAR',
            'place_type': 'VARCHAR',
            'lon': 'DOUBLE',
            'lat': 'DOUBLE',
            'wikidata': 'VARCHAR',
            'population': 'BIGINT',
            'alt_names_json': 'VARCHAR',
            'tags': 'VARCHAR'
        },
        ignore_errors=true
    ) p
    LEFT JOIN countries_meta m ON m.cc = COALESCE(NULLIF('__COUNTRY_CODE__', ''), p.country_code)
    WHERE p.name IS NOT NULL AND p.name != ''
      AND p.lon IS NOT NULL AND p.lat IS NOT NULL
      AND p.lon >= -180.0 AND p.lon <= 180.0
      AND p.lat >= -90.0 AND p.lat <= 90.0
    ORDER BY continent, country_code, place_type, lat, lon
) TO '__OUTPUT_PARQUET__' (
    FORMAT PARQUET,
    COMPRESSION ZSTD,
    ROW_GROUP_SIZE 5000
);
