"""Choose the 100 development roofs and the 30 held-back roofs.

    python tools/make_roof_set.py --regions pilot kingston ...   # writes data/roof_set.json

The development roofs are where the roof and panel rules are fixed, one
general rule at a time, against hand-drawn markup scored blind
(tools/markup_benchmark.py). Iterating on a fixed set is also how a rule ends
up fitting only that set, so 30 more roofs are drawn and held back: nobody
renders or studies them while fixing, and they are scored once, at the end,
beside the 600-roof random sample. A fix that helps the 100 and not the 30
fitted the 100.

THE DEVELOPMENT 100, in this order:
  flagged   every roof in data/roof_cases.json still on the map: a roof
            already looked at and called wrong, in the owner's words.
  include   roofs passed with --include: ones a render review found wrong
            and that are not in roof_cases.json yet.
  drawn     already-drawn complete roofs, round-robin by region so pilot
            (53 of the 119) does not dominate.
  kinds     new roofs, a few of each kind that has failed since the markup
            was drawn: commercial roofs, identical-roof copies, outlines the
            build slides, complex houses, and towns with no markup at all.
THE HELD-BACK 30: a seeded random draw over every built roof of 40 m2 or more,
neither drawn nor in the development set, so they stand for the map as a whole.
"""

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data"
OUT = DATA / "roof_set.json"
VOID = {"absent", "not_building", "unclear"}
MIN_M2 = 40.0
COMMERCIAL_M2 = 400.0
COMPLEX_FACES = 8
N_DEV, N_HOLD = 100, 30
SEED = 1101


def _built(region):
    """{building_id: (facets, panels)} from the region's built layout."""
    from src.region_build import area_paths
    p = Path(area_paths(region)["panel_layouts"])
    out = defaultdict(lambda: [0, 0])
    if not p.exists():
        return {}
    for f in json.loads(p.read_text())["features"]:
        q = f["properties"]
        k = q.get("kind")
        if k == "facet":
            out[int(q["building_id"])][0] += 1
        elif k == "panel":
            out[int(q["building_id"])][1] += 1
    return dict(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regions", nargs="+", required=True)
    ap.add_argument("--include", nargs="*", type=int, default=[],
                    help="roofs a render review found wrong")
    ap.add_argument("--include-why", default="render review")
    a = ap.parse_args()

    import geopandas as gpd
    from src.outline_fit import accepted
    from src.region_build import area_paths

    labels = json.loads((DATA / "roof_labels.json").read_text())["buildings"]
    drawn = {int(k) for k, v in labels.items()
             if v.get("faces") and v.get("complete") and v.get("problem") not in VOID}
    marked = {int(k) for k in labels}
    cases = json.loads((DATA / "roof_cases.json").read_text())
    cases = cases.get("cases", cases) if isinstance(cases, dict) else cases
    repeat_dir = DATA / "repeat_faces"

    roofs = {}       # id -> dict(region, m2, facets, panels, slid, repeat_set)
    for r in a.regions:
        p = area_paths(r)
        dd = p["dir"] / "building_outlines_dedup.geojson"
        src = dd if dd.exists() else p["outlines"]
        if not src.exists():
            continue
        g = gpd.read_file(src)
        built = _built(r)
        try:
            fits = json.loads((p["dir"] / "outline_fit.json").read_text())
        except (OSError, ValueError):
            fits = {}
        for bid, geom in zip(g["building_id"], g.geometry):
            bid = int(bid)
            fp = built.get(bid)
            if not fp or not fp[0]:
                continue            # withheld, demolished or never built
            rs = None
            rf = repeat_dir / f"{bid}.json"
            if rf.exists():
                try:
                    rs = json.loads(rf.read_text()).get("repeat_set")
                except ValueError:
                    rs = None
            roofs[bid] = {"region": r, "m2": round(geom.area, 1),
                          "facets": fp[0], "panels": fp[1],
                          "slid": bool(accepted(fits.get(str(bid)))),
                          "repeat_set": json.dumps(rs, sort_keys=True) if rs else None}
        print(f"{r}: {sum(1 for v in roofs.values() if v['region'] == r)} built roofs", flush=True)

    rng = random.Random(SEED)
    dev, why = [], {}

    def take(bid, reason):
        if bid in roofs and bid not in why and len(dev) < N_DEV:
            dev.append(bid)
            why[bid] = reason

    # flagged
    for c in cases:
        take(int(c["id"]), "flagged: " + c.get("defect", "")[:80])
    for b in a.include:
        take(b, a.include_why)
    n_flag = len(dev)

    # new kinds: commercial, identical copies, slid outlines, complex houses,
    # unmarked towns -- five of each, drawn before the old markup fills up
    pool = [b for b, v in roofs.items() if b not in marked and v["m2"] >= MIN_M2]
    rng.shuffle(pool)
    unmarked_regions = {v["region"] for v in roofs.values()} - {
        roofs[b]["region"] for b in marked if b in roofs}
    kinds = [
        ("commercial roof", lambda v: v["m2"] >= COMMERCIAL_M2),
        ("identical-roof copy", lambda v: v["repeat_set"] is not None),
        ("outline slid onto the roof", lambda v: v["slid"]),
        ("complex house", lambda v: v["m2"] < COMMERCIAL_M2 and v["facets"] >= COMPLEX_FACES),
        ("town with no markup", lambda v: v["region"] in unmarked_regions),
    ]
    n_kind = 5
    sets_used = set()
    for name, test in kinds:
        got = 0
        for b in pool:
            v = roofs[b]
            if got >= n_kind or b in why or not test(v):
                continue
            if v["repeat_set"] and v["repeat_set"] in sets_used:
                continue        # one copy per identical set
            take(b, name)
            if v["repeat_set"]:
                sets_used.add(v["repeat_set"])
            got += 1
    n_new = len(dev) - n_flag

    # already drawn, round-robin by region
    by_region = defaultdict(list)
    for b in sorted(drawn):
        if b in roofs and b not in why:
            by_region[roofs[b]["region"]].append(b)
    for lst in by_region.values():
        rng.shuffle(lst)
    order = sorted(by_region, key=lambda r: -len(by_region[r]))
    while len(dev) < N_DEV and any(by_region.values()):
        for r in order:
            if by_region[r]:
                take(by_region[r].pop(), "drawn")

    # held back: the map as a whole
    rest = [b for b, v in roofs.items()
            if b not in marked and b not in why and v["m2"] >= MIN_M2]
    hold = sorted(random.Random(SEED + 1).sample(rest, N_HOLD))

    doc = {
        "seed": SEED,
        "rule": __doc__.strip().splitlines()[0],
        "dev": [{"id": b, "region": roofs[b]["region"], "why": why[b],
                 "drawn": b in drawn} for b in dev],
        "holdout": [{"id": b, "region": roofs[b]["region"]} for b in hold],
    }
    OUT.write_text(json.dumps(doc, indent=1))
    need = sum(1 for d in doc["dev"] if not d["drawn"]) + N_HOLD
    print(f"\ndevelopment {len(dev)}: {n_flag} flagged or included, {n_new} new by kind, "
          f"{len(dev) - n_flag - n_new} already drawn")
    print(f"held back {N_HOLD}")
    print(f"to draw: {need} roofs -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
