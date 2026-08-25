"""
Diff a finished build against the previous one, per building.

Why this exists: every placement regression in this project so far was
found the same way -- Josh looked at a roof on the live map and said "this
is wrong". Three separate gate rules each deleted real panels from real
houses (4 Abbottswood Ln 61->6, 6 Shotover St 72kW->4, 7 Cedar Dr 69->6),
and nothing in the pipeline noticed, because every run prints healthy
totals whether or not it just destroyed a suburb. Totals hide it: a rule
that wipes 155 panels off one commercial roof moves a 743,303-panel total
by 0.02%.

So compare per BUILDING, and rank by what changed most. A rebuild that
intends to change one thing should show a short, explainable list.

Usage:
    # before the rebuild's merge overwrites data/solar_potential.geojson
    python src/compare_builds.py --snapshot
    # ...rebuild...
    python src/compare_builds.py [--top 40] [--min-loss 5]

--snapshot writes data/build_snapshot_prev.json (gitignored, ~0.7MB).
"""

import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SOLAR = DATA_DIR / "solar_potential.geojson"
SNAP = DATA_DIR / "build_snapshot_prev.json"


def _read_current():
    d = json.loads(SOLAR.read_text())
    out = {}
    for f in d["features"]:
        p = f["properties"]
        out[str(p["building_id"])] = [p.get("panel_count", 0), round(p.get("kwp", 0), 2),
                                      p.get("fill_panels_100", 0), p.get("address", "")]
    return out


def snapshot():
    cur = _read_current()
    SNAP.write_text(json.dumps(cur))
    print(f"snapshot: {len(cur)} buildings, {sum(v[0] for v in cur.values()):,} panels, "
          f"{sum(v[1] for v in cur.values()) / 1000:.1f} MWp -> {SNAP.name}")


def compare(top=40, min_loss=5):
    if not SNAP.exists():
        raise SystemExit(f"no {SNAP.name} -- run with --snapshot before the rebuild")
    prev, cur = json.loads(SNAP.read_text()), _read_current()

    p_tot, c_tot = sum(v[0] for v in prev.values()), sum(v[0] for v in cur.values())
    p_kwp, c_kwp = sum(v[1] for v in prev.values()), sum(v[1] for v in cur.values())
    print(f"buildings {len(prev):,} -> {len(cur):,}")
    print(f"panels    {p_tot:,} -> {c_tot:,}  ({c_tot - p_tot:+,}, {100 * (c_tot - p_tot) / max(p_tot, 1):+.1f}%)")
    print(f"capacity  {p_kwp / 1000:.1f} -> {c_kwp / 1000:.1f} MWp  ({(c_kwp - p_kwp) / 1000:+.1f})")

    gone = [b for b in prev if b not in cur]
    new = [b for b in cur if b not in prev]
    if gone:
        print(f"\n{len(gone)} buildings DISAPPEARED from the build "
              f"(dedupe ownership change, or a region that failed): {gone[:10]}")
    if new:
        print(f"{len(new)} buildings are new to the build: {new[:10]}")

    deltas = []
    for b, c in cur.items():
        p = prev.get(b)
        if p is None:
            continue
        d = c[0] - p[0]
        if abs(d) >= min_loss:
            deltas.append((d, b, p, c))
    losses = sorted(d for d in deltas if d[0] < 0)
    gains = sorted((d for d in deltas if d[0] > 0), reverse=True)

    # Losses first and always: a panel that vanished is the failure mode that
    # has actually bitten this project, repeatedly. Gains are usually the
    # intended effect of whatever changed.
    for label, rows in (("LOST panels", losses), ("GAINED panels", gains)):
        print(f"\n{len(rows)} buildings {label} (>= {min_loss}); worst {min(top, len(rows))}:")
        for d, b, p, c in rows[:top]:
            print(f"  {d:+5d}  {p[0]:4d} -> {c[0]:4d} panels, {p[1]:7.1f} -> {c[1]:7.1f} kWp  "
                  f"#{b}  {c[3] or p[3]}")

    wiped = [r for r in losses if r[3][0] == 0 and r[2][0] > 0]
    if wiped:
        print(f"\nWARNING: {len(wiped)} buildings went to ZERO panels having had some before. "
              f"That is the shape of every gate regression so far -- check these on the map "
              f"before deploying:")
        for d, b, p, c in wiped[:20]:
            print(f"  #{b}  {p[0]} -> 0 panels  {p[3]}")


def main():
    argv = sys.argv[1:]
    if "--snapshot" in argv:
        return snapshot()
    top = int(argv[argv.index("--top") + 1]) if "--top" in argv else 40
    min_loss = int(argv[argv.index("--min-loss") + 1]) if "--min-loss" in argv else 5
    compare(top=top, min_loss=min_loss)


if __name__ == "__main__":
    main()
