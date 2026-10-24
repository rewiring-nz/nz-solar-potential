"""A vision referee for roof readings: Claude looks at the roof and picks the drawing.

    python tools/vision_referee.py render <building_id> <region> [--cands DIR]   # the images, to look at
    python tools/vision_referee.py run   [--cands DIR] [--max-usd 5] [--model M]  # ask, cached
    python tools/vision_referee.py score [--cands DIR] [--oracle FILE]            # against Josh's markup

The pipeline draws several readings of each roof -- the LiDAR partition, a
precomputed photo reading, plain hip/gable forms -- and picks one by how
well its planes explain the returns. Scored against Josh's 131 drawn roofs
(25 Oct), the reading it picks has roof-line F 0.56 while the best of the
readings it already drew has 0.70: the drawings are often there, the pick
is wrong. This asks a vision model to pick instead, from what a person would
look at:

  * the aerial photo, with each candidate's faces drawn on it (moved by the
    roof's measured photo lean, so lines sit on the photo's roof);
  * a LiDAR relief image -- every 0.25 m cell coloured by the direction its
    surface faces (hue) and how steep it is (saturation), shaded -- where each
    roof face is one flat colour and every ridge, hip and valley is an edge.

Candidates come from a dump of the candidate readings (tools ... cand_dump,
~/ab/cands/<id>.json: {"cands": {name: [WKT polygons in NZTM]}}). Answers are
cached per roof, so a roof is never paid for twice, and every run stops at
--max-usd. The key is the project secret Claude-API (src/project_secrets.py),
in a workspace with its own monthly spend limit.
"""
import argparse
import base64
import io
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CANDS = Path.home() / "ab" / "cands"
CACHE = Path.home() / "ab" / "referee"
PX = 384
CELL_M = 0.25
MAX_CANDS = 4
# USD per million tokens, input / output (Sonnet-class); used only for the
# run's own budget stop, which errs high
PRICE = {"claude-sonnet-5-5": (3.0, 15.0), "claude-opus-5-5": (15.0, 75.0), "claude-haiku-5-5": (1.0, 5.0)}
LETTERS = "ABCDEFG"


def _load_cands(path):
    from shapely import wkt
    d = json.loads(Path(path).read_text())
    return d["id"], d["region"], {k: [wkt.loads(w) for w in v] for k, v in d["cands"].items() if v}


def _lines_of(polys):
    from shapely.ops import unary_union
    seams = []
    for i, p in enumerate(polys):
        for q in polys[i + 1:]:
            if p.distance(q) > 0.2:
                continue
            s = p.boundary.intersection(q.buffer(0.2))
            if not s.is_empty and s.length > 0.3:
                seams.append(s)
    return unary_union(seams) if seams else None


def _same(a, b):
    """Two readings draw the same roof (lines agree within 0.5 m both ways)."""
    la, lb = _lines_of(a), _lines_of(b)
    if la is None and lb is None:
        return len(a) == len(b)
    if la is None or lb is None:
        return False
    r = la.intersection(lb.buffer(0.5)).length / max(la.length, 1e-9)
    p = lb.intersection(la.buffer(0.5)).length / max(lb.length, 1e-9)
    return min(r, p) > 0.9


def pick_candidates(cands):
    """The final reading first, then the others that draw a different roof,
    at most MAX_CANDS."""
    order = ["final", "lidar_partition", "selected_reading"] + sorted(
        k for k in cands if k.startswith("form:") and k != "form:flat")
    out = []
    for k in order:
        if k in cands and len(cands[k]) and not any(_same(cands[k], cands[o]) for o in out):
            out.append(k)
        if len(out) >= MAX_CANDS:
            break
    return out


def _relief(pc, bounds, roof=None):
    """RGB relief of the LiDAR surface over bounds (minx, miny, maxx, maxy);
    outside `roof` (a polygon) it is dimmed, so trees and ground recede."""
    from scipy import ndimage
    x0, y0, x1, y1 = bounds
    pts = pc.points_in_bbox(x0, y0, x1, y1, building_only=False)
    w, h = int(math.ceil((x1 - x0) / CELL_M)), int(math.ceil((y1 - y0) / CELL_M))
    z = np.full((h, w), np.nan)
    if len(pts):
        c = ((pts[:, 0] - x0) / CELL_M).astype(int).clip(0, w - 1)
        r = ((y1 - pts[:, 1]) / CELL_M).astype(int).clip(0, h - 1)
        order = np.argsort(pts[:, 2])
        z[r[order], c[order]] = pts[order, 2]           # the highest return per cell wins
    if np.isnan(z).all():
        return np.zeros((h, w, 3), np.uint8)
    idx = ndimage.distance_transform_edt(np.isnan(z), return_distances=False, return_indices=True)
    z = z[tuple(idx)]
    z = ndimage.median_filter(z, size=5)
    gy, gx = np.gradient(z, CELL_M)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    aspect = (np.degrees(np.arctan2(-gx, gy)) + 360) % 360     # rows run south: +gy is downhill north
    shade = np.clip(0.55 + 0.45 * (-gx * 0.5 + gy * 0.5) / np.maximum(np.hypot(gx, gy), 1e-6) * np.clip(slope / 20, 0, 1), 0.25, 1)
    from matplotlib.colors import hsv_to_rgb
    sat = np.where(slope < 3, 0.0, np.minimum(1.0, slope / 30.0) * 0.85 + 0.15)
    rgb = hsv_to_rgb(np.dstack([aspect / 360.0, sat, shade]))
    if roof is not None:
        from rasterio.features import rasterize
        from affine import Affine
        inside = rasterize([roof], out_shape=(h, w), transform=Affine(CELL_M, 0, x0, 0, -CELL_M, y1), fill=0, default_value=1)
        rgb[inside == 0] *= 0.35
    return (rgb * 255).astype(np.uint8)


def render(bid, region, cands, names, pc=None):
    """[(label, PIL image)]: the reference pair, then one pair per candidate."""
    import rasterio
    from PIL import Image, ImageDraw
    from shapely.affinity import translate
    from shapely.ops import unary_union
    from src.region_build import area_paths
    from src.register_imagery import drawing_shifts
    if pc is None:
        from src.pointcloud_source import PointCloudSource
        pc = PointCloudSource()
    allg = unary_union([g for k in names for g in cands[k]])
    b = allg.bounds
    side = max(b[2] - b[0], b[3] - b[1]) + 6
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    bb = (cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2)
    paths = area_paths(region)
    shift = drawing_shifts(paths["dir"]).get(str(bid)) or [0, 0]
    dx, dy = float(shift[0]), float(shift[1])
    img = rasterio.open(paths["imagery"])
    pb = (bb[0] + dx, bb[1] + dy, bb[2] + dx, bb[3] + dy)
    win = rasterio.windows.from_bounds(*pb, img.transform)
    photo = np.moveaxis(img.read([1, 2, 3], window=win, boundless=True, fill_value=0), 0, -1)
    photo = Image.fromarray(photo.astype("uint8")).resize((PX, PX))
    relief = Image.fromarray(_relief(pc, bb, roof=allg.buffer(1.0))).resize((PX, PX))
    P = lambda x, y, ox=0.0, oy=0.0: ((x - bb[0] - ox) / side * PX, (1 - (y - bb[1] - oy) / side) * PX)

    def pair(polys, label):
        im = Image.new("RGB", (PX * 2 + 8, PX + 22), (20, 20, 20))
        a, r = photo.copy(), relief.copy()
        for base, (ox, oy) in ((a, (0, 0)), (r, (0, 0))):
            d = ImageDraw.Draw(base)
            for g in polys:
                for pg in getattr(g, "geoms", [g]):
                    if pg.geom_type != "Polygon":
                        continue
                    pts = [P(x, y) for x, y in pg.exterior.coords]
                    d.line(pts, fill=(255, 255, 255), width=3)
                    d.line(pts, fill=(255, 0, 180), width=1)
        im.paste(a, (0, 22)); im.paste(r, (PX + 8, 22))
        ImageDraw.Draw(im).text((6, 5), label, fill=(255, 230, 80))
        return im

    out = [("reference", pair([], f"Roof #{bid}: photo (left), LiDAR relief (right) -- no drawing"))]
    for k, n in zip(LETTERS, names):
        # the photo leans by (dx, dy): draw on it moved by the lean
        polys_photo = [translate(g, dx, dy) for g in cands[n]]
        im = pair([], "")
        a = photo.copy(); r = relief.copy()
        for base, polys, (ox, oy) in ((a, polys_photo, (dx, dy)), (r, cands[n], (0, 0))):
            d = ImageDraw.Draw(base)
            for g in polys:
                for pg in getattr(g, "geoms", [g]):
                    if pg.geom_type != "Polygon":
                        continue
                    pts = [P(x, y, ox, oy) for x, y in pg.exterior.coords]
                    d.line(pts, fill=(255, 255, 255), width=3)
                    d.line(pts, fill=(255, 0, 180), width=1)
        im.paste(a, (0, 22)); im.paste(r, (PX + 8, 22))
        ImageDraw.Draw(im).text((6, 5), f"Drawing {k}: {len(cands[n])} faces", fill=(255, 230, 80))
        out.append((k, im))
    return out


PROMPT = """You are checking roof drawings for a rooftop solar map of Queenstown, New Zealand.

The first image shows one building's roof from above: the aerial photo (left) and a LiDAR relief image (right). In the relief, colour is the direction each bit of roof faces (hue) and how steep it is (stronger colour = steeper); each flat roof plane is one even colour, and ridges, hips, valleys and steps are where the colour changes sharply. Grey is flat. North is up.

Each following image shows one candidate drawing of the roof's faces (white/magenta outlines), on the photo and on the relief. A good drawing has one outline per roof plane, with its edges on the real ridges, hips, valleys and steps -- matching the colour boundaries in the relief and the visible roof lines in the photo -- and no extra lines across a single plane.

Which drawing matches the real roof best? Judge mainly by the relief (it is exact about planes; the photo can lean or be shadowed) and use the photo to confirm. Think it through briefly (a few sentences at most), then end with one line of JSON:
{"choice": "<letter>", "confidence": <0 to 1>, "why": "<one short sentence>"}"""


def _b64(im):
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def ask(client, model, images):
    content = [{"type": "text", "text": PROMPT}]
    for label, im in images:
        content.append({"type": "text", "text": "Reference:" if label == "reference" else f"Drawing {label}:"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64(im)}})
    r = client.messages.create(model=model, max_tokens=700, messages=[{"role": "user", "content": content}])
    text = r.content[0].text.strip()
    try:
        ans = json.loads(text[text.rindex("{"):text.rindex("}") + 1])
        ans["reasoning"] = text[:text.rindex("{")].strip()[:1500]
    except Exception:
        ans = {"choice": None, "raw": text}
    return ans, r.usage.input_tokens, r.usage.output_tokens


def cmd_render(a):
    bid, region, cands = _load_cands(Path(a.cands) / f"{a.building_id}.json")
    names = pick_candidates(cands)
    ims = render(bid, region, cands, names)
    for label, im in ims:
        im.save(Path(a.out) / f"ref_{bid}_{label}.png")
    print(bid, region, "candidates:", list(zip(LETTERS, names)))


def cmd_run(a):
    import anthropic
    from src import project_secrets
    from src.pointcloud_source import PointCloudSource
    client = anthropic.Anthropic(api_key=project_secrets.anthropic_api_key())
    pin, pout = PRICE.get(a.model, (15.0, 75.0))
    CACHE.mkdir(parents=True, exist_ok=True)
    pc = PointCloudSource()
    spent = 0.0
    files = sorted(Path(a.cands).glob("*.json"))
    for f in files:
        bid, region, cands = _load_cands(f)
        out = CACHE / f"{a.model}_{bid}.json"
        if out.exists():
            continue
        names = pick_candidates(cands)
        if len(names) < 2:
            out.write_text(json.dumps({"id": bid, "names": names, "choice_name": names[0] if names else None,
                                       "skipped": "one candidate"}))
            continue
        if spent >= a.max_usd:
            print(f"budget reached (${spent:.2f}); stopping"); break
        try:
            ims = render(bid, region, cands, names, pc=pc)
            ans, tin, tout = ask(client, a.model, ims)
        except Exception as e:
            print(bid, "error", repr(e)[:200]); continue
        cost = tin / 1e6 * pin + tout / 1e6 * pout
        spent += cost
        ch = ans.get("choice")
        ch = str(ch).strip().upper()[:1] if ch else None
        name = names[LETTERS.index(ch)] if ch and ch in LETTERS[:len(names)] else None
        if name is None:
            print(bid, "unparsed answer:", str(ans)[:300])
        out.write_text(json.dumps({"id": bid, "region": region, "names": names, "answer": ans,
                                   "choice_name": name, "tokens": [tin, tout], "usd": round(cost, 4)}))
        print(f"{bid} {region}: {list(zip(LETTERS, names))} -> {ch} ({name}) conf {ans.get('confidence')}  ${cost:.3f}  total ${spent:.2f}", flush=True)
    print(f"RUN_DONE spent ${spent:.2f}")


def cmd_score(a):
    rows = {r["id"]: r for r in json.loads(Path(a.oracle).read_text())}
    picked, final, best, n = [], [], [], 0
    by_conf = []
    for f in sorted(CACHE.glob(f"{a.model}_*.json")):
        d = json.loads(f.read_text())
        r = rows.get(d["id"])
        if not r or not d.get("choice_name"):
            continue
        sc = r["scores"]
        if d["choice_name"] not in sc or "final" not in sc:
            continue
        n += 1
        picked.append(sc[d["choice_name"]]["f"]); final.append(sc["final"]["f"])
        best.append(max(sc[k]["f"] for k in d["names"] if k in sc))
        by_conf.append((float((d.get("answer") or {}).get("confidence") or 1.0), sc[d["choice_name"]]["f"], sc["final"]["f"]))
    if not n:
        print("nothing scored"); return
    m = lambda v: sum(v) / len(v)
    print(f"{n} roofs: line F  pipeline's pick {m(final):.3f} (>=0.7: {sum(x >= 0.7 for x in final)})  "
          f"referee's pick {m(picked):.3f} (>=0.7: {sum(x >= 0.7 for x in picked)})  "
          f"best shown {m(best):.3f} (>=0.7: {sum(x >= 0.7 for x in best)})")
    for t in (0.6, 0.8):
        sel = [(p if c >= t else fi) for c, p, fi in by_conf]
        print(f"  referee only when confident >= {t}: {m(sel):.3f} (>=0.7: {sum(x >= 0.7 for x in sel)})")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render"); r.add_argument("building_id"); r.add_argument("region")
    r.add_argument("--cands", default=str(CANDS)); r.add_argument("--out", default=str(Path.home() / "ab"))
    u = sub.add_parser("run"); u.add_argument("--cands", default=str(CANDS))
    u.add_argument("--max-usd", type=float, default=5.0); u.add_argument("--model", default="claude-sonnet-5-5")
    s = sub.add_parser("score"); s.add_argument("--cands", default=str(CANDS))
    s.add_argument("--oracle", default=str(Path.home() / "ab" / "oracle2.json")); s.add_argument("--model", default="claude-sonnet-5-5")
    a = ap.parse_args()
    {"render": cmd_render, "run": cmd_run, "score": cmd_score}[a.cmd](a)


if __name__ == "__main__":
    main()
