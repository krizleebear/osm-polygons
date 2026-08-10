# Polygon Administrative Level Analysis Report

Generated on 2026-08-10 for 48 datasets.

## Upstream Exporter Defect Summary

Found **20 defects/anomalies** to address in `osm-polygons` exporter or `EuropeCountryRegistry`:

| ISO | Dataset | Defect Category | Description | Actionable Recommendation |
|-----|---------|-----------------|-------------|---------------------------|
| BG | BG_bulgaria.admin-polygons.geojsonseq | **CITY_LEVEL_MISMATCH** | Configured cityLevelsOsm=[7] has 0 polygons in dataset | Update EuropeCountryRegistry cityLevelOsm for BG to match actual dataset admin_levels |
| CY | CY_cyprus.admin-polygons.geojsonseq | **STATE_LEVEL_MISMATCH** | Configured stateLevelOsm=4 has 0 polygons in dataset | Update EuropeCountryRegistry stateLevelOsm for CY or export admin_level=4 in osm-polygons |
| EE | EE_estonia.admin-polygons.geojsonseq | **STATE_LEVEL_MISMATCH** | Configured stateLevelOsm=4 has 0 polygons in dataset | Update EuropeCountryRegistry stateLevelOsm for EE or export admin_level=4 in osm-polygons |
| FO | FO_faroe-islands.admin-polygons.geojsonseq | **UNREGISTERED_COUNTRY** | ISO code is not registered in EuropeCountryRegistry | Add CountryConfig entry to EuropeCountryRegistry for FO |
| GE | GE_georgia.admin-polygons.geojsonseq | **UNREGISTERED_COUNTRY** | ISO code is not registered in EuropeCountryRegistry | Add CountryConfig entry to EuropeCountryRegistry for GE |
| HR | HR_croatia.admin-polygons.geojsonseq | **STATE_LEVEL_MISMATCH** | Configured stateLevelOsm=4 has 0 polygons in dataset | Update EuropeCountryRegistry stateLevelOsm for HR or export admin_level=4 in osm-polygons |
| IM | IM_isle-of-man.admin-polygons.geojsonseq | **UNREGISTERED_COUNTRY** | ISO code is not registered in EuropeCountryRegistry | Add CountryConfig entry to EuropeCountryRegistry for IM |
| IS | IS_iceland.admin-polygons.geojsonseq | **STATE_LEVEL_MISMATCH** | Configured stateLevelOsm=4 has 0 polygons in dataset | Update EuropeCountryRegistry stateLevelOsm for IS or export admin_level=4 in osm-polygons |
| LV | LV_latvia.admin-polygons.geojsonseq | **STATE_LEVEL_MISMATCH** | Configured stateLevelOsm=4 has 0 polygons in dataset | Update EuropeCountryRegistry stateLevelOsm for LV or export admin_level=4 in osm-polygons |
| LV | LV_latvia.admin-polygons.geojsonseq | **CITY_LEVEL_MISMATCH** | Configured cityLevelsOsm=[6] has 0 polygons in dataset | Update EuropeCountryRegistry cityLevelOsm for LV to match actual dataset admin_levels |
| MD | MD_moldova.admin-polygons.geojsonseq | **CITY_LEVEL_MISMATCH** | Configured cityLevelsOsm=[6] has 0 polygons in dataset | Update EuropeCountryRegistry cityLevelOsm for MD to match actual dataset admin_levels |
| ME | ME_montenegro.admin-polygons.geojsonseq | **STATE_LEVEL_MISMATCH** | Configured stateLevelOsm=4 has 0 polygons in dataset | Update EuropeCountryRegistry stateLevelOsm for ME or export admin_level=4 in osm-polygons |
| ME | ME_montenegro.admin-polygons.geojsonseq | **CITY_LEVEL_MISMATCH** | Configured cityLevelsOsm=[7] has 0 polygons in dataset | Update EuropeCountryRegistry cityLevelOsm for ME to match actual dataset admin_levels |
| MK | MK_macedonia.admin-polygons.geojsonseq | **STATE_LEVEL_MISMATCH** | Configured stateLevelOsm=4 has 0 polygons in dataset | Update EuropeCountryRegistry stateLevelOsm for MK or export admin_level=4 in osm-polygons |
| PT | PT_azores.admin-polygons.geojsonseq | **MISSING_NATIONAL_LAND_L2** | Level-2 polygons exist, but none represent the national land territory (only marine/exclave borders) | Include main land country relation at admin_level=2 for PT |
| PT | PT_portugal.admin-polygons.geojsonseq | **MISSING_NATIONAL_LAND_L2** | Level-2 polygons exist, but none represent the national land territory (only marine/exclave borders) | Include main land country relation at admin_level=2 for PT |
| RO | RO_romania.admin-polygons.geojsonseq | **CITY_LEVEL_MISMATCH** | Configured cityLevelsOsm=[6] has 0 polygons in dataset | Update EuropeCountryRegistry cityLevelOsm for RO to match actual dataset admin_levels |
| SI | SI_slovenia.admin-polygons.geojsonseq | **STATE_LEVEL_MISMATCH** | Configured stateLevelOsm=4 has 0 polygons in dataset | Update EuropeCountryRegistry stateLevelOsm for SI or export admin_level=4 in osm-polygons |
| TR | TR_turkey.admin-polygons.geojsonseq | **UNREGISTERED_COUNTRY** | ISO code is not registered in EuropeCountryRegistry | Add CountryConfig entry to EuropeCountryRegistry for TR |
| XK | XK_kosovo.admin-polygons.geojsonseq | **STATE_LEVEL_MISMATCH** | Configured stateLevelOsm=4 has 0 polygons in dataset | Update EuropeCountryRegistry stateLevelOsm for XK or export admin_level=4 in osm-polygons |
