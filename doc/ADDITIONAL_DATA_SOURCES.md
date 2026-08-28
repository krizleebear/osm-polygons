# Additional Data Sources

This document catalogs external OpenStreetMap-derived data sources that may complement the `osm-polygons` pipeline for ad-hoc analysis, validation, or future integration.

---

## 1. PostPASS — Public OSM PostGIS Instance

| Attribute | Value |
|---|---|
| **URL** | https://postpass.geofabrik.de |
| **Maintainer** | Frederik Ramm (Geofabrik / OSMF board) |
| **Infrastructure** | Geofabrik servers |
| **Repository** | https://github.com/woodpeck/postpass (software) · https://github.com/woodpeck/postpass-ops (operations & schema) |
| **License** | ODbL (data derived from OSM planet) |
| **Format** | SQL queries via HTTP API → GeoJSON response |
| **Last Updated** | Continuously (planet-wide replication) |

### Description

PostPASS is a publicly accessible PostGIS database containing the full OpenStreetMap planet, imported via `osm2pgsql` (Flex schema). It exposes three main tables (`postpass_point`, `postpass_line`, `postpass_polygon`) plus combined views (`postpass_pointpolygon`, `postpass_pointline`). A `land_polygons` table (from the osmdata.openstreetmap.de project) is also available.

Queries are submitted via `curl` or the Overpass Turbo/Ultra frontends using a `{{data:sql}}` magic line. The API supports bounding-box spatial filters via `ST_MakeEnvelope` and PostGIS geometry operations. A formatter endpoint additionally supports output as CSV, TSV, HTML, or Markdown table.

### Use Cases

- **Ad-hoc spatial queries** (e.g. find all amenities within a bounding box, compute buffer zones, spatial intersections)
- **Data validation** (e.g. compare pipeline output against PostPASS query results)
- **Quick prototyping** of SQL-based spatial analysis before committing to a local PostGIS setup
- **Land/water filtering** via the `land_polygons` table (find features in the sea, validate coastal boundaries)

### Limitations

- **No bulk export** — designed for ad-hoc queries, not planet-scale data dumps.
- **No SLA** — service may be unavailable or throttled at any time.
- **No history** — only current-state data, no temporal queries.
- **Read-only** — queries must always return a geometry (or use `geojson=false` for count/aggregation queries).
- **Rate limiting** — three priority queues (slow/medium/fast); expensive queries may queue behind others.

### Pipeline Integration

Not suitable as a primary pipeline data source (no stable artifact, no version pinning). Consider for:
- Post-hoc validation of exported polygon geometries
- Supplemental POI extraction for specific analysis tasks
- Prototype spatial SQL before implementing in local DuckDB/PostGIS

---

## 2. OSM Land Polygons (osmdata.openstreetmap.de)

| Attribute | Value |
|---|---|
| **URL** | https://osmdata.openstreetmap.de/data/land-polygons.html |
| **Maintainer** | Jochen Topf (osmdata project), hosted by FOSSGIS |
| **Repository** | https://github.com/giscience/land-polygons (processing) |
| **License** | ODbL (derived from OpenStreetMap `natural=coastline` ways) |
| **Format** | Shapefile (.shp) — WGS84 (EPSG:4326) and Mercator (EPSG:3857) |
| **Update Cycle** | Derived from current OSM data (irregular, not daily) |

### Description

Polygon datasets representing all land areas on Earth, derived from OSM ways tagged `natural=coastline`. Coastline ways are assembled into polygons, errors are auto-repaired, and large polygons are optionally split into smaller overlapping chunks for rendering performance.

### Variants

| Variant | Projection | Split | Use Case |
|---|---|---|---|
| `land-polygons-complete-4326` | WGS84 | No | Full-precision global land mask |
| `land-polygons-split-4326` | WGS84 | Yes | Faster spatial queries on regional subsets |
| `land-polygons-complete-3857` | Mercator | No | Web map tile rendering |
| `land-polygons-split-3857` | Mercator | Yes | Large-scale tile rendering |
| `simplified-land-polygons-complete-3857` | Mercator | No | Low zoom levels (0–9) |

### Use Cases

- **Land/water classification** for exported administrative polygons
- **Spatial validation** — detect features erroneously placed in water bodies
- **Geocoding enrichment** — add land-surface context to polygon features
- **Coastline-aware generalization** — ensure simplified polygons respect land boundaries

### Limitations

- **Coastline data quality** — OSM coastlines are often broken; auto-repair does not always succeed, resulting in gaps or artifacts.
- **Split variant overlap** — large polygons are split into overlapping chunks; area calculations require `ST_Union` to avoid double-counting.
- **No water polygons** — only land areas; water polygons are available separately at https://osmdata.openstreetmap.de/data/water-polygons.html (same ODbL license).

### Pipeline Integration

Suitable for direct pipeline integration as a supplementary artifact:
- Download and cache the WGS84 variant as a pipeline artifact (same pattern as preloaded PBFs)
- Use in DuckDB spatial queries to validate polygon placement (land vs. water)
- Consider for enrichment of GeoParquet output with land-cover metadata

---

## License Compliance (Both Sources)

Both data sources are derived from OSM and licensed under the **Open Database License (ODbL)**:

- **Attribution required**: `© OpenStreetMap contributors` must be included in any published output.
- **Share-Alike**: Derivative databases must be published under ODbL.
- **Commercial use**: Permitted under ODbL.
- **Plate notice**: A physical attribution notice is not required for purely digital distribution, but a license notice must accompany the data.

For the `osm-polygons` project, the existing ODbL license file in the repository root covers attribution requirements for all OSM-derived data.
