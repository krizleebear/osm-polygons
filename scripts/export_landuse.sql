-- DuckDB SQL script to export filtered OSM landuse polygons into OGC GeoParquet 1.1 format.
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
    WITH raw_landuse AS (
        SELECT
            COALESCE(m.continent, l.continent, 'unknown') AS continent,
            COALESCE(NULLIF('__COUNTRY_CODE__', ''), l.country_code) AS country_code,
            TRY_CAST(l.osm_id AS BIGINT) AS osm_id,
            l.osm_type,
            l.landuse,
            NULLIF(TRIM(l.name), '') AS name,
            NULLIF(TRIM(l.name_en), '') AS name_en,
            ST_GeomFromGeoJSON(l.geom_json) AS geom,
            l.tags,
            ROW_NUMBER() OVER (
                PARTITION BY l.osm_type, TRY_CAST(l.osm_id AS BIGINT)
                ORDER BY l.landuse
            ) AS rn
        FROM read_json('__INPUT_JSONL__',
            format='newline_delimited',
            columns={
                'continent': 'VARCHAR',
                'country_code': 'VARCHAR',
                'osm_id': 'BIGINT',
                'osm_type': 'VARCHAR',
                'landuse': 'VARCHAR',
                'name': 'VARCHAR',
                'name_en': 'VARCHAR',
                'geom_json': 'VARCHAR',
                'tags': 'VARCHAR'
            },
            ignore_errors=true
        ) l
        LEFT JOIN countries_meta m ON m.cc = COALESCE(NULLIF('__COUNTRY_CODE__', ''), l.country_code)
        WHERE l.osm_id IS NOT NULL
          AND l.landuse IS NOT NULL
          AND l.geom_json IS NOT NULL
    )
    SELECT
        continent,
        country_code,
        osm_id,
        osm_type,
        landuse,
        name,
        name_en,
        ST_XMin(geom) AS bbox_minx,
        ST_YMin(geom) AS bbox_miny,
        ST_XMax(geom) AS bbox_maxx,
        ST_YMax(geom) AS bbox_maxy,
        geom,
        tags
    FROM raw_landuse
    WHERE rn = 1
      AND geom IS NOT NULL
      AND ST_GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON')
      AND ST_IsValid(geom)
      AND ST_XMin(geom) >= -180.0 AND ST_XMax(geom) <= 180.0
      AND ST_YMin(geom) >= -90.0 AND ST_YMax(geom) <= 90.0
    ORDER BY continent, country_code, landuse, osm_type, osm_id
) TO '__OUTPUT_PARQUET__' (
    FORMAT PARQUET,
    COMPRESSION ZSTD,
    ROW_GROUP_SIZE 5000
);
