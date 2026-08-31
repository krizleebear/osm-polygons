-- DuckDB SQL script to export filtered OSM facilities into OGC GeoParquet 1.1 format.
-- Placeholders (__COUNTRY_CODE__, __INPUT_JSONL__, __OUTPUT_PARQUET__, __COUNTRIES_JSON__) are replaced via sed before execution.
-- Note: continent is resolved via __COUNTRIES_JSON__ lookup per country_code.

INSTALL spatial;
LOAD spatial;
INSTALL json;
LOAD json;

-- Load country -> continent/name mapping from SSOT (scripts/countries.json)
CREATE TEMP TABLE countries_meta AS
    SELECT key AS cc, value->>'continent' AS continent
    FROM (
        SELECT unnest(json_keys(doc)) AS key, doc->(unnest(json_keys(doc))) AS value
        FROM (SELECT json(content) AS doc FROM read_text('__COUNTRIES_JSON__'))
    );

COPY (
    SELECT
        COALESCE(m.continent, f.continent, 'unknown') AS continent,
        COALESCE(NULLIF('__COUNTRY_CODE__', ''), f.country_code) AS country_code,
        TRY_CAST(f.osm_id AS BIGINT) AS osm_id,
        f.osm_type,
        f.feature_class,
        ST_GeomFromGeoJSON(f.geom_json) AS geom,
        f.tags
    FROM read_json('__INPUT_JSONL__',
        format='newline_delimited',
        columns={
            'continent': 'VARCHAR',
            'country_code': 'VARCHAR',
            'osm_id': 'BIGINT',
            'osm_type': 'VARCHAR',
            'feature_class': 'VARCHAR',
            'geom_json': 'VARCHAR',
            'tags': 'VARCHAR'
        },
        ignore_errors=true
    ) f
    LEFT JOIN countries_meta m ON m.cc = COALESCE(NULLIF('__COUNTRY_CODE__', ''), f.country_code)
    WHERE f.osm_id IS NOT NULL
      AND f.feature_class IS NOT NULL
      AND f.geom_json IS NOT NULL
    ORDER BY continent, country_code, feature_class, osm_type, osm_id
) TO '__OUTPUT_PARQUET__' (
    FORMAT PARQUET,
    COMPRESSION ZSTD,
    ROW_GROUP_SIZE 5000
);
