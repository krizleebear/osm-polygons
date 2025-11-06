#!/bin/bash
set -exu

#input file is e.g. germany-latest.osm.pbf
INPUT_PBF=$1
BASENAME=$(basename ${INPUT_PBF} .osm.pbf)
ADMIN_PBF=${BASENAME}.admins.pbf
POLYGON_JSON=${BASENAME}.admin-polygons.geojsonseq

osmium tags-filter --output ${ADMIN_PBF} --overwrite ${INPUT_PBF} boundary=administrative
osmium export ${ADMIN_PBF} --output=temp.geojsonseq --overwrite --config=osmium-export-config.json

#omit polygons without name, admin_level and wikidata reference
cat temp.geojsonseq | grep "admin_level" | grep "name" | grep "wikidata" > ${POLYGON_JSON}
