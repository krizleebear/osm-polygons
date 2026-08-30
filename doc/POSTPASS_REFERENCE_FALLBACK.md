# Postpass Reference Boundary Fallback Architecture

## Problem Statement

Geofabrik country extracts are geographically clipped using rectangular bounding boxes or simplified bounding polygons. This clipping causes cross-border and coastal administrative boundary relations to break:
- **Missing member ways:** Border ways extending into neighboring countries are missing from the country extract, causing `osmium export` to silently drop the relation (e.g. US `148838`, Spain `1311341`, China `270056`, Russia `60189`).
- **Damaged multipolygons:** Island and coastal states/municipalities are truncated at artificial extract borders (e.g. Alaska `1116270`, Madeira concelhos).
- **Previous workarounds:**
  1. *Synthetic parent reconstruction (`ST_Union` / `unary_union`):* Merging child level-4 entities (e.g. US states) into synthetic level-2 boundaries, or level-8 freguesias into synthetic level-7 concelhos. This created topological slivers, gaps, and lost sovereign maritime boundaries.
  2. *Overture Maps fallback:* Fetching external geometries from Overture Maps S3 partitions. This required schema translation, loss of native OSM tags, and complicated spatial overlap heuristics.

---

## Proposed Architecture

Instead of repairing broken PBF extracts or relying on third-party sources (Overture), we pre-fetch intact, native OSM administrative boundary polygons directly from **Postpass** (`postpass.geofabrik.de`), which operates on a full, unclipped planet database.

```
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│ Upstream OSM Download Pipeline (`osm-tools` / `azure-pipelines-download-osm.yml`)        │
│                                                                                           │
│ 1. Download regional Geofabrik PBF extracts                                               │
│ 2. Pre-fetch reference polygons via Postpass API (`postpass_polygon` table)               │
│ 3. Publish artifact `admin-polygons-reference.geojsonseq` (intact L2/L4/L7 polygons)      │
└─────────────────────────────────────────────┬─────────────────────────────────────────────┘
                                              │ Artifact download
                                              ▼
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│ Downstream Export Pipeline (`osm-polygons` / `polygon-export-pipeline.yml`)               │
│                                                                                           │
│ 1. Filter and export PBF extracts with `osmium`                                           │
│ 2. Detect missing / broken relations via `validate_pbf.py`                                │
│ 3. Replace or insert intact geometries directly from `postpass_polygon` reference stream  │
│ 4. Pass unified stream to `CoverageSimplifier` (Topology preservation)                   │
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Postpass API & Performance

### Endpoint & Query Strategy
Postpass provides the optimized table `postpass_polygon` containing pre-assembled multipolygon geometries from `osm2pgsql` with indexed `osm_type` and `osm_id`:

```sql
SELECT 
    osm_type, 
    osm_id, 
    tags, 
    geom 
FROM postpass_polygon 
WHERE osm_type = 'R' AND osm_id = <RELATION_ID>;
```

- **Query Latency:** ~0.10s to 0.30s per relation.
- **Data Integrity:** 100% native OpenStreetMap geometries and properties (including full multilingual `name:*`, `ISO3166-1`, `wikidata`, `admin_level`, `population`).
- **Format:** GeoJSON Feature with standard WGS84 (`EPSG:4326`) geometry.

---

## Reference Dataset Candidates (Complete Set: 42 Relations)

Based on empirical validation across all 174 Geofabrik regional validation reports and legacy synthetic workarounds, the following 42 relations are pre-fetched:

### Level 2: Sovereign Nations (27 Relations)
| ISO | OSM Relation ID | Name | Sub-polygons / Type |
| :--- | :---: | :--- | :--- |
| **AM** | `364066` | Armenia | `MultiPolygon` |
| **BF** | `192783` | Burkina Faso | `MultiPolygon` |
| **BT** | `184629` | Bhutan | `MultiPolygon` |
| **BZ** | `287827` | Belize | `MultiPolygon` |
| **CN** | `270056` | China | `MultiPolygon` |
| **ES** | `1311341` | Spain | `MultiPolygon` |
| **FR** | `2202162` | France | `MultiPolygon` |
| **GB** | `62149` | United Kingdom | `MultiPolygon` |
| **HT** | `307829` | Haiti | `MultiPolygon` |
| **ID** | `304751` | Indonesia | `MultiPolygon` |
| **KG** | `178009` | Kyrgyzstan | `MultiPolygon` |
| **KP** | `192734` | North Korea | `MultiPolygon` |
| **MC** | `1124039` | Monaco | `MultiPolygon` |
| **MY** | `2108121` | Malaysia | `MultiPolygon` |
| **NG** | `192787` | Nigeria | `MultiPolygon` |
| **NL** | `2323309` | Netherlands | `MultiPolygon` |
| **NO** | `2978650` | Norway | `MultiPolygon` |
| **PG** | `307866` | Papua New Guinea | `MultiPolygon` |
| **PH** | `443174` | Philippines | `MultiPolygon` |
| **PT** | `295480` | Portugal | `MultiPolygon` |
| **RU** | `60189` | Russia | `MultiPolygon` |
| **SR** | `287082` | Suriname | `MultiPolygon` |
| **TW** | `449220` | Taiwan | `MultiPolygon` |
| **TZ** | `195270` | Tanzania | `MultiPolygon` |
| **US** | `148838` | United States | `MultiPolygon` |
| **VN** | `49915` | Vietnam | `MultiPolygon` |
| **XK** | `2088990` | Kosovo | `MultiPolygon` |

### Level 4: States / Autonomous Regions (4 Relations)
| Country / Region | OSM Relation ID | Name | Admin Level |
| :--- | :---: | :--- | :---: |
| **US (Alaska)** | `1116270` | Alaska | 4 |
| **CN (Tibet)** | `153292` | 西藏自治区 (Tibet) | 4 |
| **ES (Ceuta)** | `1154756` | Ceuta | 4 |
| **IR (Bushehr)** | `269575` | استان بوشهر (Bushehr) | 4 |

### Level 7: Madeira Municipalities / Concelhos (11 Relations)
| Municipality | OSM Relation ID | Name | Admin Level |
| :--- | :---: | :--- | :---: |
| **Calheta** | `8421420` | Calheta | 7 |
| **Câmara de Lobos** | `8421414` | Câmara de Lobos | 7 |
| **Machico** | `8421411` | Machico | 7 |
| **Ponta do Sol** | `8421416` | Ponta do Sol | 7 |
| **Porto Moniz** | `8421419` | Porto Moniz | 7 |
| **Porto Santo** | `8435154` | Porto Santo | 7 |
| **Ribeira Brava** | `8421415` | Ribeira Brava | 7 |
| **Santa Cruz** | `8421412` | Santa Cruz | 7 |
| **Santana** | `8421417` | Santana | 7 |
| **São Vicente** | `8421418` | São Vicente | 7 |
| **Funchal** | `8421413` | Funchal | 7 |

---

## Simplification & Deprecation of Legacy Mechanisms

With native Postpass reference geometries in place:
1. **Deprecate Overture Maps Fallback:**
   - Remove `fetch_overture_polygons.py` and `merge_overture_polygons.py`.
   - Remove S3 dependency and Overture division ID matching.
2. **Deprecate Synthetic Bottom-Up Parent Merging:**
   - Remove hardcoded `SYNTHETIC_PARENT_DEFS` (Madeira concelhos, Alaska) and `--synthetic-defs` dynamic merging from `filter_polygons.py`.
   - Prevent topological non-manifold errors, micro-slivers, and memory spikes during country-level merges.
3. **Streamlined Feature Insertion:**
   - If a target relation is missing from `osmium export`, simply insert the record from `admin-polygons-reference.geojsonseq`.
