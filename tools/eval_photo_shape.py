"""Judge the photo roof-shape model against today's guess, on towns it never saw.

    python tools/eval_photo_shape.py [--model data/models/photo_shape_v1.pt] [--view p03]

For every roof in the held-out towns (tools/train_photo_shape.py
TEST_REGIONS) three readings are scored against the LiDAR's answer:

  guess   -- what ships for a roof with no LiDAR today (src/synthesize_dsm.py:
             gable under 60 m2, hip at 20 degrees, flat from 400 m2)
  model   -- the model's per-pixel reading
  faces   -- the model's reading turned into roof faces (what would ship)

PER ROOF: the error in yearly sunshine per m2 of roof (the quantity panels
are paid by; SolarModel at the roof's own location), slope error, and the
share of roof area facing within 45 degrees of the truth. The verdict is on
the median AND the bad tail (90th percentile): a model that is better on
average but wrecks one roof in ten is not better.

--view p03 is the 2017-19 0.3 m photo, the one Glenorchy has; p26 is the
2026 map photo, the one new Arrowtown buildings have.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from src import photo_shape as ps  # noqa: E402
from train_photo_shape import TEST_REGIONS, load  # noqa: E402


def guess_maps(fp, cx, cy, side_m):
    """Per-pixel slope and downslope direction of today's no-LiDAR roof."""
    from src.synthesize_dsm import gable_faces, hip_faces, roof_model, roof_outline, wall_plane
    from photo_shape_dataset import _rasterise
    kind, pitch, eave = roof_model(fp.area)
    g = roof_outline(fp)
    faces = []
    if kind == "gable":
        faces = gable_faces(g, 0.0, eave, pitch)
    elif kind == "hip":
        faces = [(q, wall_plane(w, 0.0, eave, pitch)) for q, w in hip_faces(g)]
    n = ps.SIDE_PX
    if not faces:
        return np.zeros((n, n)), np.zeros((n, n)), np.zeros((n, n)), kind
    sv, xv, yv = [], [], []
    for q, (a, b, _) in faces:
        grad = float(np.hypot(a, b))
        s = float(np.degrees(np.arctan(grad)))
        dx, dy = (-a / grad, -b / grad) if grad > 1e-9 else (0.0, 0.0)
        sv.append((q, s)); xv.append((q, dx)); yv.append((q, dy))
    return (_rasterise(sv, cx, cy, side_m), _rasterise(xv, cx, cy, side_m),
            _rasterise(yv, cx, cy, side_m), kind)


class Sun:
    """Yearly sunshine per m2 by slope and facing, one table per town."""

    def __init__(self):
        self.models = {}

    def table(self, region):
        if region not in self.models:
            import config
            from src.solar_model import SolarModel
            b = config.REGIONS[region]
            self.models[region] = SolarModel(lat=(b[1] + b[3]) / 2, lon=(b[0] + b[2]) / 2)
        return self.models[region]

    def mean(self, region, slope, dx, dy, w):
        m = self.table(region)
        asp = (np.degrees(np.arctan2(dx, dy)) + 360) % 360
        s = np.clip(slope, 0, 45)
        # The table is binned: evaluate each distinct (slope, facing) bin once.
        sb = np.round(s).astype(int)
        ab = (np.round(asp / 5).astype(int) * 5) % 360
        tot, cache = 0.0, {}
        for si, ai, wi in zip(sb.ravel(), ab.ravel(), w.ravel()):
            if wi <= 0:
                continue
            k = (si, ai)
            if k not in cache:
                cache[k] = m.annual_poa_kwh_per_m2(si, ai if si >= ps.FLAT_DEG else 0)
            tot += cache[k] * wi
        return tot / max(w.sum(), 1e-9)


def faces_maps(flat, dx, dy, s, mask, m_per_px, fp):
    """The faces that would ship (photo_shape.snap_faces), back on the grid."""
    from rasterio.transform import from_bounds
    n = ps.SIDE_PX
    h = m_per_px * n / 2
    cx, cy = fp.centroid.x, fp.centroid.y
    t = from_bounds(cx - h, cy - h, cx + h, cy + h, n, n)
    faces, _ = ps.snap_faces(flat, dx, dy, s, mask, t, m_per_px, fp)
    from rasterio.features import rasterize
    S = np.zeros((n, n)); X = np.zeros((n, n)); Y = np.zeros((n, n))
    for poly, slope, asp in faces:
        sel = rasterize([(poly, 1)], out_shape=(n, n), transform=t, fill=0, dtype=np.uint8) > 0
        S[sel] = slope
        if asp is not None:
            X[sel] = np.sin(np.radians(asp)); Y[sel] = np.cos(np.radians(asp))
    return S, X, Y, len(faces)


def main():
    import torch
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(ROOT / "data" / "models" / "photo_shape_v1.pt"))
    ap.add_argument("--data", default=str(ROOT / "data" / "photo_shape"))
    ap.add_argument("--view", default="p03")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    torch.set_num_threads(16)
    D = load(a.data, lambda r: r in TEST_REGIONS)
    keep = D["view"] == a.view
    D = {k: v[keep] for k, v in D.items()}
    if a.limit:
        D = {k: v[:a.limit] for k, v in D.items()}
    print(f"{len(D['bid'])} held-out roofs ({a.view}) in {sorted(set(D['region']))}", flush=True)
    model = ps.load(a.model)
    outlines = {}
    from photo_shape_dataset import _outlines
    sun = Sun()
    rows = []
    for i0 in range(0, len(D["bid"]), 128):
        idx = np.arange(i0, min(i0 + 128, len(D["bid"])))
        F, X, Y, S = ps.predict(model, D["photo"][idx], D["mask"][idx], D["m_per_px"][idx])
        for j, i in enumerate(idx):
            r, bid = str(D["region"][i]), int(D["bid"][i])
            if r not in outlines:
                outlines[r] = _outlines(r)
            fp = outlines[r][bid][0]
            side_m = float(D["m_per_px"][i]) * ps.SIDE_PX
            L = D["labelled"][i] > 0
            ts = D["slope"][i].astype(float); tx = D["dirx"][i].astype(float); ty = D["diry"][i].astype(float)
            gs, gx, gy, kind = guess_maps(fp, fp.centroid.x, fp.centroid.y, side_m)
            fs, fx, fy, nf = faces_maps(F[j], X[j], Y[j], S[j], D["mask"][i], float(D["m_per_px"][i]), fp)
            mx, my = X[j].copy(), Y[j].copy()
            flat_m = (F[j] > 0.5) | (S[j] < ps.FLAT_DEG)
            ms = np.where(flat_m, np.minimum(S[j], ps.FLAT_DEG), S[j]); mx[flat_m] = 0; my[flat_m] = 0
            w = L.astype(float)
            truth_sun = sun.mean(r, ts, tx, ty, w)
            row = {"region": r, "bid": bid, "area": fp.area, "guess_kind": kind, "faces": nf,
                   "truth_slope": float(np.median(ts[L])), "truth_sun": truth_sun}
            for name, (s_, x_, y_) in (("guess", (gs, gx, gy)), ("model", (ms, mx, my)), ("faces", (fs, fx, fy))):
                tilt = L & (ts >= ps.FLAT_DEG)
                cos = x_ * tx + y_ * ty
                row[f"{name}_sun_err_pct"] = 100 * abs(sun.mean(r, s_, x_, y_, w) - truth_sun) / truth_sun
                row[f"{name}_slope_err"] = float(np.abs(s_ - ts)[L].mean())
                row[f"{name}_facing_ok"] = float((cos[tilt] > np.cos(np.radians(45))).mean()) if tilt.any() else float("nan")
                row[f"{name}_max_slope"] = float(s_[L].max())
            rows.append(row)
        print(f"  {len(rows)} roofs scored", flush=True)
    summary = {}
    for name in ("guess", "model", "faces"):
        e = np.array([r[f"{name}_sun_err_pct"] for r in rows])
        se = np.array([r[f"{name}_slope_err"] for r in rows])
        fo = np.array([r[f"{name}_facing_ok"] for r in rows])
        summary[name] = {"sun_err_median_pct": float(np.median(e)), "sun_err_p90_pct": float(np.percentile(e, 90)),
                         "sun_err_p99_pct": float(np.percentile(e, 99)),
                         "slope_err_median_deg": float(np.median(se)), "slope_err_p90_deg": float(np.percentile(se, 90)),
                         "facing_ok_median": float(np.nanmedian(fo)), "facing_ok_mean": float(np.nanmean(fo)),
                         "max_slope_any_roof": float(max(r[f"{name}_max_slope"] for r in rows))}
    wins = np.mean([r["faces_sun_err_pct"] < r["guess_sun_err_pct"] for r in rows])
    summary["faces_beats_guess_share"] = float(wins)
    summary["n_roofs"] = len(rows)
    summary["view"] = a.view
    print(json.dumps(summary, indent=1))
    out = Path(a.out or (Path(a.model).with_suffix(f".eval_{a.view}.json")))
    out.write_text(json.dumps({"summary": summary, "rows": rows}))
    print("wrote", out)


if __name__ == "__main__":
    main()
