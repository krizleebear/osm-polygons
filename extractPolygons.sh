#!/bin/bash
set -exu

#input file is e.g. germany-latest.osm.pbf
INPUT_PBF=$1
BASENAME=$(basename ${INPUT_PBF} .osm.pbf)
ADMIN_PBF=${BASENAME}.admins.pbf
POLYGON_JSON=${BASENAME}.admin-polygons.geojsonseq

# Broad osmium filter (expressions are OR-combined; precise refinement happens in filter_polygons.py):
# - Rule 1: boundary=administrative (admin_level 2..11)
# - Rule 2: boundary=local_authority/borough
# - Rule 3: relations with place=suburb/quarter/borough/neighbourhood (type=boundary/multipolygon checked in Python)
osmium tags-filter --output ${ADMIN_PBF} --overwrite ${INPUT_PBF} \
    boundary=administrative \
    boundary=local_authority,borough \
    r/place=suburb,quarter,borough,neighbourhood
osmium export ${ADMIN_PBF} --output=temp.geojsonseq --overwrite --config=osmium-export-config.json

python3 scripts/filter_polygons.py --admin-pbf ${ADMIN_PBF} temp.geojsonseq > ${POLYGON_JSON}
