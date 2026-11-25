"""Paid reader against point roof, each allowed its own best slide, on the
marked roofs that have a reader reading in <readings dir> (never a held-back
roof). Same scoring as fair_compare.py.

Usage: python tools/experiments/fair_compare_reader.py <readings dir> out.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
import numpy as np


def main():
    from shapely.geometry import Polygon, LineString
    from shapely.ops import unary_union
    from shapely.affinity import translate
    import markup_benchmark as M
    from src import point_roof as pr
    B = json.loads(Path("data/roof_labels.json").read_text())["buildings"]
    hold = {int(h["id"]) for h in json.load(open("data/roof_set.json"))["holdout"]}
    O = pr.outlines("pilot")
    close = lambda ps: unary_union([q.buffer(0.15) for q in ps]).buffer(-0.15)
    rows = []
    for f in sorted(Path(sys.argv[1]).glob("*.json")):
        bid = int(f.stem)
        v = B.get(str(bid))
        if bid in hold or not v or not v.get("faces") or bid not in O:
            continue
        doc = json.loads(f.read_text())
        if doc.get("source") != "vision":
            continue
        drawn = [q for q in (Polygon(fc["ring"]).buffer(0) for fc in v["faces"]) if q.area > 0.5]
        o = close(drawn)
        dl = M._lines(drawn, o)
        st = [LineString([(q[0], q[1]) for q in l["points"]]) for l in (v.get("lines") or [])
              if len(l.get("points") or []) >= 2]
        if st:
            u = unary_union(st).difference(o.boundary.buffer(M.OUTLINE_BAND_M))
            if dl is not None:
                u = u.difference(dl.buffer(0.6))
            if not u.is_empty:
                dl = u if dl is None else unary_union([dl, u])
        if dl is None or dl.length < 2:
            continue

        def best(polys):
            polys = [q for q in polys if q.is_valid and q.area > 0.5]
            gl = M._lines(polys, close(polys)) if polys else None
            if gl is None:
                return dict(f03=0.0, f075=0.0, n=len(polys))

            def f1(d, tol):
                rr = d.intersection(gl.buffer(tol)).length / d.length
                pp = gl.intersection(d.buffer(tol)).length / gl.length
                return 2 * rr * pp / max(rr + pp, 1e-9)
            b = (f1(dl, 0.3), 0.0, 0.0)
            for dx in np.arange(-3, 3.01, 0.25):
                for dy in np.arange(-3, 3.01, 0.25):
                    s = f1(translate(dl, dx, dy), 0.3)
                    if s > b[0]:
                        b = (s, dx, dy)
            return dict(f03=b[0], f075=f1(translate(dl, b[1], b[2]), 0.75), n=len(polys))

        r = pr.reconstruct(bid, O[bid])
        row = dict(id=bid, nd=len(drawn), conf=doc.get("confidence"),
                   reader=best([Polygon(ring).buffer(0) for ring in doc["faces"]]),
                   point=best([fc["poly"] for fc in r["faces"]]))
        rows.append(row)
        print(bid, "drawn", len(drawn), "| reader", row["reader"]["n"], round(row["reader"]["f03"], 2), round(row["reader"]["f075"], 2),
              "| point roof", row["point"]["n"], round(row["point"]["f03"], 2), round(row["point"]["f075"], 2), flush=True)
    json.dump(rows, open(sys.argv[2], "w"))
    for sel, rs in (("all", rows), ("12 faces or fewer", [r for r in rows if r["nd"] <= 12])):
        print(sel, len(rs), "roofs")
        for name in ("reader", "point"):
            a = np.array([r[name]["f03"] for r in rs]); b = np.array([r[name]["f075"] for r in rs])
            print(f"   {name:7s} line F at 0.3 m median {np.median(a):.2f}, roofs >= 0.7: {int((a >= .7).sum())} | at 0.75 m median {np.median(b):.2f}, roofs >= 0.7: {int((b >= .7).sum())}")
        w = sum(r["point"]["f03"] > r["reader"]["f03"] + 0.05 for r in rs); l = sum(r["point"]["f03"] < r["reader"]["f03"] - 0.05 for r in rs)
        print("   point roof better on", w, "| reader better on", l, "| same", len(rs) - w - l)


if __name__ == "__main__":
    main()
