"""
Everything that must be true before a rebuild is allowed near the live site.

The checks already existed -- compare_builds, invariants, the bug-doc watchlist,
the truth scorecard -- but which of them to run, in what order, and what counts
as a pass has been carried in someone's head each time. That is how the 25 Aug
rebuild shipped with panel_count 5.2% stale, and how a comparison was once run
against panel_count instead of fill_panels_100 and reported "+0, no change" on a
build that had removed 23,575 panels.

So: one command, a fixed order, and an explicit verdict.

WHAT IT CHECKS AND WHY EACH ONE EXISTS

  SPEC        every region carries the panel wattage config.py says it should.
              A region that silently kept an old file is invisible in district
              totals but wrong on every building in it. Found exactly this
              mid-rebuild on 2 Sep, where 440 W regions were simply not yet
              rebuilt -- harmless then, fatal if it had been the final state.
  TOTALS      district figures against the previous build, from the snapshot
              taken BEFORE the merge overwrote it. Compares fill_panels_100,
              not panel_count, which run_layouts_regate does not regenerate.
  YIELD       kWh per kWp per region, inside a physically defensible band.
              Catches a calibration or loss change that has gone the wrong way.
  WATCHLIST   the buildings Josh reported by hand. A district total can look
              perfect while the specific roof he complained about is broken
              again, and he WILL check that one.
  INVARIANTS  the physical bounds in src/invariants.py.

A FAIL blocks the push. A WARN is for a human to read and decide.

Usage:
    python tools/verify_rebuild.py
    python tools/verify_rebuild.py --skip-invariants
"""

import argparse
import json
import subprocess
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
SNAP = DATA / "build_snapshot_prev.json"

YIELD_MIN, YIELD_MAX = 850, 1450     # kWh/kWp/yr, Queenstown, all orientations
results = []


def record(level, name, detail):
    results.append((level, name, detail))
    mark = {"PASS": "  ok  ", "WARN": " WARN ", "FAIL": " FAIL "}[level]
    print(f"[{mark}] {name}")
    for line in detail.splitlines():
        print(f"          {line}")


def check_spec():
    import config
    want = config.PV_ASSUMPTIONS["panel_rated_power_w"]
    bad, seen = [], 0
    for p in sorted((DATA / "regions").glob("*/solar_potential.geojson")):
        try:
            feats = json.loads(p.read_text())["features"]
        except Exception:
            continue
        props = [f["properties"] for f in feats]
        pan = sum(x.get("panel_count") or 0 for x in props)
        kw = sum(x.get("kwp") or 0 for x in props)
        if not pan:
            continue
        seen += 1
        w = kw * 1000 / pan
        if abs(w - want) > 1.0:
            bad.append(f"{p.parent.name}: {w:.0f} W per panel, expected {want}")
    if not seen:
        record("WARN", "panel spec", "no per-region outputs found to check")
    elif bad:
        record("FAIL", "panel spec",
               f"{len(bad)} of {seen} regions carry the wrong wattage:\n"
               + "\n".join(bad[:8]))
    else:
        record("PASS", "panel spec", f"all {seen} regions at {want} W per panel")


def check_totals():
    if not SNAP.exists():
        record("WARN", "district totals",
               "no build_snapshot_prev.json -- nothing to compare against.\n"
               "Take one from the LIVE data before the next rebuild:\n"
               "  python src/compare_builds.py --snapshot")
        return
    r = subprocess.run([sys.executable, "src/compare_builds.py"],
                       cwd=ROOT, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    record("PASS" if r.returncode == 0 else "WARN", "district totals",
           out.strip()[-1200:] or "(no output)")


def check_yield():
    sp = DATA / "solar_potential.geojson"
    if not sp.exists():
        record("WARN", "yield", "no merged solar_potential.geojson yet")
        return
    props = [f["properties"] for f in json.loads(sp.read_text())["features"]]
    kw = sum(x.get("kwp") or 0 for x in props)
    kwh = sum(x.get("ac_kwh_year") or 0 for x in props)
    if not kw:
        record("FAIL", "yield", "no capacity in the merged file")
        return
    y = kwh / kw
    level = "PASS" if YIELD_MIN <= y <= YIELD_MAX else "FAIL"
    record(level, "yield",
           f"{y:.0f} kWh per kWp across the district "
           f"(defensible band {YIELD_MIN}-{YIELD_MAX})\n"
           f"{len(props):,} buildings, {kw / 1000:.1f} MWp, {kwh / 1e6:.1f} GWh/yr")


def check_watchlist():
    r = subprocess.run([sys.executable, "src/compare_builds.py", "--watchlist"],
                       cwd=ROOT, capture_output=True, text=True)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if not out:
        record("WARN", "bug-doc watchlist", "no output -- is the watchlist wired?")
        return
    # Parse the value AFTER the arrow. Substring-matching "0 panels" also hits
    # the bug DESCRIPTION -- several watchlist entries literally read
    # "(whole roof obstruction, 0 panels)" -- which would block a good push on
    # the text of the complaint rather than the state of the build.
    import re
    zeros = []
    for l in out.splitlines():
        m = re.search(r"->\s*(\d+)\s*panels", l)
        if m and int(m.group(1)) == 0:
            zeros.append(l)
    record("FAIL" if zeros else "PASS", "bug-doc watchlist",
           (("buildings dropped to zero:\n" + "\n".join(zeros[:6]) + "\n\n")
            if zeros else "") + out[-900:])


def check_invariants():
    inv = ROOT / "src" / "invariants.py"
    if not inv.exists():
        record("WARN", "invariants", "src/invariants.py not found")
        return
    r = subprocess.run([sys.executable, "src/invariants.py"],
                       cwd=ROOT, capture_output=True, text=True)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    record("PASS" if r.returncode == 0 else "FAIL", "invariants",
           out[-1200:] or "(no output)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-invariants", action="store_true")
    a = ap.parse_args()

    print("verifying the rebuild before it goes anywhere near the live site\n")
    check_spec()
    check_totals()
    check_yield()
    check_watchlist()
    if not a.skip_invariants:
        check_invariants()

    fails = [r for r in results if r[0] == "FAIL"]
    warns = [r for r in results if r[0] == "WARN"]
    print("\n" + "=" * 62)
    if fails:
        print(f"DO NOT PUSH — {len(fails)} check(s) failed:")
        for _, name, _ in fails:
            print(f"    {name}")
        return 1
    if warns:
        print(f"NO BLOCKERS, but {len(warns)} thing(s) want a human:")
        for _, name, _ in warns:
            print(f"    {name}")
        print("\nRead those, then the remaining gate is a human looking at the")
        print("map: totals passing does not mean any particular roof is right.")
        return 0
    print("All checks passed. The remaining gate is a human looking at the map.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
