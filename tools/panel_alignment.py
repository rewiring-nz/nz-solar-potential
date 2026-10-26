"""How square the panels sit to the roof: each panel's direction against its
building's walls and its own face's edges.

    python tools/panel_alignment.py [regions...] [--deg 3] [--worst 25]

Josh, 27 Oct: "make sure panels are generally square/perpendicular with the
edges of rooftops". A panel's direction is its long side, mod 90 (portrait and
landscape agree). Reported per region and overall:

  off walls   share of panels more than --deg from every main wall direction
              of the building outline (src/outline_axis.wall_families_deg)
  off face    share more than --deg from the direction of their own face's
              longest straight edges (length-weighted mode, as the walls)

and the buildings with the most skew panels, for renders.
"""
import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _dev(a, b):
    d = abs(a - b) % 90.0
    return min(d, 90.0 - d)


def _panel_deg(poly):
    cc = list(poly.minimum_rotated_rectangle.exterior.coords)
    e = [(math.hypot(cc[i + 1][0] - cc[i][0], cc[i + 1][1] - cc[i][1]),
          math.degrees(math.atan2(cc[i + 1][1] - cc[i][1], cc[i + 1][0] - cc[i][0]))) for i in range(2)]
    return max(e)[1] % 90.0


def region(r, deg):
    from shapely.geometry import shape
    from shapely.ops import transform as tf
    from pyproj import Transformer
    from src.outline_axis import wall_families_deg, dominant_axis_deg
    T = Transformer.from_crs(4326, 2193, always_xy=True).transform
    d = ROOT / "data" / "regions" / r
    lay = d / "panel_layouts.geojson"
    if not lay.exists():
        return None
    outl = d / "building_outlines_dedup.geojson"
    if not outl.exists():
        outl = d / "building_outlines.geojson"
    walls = {}
    for f in json.loads(outl.read_text())["features"]:
        q = f["properties"]
        bid = q.get("building_id")
        if bid is None:
            continue
        g = shape(f["geometry"])
        if g.bounds[0] > 1000:                          # already NZTM
            walls[bid] = g
        else:
            walls[bid] = tf(T, g)
    faces = defaultdict(list); panels = defaultdict(list)
    for f in json.loads(lay.read_text())["features"]:
        q = f["properties"]; k = q.get("kind")
        if k == "facet":
            faces[q["building_id"]].append((q.get("aspect_deg"), q.get("slope_deg"), tf(T, shape(f["geometry"]))))
        elif k == "panel":
            panels[q["building_id"]].append((q.get("plane_aspect_deg"), tf(T, shape(f["geometry"]))))
    n = off_w = off_f = 0
    worst = []
    for bid, ps in panels.items():
        bg = walls.get(bid)
        wf = wall_families_deg(bg) if bg is not None else []
        bad_w = bad_f = 0
        for asp, pg in ps:
            a = _panel_deg(pg)
            n += 1
            if wf and min(_dev(a, w) for w in wf) > deg:
                off_w += 1; bad_w += 1
            # its own face: the facet containing the panel's centre
            c = pg.centroid
            fc = [fg for fa, fs, fg in faces.get(bid, []) if fg.buffer(0.3).contains(c)]
            if fc:
                ax = dominant_axis_deg(fc[0])
                if ax is not None and _dev(a, ax) > deg:
                    off_f += 1; bad_f += 1
        if bad_w or bad_f:
            worst.append((bad_w, bad_f, len(ps), bid))
    return {"region": r, "panels": n, "off_walls": off_w, "off_face": off_f, "worst": sorted(worst, reverse=True)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("regions", nargs="*")
    ap.add_argument("--deg", type=float, default=3.0)
    ap.add_argument("--worst", type=int, default=25)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    import config
    regs = a.regions or sorted(p.name for p in (ROOT / "data" / "regions").iterdir()
                               if p.is_dir() and p.name not in getattr(config, "SERVED_EXCLUDE", set()))
    tot = {"panels": 0, "off_walls": 0, "off_face": 0}
    worst = []
    out = []
    for r in regs:
        s = region(r, a.deg)
        if not s or not s["panels"]:
            continue
        out.append({k: v for k, v in s.items() if k != "worst"})
        for k in tot:
            tot[k] += s[k]
        worst += [(w[0], w[1], w[2], w[3], r) for w in s["worst"]]
        print(f"{r:24s} {s['panels']:7d} panels  off walls {100 * s['off_walls'] / s['panels']:5.1f}%  "
              f"off face {100 * s['off_face'] / s['panels']:5.1f}%", flush=True)
    if tot["panels"]:
        print(f"{'ALL':24s} {tot['panels']:7d} panels  off walls {100 * tot['off_walls'] / tot['panels']:5.1f}%  "
              f"off face {100 * tot['off_face'] / tot['panels']:5.1f}%")
    print("most skew buildings (off walls, off face, of panels):")
    for w in sorted(worst, reverse=True)[:a.worst]:
        print(f"  {w[3]} {w[4]:22s} {w[0]:4d} {w[1]:4d} of {w[2]}")
    if a.json:
        Path(a.json).write_text(json.dumps({"total": tot, "regions": out,
                                            "worst": [list(w) for w in sorted(worst, reverse=True)]}, indent=1))


if __name__ == "__main__":
    main()
