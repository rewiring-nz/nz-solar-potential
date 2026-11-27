"""roadmap.json, read by the map's Status and Roadmap tabs, stays true to the
repository: every file it names exists, every switch is read somewhere in the
code, every methodology part it points at is in methodology.json, and every
evidence quote is still in the file it cites. A change that makes an entry
wrong (a file moved, a switch removed, a ledger figure rewritten) fails here,
so the tabs cannot quietly go stale."""
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RM = json.loads((ROOT / "roadmap.json").read_text(encoding="utf-8"))
METH = json.loads((ROOT / "methodology.json").read_text(encoding="utf-8"))
CODE_DIRS = ("src", "tools", "config.py", "economics.js", "bill_tool.js",
             "generation.js", "rooftop-solar.html", "low_solar.js")
STATUSES = ("underway", "next", "later")
EFFORTS = ("S", "M", "L")

_code_text = None


def code_text():
    global _code_text
    if _code_text is None:
        parts = []
        for d in CODE_DIRS:
            p = ROOT / d
            files = [p] if p.is_file() else [f for f in p.rglob("*")
                                              if f.suffix in (".py", ".sh", ".js", ".html") and f.is_file()]
            parts += [f.read_text(encoding="utf-8", errors="replace") for f in files]
        _code_text = "\n".join(parts)
    return _code_text


def quote_ok(e):
    p = ROOT / e["file"]
    return p.is_file() and e["quote"] in p.read_text(encoding="utf-8", errors="replace")


def method_parts():
    return {it["title"] for s in METH["steps"] for it in s["items"]}


def test_reviewed_date():
    d = dt.date.fromisoformat(RM["reviewed"])
    assert d <= dt.date.today(), "reviewed date is in the future"


def test_measures_cite_real_text():
    bad = [m["label"] for m in RM["measures"] if not quote_ok(m)]
    assert not bad, f"measure quotes no longer in their files: {bad}"
    for m in RM["measures"]:
        assert m["label"] and m["value"] and m["measured"], m


def test_items_well_formed():
    ids = [it["id"] for it in RM["items"]]
    assert len(ids) == len(set(ids)), "duplicate roadmap ids"
    for it in RM["items"]:
        assert it["status"] in STATUSES, (it["id"], it["status"])
        assert it["effort"] in EFFORTS, (it["id"], it["effort"])
        assert it["title"] and it["why"] and it["fix"], it["id"]


def test_items_point_at_real_code():
    parts = method_parts()
    problems = []
    for it in RM["items"]:
        r = it["refs"]
        for f in r["files"]:
            if not (ROOT / f).exists():
                problems.append(f"{it['id']}: no file {f}")
        for s in r["switches"]:
            if s not in code_text():
                problems.append(f"{it['id']}: switch {s} is read nowhere")
        if r.get("how") and r["how"] not in parts:
            problems.append(f"{it['id']}: no methodology part '{r['how']}'")
        for e in it["evidence"]:
            if not quote_ok(e):
                problems.append(f"{it['id']}: quote not in {e['file']}: {e['quote']!r}")
    assert not problems, "\n".join(problems)


def test_tabs_are_published():
    from tools.site_assets import FRONTEND_FILES
    assert "roadmap.json" in FRONTEND_FILES


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    tests = [test_reviewed_date, test_measures_cite_real_text, test_items_well_formed,
             test_items_point_at_real_code, test_tabs_are_published]
    failed = 0
    for t in tests:
        try:
            t(); print(f"  pass  {t.__name__}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
