#!/usr/bin/env python3
"""
Filter and enhance OSM admin polygon GeoJSON sequence streams for osm-polygons pipeline.

Main tasks:
1. Enforce admin_level=2 for key national mainland relations (FR 11980/2202162, NL 47796/2323309, GB 62149, NO 2978650, PT 295438).
2. Exclude non-administrative and political boundaries (boundary IN ('political', 'statistical', 'census', 'historic'), political=*, election=*).
3. Exclude non-relation border sliver ways (@type=way) tagged admin_level=2 lacking official ISO3166-1 tags.
4. Prevent tag loss: fallback missing or null 'name' tags to name:en, official_name, ISO3166-1, or default.
5. Flag maritime / territorial sea polygons (border_type=territorial, maritime=yes) to prioritize landmasses.
6. Provide mapped level-4 fallback properties for countries lacking native admin_level=4 relations.
7. Synthesize admin_level for sub-municipal boundaries per SPEC_OSM_POLYGONS_SUBDIVISIONS.md:
   - Rule 2: boundary=local_authority/borough without admin_level -> default "10".
   - Rule 3: type=boundary/multipolygon relations with place=suburb/quarter/neighbourhood/borough -> default 9/10/11/9.
8. Keep nameless admin_level=2 land borders that carry ISO3166-1 (instead of dropping them).
9. Synthesize missing major parent entities (e.g. Funchal, Kaohsiung) from constituent sub-divisions
   per SPEC_UPSTREAM_OSM_POLYGONS_MISSING_ENTITIES.md when unclosed island/coastal rings cause osmium export drop.
10. Enrich administrative boundary relations with official admin_centre and label member coordinates
    (center_lat, center_lon, admin_centre:lat, admin_centre:lon, label:lat, label:lon) per SPEC_ADMINISTRATIVE_CENTERS.md.
"""

import os
import sys
import json
import argparse
import subprocess

# National mainland relations that must be preserved as admin_level=2
MAINLAND_RELATION_IDS = {
    11980:   {"country": "FR", "default_name": "France"},
    2202162: {"country": "FR", "default_name": "France (Métropole)"},
    47796:   {"country": "NL", "default_name": "Nederland"},
    2323309: {"country": "NL", "default_name": "Nederland"},
    62149:   {"country": "GB", "default_name": "Great Britain"},
    2978650: {"country": "NO", "default_name": "Norway (Mainland)"},
    295438:  {"country": "PT", "default_name": "Portugal (Continental)"},
}

# Non-administrative boundary tag values that must be excluded:
EXCLUDED_BOUNDARY_VALUES = {"political", "census", "historic"}

# Countries known to lack native admin_level=4 relations, mapping fallback levels (e.g., level 6 -> 4)
LEVEL_4_FALLBACK_COUNTRIES = {
    "EE", "HR", "ME", "SI", "XK", "CY", "IS", "LV", "MK"
}

# Foreign administrative entities that leak into a neighbouring country's Geofabrik
# extract bounding box and must NOT be exported under that neighbour's country code.
# Keyed by the Geofabrik COUNTRY_CODE of the export job (e.g. "MA").
# Each entry specifies:
#   iso3166_2_prefixes — ISO3166-2 tag prefixes that identify a foreign country (e.g. "ES-").
#   relation_ids       — explicit OSM relation IDs of the exclave parent entities.
# Only L4 (city-level) exclaves are covered here; L8 subdivisions without ISO tags
# are a documented follow-up (issue #6 — vorerst nur L4).
FOREIGN_FEATURE_EXCLUSIONS = {
    # Morocco: Spanish exclaves Ceuta (r1154756, ES-CE) and Melilla (r5812120, ES-ML)
    # are geographically within the africa/morocco Geofabrik bbox and thus present in
    # morocco-latest.osm.pbf, but they belong to Spain (ISO3166-2 ES-*).
    "MA": {
        "iso3166_2_prefixes": ("ES-",),
        "relation_ids": {1154756, 5812120},
    },
}

# Sub-municipal boundary tag values (Rule 2 of SPEC_OSM_POLYGONS_SUBDIVISIONS.md & feat/named-areas):
# boundary IN (local_authority, borough, traditional, statistical, cadastral) without admin_level -> default admin_level.
SUBMUNICIPAL_BOUNDARY_VALUES = ("local_authority", "borough", "traditional", "statistical", "cadastral")
DEFAULT_SUBMUNICIPAL_ADMIN_LEVEL = "10"

# Place-based boundary relations & areas (Rule 3 of SPEC_OSM_POLYGONS_SUBDIVISIONS.md & feat/named-areas):
# place tag combined with boundary/multipolygon or polygon ways -> default admin_level.
PLACE_TO_ADMIN_LEVEL = {
    "suburb": "9",
    "quarter": "10",
    "neighbourhood": "11",
    "neighborhood": "11",
    "borough": "9",
    "city_block": "11",
    "hamlet": "10",
    "isolated_dwelling": "11",
    "village": "8",
    "town": "8",
    "city": "8",
    "locality": "10",
    "townlet": "9",
}
PLACE_RELATION_TYPES = ("boundary", "multipolygon")

# Synthetic parent entity definitions (deprecated in favor of Postpass reference dataset)
SYNTHETIC_PARENT_DEFINITIONS = {}



def load_synthetic_defs(path):
    """Load synthetic parent definitions from JSON and merge into global dict."""
    global SYNTHETIC_PARENT_DEFINITIONS
    with open(path, "r") as f:
        defs = json.load(f)
    for rid_str, pdef in defs.items():
        rid = int(rid_str)
        if isinstance(pdef.get("child_relation_ids"), list):
            pdef["child_relation_ids"] = set(pdef["child_relation_ids"])
        if isinstance(pdef.get("child_names"), list):
            pdef["child_names"] = set(pdef["child_names"])
        SYNTHETIC_PARENT_DEFINITIONS[rid] = pdef


PARENT_MAPPING = {}


def load_parent_mapping(path):
    """Load child-to-parent mapping from JSON and set global dict."""
    global PARENT_MAPPING
    with open(path, "r") as f:
        PARENT_MAPPING = json.load(f)
    sys.stderr.write(f"Loaded parent mapping: {len(PARENT_MAPPING)} child entries\n")


def extract_relation_centres(admin_pbf_path):
    """
    Extracts admin_centre and label member coordinates from an OSM admin PBF extract.
    Uses 'osmium cat -f opl' to parse relation members and node coordinates efficiently.
    Returns a dict: { rel_id (int): { 'admin_centre': (lon, lat), 'label': (lon, lat) } }
    """
    if not admin_pbf_path or not os.path.exists(admin_pbf_path):
        return {}

    rel_centres = {}
    needed_nodes = set()

    try:
        proc_rel = subprocess.Popen(
            ['osmium', 'cat', admin_pbf_path, '-f', 'opl', '-t', 'relation'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True
        )
        for line in proc_rel.stdout:
            if not line.startswith('r'):
                continue
            parts = line.rstrip('\n').split(' ')
            try:
                rel_id = int(parts[0][1:])
            except (ValueError, IndexError):
                continue
            for part in parts:
                if part.startswith('M'):
                    for m in part[1:].split(','):
                        if not m or not m.startswith('n'):
                            continue
                        if '@admin_centre' in m or '@admin_center' in m:
                            at_idx = m.find('@')
                            try:
                                node_id = int(m[1:at_idx])
                                if rel_id not in rel_centres:
                                    rel_centres[rel_id] = {}
                                rel_centres[rel_id]['admin_centre_id'] = node_id
                                needed_nodes.add(node_id)
                            except ValueError:
                                pass
                        elif '@label' in m:
                            at_idx = m.find('@')
                            try:
                                node_id = int(m[1:at_idx])
                                if rel_id not in rel_centres:
                                    rel_centres[rel_id] = {}
                                rel_centres[rel_id]['label_id'] = node_id
                                needed_nodes.add(node_id)
                            except ValueError:
                                pass
        proc_rel.wait()
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to extract relation members from {admin_pbf_path}: {e}\n")
        return {}

    if not needed_nodes:
        return {}

    node_coords = {}
    try:
        proc_node = subprocess.Popen(
            ['osmium', 'cat', admin_pbf_path, '-f', 'opl', '-t', 'node'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True
        )
        for line in proc_node.stdout:
            if not line.startswith('n'):
                continue
            parts = line.rstrip('\n').split(' ')
            try:
                node_id = int(parts[0][1:])
            except (ValueError, IndexError):
                continue
            if node_id in needed_nodes:
                lon, lat = None, None
                for p in parts:
                    if p.startswith('x'):
                        try:
                            lon = float(p[1:])
                        except ValueError:
                            pass
                    elif p.startswith('y'):
                        try:
                            lat = float(p[1:])
                        except ValueError:
                            pass
                if lon is not None and lat is not None:
                    node_coords[node_id] = (lon, lat)
        proc_node.wait()
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to extract node coordinates from {admin_pbf_path}: {e}\n")
        return {}

    resolved = {}
    for rel_id, members in rel_centres.items():
        res = {}
        if 'admin_centre_id' in members:
            nid = members['admin_centre_id']
            if nid in node_coords:
                res['admin_centre'] = node_coords[nid]
        if 'label_id' in members:
            nid = members['label_id']
            if nid in node_coords:
                res['label'] = node_coords[nid]
        if res:
            resolved[rel_id] = res

    return resolved

def derive_area_type(props, admin_level_str):
    """
    Derives a semantic area_type (e.g. 'quarter', 'suburb', 'hamlet', 'cadastral', 'traditional')
    from OSM tags or admin_level fallback per feat/named-areas specification.
    """
    # 1. Check explicit place tag
    place = str(props.get("place", "")).strip().lower()
    if place in ("quarter", "suburb", "neighbourhood", "neighborhood", "hamlet", "village",
                 "isolated_dwelling", "locality", "city_block", "borough", "townlet", "city", "town",
                 "island", "islet", "archipelago"):
        if place == "neighborhood":
            return "neighbourhood"
        return place

    # 2. Check subdistrict tag
    subdistrict = str(props.get("subdistrict", "")).strip().lower()
    if subdistrict:
        return subdistrict

    # 3. Check specific non-administrative boundary tags
    boundary = str(props.get("boundary", "")).strip().lower()
    if boundary in ("traditional", "statistical", "cadastral", "borough", "local_authority"):
        return boundary

    # 4. Check country-specific semantic tags
    admin_type_fr = str(props.get("admin_type:FR", "")).strip().lower()
    if admin_type_fr in ("quartier", "arrondissement", "commune"):
        return admin_type_fr

    border_type = str(props.get("border_type", "")).strip().lower()
    if border_type in ("suburb", "quarter", "neighbourhood", "borough", "municipality", "county", "state", "province"):
        return border_type

    # 5. Fallback based on admin_level
    try:
        lvl = int(admin_level_str)
        if lvl == 2:
            return "country"
        elif lvl in (3, 4):
            return "state"
        elif lvl in (5, 6):
            return "county"
        elif lvl in (7, 8):
            return "municipality"
        elif lvl == 9:
            return "suburb"
        elif lvl == 10:
            return "quarter"
        elif lvl >= 11:
            return "neighbourhood"
    except (ValueError, TypeError):
        pass

    return "administrative"

def process_feature(data, require_wikidata=False, country_code=None, relation_centres=None, parent_mapping=None):
    if data.get("type") != "Feature":
        return None

    props = data.get("properties")
    if not props or not isinstance(props, dict):
        return None

    # Spec §4.2: only Polygon / MultiPolygon geometries are emitted.
    # osmium tags-filter pulls in referenced member nodes/ways which osmium export
    # would otherwise leak through as Point/LineString features.
    geometry_type = (data.get("geometry") or {}).get("type")
    if geometry_type not in ("Polygon", "MultiPolygon"):
        return None

    # Exclude non-administrative boundaries (political, census, electoral districts)
    boundary_val = str(props.get("boundary", "")).strip().lower()
    if boundary_val in EXCLUDED_BOUNDARY_VALUES:
        return None

    political_val = str(props.get("political", "")).strip().lower()
    if political_val and political_val not in ("no", "false", "0"):
        return None

    if props.get("election:parliament") or props.get("election") or props.get("electoral_district"):
        return None

    historic_val = str(props.get("historic", "")).strip().lower()
    if historic_val and historic_val not in ("no", "false", "0"):
        return None

    if props.get("end_date") and str(props.get("end_date")).strip() != "":
        return None

    admin_type_fr = str(props.get("admin_type:FR", "")).strip().lower()
    if admin_type_fr == "ancienne commune":
        return None

    # Exclude NUTS/ITL statistical macro-regions (only local urban statistical districts are retained)
    if boundary_val == "statistical":
        if any("nuts" in k.lower() or "itl" in k.lower() for k in props.keys()):
            return None

    border_type = str(props.get("border_type", "")).strip().lower()
    if border_type in EXCLUDED_BOUNDARY_VALUES or border_type == "historic":
        return None

    # Check admin_level presence
    raw_level = str(props.get("admin_level", "")).strip()
    osm_id = props.get("id") or props.get("@id") or props.get("osm_id")
    try:
        osm_id_num = int(osm_id) if osm_id else None
    except (ValueError, TypeError):
        osm_id_num = None

    # Task 1: Enforce admin_level=2 for key mainland relations
    if osm_id_num in MAINLAND_RELATION_IDS:
        info = MAINLAND_RELATION_IDS[osm_id_num]
        props["admin_level"] = "2"
        raw_level = "2"
        if not props.get("name"):
            props["name"] = info["default_name"]
        props["ISO3166-1"] = info["country"]

    # Foreign-exclave exclusion: drop features that belong to a neighbouring country's
    # territory but leak into this extract's bounding box (e.g. Ceuta/Melilla in MA).
    # Only applies when a curated exclusion entry exists for the current country_code.
    if country_code:
        excl = FOREIGN_FEATURE_EXCLUSIONS.get(str(country_code).upper().strip())
        if excl:
            iso2_val = str(props.get("ISO3166-2", "")).strip()
            for prefix in excl.get("iso3166_2_prefixes", ()):
                if iso2_val.upper().startswith(prefix.upper()):
                    return None
            if osm_id_num in excl.get("relation_ids", set()):
                return None

    # Filter L2 border ways: Exclude non-relation border ways (@type=way) tagged admin_level=2 without ISO3166-1
    element_type = str(props.get("@type", props.get("osm_type", ""))).strip().lower()
    if (element_type == "way" or element_type != "relation") and raw_level == "2":
        iso_val = str(props.get("ISO3166-1", "")).strip()
        if not iso_val or iso_val.lower() in ("none", "null"):
            return None

    # Task 5: Synthesize admin_level for sub-municipal boundaries & place-based areas
    # (Rule 3 takes precedence over Rule 2)
    rel_type = str(props.get("type", "")).strip().lower()
    place_val = str(props.get("place", "")).strip().lower()
    is_non_area_relation = (element_type == "relation" and rel_type and rel_type not in PLACE_RELATION_TYPES)
    if not is_non_area_relation and place_val in PLACE_TO_ADMIN_LEVEL and (not raw_level or raw_level == "None"):
        props["admin_level"] = PLACE_TO_ADMIN_LEVEL[place_val]
        raw_level = PLACE_TO_ADMIN_LEVEL[place_val]

    if (boundary_val in SUBMUNICIPAL_BOUNDARY_VALUES
            and (not raw_level or raw_level == "None")):
        props["admin_level"] = DEFAULT_SUBMUNICIPAL_ADMIN_LEVEL
        raw_level = DEFAULT_SUBMUNICIPAL_ADMIN_LEVEL

    if not raw_level or raw_level == "None":
        return None

    # Semantic area_type derivation
    props["area_type"] = derive_area_type(props, raw_level)

    # Task 2: Prevent tag-loss by falling back missing/null name tags
    name = props.get("name")
    if not name or str(name).strip() == "" or str(name).lower() == "null":
        fallback_name = (
            props.get("name:en") or
            props.get("official_name") or
            props.get("name:de") or
            props.get("short_name") or
            props.get("ref") or
            props.get("ISO3166-2") or
            props.get("ISO3166-1")
        )
        if fallback_name and str(fallback_name).strip() != "":
            props["name"] = str(fallback_name).strip()
        else:
            # Drop features without any identifiable name, EXCEPT national land borders
            # (admin_level=2 with ISO3166-1, per SPEC_OSM_POLYGONS_SUBDIVISIONS.md §3).
            if raw_level == "2" and props.get("ISO3166-1"):
                props["name"] = str(props.get("ISO3166-1")).strip()
            else:
                return None

    if require_wikidata and not props.get("wikidata"):
        return None

    # Task 3: Flag maritime / territorial sea polygons
    border_type = str(props.get("border_type", "")).lower()
    maritime_tag = str(props.get("maritime", "")).lower()
    name_lower = str(props.get("name", "")).lower()
    
    is_territorial = (
        border_type in ("territorial", "baseline", "maritime") or
        maritime_tag in ("yes", "true", "1") or
        "águas territoriais" in name_lower or
        "territorial sea" in name_lower or
        "maritime border" in name_lower
    )
    if is_territorial:
        props["is_territorial_sea"] = True

    # Task 4: Provide mapped level-4 fallback for countries lacking admin_level=4
    c_code = country_code or props.get("ISO3166-1")
    if c_code and str(c_code).upper() in LEVEL_4_FALLBACK_COUNTRIES:
        if raw_level in ("5", "6", "7") and "admin_level_mapped" not in props:
            props["admin_level_mapped"] = "4"

    # Task 10: Centerpoint & Label coordinates enrichment (SPEC_ADMINISTRATIVE_CENTERS.md)
    if osm_id_num is not None and relation_centres and osm_id_num in relation_centres:
        centres = relation_centres[osm_id_num]
        if "admin_centre" in centres:
            c_lon, c_lat = centres["admin_centre"]
            props["admin_centre:lat"] = c_lat
            props["admin_centre:lon"] = c_lon
        if "label" in centres:
            l_lon, l_lat = centres["label"]
            props["label:lat"] = l_lat
            props["label:lon"] = l_lon

        if "admin_centre" in centres:
            props["center_lat"] = centres["admin_centre"][1]
            props["center_lon"] = centres["admin_centre"][0]
        elif "label" in centres:
            props["center_lat"] = centres["label"][1]
            props["center_lon"] = centres["label"][0]

    # Task 11: Parent-child relationship enrichment
    if osm_id_num is not None and parent_mapping:
        parent_info = parent_mapping.get(str(osm_id_num))
        if parent_info:
            props["parent_osm_id"] = parent_info["parent_osm_id"]
            if parent_info.get("parent_iso3166_2"):
                props["parent_iso3166_2"] = parent_info["parent_iso3166_2"]
            if parent_info.get("parent_name"):
                props["parent_name"] = parent_info["parent_name"]

    return data

class StreamProcessor:
    """
    Processes a stream of GeoJSON features, tracking known entities and synthesizing
    missing parent divisions from constituent child divisions per SPEC_UPSTREAM_OSM_POLYGONS_MISSING_ENTITIES.md.
    """
    def __init__(self, require_wikidata=False, country_code=None, relation_centres=None, admin_pbf=None, parent_mapping=None):
        self.require_wikidata = require_wikidata
        self.country_code = country_code
        self.seen_parents = set()
        self.parent_collected_polygons = {pid: [] for pid in SYNTHETIC_PARENT_DEFINITIONS}
        self.parent_mapping = parent_mapping or PARENT_MAPPING
        if relation_centres is not None:
            self.relation_centres = relation_centres
        elif admin_pbf:
            self.relation_centres = extract_relation_centres(admin_pbf)
        else:
            self.relation_centres = {}

        self.count_total = 0
        self.count_with_center = 0
        self.count_with_admin_centre = 0
        self.count_with_label = 0
        self.count_synthesized = 0
        self.count_with_parent = 0

    def process_line(self, line_str):
        if not line_str or not line_str.strip():
            return None
        try:
            data = json.loads(line_str.strip())
        except json.JSONDecodeError:
            return None

        processed = process_feature(
            data,
            require_wikidata=self.require_wikidata,
            country_code=self.country_code,
            relation_centres=self.relation_centres,
            parent_mapping=self.parent_mapping
        )
        if not processed:
            return None

        self.count_total += 1
        props = processed.get("properties", {})
        if "center_lat" in props:
            self.count_with_center += 1
        if "admin_centre:lat" in props:
            self.count_with_admin_centre += 1
        if "label:lat" in props:
            self.count_with_label += 1
        if "parent_osm_id" in props:
            self.count_with_parent += 1

        osm_id = props.get("id") or props.get("@id") or props.get("osm_id")
        try:
            osm_id_num = int(osm_id) if osm_id else None
        except (ValueError, TypeError):
            osm_id_num = None

        geom = processed.get("geometry")
        if osm_id_num in SYNTHETIC_PARENT_DEFINITIONS:
            # Only consider the parent as "seen" (so synthetic generation is skipped)
            # IF the processed parent actually has a valid, non-empty polygon geometry!
            if geom and (geom.get("type") in ("Polygon", "MultiPolygon")) and geom.get("coordinates"):
                self.seen_parents.add(osm_id_num)

        # Track child polygons for potential parent synthesis
        name = props.get("name", "")
        name_en = props.get("name:en", "")
        admin_lvl = str(props.get("admin_level", "")).strip()
        feature_country = (self.country_code or props.get("ISO3166-1") or "").upper().strip()

        for pid, pdef in SYNTHETIC_PARENT_DEFINITIONS.items():
            if pid in self.seen_parents and not pdef.get("force_collect"):
                continue

            target_country = (pdef.get("country_code") or "").upper().strip()
            if target_country and feature_country and feature_country != target_country:
                continue

            child_ids = pdef.get("child_relation_ids", set())
            child_names = pdef.get("child_names", set())
            target_child_lvl = str(pdef.get("child_admin_level", "")).strip()

            is_match = False
            if osm_id_num and osm_id_num in child_ids:
                is_match = True
            elif target_child_lvl and admin_lvl == target_child_lvl:
                if name in child_names or name_en in child_names:
                    is_match = True

            if is_match and geom:
                self.parent_collected_polygons[pid].append(geom)

        return processed

    def get_synthetic_parents(self):
        synthesized = []
        for pid, pdef in SYNTHETIC_PARENT_DEFINITIONS.items():
            if pid in self.seen_parents:
                continue

            target_country = (pdef.get("country_code") or "").upper().strip()
            if target_country and self.country_code:
                if self.country_code.upper().strip() != target_country:
                    continue

            geoms = self.parent_collected_polygons.get(pid, [])
            if not geoms:
                continue

            # Combine child polygon coordinates into a MultiPolygon
            coords = []
            for g in geoms:
                gtype = g.get("type")
                gcoords = g.get("coordinates")
                if not gcoords:
                    continue
                if gtype == "Polygon":
                    coords.append(gcoords)
                elif gtype == "MultiPolygon":
                    coords.extend(gcoords)

            if not coords:
                continue

            synth_props = dict(pdef["properties"])
            synth_props["area_type"] = derive_area_type(synth_props, synth_props.get("admin_level", "4"))

            synth_feature = {
                "type": "Feature",
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": coords
                },
                "properties": synth_props
            }
            self.count_total += 1
            self.count_synthesized += 1
            synthesized.append(synth_feature)
        return synthesized

    def print_summary(self, stream_name=None):
        pct = (self.count_with_center / self.count_total * 100.0) if self.count_total > 0 else 0.0
        parent_pct = (self.count_with_parent / self.count_total * 100.0) if self.count_total > 0 else 0.0
        label = f": {stream_name}" if stream_name else ""
        sys.stderr.write("============================================================\n")
        sys.stderr.write(f" filter_polygons Execution Summary{label}\n")
        sys.stderr.write(f" Total Features Emitted:    {self.count_total:,}\n")
        sys.stderr.write(f" With Center Coordinates:   {self.count_with_center:,} ({pct:.1f}%)\n")
        sys.stderr.write(f"   - From admin_centre:     {self.count_with_admin_centre:,}\n")
        sys.stderr.write(f"   - From label:            {self.count_with_label:,}\n")
        sys.stderr.write(f" With Parent Relation:      {self.count_with_parent:,} ({parent_pct:.1f}%)\n")
        if self.count_synthesized > 0:
            sys.stderr.write(f" Synthesized Parents:       {self.count_synthesized:,}\n")
        sys.stderr.write("============================================================\n")
        sys.stderr.flush()

def filter_features(feature_iterable, require_wikidata=False, country_code=None, relation_centres=None, admin_pbf=None):
    """
    Generator that processes features and yields output features including synthesized parents.
    """
    processor = StreamProcessor(
        require_wikidata=require_wikidata,
        country_code=country_code,
        relation_centres=relation_centres,
        admin_pbf=admin_pbf
    )
    for item in feature_iterable:
        line_str = json.dumps(item) if isinstance(item, dict) else str(item)
        res = processor.process_line(line_str)
        if res:
            yield res
    for synth in processor.get_synthetic_parents():
        yield synth

def main():
    parser = argparse.ArgumentParser(description="Filter and enhance GeoJSON sequence stream for osm-polygons exporter.")
    parser.add_argument("--require-wikidata", action="store_true", help="Require wikidata property")
    parser.add_argument("--country-code", type=str, help="Optional country code ISO override")
    parser.add_argument("--admin-pbf", type=str, help="Optional path to filtered admin PBF file to extract relation center coordinates")
    parser.add_argument("--synthetic-defs", type=str, help="Path to synthetic parent definitions JSON (from validate_pbf.py)")
    parser.add_argument("--parent-mapping", type=str, help="Path to parent-mapping JSON (child_id -> parent info, from validate_pbf.py)")
    parser.add_argument("input_file", nargs="?", help="Input geojsonseq file (or stdin if omitted)")
    args = parser.parse_args()

    if args.synthetic_defs:
        load_synthetic_defs(args.synthetic_defs)
    if args.parent_mapping:
        load_parent_mapping(args.parent_mapping)

    processor = StreamProcessor(
        require_wikidata=args.require_wikidata,
        country_code=args.country_code,
        admin_pbf=args.admin_pbf
    )

    stream_name = os.path.basename(args.input_file) if args.input_file else (args.country_code or "stdin")

    if args.input_file:
        input_stream = open(args.input_file, "r", encoding="utf-8")
    else:
        input_stream = sys.stdin

    for line in input_stream:
        res = processor.process_line(line)
        if res:
            sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")

    for synth in processor.get_synthetic_parents():
        sys.stdout.write(json.dumps(synth, ensure_ascii=False) + "\n")

    if args.input_file:
        input_stream.close()

    processor.print_summary(stream_name=stream_name)

if __name__ == "__main__":
    main()
