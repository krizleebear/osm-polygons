-- DuckDB SQL script to export filtered OSM postal code boundaries into OGC GeoParquet 1.1 format.
-- Placeholders (__COUNTRY_CODE__, __INPUT_JSONL__, __OUTPUT_PARQUET__, __COUNTRIES_JSON__, __EXPORTED_AT__, __OSM_DATA_TIMESTAMP__) are replaced via sed before execution.
-- Note: continent is resolved via __COUNTRIES_JSON__ lookup per country_code.

INSTALL spatial;
LOAD spatial;
INSTALL json;
LOAD json;
SET geometry_always_xy = true;

-- Load country -> continent/name mapping from SSOT (scripts/countries.json)
CREATE TEMP TABLE countries_meta AS
    SELECT key AS cc, value->>'continent' AS continent
    FROM (
        SELECT unnest(json_keys(doc)) AS key, doc->(unnest(json_keys(doc))) AS value
        FROM (SELECT json(content) AS doc FROM read_text('__COUNTRIES_JSON__'))
    );

COPY (
    WITH parsed_json AS (
        SELECT
            COALESCE(m.continent, NULLIF(p.continent, ''), 'unknown') AS continent,
            COALESCE(NULLIF('__COUNTRY_CODE__', ''), p.country_code) AS country_code,
            TRY_CAST(p.osm_id AS BIGINT) AS osm_id,
            p.osm_type,
            NULLIF(TRIM(p.postal_code), '') AS postal_code,
            NULLIF(TRIM(p.name), '') AS name,
            ST_GeomFromGeoJSON(p.geom_json) AS geom_raw,
            p.tags
        FROM read_json('__INPUT_JSONL__',
            format='newline_delimited',
            columns={
                'continent': 'VARCHAR',
                'country_code': 'VARCHAR',
                'osm_id': 'BIGINT',
                'osm_type': 'VARCHAR',
                'postal_code': 'VARCHAR',
                'name': 'VARCHAR',
                'geom_json': 'VARCHAR',
                'tags': 'VARCHAR'
            },
            maximum_object_size=134217728,
            ignore_errors=true
        ) p
        LEFT JOIN countries_meta m ON m.cc = COALESCE(NULLIF('__COUNTRY_CODE__', ''), p.country_code)
        WHERE p.osm_id IS NOT NULL
          AND NULLIF(TRIM(p.postal_code), '') IS NOT NULL
          AND p.geom_json IS NOT NULL
    ),
    raw_postal_codes AS (
        SELECT
            continent,
            country_code,
            osm_id,
            osm_type,
            postal_code,
            name,
            CASE
                WHEN ST_NPoints(geom_raw) > 50000
                THEN ST_SimplifyPreserveTopology(geom_raw, 0.00005)
                ELSE geom_raw
            END AS geom,
            tags,
            ROW_NUMBER() OVER (
                PARTITION BY osm_type, osm_id
                ORDER BY postal_code
            ) AS rn
        FROM parsed_json
    )
    SELECT
        continent,
        country_code,
        osm_id,
        osm_type,
        postal_code,
        name,
        ST_XMin(geom) AS bbox_minx,
        ST_YMin(geom) AS bbox_miny,
        ST_XMax(geom) AS bbox_maxx,
        ST_YMax(geom) AS bbox_maxy,
        ST_X(ST_PointOnSurface(geom)) AS center_lon,
        ST_Y(ST_PointOnSurface(geom)) AS center_lat,
        ST_Area_Spheroid(geom) AS area_m2,
        tags,
        geom
    FROM raw_postal_codes
    WHERE rn = 1
      AND geom IS NOT NULL
      AND NOT ST_IsEmpty(geom)
      AND ST_GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON')
      AND ST_IsValid(geom)
      AND ST_XMin(geom) >= -180.0 AND ST_XMax(geom) <= 180.0
      AND ST_YMin(geom) >= -90.0 AND ST_YMax(geom) <= 90.0
    ORDER BY continent, country_code, ST_Hilbert(ST_PointOnSurface(geom), ST_Extent(ST_Extent_Agg(geom) OVER ()))
) TO '__OUTPUT_PARQUET__' (
    FORMAT PARQUET,
    COMPRESSION ZSTD,
    ROW_GROUP_SIZE 5000,
    KV_METADATA {
        'source': 'OpenStreetMap',
        'origin': 'OpenStreetMap (https://www.openstreetmap.org)',
        'dataset': 'OpenStreetMap Postal Codes',
        'attribution': '© OpenStreetMap contributors',
        'attribution_url': 'https://www.openstreetmap.org/copyright',
        'license': 'ODbL-1.0 (https://opendatacommons.org/licenses/odbl/)',
        'license_url': 'https://opendatacommons.org/licenses/odbl/',
        'copyright': 'Data © OpenStreetMap contributors, licensed under Open Data Commons Open Database License 1.0 (ODbL)',
        'schema': 'https://github.com/krizleebear/osm-polygons',
        'schema_url': 'https://github.com/krizleebear/osm-polygons',
        'compiler': 'osm-polygons (https://github.com/krizleebear/osm-polygons)',
        'country_code': '__COUNTRY_CODE__',
        'exported_at': '__EXPORTED_AT__',
        'osm_data_timestamp': '__OSM_DATA_TIMESTAMP__'
    }
);
