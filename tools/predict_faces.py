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

        # NEAR-FLAT ROOFS ARE NOT THE SELECTOR'S TO SHIP. Josh, on the first
        # region render: the flat, obstruction-heavy commercials came out as
        # arbitrary webs -- the line net polygonises plant edges, and the
        # scorer cannot tell, because a flat plane fits every partition of
        # itself. The proven LiDAR path already handles these acceptably on
        # the live map, so the selector writes nothing and the build falls
        # through to it.
        # ...and flatness is judged PER PART: #5371128 is a flat block joined
        # to a gabled hall, and a whole-building fit read the pair as flat.
        # Defer only when every reflex-split part reads flat -- 118 of 152
        # deferred under the whole-building test, which was the test failing,
        # not the roofs.
        if pts is not None and len(pts) > 80:
            import numpy as _np
            from src.roof_partition import (_fit_plane_robust, _slope_aspect,
                                            _points_in)
            from src.face_candidates import rect_parts
            all_flat = True
            for part in rect_parts(geom):
                sub = _points_in(part, pts)
                if len(sub) < 40:
                    continue
                z = sub[:, 2]
                spread = float(_np.percentile(z, 95) - _np.percentile(z, 5))
                pl0 = _fit_plane_robust(sub)
                sl0 = _slope_aspect(pl0)[0] if pl0 is not None else 99.0
                if not (sl0 < 4.5 and spread < 1.2):
                    all_flat = False
                    break
            if all_flat:
                continue

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
        # Josh: "If you are not detecting clear lines you should not just
        # randomly draw them." A line-winner must stand on clear lines --
        # length-weighted activation along its interior edges >= 0.5.
        # Calibrated on his verdicts: the two webs he flagged sit at 0.43 and
        # 0.47, Anderson at 0.87. A roof that fails falls to SAM if SAM earned
        # a score, else to no file and the old pipeline -- deferring a decent
        # roof costs little, shipping a web costs a flag.
        line_ok = bool(f_line)
        if line_ok:
            from src.line_extract import _line_mean as _lm2
            import shapely.geometry as _sg
            rim2 = geom.exterior.buffer(0.5)
            sup2 = len2 = 0.0
            for f in f_line:
                cs = list(f.exterior.coords)
                for aa, bb in zip(cs, cs[1:]):
                    seg2 = _sg.LineString([aa, bb])
                    Li = seg2.difference(rim2).length
                    if Li < 0.5:
                        continue
                    pa2 = np.array(to_px(*aa))
                    pb2 = np.array(to_px(*bb))
                    sup2 += Li * _lm2(P, pa2, pb2)
                    len2 += Li
            line_ok = len2 > 2 and (sup2 / len2) >= 0.5
        if line_ok and sc_line >= sc_sam:
            pick, faces, score = "line", f_line, sc_line
        elif f_sam and sc_sam >= 0.30:
            pick, faces, score = "sam", f_sam, sc_sam
        elif line_ok:
            pick, faces, score = "line", f_line, sc_line
        elif f_sam:
            # a pitched building must not fall back to the old path's webs
            # (#5372567: both candidates dropped, the old pipeline drew "lots
            # of incorrect lines"). SAM's honest partial ships if its faces
            # are good QUALITY even at low coverage -- score is quality x
            # coverage, so divide coverage back out.
            cov = min(1.0, sum(f.area for f in f_sam) / max(geom.area, 1e-9))
            if cov > 0.15 and sc_sam / max(cov, 1e-9) >= 0.55:
                pick, faces, score = "sam", f_sam, sc_sam
            else:
                continue
        else:
            continue
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
