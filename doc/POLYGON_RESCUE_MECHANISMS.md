# Polygon Rescue Mechanisms — Overview

This document describes all mechanisms implemented in the `osm-polygons` pipeline to compensate for incomplete or broken administrative boundary data from Geofabrik PBF extracts.

## Problem Statement

Geofabrik country extracts use spatial bounding polygons that clip boundary ways at extract boundaries. This causes:

- **Broken member references:** Relations reference ways that lie outside the extract (e.g. shared border ways with neighboring countries).
- **Unclosed polygon rings:** Island/coastal multipolygon relations where outer ways cross the extract boundary, causing `osmium export` to silently drop the entire polygon.
- **Missing parent-child nesting:** Some countries (e.g. US) define L2 boundaries with direct ways instead of nesting L4 child relations.
- **Inconsistent tagging:** Mainland relations sometimes lack `admin_level=2` or have non-standard boundary types.

The result: major administrative entities (countries, states, municipalities) vanish from the exported polygons.

## Pipeline Architecture

```
Geofabrik PBF
    │
    ▼
osmium tags-filter ─── 3-rule extraction (boundary=administrative + sub-municipal + place-based)
    │
    ▼
Filtered Admin PBF
    │
    ├──▶ validate_pbf.py ─── broken ref detection + scanner + parent mapping
    │        ├──▶ *-synthetic-defs.json
    │        ├──▶ *-parent-mapping.json
    │        └──▶ *-validation.json
    │
    └──▶ osmium export ───▶ GeoJSON stream
              │
              ▼
         filter_polygons.py ─── 13 enrichment/repair stages
              │
              ▼
         Final .geojsonseq (rescued + enriched)
```

## Mechanism Index

### A. Validation & Detection (`validate_pbf.py`)

| # | Mechanism | Purpose |
|---|-----------|---------|
| 1 | `AdminBoundaryScanner` | Scan entire PBF to build a registry of all admin boundary relations with membership and parent-child relationships |
| 2 | `osmium check-refs` | Detect relations with broken member way references (ways missing from extract) |
| 3 | `infer_missing_children()` | For L2+ relations without relation-members, find children by ISO3166-2 prefix matching (e.g. US → US-VT, US-TX) |
| 4 | `generate_synthetic_defs()` | Auto-generate synthetic parent definitions for broken relations so `filter_polygons.py` can reconstruct them from child polygons |
| 5 | `generate_parent_mapping()` | Build child→parent mapping for hierarchy enrichment of every polygon feature |
| 6 | `*-validation.json` | Emit per-region validation report (broken relations, admin levels, children) |

### B. Filtering & Enrichment (`filter_polygons.py`)

| # | Mechanism | Purpose |
|---|-----------|---------|
| 7 | Mainland Relation Enforcement | Force `admin_level=2` on 7 hardcoded national mainland relations (FR, NL, GB, NO, PT) |
| 8 | Non-Administrative Boundary Exclusion | Drop political, census, historic, electoral, NUTS/ITL statistical boundaries |
| 9 | L2 Border Way Exclusion | Drop standalone way features tagged `admin_level=2` without `ISO3166-1` (boundary slivers) |
| 10 | Name Fallback Logic | Prevent features from being dropped due to missing names (fallback chain: `name:en` → `official_name` → `name:de` → `short_name` → `ref` → `ISO3166-2` → `ISO3166-1`) |
| 11 | Maritime / Territorial Sea Flagging | Identify and flag maritime polygons with `is_territorial_sea` property |
| 12 | Admin Level 4 Fallback | For 9 countries lacking native L4 (EE, HR, ME, SI, XK, CY, IS, LV, MK), map L5/6/7 to synthetic L4 |
| 13 | Sub-Municipal Admin Level Synthesis | Assign default `admin_level=10` to `local_authority`, `borough`, `traditional`, `statistical`, `cadastral` boundaries |
| 14 | Place-Based Admin Level Synthesis | Assign admin levels to place-tagged boundary relations (suburb→9, quarter→10, village→8, etc.) |
| 15 | Static Synthetic Parent Reconstruction | Reconstruct 13 hardcoded missing parents (Madeira concelhos, Funchal, Kaohsiung, Alaska) from child polygon geometries |
| 16 | Dynamic Synthetic Parent Reconstruction | Load broken relation definitions from `--synthetic-defs` at runtime and reconstruct from child geometries |
| 17 | Parent-Child Hierarchy Enrichment | Add `parent_osm_id`, `parent_iso3166_2`, `parent_name` to every feature via `--parent-mapping` |
| 18 | Admin Centre / Label Enrichment | Resolve and embed `admin_centre` and `label` node coordinates from relation members |
| 19 | Semantic `area_type` Derivation | Classify every feature with a human-readable `area_type` (country, state, county, municipality, suburb, quarter, neighbourhood) |

### C. Pipeline Artifacts

| # | Artifact | Source | Consumer |
|---|----------|--------|----------|
| 20 | `*-synthetic-defs.json` | `validate_pbf.py` | `filter_polygons.py --synthetic-defs` |
| 21 | `*-parent-mapping.json` | `validate_pbf.py` | `filter_polygons.py --parent-mapping` |
| 22 | `*-validation.json` | `validate_pbf.py` | `generate_validation_summary.py` |
| 23 | `validation-summary.md/.json` | `generate_validation_summary.py` | GitHub Release asset |

## Known Gaps

### US admin_level=2 (osm_id 148838)

The US country boundary relation has **1,710 outer ways** spanning the full North American continent. Many border ways are shared with Canada/Mexico and may be incomplete in the Geofabrik US extract. `osmium export` silently drops the relation when it cannot assemble a valid multipolygon.

**Current state:** The 51 US states (L4) are correctly exported with `parent_osm_id=148838` (via `infer_missing_children()`), but the parent polygon itself is missing from the output.

**Potential future fix:** Load missing geometries from Overture Maps using the known relation ID.

### Other Potential L2 Gaps

Any country boundary relation with extensive cross-border ways may exhibit the same issue. The validation summary (`*-validation.json`) tracks these cases.

## Spec Documents

| Spec | Topic |
|------|-------|
| `SPEC_UPSTREAM_OSM_POLYGONS_MISSING_ENTITIES.md` | Root cause analysis and synthetic parent reconstruction |
| `SPEC_OSM_POLYGONS_SUBDIVISIONS.md` | 3-rule extraction filter and admin_level synthesis rules |
| `SPEC_ADMINISTRATIVE_CENTERS.md` | Admin centre / label coordinate extraction |
| `SPEC_GEOPARQUET_ADMIN_POLYGONS.md` | Unsimplified GeoParquet export path |
