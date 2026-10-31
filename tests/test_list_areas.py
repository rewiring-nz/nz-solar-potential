"""Offline tests for the national locality documentation catalogue."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.list_areas import render


def locality(linz_id=1, **values):
    props = {
        "id": linz_id, "name": "Frankton", "type": "Suburb",
        "major_name": "Queenstown",
        "territorial_authority": "Queenstown-Lakes District",
    }
    props.update(values)
    return props


class CatalogueTests(unittest.TestCase):
    def test_counts_water_and_retains_other_types(self):
        text = render([
            locality(), locality(2, name="Lake", type="Lake"),
            locality(3, name="Bay", type="Inland Bay"),
            locality(4, name="Island", type="Island"),
            locality(5, name="Coast", type="Coastal Bay"),
            locality(6, name="Park", type="Conservation Land"),
        ], "2026-10-31")
        self.assertIn("**4 candidate areas**", text)
        self.assertIn("source has 6 features; 2 lakes and inland bays", text)
        self.assertNotIn("`queenstown-lakes-district/queenstown/lake`", text)
        for name in ("island", "coast", "park"):
            self.assertIn(f"`queenstown-lakes-district/queenstown/{name}`", text)

    def test_collisions_include_excluded_water(self):
        text = render([locality(), locality(2, type="Lake")], "2026-10-31")
        self.assertIn("`queenstown-lakes-district/queenstown/frankton-1`", text)
        self.assertNotIn("frankton-2", text)

    def test_multiple_councils_no_town_and_macrons(self):
        text = render([locality(
            name="Hāwea Flat", major_name=None,
            territorial_authority="Kaikōura District, Hurunui District",
        )], "2026-10-31")
        self.assertIn("**1 territorial-authority groups**", text)
        self.assertIn('<a id="kaikoura-district"></a>', text)
        self.assertIn("[Kaikōura District](#kaikoura-district)", text)
        self.assertIn("`kaikoura-district/no-town/hawea-flat`", text)
        self.assertNotIn("## Hurunui District", text)

    def test_deterministic_sort_and_table_escaping(self):
        rows = [locality(2, name="Z | place"), locality(1, name="A\nplace")]
        self.assertEqual(render(rows, "2026-10-31"),
                         render(list(reversed(rows)), "2026-10-31"))
        self.assertIn(r"Z \| place", render(rows, "2026-10-31"))
        self.assertIn("A place", render(rows, "2026-10-31"))

    def test_empty_or_duplicate_response_fails(self):
        for rows in ([], [locality(type="Lake")], [locality(), locality()]):
            with self.assertRaises(ValueError):
                render(rows, "2026-10-31")


if __name__ == "__main__":
    unittest.main()
