"""Region ids: LINZ locality paths, legacy names, listing and file slugs.

Offline: no LINZ calls. src/localities.py functions tested here are the pure
ones (ids, CQL, bbox); search/resolve/prepare need a LINZ key.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import localities, region_ids
from src.local_queue import QueueError, validate_task
from tools.pipeline_queue import make_task


def props(**kw):
    base = {"id": 1001, "name": "Frankton", "name_ascii": "Frankton",
            "major_name": "Queenstown", "major_name_ascii": "Queenstown",
            "territorial_authority": "Queenstown-Lakes District",
            "territorial_authority_ascii": "Queenstown-Lakes District"}
    base.update(kw)
    return base


class RegionIdTests(unittest.TestCase):
    def test_validate_accepts_both_shapes(self):
        for rid in ("pilot", "town_gorge_north", "Arrowtown-2",
                    "queenstown-lakes-district/queenstown/frankton",
                    "southland-district/no-town/halfway-bay-1234"):
            self.assertEqual(region_ids.validate(rid), rid)

    def test_validate_rejects_unsafe(self):
        for rid in ("", "../etc", "a/b", "a/b/c/d", "/a/b/c", "a/../c",
                    "A/b/c", "a b", "a/b/c/", ".hidden", None):
            with self.assertRaises(ValueError, msg=rid):
                region_ids.validate(rid)

    def test_file_slug_is_one_segment(self):
        self.assertEqual(region_ids.file_slug("a/b/c"), "a__b__c")
        self.assertEqual(region_ids.file_slug("pilot"), "pilot")

    def test_list_regions_mixes_legacy_and_nested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def touch(rel):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text("{}")

            touch("pilot/building_outlines.geojson")
            touch("pilot/display_tiles/0.json")      # subdirs don't matter
            touch("legacy_x/summary.json")
            touch("qld/queenstown/frankton/building_outlines.geojson")
            touch("qld/queenstown/frankton/summary.json")
            touch("qld/no-town/kingston/building_outlines.geojson")
            (root / "qld/queenstown/empty").mkdir()
            (root / "empty_council/town").mkdir(parents=True)
            touch("_scratch/x.json")
            touch(".cache/x.json")
            self.assertEqual(region_ids.list_regions(root),
                             ["legacy_x", "pilot", "qld/no-town/kingston",
                              "qld/queenstown/frankton"])
            self.assertEqual(region_ids.list_regions(root, sentinel="summary.json"),
                             ["legacy_x", "qld/queenstown/frankton"])
            self.assertEqual(region_ids.list_regions(root / "missing"), [])


class LocalityTests(unittest.TestCase):
    def test_slug_folds_macrons_and_punctuation(self):
        self.assertEqual(localities.slug("Hāwea Flat"), "hawea-flat")
        self.assertEqual(localities.slug("Queenstown-Lakes District"),
                         "queenstown-lakes-district")
        self.assertEqual(localities.slug("St Andrews (Hamilton)"), "st-andrews-hamilton")

    def test_region_id(self):
        self.assertEqual(localities.region_id(props()),
                         "queenstown-lakes-district/queenstown/frankton")
        self.assertEqual(localities.region_id(props(major_name=None, major_name_ascii=None)),
                         "queenstown-lakes-district/no-town/frankton")
        multi = props(territorial_authority="Queenstown-Lakes District, Southland District",
                      territorial_authority_ascii="Queenstown-Lakes District, Southland District")
        self.assertTrue(localities.region_id(multi).startswith("queenstown-lakes-district/"))
        self.assertEqual(localities.region_id(props(), with_id=True),
                         "queenstown-lakes-district/queenstown/frankton-1001")
        long = props(name="x" * 80, name_ascii="x" * 80, id=123456)
        self.assertTrue(region_ids.is_locality_id(localities.region_id(long, with_id=True)))

    def test_ambiguous_ids_only_for_collisions(self):
        a, b, c = props(id=1), props(id=2), props(id=3, name="Kelvin Heights",
                                                   name_ascii="Kelvin Heights")
        amb = localities.ambiguous_ids([a, b, c])
        self.assertEqual(amb, {1, 2})
        self.assertTrue(localities.display_region_id(a, amb).endswith("/frankton-1"))
        self.assertTrue(localities.display_region_id(c, amb).endswith("/kelvin-heights"))

    def test_outline_filter(self):
        self.assertEqual(
            localities.outline_filter(props(name="O'Kains Bay")),
            "suburb_locality = 'O''Kains Bay' AND town_city = 'Queenstown' AND "
            "territorial_authority IN ('Queenstown-Lakes District')")
        no_town = localities.outline_filter(props(
            major_name=None,
            territorial_authority="Queenstown-Lakes District, Southland District"))
        self.assertIn("(town_city IS NULL OR town_city = '')", no_town)
        self.assertIn("IN ('Queenstown-Lakes District', 'Southland District')", no_town)

    def test_outlines_bbox_and_imagery_estimate(self):
        # A 1 km square of NZTM2000 near Frankton.
        x, y = 1_268_000.0, 5_007_000.0
        ring = [[x, y], [x + 1000, y], [x + 1000, y + 1000], [x, y + 1000], [x, y]]
        fc = {"features": [{"geometry": {"type": "Polygon", "coordinates": [ring]}},
                           {"geometry": None}]}
        w, s, e, n = localities.outlines_bbox_wgs84(fc, pad_m=0)
        self.assertTrue(168.0 < w < e < 169.5 and -45.5 < s < n < -44.5)
        self.assertAlmostEqual(localities.bbox_km2([w, s, e, n]), 1.0, delta=0.02)
        self.assertAlmostEqual(localities.imagery_gb_estimate([w, s, e, n]),
                               localities.IMAGERY_GB_PER_KM2, delta=0.01)
        with self.assertRaises(ValueError):
            localities.outlines_bbox_wgs84({"features": []})


class QueueRegionTests(unittest.TestCase):
    def test_queue_accepts_locality_and_rejects_traversal(self):
        task = make_task(ROOT, "qld/queenstown/frankton", sys.executable)
        self.assertEqual(task["region"], "qld/queenstown/frankton")
        with self.assertRaises(QueueError):
            validate_task(dict(task, region="../../etc"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
