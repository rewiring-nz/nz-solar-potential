"""Does the selector pick the reading that agrees with Josh's faces?

Josh: "You should only conclude things based on my direct feedback or direct
measurement of my mark ups." So before any render: on every benchmark roof he
has drawn, build BOTH candidate face-sets, measure each against HIS faces, and
check whether the evidence scorer picks the better one. Selection accuracy
against his markup is the number that licenses this design.

AGREEMENT is continuous, not the deploy-gate's exact-match: mean over his
usable faces of the best IoU any candidate face achieves. A reading that gets
every face roughly right beats one that nails three and misses six.

    .venv-sam/bin/python tools/select_faces.py            # measure
    .venv-sam/bin/python tools/select_faces.py --render   # + winners page
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

PAD_M = 4.0


def agreement(cand, drawn):
    if not drawn:
        return None
    if not cand:
        return 0.0
    tot = 0.0
    for d in drawn:
        best = 0.0
        for c in cand:
            u = c.union(d).area
            if u > 0:
                best = max(best, c.intersection(d).area / u)
        tot += best
    return tot / len(drawn)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="*", type=int, default=None)
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--out", default="selected_faces.html")
    a = ap.parse_args()

    import numpy as np
    import torch
    import geopandas as gpd
    import rasterio
    import rasterio.windows
    from shapely.geometry import Polygon
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

    labels = json.loads((ROOT / "data/roof_labels.json").read_text())["buildings"]
    if a.ids:
        ids = a.ids
    else:
        bench = set((ROOT / "data/bench_ids.txt").read_text().split())
        ids = sorted(int(k) for k in bench
                     if labels.get(k, {}).get("complete")
                     and labels.get(k, {}).get("faces"))

    p = area_paths("pilot")
    gdf = gpd.read_file(p["dir"] / "building_outlines_dedup.geojson"
                        if (p["dir"] / "building_outlines_dedup.geojson").exists()
                        else p["outlines"]).set_index("building_id", drop=False)
    img = rasterio.open(p["imagery"])
    pc = PointCloudSource(max_cached_tiles=3)

    rows = []
    right = wrong = 0
    print(f"  {'roof':>10s} {'SAM':>6s} {'LINE':>6s} {'oracle':>7s} "
          f"{'scorer picks':>12s} {'correct':>8s}")
    for bid in ids:
        if bid not in gdf.index:
            continue
        geom = gdf.loc[bid].geometry
        minx, miny, maxx, maxy = geom.bounds
        b = (minx - PAD_M, miny - PAD_M, maxx + PAD_M, maxy + PAD_M)
        win = rasterio.windows.from_bounds(*b, img.transform)
        rgb = np.moveaxis(img.read([1, 2, 3], window=win, boundless=True,
                                   fill_value=0), 0, -1).astype("uint8")
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
        except Exception as e:
            print(f"  #{bid} SAM failed: {e}")
            f_sam = []
        try:
            f_line, pr = line_faces(lm, device, rgb, geom, b, pts, bid)
        except Exception as e:
            print(f"  #{bid} LINE failed: {e}")
            f_line, pr = [], None
        if pr is None:
            import torch as _t
            ph, pw = (-h) % 16, (-w) % 16
            arr = np.pad(rgb, ((0, ph), (0, pw), (0, 0)))
            x2 = _t.from_numpy(arr).float().permute(2, 0, 1)[None] / 255.0
            with _t.no_grad():
                pr = _t.sigmoid(lm(x2.to(device)))[0].cpu().numpy()[:, :h, :w]
        P = pr.max(axis=0)

        drawn = []
        for f in labels.get(str(bid), {}).get("faces") or []:
            if not f.get("usable", True):
                continue
            try:
                poly = Polygon([(q[0], q[1]) for q in f["ring"]])
                if poly.is_valid and poly.area >= 1.0:
                    drawn.append(poly)
            except Exception:
                pass

        if not drawn:
            continue
        ag_sam = agreement(f_sam, drawn)
        ag_line = agreement(f_line, drawn)
        sc_sam = score_candidate(f_sam, geom, P, to_px, pts, inv_px)
        sc_line = score_candidate(f_line, geom, P, to_px, pts, inv_px)
        pick = "SAM" if sc_sam >= sc_line else "LINE"
        oracle = "SAM" if (ag_sam or 0) >= (ag_line or 0) else "LINE"
        ok = pick == oracle or abs((ag_sam or 0) - (ag_line or 0)) < 0.03
        right += ok
        wrong += not ok
        rows.append({"id": bid, "ag_sam": ag_sam, "ag_line": ag_line,
                     "pick": pick, "n_sam": len(f_sam), "n_line": len(f_line),
                     "faces": f_sam if pick == "SAM" else f_line})
        print(f"  #{bid:<9d} {ag_sam:6.2f} {ag_line:6.2f} {oracle:>7s} "
              f"{pick:>12s} {'yes' if ok else 'NO':>8s}"
              f"   sc {sc_sam:.2f}/{sc_line:.2f}  n {len(f_sam)}/{len(f_line)}")

    if a.render:
        import base64, io
        from PIL import Image, ImageDraw
        FILLS = [(31, 255, 122), (53, 182, 255), (255, 179, 60),
                 (255, 99, 195), (170, 120, 255), (120, 235, 235),
                 (255, 235, 90), (140, 255, 140), (255, 130, 90),
                 (90, 160, 255)]
        cells = []
        for r in rows:
            bid = r["id"]
            geom = gdf.loc[bid].geometry
            minx, miny, maxx, maxy = geom.bounds
            b = (minx - PAD_M, miny - PAD_M, maxx + PAD_M, maxy + PAD_M)
            win = rasterio.windows.from_bounds(*b, img.transform)
            rgb = np.moveaxis(img.read([1, 2, 3], window=win, boundless=True,
                                       fill_value=0), 0, -1).astype("uint8")
            h, w = rgb.shape[:2]

            def draw(faces_w):
                im = Image.fromarray(rgb).convert("RGB").resize(
                    (w * 3, h * 3), Image.LANCZOS)
                lay = Image.new("RGBA", im.size, (0, 0, 0, 0))
                d = ImageDraw.Draw(lay)

                def px(x, y):
                    return ((x - b[0]) / (b[2] - b[0]) * w * 3,
                            (1 - (y - b[1]) / (b[3] - b[1])) * h * 3)
                for i, f in enumerate(faces_w):
                    col = FILLS[i % len(FILLS)]
                    pts2 = [px(x, y) for x, y in f.exterior.coords]
                    if len(pts2) >= 3:
                        d.polygon(pts2, fill=col + (70,),
                                  outline=col + (255,), width=3)
                im = Image.alpha_composite(im.convert("RGBA"), lay).convert("RGB")
                d2 = ImageDraw.Draw(im)
                d2.line([px(x, y) for x, y in geom.exterior.coords],
                        fill=(255, 200, 80), width=2)
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=86)
                return base64.b64encode(buf.getvalue()).decode()

            drawn2 = []
            for f in labels.get(str(bid), {}).get("faces") or []:
                try:
                    poly = Polygon([(q[0], q[1]) for q in f["ring"]])
                    if poly.is_valid:
                        drawn2.append(poly)
                except Exception:
                    pass
            panels = [(f'SELECTED ({r["pick"]})', draw(r["faces"]))]
            if drawn2:
                panels.append(("JOSH", draw(drawn2)))
            imgs = "".join(
                f'<figure><img src="data:image/jpeg;base64,{j}">'
                f"<figcaption>{c}</figcaption></figure>" for c, j in panels)
            cells.append(f"<section><h2>#{bid}</h2>"
                         f"<div class=row>{imgs}</div></section>")
        html = ("<title>Selected Roof Faces</title><style>"
                "body{background:#12161a;color:#e8edf2;font:15px/1.5 "
                "system-ui;margin:0;padding:20px}"
                "h2{font-size:15px;margin:18px 0 6px}"
                ".row{display:flex;gap:10px;flex-wrap:wrap}figure{margin:0}"
                "img{max-width:430px;height:auto;display:block;"
                "border-radius:4px}figcaption{color:#8b97a3;font-size:12px;"
                "letter-spacing:.08em;margin-top:3px}</style>"
                "<h1 style='font-size:20px;margin:0 0 14px'>Per-roof winner, "
                "as the selector would ship it</h1>" + "".join(cells))
        dest = ROOT / "data" / "preview" / a.out
        dest.write_text(html)
        print(f"\n  wrote {dest}")

    n = right + wrong
    if n:
        m_s = np.mean([r["ag_sam"] for r in rows])
        m_l = np.mean([r["ag_line"] for r in rows])
        m_p = np.mean([max(r["ag_sam"], r["ag_line"]) if r["pick"] ==
                       ("SAM" if r["ag_sam"] >= r["ag_line"] else "LINE")
                       else min(r["ag_sam"], r["ag_line"]) for r in rows])
        picked = np.mean([r["ag_sam"] if r["pick"] == "SAM" else r["ag_line"]
                          for r in rows])
        oracle_m = np.mean([max(r["ag_sam"], r["ag_line"]) for r in rows])
        print(f"\n  agreement with Josh's faces (mean of {n} roofs):")
        print(f"    SAM always   {m_s:.3f}")
        print(f"    LINE always  {m_l:.3f}")
        print(f"    scorer-picked {picked:.3f}")
        print(f"    oracle       {oracle_m:.3f}")
        print(f"  selection correct (or within 0.03): {right}/{n}")
        # STRUCTURAL RULES, simulated on the same data: the line reading is
        # trustworthy exactly when detection was rich enough to structure the
        # roof. Try "LINE if it made >= k faces, else SAM" for several k.
        for k in (2, 3, 4, 5):
            pk = [r["ag_line"] if r["n_line"] >= k else r["ag_sam"]
                  for r in rows]
            print(f"    rule LINE-if->={k}-faces: {np.mean(pk):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
