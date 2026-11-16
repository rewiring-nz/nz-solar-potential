"""Point roof placed by the photo's lean, on every marked roof whose region is
on this machine, scored against the markup's lines at 0.3 m.

The markup is drawn on the region's pipeline photo (imagery_mosaic.tif): the
survey-year photo in pilot, the map's own 2026 photo in every other
Queenstown region. So the regions outside pilot are a direct check of the
lean on the photo the live map shows. Held-back roofs are never read.

Per roof: line F as built (LiDAR frame), with the best single slide the
markup allows (upper figure), and leaned by photo_lean at each PRIOR.

Usage: python tools/experiments/lean_check_regions.py out.json [--priors 0.5 1.0 1.5] [--regions a b]
"""
import argparse
import json
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
sys.path.insert(0, "tools/experiments")
import numpy as np

_CACHE = {}


def one(job):
    bid, region, priors = job
    try:
        import rasterio
        from shapely.geometry import Polygon, LineString
        from shapely.ops import unary_union
        from shapely.affinity import translate
        import markup_benchmark as M
        import photo_lean as PL
        from src import point_roof as pr
        from src.region_build import area_paths
        if "B" not in _CACHE:
            _CACHE["B"] = json.loads(Path("data/roof_labels.json").read_text())["buildings"]
        if region not in _CACHE:
            _CACHE[region] = (pr.outlines(region), rasterio.open(area_paths(region)["imagery"]))
        O, img = _CACHE[region]
        v = _CACHE["B"][str(bid)]
        close = lambda ps: unary_union([q.buffer(0.15) for q in ps]).buffer(-0.15)
        drawn = [p for p in (Polygon(f["ring"]).buffer(0) for f in v["faces"]) if p.area > 0.5]
        o = close(drawn)
        dl = M._lines(drawn, o)
        st = [LineString([(p[0], p[1]) for p in l["points"]]) for l in (v.get("lines") or [])
              if len(l.get("points") or []) >= 2]
        if st:
            u = unary_union(st).difference(o.boundary.buffer(M.OUTLINE_BAND_M))
            if dl is not None:
                u = u.difference(dl.buffer(0.6))
            if not u.is_empty:
                dl = u if dl is None else unary_union([dl, u])
        if dl is None or dl.length < 4 or bid not in O:
            return None
        r = pr.reconstruct(bid, O[bid])
        if not r["faces"]:
            return None
        org = (r["poly"].centroid.x, r["poly"].centroid.y)
        asb = [f["poly"] for f in r["faces"]]
        gl0 = M._lines(asb, close(asb))
        if gl0 is None or gl0.length < 4:
            return None

        def f1(gl, d=dl, tol=0.3):
            if gl is None:
                return 0.0
            rr = d.intersection(gl.buffer(tol)).length / d.length
            pp = gl.intersection(d.buffer(tol)).length / gl.length
            return 2 * rr * pp / max(rr + pp, 1e-9)

        res = dict(id=bid, region=region, base=f1(gl0), nd=len(drawn), ng=len(asb))
        best = (res["base"], 0.0, 0.0)
        for dx in np.arange(-3, 3.01, 0.25):
            for dy in np.arange(-3, 3.01, 0.25):
                s = f1(gl0, translate(dl, -dx, -dy))
                if s > best[0]:
                    best = (s, float(dx), float(dy))
        res.update(oracle=best[0], slide=[best[1], best[2]])
        seams, edge, _ = PL.line_samples(r["faces"], org)
        res["h"] = float(np.mean(np.r_[seams[:, 4], edge[:, 4]]))
        for p in priors:
            PL.PRIOR = p
            k = PL.photo_lean(r["faces"], org, img)
            if k is None:
                continue
            ln = PL.leaned(r["faces"], org, k[0], k[1])
            res[f"lean_{p}"] = f1(M._lines(ln, close(ln)))
            res[f"k_{p}"] = [k[0], k[1], k[2]]
        return res
    except Exception as exc:
        return dict(id=bid, region=region, err=repr(exc)[:160])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--priors", nargs="*", type=float, default=[0.5, 1.0, 1.5])
    ap.add_argument("--regions", nargs="*", default=None)
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    from src.region_build import area_paths
    B = json.loads(Path("data/roof_labels.json").read_text())["buildings"]
    hold = {int(h["id"]) for h in json.load(open("data/roof_set.json"))["holdout"]}
    jobs = []
    for b, v in B.items():
        reg = v.get("area")
        if int(b) in hold or v.get("problem") or not reg or not (2 <= len(v.get("faces") or []) <= 20):
            continue
        if a.regions and reg not in a.regions:
            continue
        try:
            if not Path(area_paths(reg)["imagery"]).exists():
                continue
        except Exception:
            continue
        jobs.append((int(b), reg, a.priors))
    jobs.sort(key=lambda j: j[1])
    print(len(jobs), "roofs in", len({j[1] for j in jobs}), "regions", flush=True)
    with ProcessPoolExecutor(a.jobs) as ex:
        rows = [r for r in ex.map(one, jobs, chunksize=4) if r]
    json.dump(rows, open(a.out, "w"))
    errs = [r for r in rows if "err" in r]
    ok = [r for r in rows if "err" not in r]
    print(len(ok), "scored,", len(errs), "errors", [e["err"] for e in errs[:3]])
    groups = defaultdict(list)
    for r in ok:
        groups["pilot (survey-year photo)" if r["region"] == "pilot" else "other regions (the map's photo)"].append(r)
    for g, rs in groups.items():
        print(g, len(rs), "roofs")
        for key in ["base", "oracle"] + [f"lean_{p}" for p in a.priors]:
            v = np.array([r.get(key, 0.0) for r in rs])
            print(f"   {key:10s} line F at 0.3 m: median {np.median(v):.2f}, roofs >= 0.7: {int((v >= .7).sum())}, >= 0.5: {int((v >= .5).sum())}")
        sl = np.array([np.hypot(*r["slide"]) for r in rs if r["oracle"] >= 0.6])
        if len(sl):
            print(f"   markup's slide (roofs with oracle >= 0.6, n={len(sl)}): median {np.median(sl):.2f} m, p90 {np.percentile(sl, 90):.2f} m")


if __name__ == "__main__":
    main()
