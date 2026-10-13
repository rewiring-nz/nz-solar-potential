"""Obstruction answers for the photo roof samples: what stands proud of each roof.

    python tools/photo_obstruction_dataset.py [--data data/photo_shape] [--workers 14]

For every roof in tools/photo_shape_dataset.py's samples whose faces were
measured from LiDAR (data/selected_faces/<id>.json), the point
cloud is asked what sits off each face's plane --
src/obstruction_detection.detect_obstructions_from_height, the same test the
build runs: chimneys, vents, flues, plant, skylight upstands, roof hatches.
The answer is rasterised onto that sample's own grid and written beside it as
data/photo_shape/obstruct_<shard>.npz (obstruct[N,H,W] uint8, bid, view), in
the same row order as the shard, so a model can learn to see from the photo
what the LiDAR saw.

Hand-drawn obstructions (data/roof_labels.json) replace the LiDAR's answer
where they exist: the markup governs. Roofs with no LiDAR faces get no answer
(obs_known = 0) and are left out of training rather than taught "clear".

What LiDAR cannot see -- flush skylights, existing solar panels -- is not in
these answers; the photo colour detector keeps covering those.
"""

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

SELECTED = ROOT / "data" / "selected_faces"
LABELS = ROOT / "data" / "roof_labels.json"

_pc = None
_drawn = None


def _init():
    global _pc, _drawn
    import os
    os.environ.setdefault("SOLAR_LAZ_SINGLE", "1")
    from src.pointcloud_source import PointCloudSource
    _pc = PointCloudSource(max_cached_tiles=6)
    _drawn = {}
    if LABELS.exists():
        from shapely.geometry import Polygon
        for bid, lab in json.loads(LABELS.read_text())["buildings"].items():
            obs = []
            for o in lab.get("obstructions") or []:
                try:
                    obs.append(Polygon(o["ring"]).buffer(0))
                except Exception:
                    continue
            if lab.get("complete") or obs:
                _drawn[int(bid)] = obs


def roof_obstructions(bid):
    """Obstruction polygons (NZTM) for one roof, or None when unknown."""
    from shapely.geometry import Polygon
    from src.obstruction_detection import detect_obstructions_from_height
    if bid in _drawn:
        return _drawn[bid]
    f = SELECTED / f"{bid}.json"
    if not f.exists():
        return None
    import shapely
    doc = json.loads(f.read_text())
    if doc.get("source") == "synthetic" or not doc.get("faces"):
        return None
    out, measured = [], 0
    for ring in doc["faces"]:
        q = Polygon(ring)
        if not q.is_valid:
            q = q.buffer(0)
        if q.is_empty or q.area < 2:
            continue
        # The face files hold outlines only; the build fits each plane on the
        # fly. A least-squares start is enough: the height test re-fits the
        # face's dominant surface before measuring anything against it.
        try:
            pts = _pc.points_in_bbox(*q.bounds, building_only=True)
            pts = pts[shapely.contains_xy(q, pts[:, 0], pts[:, 1])]
            if len(pts) < 12:
                continue
            A = np.c_[pts[:, 0], pts[:, 1], np.ones(len(pts))]
            plane = np.linalg.lstsq(A, pts[:, 2], rcond=None)[0]
            out += detect_obstructions_from_height(_pc, q, tuple(plane))
            measured += 1
        except Exception:
            continue
    return out if measured else None


def one_shard(path):
    from photo_shape_dataset import REGIONS_DIR, _outlines, _rasterise  # noqa: F401
    from src import photo_shape as ps
    d = np.load(path)
    n = len(d["bid"])
    obstruct = np.zeros((n, ps.SIDE_PX, ps.SIDE_PX), np.uint8)
    known = np.zeros(n, np.uint8)
    region = str(d["region"][0])
    outlines = _outlines(region)
    cache = {}
    for i in range(n):
        bid = int(d["bid"][i])
        if bid not in cache:
            cache[bid] = roof_obstructions(bid)
        obs = cache[bid]
        if obs is None or bid not in outlines:
            continue
        fp = outlines[bid][0]
        side_m = float(d["m_per_px"][i]) * ps.SIDE_PX
        obs = [o for o in obs if not o.is_empty]
        if obs:
            obstruct[i] = _rasterise([(o, 1) for o in obs], fp.centroid.x, fp.centroid.y, side_m, 0, np.uint8)
        known[i] = 1
    out = path.with_name("obstruct_" + path.name)
    np.savez_compressed(out, obstruct=obstruct, known=known, bid=d["bid"], view=d["view"])
    return path.name, int(known.sum()), int((obstruct.reshape(n, -1).max(1) > 0).sum()), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data" / "photo_shape"))
    ap.add_argument("--workers", type=int, default=14)
    a = ap.parse_args()
    shards = sorted(p for p in Path(a.data).glob("*.npz") if not p.name.startswith("obstruct_"))
    tot = np.zeros(3, int)
    with ProcessPoolExecutor(a.workers, initializer=_init) as ex:
        for name, k, withobs, n in ex.map(one_shard, shards):
            tot += (k, withobs, n)
            print(f"  {name}: {k}/{n} roofs answered, {withobs} with obstructions", flush=True)
    print(f"total: {tot[0]}/{tot[2]} answered, {tot[1]} with obstructions")


if __name__ == "__main__":
    main()
