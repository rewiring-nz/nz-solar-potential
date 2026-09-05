"""Show WHERE the detector puts its lines, so Josh can judge placement by eye.

Josh: "Visually present me your work as a way to check it is right, don't
measure arbitrary things like number of panels. I can visually check if it is
right or not."

So this renders no metrics at all. Per roof, side by side on the same crop:

  DEPLOYED   the v1 model through the old component-axis extraction -- what the
             live build currently sees
  REBUILT    the chosen model through the skeleton-traced, junction-split,
             peak-snapped extraction (src/line_extract)
  JOSH       the lines he drew, where the roof is labelled -- the standard the
             other two panels are aiming at

Line colours match his markup tool exactly (ridge green, valley blue, cliff
red), so a wrong KIND is as visible as a wrong position.

Usage:
    python tools/lines_preview.py --ids 4734914 4735341 --out lines_check.html
    python tools/lines_preview.py --flagged --out lines_check.html
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
sys.path.insert(0, str(ROOT / "tools"))

PAD_M = 4.0
SCALE = 3            # crops are ~200 px; lines need room to be judged
COL = {"ridge": (31, 255, 122), "valley": (53, 182, 255), "cliff": (255, 59, 48)}


def render(rgb, lines, bounds, outline):
    """One panel: the crop with lines over it, world coords -> pixels."""
    import numpy as np
    from PIL import Image, ImageDraw
    h, w = rgb.shape[:2]
    im = Image.fromarray(rgb.astype("uint8")).resize((w * SCALE, h * SCALE),
                                                     Image.LANCZOS)
    d = ImageDraw.Draw(im)
    minx, miny, maxx, maxy = bounds

    def px(x, y):
        return ((x - minx) / (maxx - minx) * w * SCALE,
                (1 - (y - miny) / (maxy - miny)) * h * SCALE)

    if outline is not None:
        d.line([px(x, y) for x, y in outline.exterior.coords],
               fill=(255, 200, 80), width=2)
    for x1, y1, x2, y2, kind in lines:
        d.line([px(x1, y1), px(x2, y2)], fill=COL.get(kind, (255, 255, 255)),
               width=3)
        for x, y in ((x1, y1), (x2, y2)):
            cx, cy = px(x, y)
            d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3],
                      fill=COL.get(kind, (255, 255, 255)))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="*", type=int, default=None)
    ap.add_argument("--flagged", action="store_true",
                    help="the roofs Josh has flagged (data/flagged_ids.txt)")
    ap.add_argument("--model-old", default="data/models/roof_lines_v1.pt")
    ap.add_argument("--model-new", default="data/models/roof_lines_v3.pt")
    ap.add_argument("--out", default="lines_check.html")
    a = ap.parse_args()

    import numpy as np
    import torch
    import geopandas as gpd
    import rasterio
    import rasterio.windows
    from src.region_build import area_paths, all_areas
    import train_line_model as T
    from predict_roof_lines import segments_from_mask
    from src.line_extract import extract, clip_to

    ids = a.ids
    if a.flagged and not ids:
        ids = [int(x) for x in
               (ROOT / "data" / "flagged_ids.txt").read_text().split()]
        ids = sorted(set(ids))
    if not ids:
        print("give --ids or --flagged")
        return 2

    def load(path):
        ck = torch.load(path, map_location="cpu", weights_only=False)
        m = T.build_unet(ck.get("pretrained", False))
        m.load_state_dict(ck["state_dict"])
        m.eval()
        return m

    m_old = load(ROOT / a.model_old)
    m_new = load(ROOT / a.model_new)

    labels = {}
    lp = ROOT / "data" / "roof_labels.json"
    if lp.exists():
        labels = json.loads(lp.read_text()).get("buildings", {})

    rows = []
    ctxs = {}
    for bid in ids:
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
                              0, -1)
            h, w = rgb.shape[:2]
            if h < 32 or w < 32:
                break
            arr = np.pad(rgb, ((0, (-h) % 16), (0, (-w) % 16), (0, 0)))
            x = torch.from_numpy(arr).float().permute(2, 0, 1)[None] / 255.0

            def tw(px, py):
                return (b[0] + px / w * (b[2] - b[0]),
                        b[1] + (1 - py / h) * (b[3] - b[1]))

            with torch.no_grad():
                pr_old = torch.sigmoid(m_old(x))[0].numpy()[:, :h, :w]
                pr_new = torch.sigmoid(m_new(x))[0].numpy()[:, :h, :w]

            old_lines = []
            for k in range(3):
                for seg, _ in segments_from_mask(pr_old[k], tw):
                    old_lines.append(seg + [["ridge", "valley", "cliff"][k]])
            new_lines = [r["seg"] + [r["kind"]]
                         for r in clip_to(extract(pr_new, tw), geom)]

            drawn = []
            lab = labels.get(str(bid)) or {}
            for l in lab.get("lines") or []:
                if l.get("a") and l.get("b"):
                    drawn.append([l["a"][0], l["a"][1], l["b"][0], l["b"][1],
                                  l.get("kind", "ridge")])

            panels = [("DEPLOYED", render(rgb, old_lines, b, geom)),
                      ("REBUILT", render(rgb, new_lines, b, geom))]
            if drawn:
                panels.append(("JOSH", render(rgb, drawn, b, geom)))
            rows.append({"id": bid,
                         "addr": lab.get("address", ""),
                         "panels": panels})
            placed = True
            break
        if not placed:
            print(f"  skip #{bid}: no imagery here")

    cells = []
    for r in rows:
        imgs = "".join(
            f'<figure><img src="data:image/jpeg;base64,{jpg}">'
            f"<figcaption>{cap}</figcaption></figure>"
            for cap, jpg in r["panels"])
        cells.append(f'<section><h2>#{r["id"]}'
                     + (f' &middot; {r["addr"]}' if r["addr"] else "")
                     + f"</h2><div class=row>{imgs}</div></section>")

    html = f"""<title>Roof Line Placement Check</title>
<style>
 body{{background:#12161a;color:#e8edf2;font:15px/1.5 system-ui;margin:0;padding:20px}}
 h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#8b97a3;margin-bottom:18px}}
 h2{{font-size:15px;margin:18px 0 6px}}
 .row{{display:flex;gap:10px;flex-wrap:wrap}}
 figure{{margin:0}} img{{max-width:420px;height:auto;display:block;border-radius:4px}}
 figcaption{{color:#8b97a3;font-size:12px;letter-spacing:.08em;margin-top:3px}}
 .leg span{{display:inline-block;margin-right:14px}}
 .leg i{{display:inline-block;width:18px;height:4px;vertical-align:middle;margin-right:5px}}
</style>
<h1>Where the detector puts its lines</h1>
<div class=sub>DEPLOYED is what the live build sees. REBUILT is the retrained
model through the new extraction. JOSH is your drawing, where it exists.</div>
<div class="sub leg">
 <span><i style="background:#1fff7a"></i>ridge</span>
 <span><i style="background:#35b6ff"></i>valley</span>
 <span><i style="background:#ff3b30"></i>cliff</span>
 <span><i style="background:#ffc850"></i>outline</span></div>
{"".join(cells)}"""
    dest = ROOT / "data" / "preview" / a.out
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html)
    print(f"wrote {dest}  ({dest.stat().st_size/1e6:.1f} MB, {len(rows)} roofs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
