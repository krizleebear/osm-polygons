#!/usr/bin/env python3
"""
Local smoke-test: feed filter_polygons.py with synthetic Madeira freguesia
GeoJSONSeq features and verify that all 11 Concelho parents are emitted.

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


def make_freguesia_feature(rel_id, name):
    """Create a minimal GeoJSON Feature for a freguesia."""
    return {
        "type": "Feature",
        "geometry": DUMMY_POLYGON,
        "properties": {
            "@id": f"relation/{rel_id}",
            "@type": "relation",
            "id": rel_id,
            "admin_level": "8",
            "boundary": "administrative",
            "name": name,
            "ISO3166-1": "PT",
            "ISO3166-2": "PT-30",
        },
    }


def main():
    processor = StreamProcessor(country_code="PT")

    # Feed all freguesias through the processor
    for rel_id, name in FREGUESIAS:
        feature = make_freguesia_feature(rel_id, name)
        line = json.dumps(feature)
        processor.process_line(line)

    # Collect synthesized parents
    synthesized = processor.get_synthetic_parents()
    found = {s["properties"]["id"]: s["properties"]["name"] for s in synthesized}

    print(f"\n=== Madeira Concelho Synthetic Parent Test ===")
    print(f"Freguesias fed:        {len(FREGUESIAS)}")
    print(f"Synthesized parents:   {len(synthesized)}")
    print()

    ok = True
    for concelho_id, expected_name in sorted(EXPECTED_CONCELHOS.items()):
        if concelho_id in found:
            print(f"  OK  {concelho_id} ({expected_name})")
        else:
            print(f"  MISSING  {concelho_id} ({expected_name})")
            ok = False

    print()
    if ok:
        print("PASS — all 11 Madeira Concelhos synthesized.")
        return 0
    else:
        print("FAIL — some Concelhos are missing.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
