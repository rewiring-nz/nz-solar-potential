"""What a foundation segmentation model sees on each roof, judged by eye.

Josh, after a day of watching a small custom detector fumble clear roofs:
"The images have all the pixel data to clearly show the shape of the roof. It
makes no sense you can't detect that when image recognition models can detect
things in far more detail. You should be able to clearly see the shape of the
roof, obstructions, what are just shadows from surrounding trees etc."

He is right, and this is the test of it. SAM (ViT-B, the checkpoint already in
data/) segments each roof crop with no training on our data at all. Every mask
that sits on the building becomes a candidate FACE -- no line extraction, no
archetypes, no fusion heuristics. The panel pairs it with the faces Josh drew,
which are the standard.

Runs under .venv-sam (torch + segment_anything live there):
    .venv-sam/bin/python tools/faces_preview.py --ids ... --out faces_check.html
"""

import argparse
import base64
import io
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PAD_M = 4.0
SCALE = 3
FILLS = [(31, 255, 122), (53, 182, 255), (255, 179, 60), (255, 99, 195),
         (170, 120, 255), (120, 235, 235), (255, 235, 90), (140, 255, 140),
         (255, 130, 90), (90, 160, 255)]


def render(rgb, faces, bounds, outline):
    """Translucent face fills + boundaries over the crop."""
    import numpy as np
    from PIL import Image, ImageDraw
    h, w = rgb.shape[:2]
    im = Image.fromarray(rgb.astype("uint8")).convert("RGB").resize(
        (w * SCALE, h * SCALE), Image.LANCZOS)
    lay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    minx, miny, maxx, maxy = bounds

    def px(x, y):
        return ((x - minx) / (maxx - minx) * w * SCALE,
                (1 - (y - miny) / (maxy - miny)) * h * SCALE)

    for i, ring in enumerate(faces):
        col = FILLS[i % len(FILLS)]
        pts = [px(x, y) for x, y in ring]
        if len(pts) >= 3:
            d.polygon(pts, fill=col + (70,), outline=col + (255,), width=3)
    im = Image.alpha_composite(im.convert("RGBA"), lay).convert("RGB")
    d2 = ImageDraw.Draw(im)
    if outline is not None:
        d2.line([px(x, y) for x, y in outline.exterior.coords],
                fill=(255, 200, 80), width=2)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="*", type=int, required=True)
    ap.add_argument("--checkpoint", default="data/sam_vit_b.pth")
    ap.add_argument("--out", default="faces_check.html")
    ap.add_argument("--points", type=int, default=24)
    a = ap.parse_args()

    import numpy as np
    import torch
    import geopandas as gpd
    import rasterio
    import rasterio.windows
    import rasterio.features
    from shapely.geometry import shape as shp_shape, Polygon
    from segment_anything import (sam_model_registry, SamAutomaticMaskGenerator,
                                  SamPredictor)
    from src.region_build import area_paths, all_areas

    # SAM's automatic generator hands float64 point grids to torch, which MPS
    # refuses; cast at the seam rather than falling back to CPU
    from segment_anything.utils.transforms import ResizeLongestSide as _RLS
    _oac = _RLS.apply_coords
    _RLS.apply_coords = (lambda self, c, sz:
                         _oac(self, c, sz).astype(np.float32))
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    sam = sam_model_registry["vit_b"](checkpoint=str(ROOT / a.checkpoint))
    sam.to(device)
    predictor = SamPredictor(sam)

    labels = {}
    lp = ROOT / "data" / "roof_labels.json"
    if lp.exists():
        labels = json.loads(lp.read_text()).get("buildings", {})

    rows = []
    ctxs = {}
    for bid in a.ids:
        placed = False
        for name in ["pilot"] + [x for x in all_areas() if x != "pilot"]:
            if name not in ctxs:
                p = area_paths(name)
                if not (p["outlines"].exists() and p["imagery"].exists()):
                    ctxs[name] = None
                    continue
                dd = p["dir"] / "building_outlines_dedup.geojson"
                ctxs[name] = {
                    "gdf": gpd.read_file(dd if dd.exists() else p["outlines"]
                                         ).set_index("building_id", drop=False),
                    "img": rasterio.open(p["imagery"])}
            ctx = ctxs[name]
            if ctx is None or bid not in ctx["gdf"].index:
                continue
            geom = ctx["gdf"].loc[bid].geometry
            minx, miny, maxx, maxy = geom.bounds
            b = (minx - PAD_M, miny - PAD_M, maxx + PAD_M, maxy + PAD_M)
            win = rasterio.windows.from_bounds(*b, ctx["img"].transform)
            rgb = np.moveaxis(ctx["img"].read([1, 2, 3], window=win,
                                              boundless=True, fill_value=0),
                              0, -1).astype("uint8")
            h, w = rgb.shape[:2]
            if h < 32 or w < 32:
                break

            # COVERAGE-COMPLETION PROMPTING. Josh, on the automatic grid:
            # "A lot of faces are being missed, but it does seem better at
            # detecting where they are and their shape than the line method".
            # SAM answers where it is asked; the grid does not ask everywhere.
            # So ask deliberately: segment, subtract what came back, and ask
            # again at the biggest patch of roof still unaccounted for, until
            # the footprint is covered. Recall stops being luck.
            predictor.set_image(rgb)

            def to_world(ring_px):
                return [(b[0] + x / w * (b[2] - b[0]),
                         b[1] + (1 - y / h) * (b[3] - b[1]))
                        for x, y in ring_px]

            def px_of(pt):
                return np.array([[(pt.x - b[0]) / (b[2] - b[0]) * w,
                                  (1 - (pt.y - b[1]) / (b[3] - b[1])) * h]],
                                dtype=np.float32)

            def mask_to_poly(mask):
                best = None
                for geo, val in rasterio.features.shapes(
                        mask.astype("uint8")):
                    if val != 1:
                        continue
                    poly = Polygon(to_world(geo["coordinates"][0]))
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                    if poly.is_empty:
                        continue
                    if best is None or poly.area > best.area:
                        best = poly
                return best

            from shapely.ops import unary_union as _uu
            faces = []
            uncovered = geom
            for _ in range(14):
                if uncovered.is_empty or uncovered.area < 3.0:
                    break
                probe = max(getattr(uncovered, "geoms", [uncovered]),
                            key=lambda g2: g2.area)
                if probe.area < 3.0:
                    break
                seed = probe.representative_point()
                mk, sc, _ = predictor.predict(
                    point_coords=px_of(seed),
                    point_labels=np.array([1]), multimask_output=True)
                pick = None
                for i2 in np.argsort(-sc):
                    poly = mask_to_poly(mk[i2])
                    if poly is None:
                        continue
                    clipped = poly.intersection(geom)
                    if clipped.is_empty or clipped.area < 2.0:
                        continue
                    if clipped.area > 0.85 * geom.area:
                        continue      # the whole building, not a face
                    pick = clipped
                    break
                if pick is None:
                    uncovered = uncovered.difference(seed.buffer(0.8))
                    continue
                fresh = pick.difference(_uu(faces)) if faces else pick
                for g2 in getattr(fresh, "geoms", [fresh]):
                    if g2.geom_type == "Polygon" and g2.area >= 2.0:
                        faces.append(g2.simplify(0.15))
                uncovered = uncovered.difference(pick.buffer(0.05))

            face_rings = [list(f.exterior.coords) for f in faces]

            drawn = []
            lab = labels.get(str(bid)) or {}
            for f in lab.get("faces") or []:
                try:
                    drawn.append([(q[0], q[1]) for q in f["ring"]])
                except Exception:
                    pass

            panels = [("SAM FACES", render(rgb, face_rings, b, geom))]
            if drawn:
                panels.append(("JOSH FACES", render(rgb, drawn, b, geom)))
            rows.append({"id": bid, "addr": lab.get("address", ""),
                         "n": len(face_rings), "panels": panels})
            print(f"  #{bid}: {len(face_rings)} faces, "
                  f"{100 * sum(f.area for f in faces) / geom.area:.0f}% covered")
            placed = True
            break
        if not placed:
            print(f"  skip #{bid}")

    cells = []
    for r in rows:
        imgs = "".join(
            f'<figure><img src="data:image/jpeg;base64,{jpg}">'
            f"<figcaption>{cap}</figcaption></figure>"
            for cap, jpg in r["panels"])
        cells.append(f'<section><h2>#{r["id"]}'
                     + (f' &middot; {r["addr"]}' if r["addr"] else "")
                     + f"</h2><div class=row>{imgs}</div></section>")

    html = f"""<title>Roof Faces From SAM</title>
<style>
 body{{background:#12161a;color:#e8edf2;font:15px/1.5 system-ui;margin:0;padding:20px}}
 h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#8b97a3;margin-bottom:18px}}
 h2{{font-size:15px;margin:18px 0 6px}}
 .row{{display:flex;gap:10px;flex-wrap:wrap}}
 figure{{margin:0}} img{{max-width:430px;height:auto;display:block;border-radius:4px}}
 figcaption{{color:#8b97a3;font-size:12px;letter-spacing:.08em;margin-top:3px}}
</style>
<h1>What a foundation segmentation model sees</h1>
<div class=sub>SAM, zero training on our data: every coloured region is a face
it found on the building. JOSH FACES is your markup, the standard.</div>
{"".join(cells)}"""
    dest = ROOT / "data" / "preview" / a.out
    dest.write_text(html)
    print(f"wrote {dest}  ({dest.stat().st_size/1e6:.1f} MB, {len(rows)} roofs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
