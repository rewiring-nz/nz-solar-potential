"""Problem reports from the map: what is accepted, and what reaches GitHub.

Covers services/report_inbox/report_schema.py (shared by the inbox function
and the filing job) and the grouping in tools/user_reports.py.
"""
import base64
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "report_inbox"))
sys.path.insert(0, str(ROOT / "tools"))
import report_schema as rs  # noqa: E402
import user_reports as ur   # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


def base(**kw):
    p = {"kind": "note", "note": "panels on the shady side", "building_id": 4718482,
         "address": "10 Elspeth Street, Cromwell", "view": {"lat": -45.03, "lng": 169.19, "z": 18.25},
         "context": "mode layout | v86", "data_version": "86"}
    p.update(kw)
    return p


class Clean(unittest.TestCase):
    def test_keeps_a_normal_report(self):
        r = rs.clean(base())
        self.assertEqual(r["building_id"], 4718482)
        self.assertEqual(r["view"]["z"], 18.2)

    def test_refuses(self):
        for bad in (None, [], base(kind="spam"), base(note="  "), base(building_id="x")):
            with self.assertRaises(rs.BadReport):
                rs.clean(bad)

    def test_honeypot(self):
        with self.assertRaisesRegex(rs.BadReport, "honeypot"):
            rs.clean(base(website="http://x"))

    def test_caps_lengths(self):
        r = rs.clean(base(note="a" * 10_000, context="b" * 10_000))
        self.assertEqual(len(r["note"]), rs.MAX_NOTE)
        self.assertEqual(len(r["context"]), rs.MAX_CONTEXT)

    def test_view_outside_nz_dropped(self):
        self.assertIsNone(rs.clean(base(view={"lat": 51.5, "lng": 0, "z": 15}))["view"])

    def test_screenshot_must_be_small_jpeg(self):
        ok = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()
        self.assertEqual(rs.clean(base(screenshot=ok))["screenshot"], JPEG)
        png = "data:image/jpeg;base64," + base64.b64encode(b"\x89PNG....").decode()
        self.assertIsNone(rs.clean(base(screenshot=png))["screenshot"])
        big = "data:image/jpeg;base64," + base64.b64encode(JPEG + b"0" * rs.MAX_SCREENSHOT).decode()
        self.assertIsNone(rs.clean(base(screenshot=big))["screenshot"])

    def test_origins(self):
        self.assertTrue(rs.origin_allowed("https://rewiring-nz.github.io"))
        self.assertTrue(rs.origin_allowed("http://localhost:8000"))
        self.assertFalse(rs.origin_allowed("https://rewiring-nz.github.io.evil.com"))
        self.assertFalse(rs.origin_allowed(""))


class Issue(unittest.TestCase):
    def test_mentions_cannot_notify(self):
        r = rs.clean(base(note="cc @JoshAtIdeatious", address="@x"))
        self.assertNotIn("@JoshAtIdeatious", rs.body_for(r, "now"))
        self.assertNotIn("(@x)", rs.title_for(r))

    def test_note_is_quoted(self):
        r = rs.clean(base(note="# heading\n<img src=x>"))
        self.assertIn("> # heading\n> <img src=x>", rs.body_for(r, "now"))

    def test_context_cannot_close_its_code_block(self):
        r = rs.clean(base(context="a ``` b"))
        self.assertNotIn("a ``` b", rs.body_for(r, "now"))

    def test_title_round_trips_building(self):
        r = rs.clean(base())
        self.assertEqual(rs.title_building(rs.title_for(r)), 4718482)
        self.assertIsNone(rs.title_building(rs.title_for(rs.clean(base(building_id=None)))))

    def test_map_link(self):
        self.assertTrue(rs.map_link(rs.clean(base())).endswith(
            "rooftop-solar.html?lat=-45.03&lng=169.19&z=18.2&b=4718482"))


class Grouping(unittest.TestCase):
    def test_one_issue_per_building(self):
        reps = [dict(rs.clean(base(building_id=b)), id=i) for i, b in
                (("3", 7), ("1", 5), ("2", 5), ("4", None), ("5", None))]
        got = [(r["id"], a) for r, a, _ in ur.plan(reps, {7: 42})]
        self.assertEqual(got, [("1", "create"), ("2", "comment"), ("3", "comment"),
                               ("4", "create"), ("5", "create")])


if __name__ == "__main__":
    unittest.main()
