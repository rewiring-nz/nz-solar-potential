"""Roof faces read from the photo, for buildings the LiDAR cannot describe.

    python tools/predict_photo_shape.py glenorchy kinloch     # every building (no-LiDAR towns)
    python tools/predict_photo_shape.py arrowtown_east        # LiDAR town: only its no_lidar buildings

Writes data/regions/<r>/photo_roofs.json:
    {"model": ..., "photo": ..., "buildings": {bid: [{"ring": [[x, y], ...], "slope": s, "aspect": a|null}]}}
rings in NZTM, slope in degrees (<= 45), aspect the downslope bearing (null =
flat). src/synthesize_dsm.py builds a no-LiDAR town's roofs from it, and the
layout stage uses it for buildings in LiDAR towns that the LiDAR shows only
as ground (newer than the survey, or in its gaps).

WHICH BUILDINGS. In a synthetic (no-LiDAR) region, all of them. In a LiDAR
region, those its last build marked no_estimate / no_lidar -- the roof is
not in the point cloud, so the photo is the only evidence.

WHICH PHOTO. The region's imagery_mosaic.tif: in Glenorchy and Kinloch that
is the 2017-19 0.3 m photo the model trained on; in a LiDAR town it is the
2026 map photo, the one that shows buildings newer than the LiDAR (the model
trained on both).
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
from photo_shape_dataset import SIDE_MIN_M, _crop, _outlines, _rasterise  # noqa: E402

REGIONS_DIR = ROOT / "data" / "regions"
MODEL = ROOT / "data" / "models" / "photo_shape_v1.pt"


def targets(region, outlines):
    src = REGIONS_DIR / region / "elevation_source.json"
    if src.exists() and json.loads(src.read_text()).get("source") == "synthetic":
        return list(outlines)
    pl = REGIONS_DIR / region / "panel_layouts.geojson"
    if not pl.exists():
        return []
    out = []
    for f in json.loads(pl.read_text())["features"]:
        p = f["properties"]
        if p.get("kind") == "no_estimate" and p.get("no_estimate_reason") == "no_lidar":
            out.append(int(p["building_id"]))
    return [b for b in out if b in outlines]


def predict_region(region, model, photo_name="imagery_mosaic.tif"):
    import rasterio
    from rasterio.transform import from_bounds
    outlines = _outlines(region)
    want = targets(region, outlines)
    photo = REGIONS_DIR / region / photo_name
    if not want or not photo.exists():
        print(f"[{region}] {len(want)} buildings to read; photo {'present' if photo.exists() else 'missing'} -- skipped")
        return None
    rows = []
    with rasterio.open(photo) as src:
        for bid in want:
            fp = outlines[bid][0]
            minx, miny, maxx, maxy = fp.bounds
            side_m = max(SIDE_MIN_M, 1.15 * max(maxx - minx, maxy - miny))
            cx, cy = fp.centroid.x, fp.centroid.y
            img = _crop(src, cx, cy, side_m)
            if img is None:
                continue
            mask = _rasterise([(fp, 1)], cx, cy, side_m, 0, np.uint8)
            rows.append((bid, fp, cx, cy, side_m, img, mask))
    out = {}
    for i0 in range(0, len(rows), 128):
        chunk = rows[i0:i0 + 128]
        F, X, Y, S = ps.predict(model, np.stack([r[5] for r in chunk]), np.stack([r[6] for r in chunk]),
                                np.array([r[4] / ps.SIDE_PX for r in chunk]))
        for j, (bid, fp, cx, cy, side_m, _, mask) in enumerate(chunk):
            h = side_m / 2
            t = from_bounds(cx - h, cy - h, cx + h, cy + h, ps.SIDE_PX, ps.SIDE_PX)
            faces = ps.faces_from_maps(F[j], X[j], Y[j], S[j], mask, t, side_m / ps.SIDE_PX)
            keep = []
            for poly, slope, asp in faces:
                g = poly.intersection(fp)
                if g.is_empty or g.area < ps.MIN_FACE_M2 * 0.5:
                    continue
                if g.geom_type != "Polygon":
                    g = max(getattr(g, "geoms", [g]), key=lambda q: q.area)
                if g.geom_type != "Polygon":
                    continue
                keep.append({"ring": [[round(x, 2), round(y, 2)] for x, y in g.exterior.coords],
                             "slope": slope, "aspect": asp})
            if keep:
                out[str(bid)] = keep
    doc = {"model": str(MODEL.name), "photo": photo_name, "buildings": out}
    (REGIONS_DIR / region / "photo_roofs.json").write_text(json.dumps(doc))
    slopes = [f["slope"] for fs in out.values() for f in fs]
    print(f"[{region}] {len(out)} of {len(want)} buildings read from the photo; "
          f"face slope median {np.median(slopes) if slopes else 0:.0f} deg, max {max(slopes) if slopes else 0:.0f}")
    return doc


def main():
    import torch
    ap = argparse.ArgumentParser()
    ap.add_argument("regions", nargs="+")
    ap.add_argument("--model", default=str(MODEL))
    a = ap.parse_args()
    torch.set_num_threads(16)
    model = ps.load(a.model)
    for r in a.regions:
        predict_region(r, model)


if __name__ == "__main__":
    main()
