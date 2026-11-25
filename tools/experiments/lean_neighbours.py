"""Does a roof sit better on the photo when its lean is read with its
neighbours' than when it is read alone?

THE RULE UNDER TEST. An orthophoto draws a point h metres up k*h metres from
where the LiDAR has it (src/photo_lean.py). k comes from where the camera
was, so buildings a street apart share it, whatever their heights. A roof's
own reading of k is noisy (a gutter or a shadow edge can win); the median k
of the roofs round it is not.

THE MEASUREMENT. Marked pilot roofs (not held back) with point faces. Truth
is the flat slide that best lays the point roof's lines on the drawn lines
(the markup is traced on this photo). Each variant predicts a slide
= k * mean line height; the error is its distance from the truth.
  none        no slide
  own         the roof's own lean (what the build does now)
  neighbours  median k of sharp roofs within NEIGHBOUR_M, own reading unused
  centred     own reading, with the prior centred on the neighbours' k
              instead of on no lean

  .venv/bin/python tools/experiments/lean_neighbours.py [region]
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
NEIGHBOUR_M = 150.0
SHARP = 3.5
MIN_N = 4


def surface(bid):
    """(bid, x, y, hm, own k, sharpness, s) with s the unpenalised surface."""
    import rasterio
    from scipy import ndimage as ndi
    from rasterio.windows import from_bounds
    from src import photo_lean as PL
    from src.region_build import area_paths
    global _I
    if "_I" not in globals():
        _I = rasterio.open(area_paths(sys.argv[1] if len(sys.argv) > 1 else "pilot")["imagery"])
    try:
        doc = json.loads(Path(f"data/point_faces/{bid}.json").read_text())
        faces, origin = PL.doc_faces(doc)
        if not faces:
            return None
        seams, edge, out = PL.line_samples(faces, origin)
        if len(edge) < 15:
            return None
        x0, y0, x1, y1 = out.buffer(PL.PAD_M).bounds
        px = _I.read([1, 2, 3], window=from_bounds(x0, y0, x1, y1, transform=_I.transform),
                     boundless=True).astype(np.float32).mean(0)
        if px.size == 0 or float(px.max()) <= 0:
            return None
        H, W = px.shape
        sx, sy = (x1 - x0) / W, (y1 - y0) / H
        g = ndi.gaussian_filter(px, PL.SIGMA_PX)
        gx, gy = ndi.sobel(g, axis=1), -ndi.sobel(g, axis=0)
        ks = np.arange(-PL.K_MAX, PL.K_MAX + 1e-9, PL.K_STEP)

        def surf(S):
            if len(S) < 15:
                return None
            C = np.clip(((S[None, :, 0] + ks[:, None] * S[None, :, 4] - x0) / sx).astype(int), 0, W - 1)
            R = np.clip(((y1 - S[None, :, 1] - ks[:, None] * S[None, :, 4]) / sy).astype(int), 0, H - 1)
            s = np.zeros((len(ks), len(ks)))
            for i in range(len(ks)):
                s[i, :] = np.abs(gx[R, C[i][None, :]] * S[:, 2] + gy[R, C[i][None, :]] * S[:, 3]).mean(axis=1)
            return (s - s.mean()) / max(s.std(), 1e-9)

        ss, se = surf(seams), surf(edge)
        s = se if ss is None else ss + se
        s = (s - s.mean()) / max(s.std(), 1e-9)
        hm = float(np.mean(np.r_[seams[:, 4], edge[:, 4]]))
        k, sharp = pick(s, hm, (0.0, 0.0))
        return dict(id=bid, x=origin[0], y=origin[1], hm=hm, k=k, sharp=sharp, s=s.astype(np.float32))
    except Exception as exc:
        print("failed", bid, repr(exc), flush=True)
        return None


def pick(s, hm, k0):
    from src import photo_lean as PL
    ks = np.arange(-PL.K_MAX, PL.K_MAX + 1e-9, PL.K_STEP)
    KX, KY = np.meshgrid(ks, ks, indexing="ij")
    t = s - PL.PRIOR * np.hypot(KX - k0[0], KY - k0[1]) * hm
    i, j = np.unravel_index(np.argmax(t), t.shape)
    return (float(ks[i]), float(ks[j])), float(t[i, j])


def truth(bid):
    """Best flat slide (dx, dy) of the point roof's lines onto the drawn
    lines (ridges, hips, valleys: not the outline, which a marked roof's
    point faces are clipped to), and the line F at 0.3 m there."""
    sys.path.insert(0, "tools")
    from shapely.affinity import translate
    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union
    import markup_benchmark as M
    from src import photo_lean as PL
    try:
        v = json.loads(Path("data/roof_labels.json").read_text())["buildings"][str(bid)]
        drawn = [p for p in (Polygon(f["ring"]).buffer(0) for f in v["faces"]) if p.area > 0.5]
        if len(drawn) < 2 or len(drawn) > 20:
            return None
        o = PL._close(drawn); dl = M._lines(drawn, o)
        st = [LineString([(p[0], p[1]) for p in l["points"]]) for l in (v.get("lines") or []) if len(l.get("points") or []) >= 2]
        if st:
            u = unary_union(st).difference(o.boundary.buffer(M.OUTLINE_BAND_M))
            if dl is not None:
                u = u.difference(dl.buffer(0.6))
            if not u.is_empty:
                dl = u if dl is None else unary_union([dl, u])
        faces, _ = PL.doc_faces(json.loads(Path(f"data/point_faces/{bid}.json").read_text()))
        got = [f["poly"] for f in faces]; gl = M._lines(got, PL._close(got))
        if dl is None or gl is None or dl.length < 4 or gl.length < 4:
            return None
        gb, db = gl.buffer(0.3), dl.buffer(0.3)

        def f1(dx, dy):
            t = translate(dl, -dx, -dy)
            rr = t.intersection(gb).length / t.length
            pp = gl.intersection(translate(db, -dx, -dy)).length / gl.length
            return 2 * rr * pp / max(rr + pp, 1e-9)
        best = (0.0, 0.0, f1(0, 0))
        for step, span in ((0.25, 3.0), (0.0625, 0.1875)):
            cx, cy = best[0], best[1]
            for dx in np.arange(cx - span, cx + span + 1e-9, step):
                for dy in np.arange(cy - span, cy + span + 1e-9, step):
                    q = f1(dx, dy)
                    if q > best[2]:
                        best = (float(dx), float(dy), q)
        return bid, best
    except Exception as exc:
        print("truth failed", bid, repr(exc), flush=True)
        return None


if __name__ == "__main__":
    REGION = sys.argv[1] if len(sys.argv) > 1 else "pilot"
    from src.outline_fit import _marked
    hold = {h["id"] for h in json.load(open("data/roof_set.json"))["holdout"]}
    ids = [int(k) for k in json.load(open(f"data/regions/{REGION}/image_shift.json"))
           if Path(f"data/point_faces/{k}.json").exists() and int(k) not in hold]
    with ProcessPoolExecutor(14) as ex:
        rows = [r for r in ex.map(surface, ids, chunksize=8) if r]
    by = {r["id"]: r for r in rows}
    xy = np.array([(r["x"], r["y"]) for r in rows])
    kk = np.array([r["k"] for r in rows]); sh = np.array([r["sharp"] for r in rows])
    print(len(rows), "roofs read;", int((sh >= SHARP).sum()), "sharp")

    def neighbours(r):
        d = np.hypot(xy[:, 0] - r["x"], xy[:, 1] - r["y"])
        m = (d < NEIGHBOUR_M) & (d > 0.01) & (sh >= SHARP)
        return kk[m] if m.sum() >= MIN_N else None

    # how alike are neighbours' leans?
    dev = []
    for r in rows:
        n = neighbours(r)
        if n is not None and r["sharp"] >= SHARP:
            dev.append(np.hypot(*(np.array(r["k"]) - np.median(n, 0))))
    dev = np.array(dev)
    print("sharp roofs: own k against neighbours' median k, m per m of height: median %.3f, p75 %.3f, p90 %.3f"
          % (np.median(dev), np.percentile(dev, 75), np.percentile(dev, 90)))

    marked = sorted(b for b in _marked() if b in by)
    with ProcessPoolExecutor(14) as ex:
        tr = dict(t for t in ex.map(truth, marked) if t)
    err = {"none": [], "own": [], "neighbours": [], "centred": []}
    out = []
    for b in marked:
        if b not in tr or tr[b][2] < 0.6:
            continue
        r = by[b]; t = np.array(tr[b][:2]); n = neighbours(r)
        if n is None:
            continue
        k0 = tuple(np.median(n, 0))
        kc, _ = pick(r["s"], r["hm"], k0)
        pred = {"none": (0, 0), "own": r["k"], "neighbours": k0, "centred": kc}
        row = dict(id=b, hm=round(r["hm"], 1), truth=[round(float(v), 2) for v in t], sharp=round(r["sharp"], 1))
        for name, k in pred.items():
            e = float(np.hypot(*(np.array(k) * r["hm"] - t)))
            err[name].append(e); row[name] = round(e, 2)
        out.append(row)
    json.dump(out, open(f"/tmp/lean_neighbours_{REGION}.json", "w"))
    print(len(out), "marked roofs scored (line F at the best slide >= 0.6)")
    for name, e in err.items():
        e = np.array(e)
        print("%-10s median %.2f m, within 0.3 m: %2d%%, within 0.5 m: %2d%%, over 1 m: %2d%%"
              % (name, np.median(e), 100 * (e <= .3).mean(), 100 * (e <= .5).mean(), 100 * (e > 1).mean()))
    worst = sorted(out, key=lambda r: -r["own"])[:10]
    for r in worst:
        print(r)
