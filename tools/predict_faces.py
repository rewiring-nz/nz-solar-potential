"""Precompute selected roof faces per building, for the build to read.

The selector (src/face_candidates + the scorer) needs SAM and torch, which
live in .venv-sam and have no business inside the build environment. So faces
are computed here, ahead of time, one JSON per building -- exactly the seam
vision_lines already uses -- and roof_partition reads them behind a flag, the
same way it reads Josh's drawn faces.

Written per building: the winning candidate's face rings (NZTM), which reading
won, its evidence score, and both candidates' scores, so the build can apply
its own shipping threshold without recomputing anything.

    .venv-sam/bin/python tools/predict_faces.py --region pilot --ids ...
    .venv-sam/bin/python tools/predict_faces.py --bench
"""

import argparse
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

OUT = ROOT / "data" / "selected_faces"
PAD_M = 4.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="pilot")
    ap.add_argument("--ids", nargs="*", type=int, default=None)
    ap.add_argument("--bench", action="store_true",
                    help="every roof in the benchmark set")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    import numpy as np
    import torch
    import geopandas as gpd
    import rasterio
    import rasterio.windows
    from segment_anything import sam_model_registry, SamPredictor
    from segment_anything.utils.transforms import ResizeLongestSide as _RLS
    from src.region_build import area_paths
    from src.pointcloud_source import PointCloudSource
    from src.roof_partition import top_surface
    from src.face_candidates import sam_faces, line_faces, score_candidate
    import train_line_model as T

    _oac = _RLS.apply_coords
    _RLS.apply_coords = (lambda self, c, sz:
                         _oac(self, c, sz).astype(np.float32))

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    sam = sam_model_registry["vit_b"](checkpoint=str(ROOT / "data/sam_vit_b.pth"))
    sam.to(device)
    predictor = SamPredictor(sam)
    ck = torch.load(ROOT / "data/models/roof_lines_v3.pt",
                    map_location="cpu", weights_only=False)
    lm = T.build_unet(ck.get("pretrained", False))
    lm.load_state_dict(ck["state_dict"])
    lm.to(device).eval()

    ids = a.ids
    if a.bench and not ids:
        ids = [int(x) for x in
               (ROOT / "data/bench_ids.txt").read_text().split()]

    p = area_paths(a.region)
    dd = p["dir"] / "building_outlines_dedup.geojson"
    gdf = gpd.read_file(dd if dd.exists() else p["outlines"]
                        ).set_index("building_id", drop=False)
    img = rasterio.open(p["imagery"])
    pc = PointCloudSource(max_cached_tiles=3)
    if not ids:
        ids = [int(x) for x in gdf["building_id"]]
    if a.limit:
        ids = ids[:a.limit]

    OUT.mkdir(parents=True, exist_ok=True)
    done = 0
    for bid in ids:
        if bid not in gdf.index:
            continue
        geom = gdf.loc[bid].geometry
        minx, miny, maxx, maxy = geom.bounds
        b = (minx - PAD_M, miny - PAD_M, maxx + PAD_M, maxy + PAD_M)
        win = rasterio.windows.from_bounds(*b, img.transform)
        try:
            rgb = np.moveaxis(img.read([1, 2, 3], window=win, boundless=True,
                                       fill_value=0), 0, -1).astype("uint8")
        except Exception:
            continue
        h, w = rgb.shape[:2]
        if h < 32 or w < 32:
            continue
        pts = top_surface(pc.points_in_bbox(minx - 1, miny - 1,
                                            maxx + 1, maxy + 1,
                                            building_only=True))

        def to_px(x, y):
            return ((x - b[0]) / (b[2] - b[0]) * w,
                    (1 - (y - b[1]) / (b[3] - b[1])) * h)

        def inv_px(px2, py2):
            return (b[0] + px2 / w * (b[2] - b[0]),
                    b[1] + (1 - py2 / h) * (b[3] - b[1]))

        try:
            f_sam, _ = sam_faces(predictor, rgb, geom, b, pts)
        except Exception:
            f_sam = []
        try:
            f_line, pr = line_faces(lm, device, rgb, geom, b, pts, bid)
        except Exception:
            f_line, pr = [], None
        if pr is None:
            ph, pw = (-h) % 16, (-w) % 16
            arr = np.pad(rgb, ((0, ph), (0, pw), (0, 0)))
            x2 = torch.from_numpy(arr).float().permute(2, 0, 1)[None] / 255.0
            with torch.no_grad():
                pr = torch.sigmoid(lm(x2.to(device)))[0].cpu().numpy()[:, :h, :w]
        P = pr.max(axis=0)
        sc_sam = score_candidate(f_sam, geom, P, to_px, pts, inv_px) \
            if f_sam else 0.0
        sc_line = score_candidate(f_line, geom, P, to_px, pts, inv_px) \
            if f_line else 0.0
        if not f_sam and not f_line:
            continue
        pick, faces, score = (("sam", f_sam, sc_sam)
                              if sc_sam >= sc_line
                              else ("line", f_line, sc_line))
        (OUT / f"{bid}.json").write_text(json.dumps({
            "source": pick, "score": round(score, 3),
            "score_sam": round(sc_sam, 3), "score_line": round(sc_line, 3),
            "faces": [[[round(v, 2) for v in xy]
                       for xy in f.exterior.coords] for f in faces]}))
        done += 1
        if done % 20 == 0:
            print(f"  {done}...")
    print(f"{done} buildings -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
