-- DuckDB SQL script to export filtered OSM place nodes into OGC GeoParquet 1.1 format.
-- Placeholders (__CONTINENT__, __COUNTRY_CODE__, __INPUT_JSONL__, __OUTPUT_PARQUET__) are replaced via sed before execution.

INSTALL spatial;
LOAD spatial;

COPY (
    SELECT
        COALESCE(NULLIF('__CONTINENT__', ''), continent) AS continent,
        COALESCE(NULLIF('__COUNTRY_CODE__', ''), country_code) AS country_code,
        TRY_CAST(osm_id AS BIGINT) AS osm_id,
        name,
        place_type,
        TRY_CAST(lon AS DOUBLE) AS lon,
        TRY_CAST(lat AS DOUBLE) AS lat,
        wikidata,
        TRY_CAST(population AS BIGINT) AS population,
        alt_names_json,
        tags,
        ST_Point(TRY_CAST(lon AS DOUBLE), TRY_CAST(lat AS DOUBLE)) AS geom
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
        maximum_object_size=134217728,
        ignore_errors=true
    )
    WHERE name IS NOT NULL AND name != ''
      AND lon IS NOT NULL AND lat IS NOT NULL
      AND lon >= -180.0 AND lon <= 180.0
      AND lat >= -90.0 AND lat <= 90.0
    ORDER BY continent, country_code, place_type, lat, lon
) TO '__OUTPUT_PARQUET__' (
    FORMAT PARQUET,
    COMPRESSION ZSTD,
    ROW_GROUP_SIZE 5000
);
