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
"""

import sys
import json
import argparse

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
EXCLUDED_BOUNDARY_VALUES = {"political", "statistical", "census", "historic"}

# Countries known to lack native admin_level=4 relations, mapping fallback levels (e.g., level 6 -> 4)
LEVEL_4_FALLBACK_COUNTRIES = {
    "EE", "HR", "ME", "SI", "XK", "CY", "IS", "LV", "MK"
}

# Sub-municipal boundary tag values (Rule 2 of SPEC_OSM_POLYGONS_SUBDIVISIONS.md):
# boundary IN (local_authority, borough) without admin_level -> default admin_level.
SUBMUNICIPAL_BOUNDARY_VALUES = ("local_authority", "borough")
DEFAULT_SUBMUNICIPAL_ADMIN_LEVEL = "10"

# Place-based boundary relations (Rule 3 of SPEC_OSM_POLYGONS_SUBDIVISIONS.md):
# type=boundary OR type=multipolygon combined with place -> default admin_level.
PLACE_TO_ADMIN_LEVEL = {
    "suburb": "9",
    "quarter": "10",
    "neighbourhood": "11",
    "borough": "9",
}
PLACE_RELATION_TYPES = ("boundary", "multipolygon")

# Synthetic parent entity definitions per SPEC_UPSTREAM_OSM_POLYGONS_MISSING_ENTITIES.md
SYNTHETIC_PARENT_DEFINITIONS = {
    # Portugal: Funchal (relation 8421413, admin_level 7)
    8421413: {
        "properties": {
            "@id": 8421413,
            "@type": "relation",
            "id": 8421413,
            "admin_level": "7",
            "boundary": "administrative",
            "name": "Funchal",
            "official_name": "Município do Funchal",
            "wikidata": "Q25444",
            "ISO3166-1": "PT",
            "border_type": "município",
        },
        "child_relation_ids": {8427682, 8427683, 8427684, 8426650, 8426651, 8426652, 8426653, 8426654, 8426655, 8426656},
        "child_names": {
            "São Martinho", "Santa Maria Maior", "São Pedro", "São Roque",
            "Santo António", "Santa Luzia", "Monte", "Imaculado Coração de Maria",
            "São Gonçalo", "Sé"
        },
        "child_admin_level": "8",
    },
    # Taiwan: Kaohsiung City (relation 2127079, admin_level 4)
    2127079: {
        "properties": {
            "@id": 2127079,
            "@type": "relation",
            "id": 2127079,
            "admin_level": "4",
            "boundary": "administrative",
            "name": "高雄市",
            "name:en": "Kaohsiung City",
            "wikidata": "Q181557",
            "ISO3166-2": "TW-KHH",
            "ISO3166-1": "TW",
        },
        "child_names": {
            "鹽埕區", "鼓山區", "左營區", "楠梓區", "三民區", "新興區", "前金區", "苓雅區",
            "前鎮區", "旗津區", "小港區", "鳳山區", "林園區", "大寮區", "大樹區", "大社區",
            "仁武區", "鳥松區", "岡山區", "橋頭區", "燕巢區", "田寮區", "阿蓮區", "路竹區",
            "湖內區", "茄萣區", "永安區", "彌陀區", "梓官區", "旗山區", "美濃區", "六龜區",
            "甲仙區", "杉林區", "內門區", "茂林區", "桃源區", "那瑪夏區"
        },
        "child_admin_level": "7",
    }
}

def process_feature(data, require_wikidata=False, country_code=None):
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

    # Exclude non-administrative boundaries (political, statistical, census, historic, electoral districts)
    boundary_val = str(props.get("boundary", "")).strip().lower()
    if boundary_val in EXCLUDED_BOUNDARY_VALUES:
        return None

    political_val = str(props.get("political", "")).strip().lower()
    if political_val and political_val not in ("no", "false", "0"):
        return None

    if props.get("election:parliament") or props.get("election") or props.get("electoral_district"):
        return None

    border_type = str(props.get("border_type", "")).strip().lower()
    if border_type in EXCLUDED_BOUNDARY_VALUES:
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

    # Filter L2 border ways: Exclude non-relation border ways (@type=way) tagged admin_level=2 without ISO3166-1
    element_type = str(props.get("@type", props.get("osm_type", ""))).strip().lower()
    if (element_type == "way" or element_type != "relation") and raw_level == "2":
        iso_val = str(props.get("ISO3166-1", "")).strip()
        if not iso_val or iso_val.lower() in ("none", "null"):
            return None

    # Task 5: Synthesize admin_level for sub-municipal boundaries (Rule 3 takes precedence over Rule 2)
    rel_type = str(props.get("type", "")).strip().lower()
    place_val = str(props.get("place", "")).strip().lower()
    if (element_type == "relation" and rel_type in PLACE_RELATION_TYPES
            and place_val in PLACE_TO_ADMIN_LEVEL
            and (not raw_level or raw_level == "None")):
        props["admin_level"] = PLACE_TO_ADMIN_LEVEL[place_val]
        raw_level = PLACE_TO_ADMIN_LEVEL[place_val]

    if (boundary_val in SUBMUNICIPAL_BOUNDARY_VALUES
            and (not raw_level or raw_level == "None")):
        props["admin_level"] = DEFAULT_SUBMUNICIPAL_ADMIN_LEVEL
        raw_level = DEFAULT_SUBMUNICIPAL_ADMIN_LEVEL

    if not raw_level or raw_level == "None":
        return None

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

    return data

class StreamProcessor:
    """
    Processes a stream of GeoJSON features, tracking known entities and synthesizing
    missing parent divisions from constituent child divisions per SPEC_UPSTREAM_OSM_POLYGONS_MISSING_ENTITIES.md.
    """
    def __init__(self, require_wikidata=False, country_code=None):
        self.require_wikidata = require_wikidata
        self.country_code = country_code
        self.seen_parents = set()
        self.parent_collected_polygons = {pid: [] for pid in SYNTHETIC_PARENT_DEFINITIONS}

    def process_line(self, line_str):
        if not line_str or not line_str.strip():
            return None
        try:
            data = json.loads(line_str.strip())
        except json.JSONDecodeError:
            return None

        processed = process_feature(data, require_wikidata=self.require_wikidata, country_code=self.country_code)
        if not processed:
            return None

        props = processed.get("properties", {})
        osm_id = props.get("id") or props.get("@id") or props.get("osm_id")
        try:
            osm_id_num = int(osm_id) if osm_id else None
        except (ValueError, TypeError):
            osm_id_num = None

        if osm_id_num in SYNTHETIC_PARENT_DEFINITIONS:
            self.seen_parents.add(osm_id_num)

        # Track child polygons for potential parent synthesis
        geom = processed.get("geometry")
        name = props.get("name", "")
        name_en = props.get("name:en", "")
        admin_lvl = str(props.get("admin_level", "")).strip()

        for pid, pdef in SYNTHETIC_PARENT_DEFINITIONS.items():
            if pid in self.seen_parents:
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

            synth_feature = {
                "type": "Feature",
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": coords
                },
                "properties": dict(pdef["properties"])
            }
            synthesized.append(synth_feature)
        return synthesized

def filter_features(feature_iterable, require_wikidata=False, country_code=None):
    """
    Generator that processes features and yields output features including synthesized parents.
    """
    processor = StreamProcessor(require_wikidata=require_wikidata, country_code=country_code)
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
    parser.add_argument("input_file", nargs="?", help="Input geojsonseq file (or stdin if omitted)")
    args = parser.parse_args()

    processor = StreamProcessor(require_wikidata=args.require_wikidata, country_code=args.country_code)

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

if __name__ == "__main__":
    main()
