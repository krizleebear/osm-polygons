-- DuckDB SQL script to export OSM administrative polygon GeoJSON sequence streams into OGC GeoParquet 1.1 format.
-- Placeholders (__CONTINENT__, __COUNTRY_CODE__, __INPUT_GEOJSONSEQ__, __OUTPUT_PARQUET__) are replaced via sed before execution.

INSTALL spatial;
LOAD spatial;

COPY (
    SELECT
        '__CONTINENT__' AS continent,
        '__COUNTRY_CODE__' AS country_code,
        TRY_CAST(json_extract_string(properties, '$.admin_level') AS TINYINT) AS admin_level,
        COALESCE(json_extract_string(properties, '$.boundary'), 'administrative') AS boundary,
        TRY_CAST(regexp_replace(COALESCE(json_extract_string(properties, '$.@id'), json_extract_string(properties, '$.id'), '0'), '[^0-9]', '', 'g') AS BIGINT) AS osm_id,
        COALESCE(json_extract_string(properties, '$.@type'), 'relation') AS osm_type,
        COALESCE(json_extract_string(properties, '$.name'), json_extract_string(properties, '$.name:en'), json_extract_string(properties, '$.official_name'), '') AS name,
        json_extract_string(properties, '$.name:en') AS name_en,
        json_extract_string(properties, '$.wikidata') AS wikidata,
        COALESCE(json_extract_string(properties, '$.ISO3166-1'), json_extract_string(properties, '$.ISO3166-1:alpha2'), json_extract_string(properties, '$.ISO3166-1:alpha3')) AS iso3166_1,
        json_extract_string(properties, '$.ISO3166-2') AS iso3166_2,
        TRY_CAST(regexp_replace(COALESCE(json_extract_string(properties, '$.parent_osm_id'), ''), '[^0-9]', '', 'g') AS BIGINT) AS parent_osm_id,
        json_extract_string(properties, '$.parent_iso3166_2') AS parent_iso3166_2,
        json_extract_string(properties, '$.parent_name') AS parent_name,
        COALESCE(json_extract_string(properties, '$.postal_code'), json_extract_string(properties, '$.postcode'), json_extract_string(properties, '$.addr:postcode')) AS postal_code,
        COALESCE(json_extract_string(properties, '$.de:amtlicher_gemeindeschluessel'), json_extract_string(properties, '$.ref:INSEE'), json_extract_string(properties, '$.ref'), json_extract_string(properties, '$.de:regionalschluessel')) AS ref,
        ST_XMin(geom) AS bbox_minx,
        ST_YMin(geom) AS bbox_miny,
        ST_XMax(geom) AS bbox_maxx,
        ST_YMax(geom) AS bbox_maxy,
        properties AS tags,
        geom
    FROM (
        SELECT 
            properties,
            ST_GeomFromGeoJSON(to_json(geometry)) AS geom
        FROM read_json('__INPUT_GEOJSONSEQ__', format='auto')
        WHERE json_extract_string(properties, '$.admin_level') IS NOT NULL
          AND COALESCE(json_extract_string(properties, '$.boundary'), 'administrative') NOT IN ('maritime', 'census', 'electoral')
          AND (json_extract_string(properties, '$.end_date') IS NULL OR json_extract_string(properties, '$.end_date') = '')
          AND (json_extract_string(properties, '$.historic') IS NULL OR json_extract_string(properties, '$.historic') = 'no')
          AND (json_extract_string(properties, '$.admin_type:FR') IS NULL OR json_extract_string(properties, '$.admin_type:FR') != 'ancienne commune')
    )
    WHERE geom IS NOT NULL 
      AND ST_GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON')
      AND ST_IsValid(geom)
    ORDER BY continent, country_code, admin_level, bbox_miny, bbox_minx
) TO '__OUTPUT_PARQUET__' (
    FORMAT PARQUET, 
    COMPRESSION ZSTD, 
    ROW_GROUP_SIZE 5000
);
