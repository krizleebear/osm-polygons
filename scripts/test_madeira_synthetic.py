#!/usr/bin/env python3
"""
Local smoke-test: feed filter_polygons.py with synthetic Madeira GeoJSONSeq
features and verify that all 11 Concelho parents AND the Madeira L4 region
are emitted.

No osmium or PBF download required — pure stream-process test.
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from filter_polygons import StreamProcessor, SYNTHETIC_PARENT_DEFINITIONS

# All 42 Madeira freguesias (admin_level 8) with minimal GeoJSON Polygon geometry
# Coordinates are dummy bounding boxes — only topology-preserving union matters.
FREGUESIAS = [
    # Funchal
    (8427682, "São Gonçalo"), (8427683, "Santa Maria Maior"), (8427684, "Sé"),
    (8426650, "Santa Luzia"), (8426651, "São Pedro"),
    (8426652, "Imaculado Coração de Maria"), (8426653, "Monte"),
    (8426654, "São Roque"), (8426655, "São Martinho"),
    (8426656, "Santo António"), (8426661, "Quinta Grande"),
    # Calheta
    (8422551, "Arco da Calheta"), (8422552, "Calheta"),
    (8422553, "Estreito da Calheta"), (8422554, "Prazeres"),
    (8422555, "Jardim do Mar"), (8422556, "Paul do Mar"),
    (8422557, "Fajã da Ovelha"), (8422558, "Ponta do Pargo"),
    # Câmara de Lobos
    (8426657, "Câmara de Lobos"), (8426658, "Estreito de Câmara de Lobos"),
    (8426659, "Jardim da Serra"), (8426660, "Curral das Freiras"),
    # Machico
    (8435149, "Santo António da Serra"), (8435150, "Porto da Cruz"),
    (8435151, "Água de Pena"), (8435152, "Machico"), (8435153, "Caniçal"),
    # Ponta do Sol
    (8426666, "Ponta do Sol"), (8426667, "Canhas"), (8426668, "Madalena do Mar"),
    # Porto Moniz
    (8422559, "Seixal"), (8422560, "Ribeira da Janela"),
    (8422561, "Porto Moniz"), (8422562, "Achadas da Cruz"),
    # Porto Santo
    (8435139, "Porto Santo"),
    # Ribeira Brava
    (8426662, "Campanário"), (8426663, "Ribeira Brava"),
    (8426664, "Serra de Água"), (8426665, "Tábua"),
    # Santa Cruz
    (8427677, "Santa Cruz"), (8427678, "Gaula"),
    (8427679, "Santo António da Serra"), (8427680, "Camacha"),
    (8427681, "Caniço"),
    # Santana
    (8435140, "São Roque do Faial"), (8435141, "Faial"),
    (8435142, "Santana"), (8435143, "Ilha"),
    (8435144, "São Jorge"), (8435145, "Arco de São Jorge"),
    # São Vicente
    (8435146, "Boa Ventura"), (8435147, "Ponta Delgada"),
    (8435148, "São Vicente"),
]

DUMMY_POLYGON = {
    "type": "Polygon",
    "coordinates": [[[-17.0, 32.4], [-17.0, 33.1], [-16.1, 33.1],
                     [-16.1, 32.4], [-17.0, 32.4]]]
}

# Expected Concelho relation IDs (admin_level 7)
EXPECTED_CONCELHOS = {
    8421413: "Funchal",
    8421420: "Calheta",
    8421414: "Câmara de Lobos",
    8421411: "Machico",
    8421416: "Ponta do Sol",
    8421419: "Porto Moniz",
    8435154: "Porto Santo",
    8421415: "Ribeira Brava",
    8421412: "Santa Cruz",
    8421417: "Santana",
    8421418: "São Vicente",
}

# Concelho features (admin_level 7) for Madeira L4 synthesis test
CONCELHOS_L7 = [
    (8421413, "Funchal"), (8421420, "Calheta"), (8421414, "Câmara de Lobos"),
    (8421411, "Machico"), (8421416, "Ponta do Sol"), (8421419, "Porto Moniz"),
    (8435154, "Porto Santo"), (8421415, "Ribeira Brava"), (8421412, "Santa Cruz"),
    (8421417, "Santana"), (8421418, "São Vicente"),
]


def make_feature(rel_id, name, admin_level):
    """Create a minimal GeoJSON Feature."""
    return {
        "type": "Feature",
        "geometry": DUMMY_POLYGON,
        "properties": {
            "@id": f"relation/{rel_id}",
            "@type": "relation",
            "id": rel_id,
            "admin_level": admin_level,
            "boundary": "administrative",
            "name": name,
            "ISO3166-1": "PT",
            "ISO3166-2": "PT-30",
        },
    }


def test_concelhos_from_freguesias():
    """Test 1: Feed freguesias (L8) → expect 11 Concelho parents (L7)."""
    processor = StreamProcessor(country_code="PT")
    for rel_id, name in FREGUESIAS:
        feature = make_feature(rel_id, name, "8")
        processor.process_line(json.dumps(feature))

    synthesized = processor.get_synthetic_parents()
    found = {s["properties"]["id"]: s["properties"]["name"] for s in synthesized}

    print(f"\n=== Test 1: Concelhos from Freguesias ===")
    print(f"Freguesias fed:      {len(FREGUESIAS)}")
    print(f"Synthesized:         {len(synthesized)}")

    ok = True
    for cid, cname in sorted(EXPECTED_CONCELHOS.items()):
        status = "OK" if cid in found else "MISSING"
        print(f"  {status:7s} {cid} ({cname})")
        if cid not in found:
            ok = False
    return ok


def test_madeira_from_concelhos():
    """Test 2: Feed Concelhos (L7) → expect Madeira L4 parent via force_collect."""
    processor = StreamProcessor(country_code="PT")
    for rel_id, name in CONCELHOS_L7:
        feature = make_feature(rel_id, name, "7")
        processor.process_line(json.dumps(feature))

    synthesized = processor.get_synthetic_parents()
    found = {s["properties"]["id"]: s["properties"]["name"] for s in synthesized}

    print(f"\n=== Test 2: Madeira L4 from Concelhos ===")
    print(f"Concelhos fed:      {len(CONCELHOS_L7)}")
    print(f"Synthesized:        {len(synthesized)}")

    madeira_id = 1629145
    if madeira_id in found:
        print(f"  OK     {madeira_id} (Madeira)")
        return True
    else:
        print(f"  MISSING {madeira_id} (Madeira)")
        return False


def main():
    ok1 = test_concelhos_from_freguesias()
    ok2 = test_madeira_from_concelhos()

    print()
    if ok1 and ok2:
        print("PASS — all tests passed.")
        return 0
    else:
        print("FAIL — some tests failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
