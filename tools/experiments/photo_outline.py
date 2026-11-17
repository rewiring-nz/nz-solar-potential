"""Where is the roof in the map's photo? A segmentation model (SAM ViT-B,
data/sam_vit_b.pth) prompted with the surveyed outline, on named buildings.

THE QUESTION. The surveyed outline says which building; the photo says where
its roof is. Prompt: a box round the outline (+BOX_PAD_M) and points inside
it. Drawn per building over the map's own tiles: surveyed outline (yellow),
the model's roof (red), for judging by eye whether the photo alone gives a
usable roof edge.

  .venv/bin/python tools/experiments/photo_outline.py <out.jpg> <id> [<id> ...]
"""
import glob
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
BOX_PAD_M = 2.0
VIEW_PAD_M = 9.0
N_POINTS = 6

if __name__ == "__main__":
    import geopandas as gpd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import torch
    from rasterio.features import shapes
    from segment_anything import SamPredictor, sam_model_registry
    from shapely.geometry import Point, shape
    from shapely.ops import unary_union
    from src.register_imagery import RES_M, _Tiles

    out, ids = sys.argv[1], [int(a) for a in sys.argv[2:]]
    where = {}
    for p in sorted(glob.glob("data/regions/*/building_outlines.geojson")):
        g = gpd.read_file(p)
        for b in ids:
            if b not in where and (g.building_id == b).any():
                where[b] = (Path(p).parent, g[g.building_id == b].geometry.iloc[0])
    sam = sam_model_registry["vit_b"](checkpoint="data/sam_vit_b.pth")
    sam.to("mps" if torch.backends.mps.is_available() else "cpu")
    pred = SamPredictor(sam)
    rng = np.random.default_rng(0)
    cols = 4
    rows = (len(ids) + cols - 1) // cols
    fig, ax = plt.subplots(rows, cols, figsize=(6.5 * cols, 6.5 * rows), squeeze=False)
    for a in ax.ravel():
        a.axis("off")
    for n, b in enumerate(ids):
        a = ax.ravel()[n]
        if b not in where:
            a.set_title(f"{b}: no local data"); continue
        d, geom = where[b]
        x0, y0, x1, y1 = geom.buffer(VIEW_PAD_M).bounds
        H, W = int(round((y1 - y0) / RES_M)), int(round((x1 - x0) / RES_M))
        img = np.ascontiguousarray(np.moveaxis(np.asarray(_Tiles(d / "display_tiles").window((x0, y0, x1, y1), H, W)), 0, -1))
        px = lambda x, y: ((x - x0) / RES_M, (y1 - y) / RES_M)
        bx0, by0, bx1, by1 = geom.buffer(BOX_PAD_M).bounds
        box = np.array([*px(bx0, by1), *px(bx1, by0)])
        inner = geom.buffer(-1.0) if not geom.buffer(-1.0).is_empty else geom
        pts = []
        mx0, my0, mx1, my1 = inner.bounds
        while len(pts) < N_POINTS:
            q = Point(rng.uniform(mx0, mx1), rng.uniform(my0, my1))
            if inner.contains(q):
                pts.append(px(q.x, q.y))
        pred.set_image(img)
        masks, scores, _ = pred.predict(point_coords=np.array(pts), point_labels=np.ones(len(pts)), box=box, multimask_output=True)
        k = int(np.argmax(scores))
        polys = [shape(s) for s, v in shapes(masks[k].astype(np.uint8), mask=masks[k]) if v == 1]
        roof = unary_union(polys).buffer(0).simplify(3.0) if polys else None      # pixel units
        a.imshow(img, extent=(x0, x1, y0, y1))
        for p in getattr(geom, "geoms", [geom]):
            a.plot(*p.exterior.xy, color="yellow", lw=1.4)
        area = 0.0
        if roof is not None and not roof.is_empty:
            for p in getattr(roof, "geoms", [roof]):
                if p.area * RES_M * RES_M < 4:
                    continue
                xs, ys = np.array(p.exterior.xy)
                a.plot(x0 + xs * RES_M, y1 - ys * RES_M, color="red", lw=2.0)
                area += p.area * RES_M * RES_M
        a.set_xlim(x0, x1); a.set_ylim(y0, y1)
        a.set_title(f"{b} {d.name}: surveyed {geom.area:.0f} m2, photo {area:.0f} m2, score {scores[k]:.2f}", fontsize=10)
        print(b, d.name, round(geom.area), round(area), round(float(scores[k]), 2), flush=True)
    plt.tight_layout(); plt.savefig(out, dpi=70)
