#!/usr/bin/env python3
"""
Unit tests for scripts/composite-extracts.json and scripts/extract_composites.py.

Verifies:
  1. All 3 composite sources and 23 derived extracts are well-formed with unique regions and CCs.
  2. Polygon rings are closed and valid; bboxes have min < max.
  3. Zero pairwise interior overlap among all 23 derived bboxes/polygons (shared border edges allowed).
  4. extract_composites.py --dry-run produces valid osmium-extract config JSON for each source.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import extract_composites

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, "scripts", "composite-extracts.json")


def _ring_from_extract(ext):
    if "bbox" in ext:
        minx, miny, maxx, maxy = ext["bbox"]
        return [
            [minx, miny],
            [maxx, miny],
            [maxx, maxy],
            [minx, maxy],
            [minx, miny],
        ]
    return ext["polygon"]


def _segments(ring):
    return [(ring[i], ring[i + 1]) for i in range(len(ring) - 1)]


def _point_in_ring(x, y, ring):
    inside = False
    for (x1, y1), (x2, y2) in _segments(ring):
        if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1) + x1):
            inside = not inside
    return inside


def _point_on_segment(px, py, a, b, eps=1e-9):
    x1, y1 = a
    x2, y2 = b
    cross = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
    if abs(cross) > eps:
        return False
    return (
        min(x1, x2) - eps <= px <= max(x1, x2) + eps
        and min(y1, y2) - eps <= py <= max(y1, y2) + eps
    )


def _point_strictly_inside(x, y, ring):
    if any(_point_on_segment(x, y, a, b) for a, b in _segments(ring)):
        return False
    return _point_in_ring(x, y, ring)


def _proper_segment_intersection(a, b, c, d, eps=1e-9):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1 = orient(a, b, c)
    o2 = orient(a, b, d)
    o3 = orient(c, d, a)
    o4 = orient(c, d, b)
    return (o1 * o2 < -eps) and (o3 * o4 < -eps)


def _rings_interior_overlap(ring_a, ring_b):
    for a1, a2 in _segments(ring_a):
        for b1, b2 in _segments(ring_b):
            if _proper_segment_intersection(a1, a2, b1, b2):
                return True
    for px, py in ring_a[:-1]:
        if _point_strictly_inside(px, py, ring_b):
            return True
    for px, py in ring_b[:-1]:
        if _point_strictly_inside(px, py, ring_a):
            return True
    return False


class TestCompositeExtracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cls.config = json.load(fh)
        cls.sources = cls.config["sources"]

    def test_expected_sources_and_23_unique_extracts(self):
        self.assertEqual(
            set(self.sources.keys()),
            {"guernsey-jersey", "american-oceania", "central-america"},
        )
        all_extracts = []
        for src_id, info in self.sources.items():
            self.assertTrue(info["pbf_url"].startswith("https://download.geofabrik.de/"))
            for ext in info["extracts"]:
                all_extracts.append((src_id, ext))

        self.assertEqual(len(all_extracts), 23)
        regions = [ext["region"] for _, ext in all_extracts]
        ccs = [ext["country_code"] for _, ext in all_extracts]
        self.assertEqual(len(regions), len(set(regions)), "Duplicate region in composite-extracts.json")
        self.assertEqual(len(ccs), len(set(ccs)), "Duplicate country_code in composite-extracts.json")

    def test_geometries_are_valid_and_pairwise_disjoint_in_interior(self):
        items = []
        for src_id, info in self.sources.items():
            for ext in info["extracts"]:
                if "bbox" in ext:
                    minx, miny, maxx, maxy = ext["bbox"]
                    self.assertLess(minx, maxx, ext["region"])
                    self.assertLess(miny, maxy, ext["region"])
                else:
                    ring = ext["polygon"]
                    self.assertGreaterEqual(len(ring), 4, ext["region"])
                    self.assertEqual(ring[0], ring[-1], f"Unclosed polygon for {ext['region']}")
                items.append((ext["region"], _ring_from_extract(ext)))

        for i in range(len(items)):
            reg_a, ring_a = items[i]
            for j in range(i + 1, len(items)):
                reg_b, ring_b = items[j]
                self.assertFalse(
                    _rings_interior_overlap(ring_a, ring_b),
                    f"Interior overlap detected between '{reg_a}' and '{reg_b}'",
                )

    def test_dry_run_generates_valid_osmium_config_for_all_sources(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            for src_id, info in self.sources.items():
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = extract_composites.main(
                        [
                            "--config",
                            CONFIG_PATH,
                            "--source",
                            src_id,
                            "--input-pbf",
                            "/nonexistent/input.osm.pbf",
                            "--out-dir",
                            tmp_dir,
                            "--dry-run",
                        ]
                    )
                self.assertEqual(rc, 0)
                cfg = json.loads(buf.getvalue())
                self.assertEqual(len(cfg["extracts"]), len(info["extracts"]))
                for ext_cfg in cfg["extracts"]:
                    self.assertTrue(ext_cfg["output"].endswith(".osm.pbf"))
                    self.assertTrue("bbox" in ext_cfg or "polygon" in ext_cfg)

    def test_osmium_command_uses_smart_strategy_for_boundaries(self):
        cmd = extract_composites.build_osmium_command("/tmp/cfg.json", "/tmp/in.osm.pbf")
        self.assertIn("--strategy=smart", cmd)
        self.assertIn("-S", cmd)
        self.assertIn("types=multipolygon,boundary", cmd)


if __name__ == "__main__":
    unittest.main()
