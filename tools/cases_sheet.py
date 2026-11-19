#!/usr/bin/env python3
"""Every reported roof (data/roof_cases.json) as the map draws it, version
beside version.

THE RULE. Each build that is shown to the owner gets a snapshot of every
reported roof: its faces, obstructions and panels and the shift the map
moves it by. A sheet sets two snapshots side by side over the map's own
photo, so each release shows what every reported roof has become. A roof
is never dropped from the sheets, fixed or not.

THE REASON. Reported roofs lived in chat screenshots; a fix was claimed
from a different render, or never reached the map, and nobody looked at
the same roof again (tools/cases.py header). tools/render_built.py draws
the survey photo without the map's shift, which is not what the owner saw.

  snap  <label> [--layouts <dir>]   write data/case_snapshots/<label>.json
        from data/regions (the shift emit uses, register_imagery.drawing_shifts)
        or from a kept copy <dir>/<region>/{panel_layouts.geojson,image_shift.json}
        (the shift on file only: a roof led by a photo reading in that
        build may be drawn up to its reading's slide from where the map had it)
  sheet <out dir> <before label> <now label>   <id>.jpg a roof (before | now) and index.html;
        the same label twice draws that one version alone

Solid blue panels are what the map shows by default: the first
HOME_CAP_PANELS in fill order on a roof under HOME_CAP_ROOF_M2 of faces,
every panel on a larger one (building type is not in the layout, so a small
business is drawn as a home). Outlined panels are the rest the slider can reach.
Held-back roofs (data/roof_set.json holdout) are skipped.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SNAPS = ROOT / "data" / "case_snapshots"
REGIONS = ROOT / "data" / "regions"
HOME_CAP_PANELS = 28        # src/emit_region.py HOME_CAP_KW at 500 W panels
HOME_CAP_ROOF_M2 = 350      # src/emit_region.py
PAD_M = 6.0
IMAGE_DPI = 150             # 3600 x 1890 px a roof
PHOTO_Z = 21                # LINZ Basemaps zoom: about 0.05 m a pixel on the ground here
PHOTO_RES_M = 0.05


def _cases():
    hold = {int(h["id"]) for h in json.loads((ROOT / "data/roof_set.json").read_text())["holdout"]}
    return [c for c in json.loads((ROOT / "data/roof_cases.json").read_text())["cases"]
            if int(c["id"]) not in hold]


def snap(label, layouts=None):
    from src.register_imagery import drawing_shifts
    base = Path(layouts).expanduser() if layouts else REGIONS
    cases = _cases()
    want = {int(c["id"]) for c in cases}
    found = {}
    # the case's own region first, then any other region that holds it
    order = sorted({c.get("region") for c in cases if c.get("region")}) + \
        sorted(p.parent.name for p in base.glob("*/panel_layouts.geojson"))
    seen = set()
    for reg in order:
        f = base / reg / "panel_layouts.geojson"
        if reg in seen or not f.exists() or not (want - set(found)):
            continue
        seen.add(reg)
        by = {}
        for ft in json.loads(f.read_text())["features"]:
            b = ft["properties"].get("building_id")
            if b in want and b not in found:
                by.setdefault(int(b), []).append(ft)
        if not by:
            continue
        if layouts:
            sf = base / reg / "image_shift.json"
            sh = json.loads(sf.read_text()) if sf.exists() else {}
        else:
            sh = drawing_shifts(REGIONS / reg)
        for b, feats in by.items():
            found[b] = dict(region=reg, shift=[float(v) for v in (sh.get(str(b)) or sh.get(b) or [0, 0, 0])[:2]],
                            features=feats)
    SNAPS.mkdir(parents=True, exist_ok=True)
    (SNAPS / f"{label}.json").write_text(json.dumps(
        dict(label=label, shift_source="image_shift.json" if layouts else "drawing_shifts", buildings=found)))
    print(label, ":", len(found), "of", len(want), "reported roofs have a layout; missing",
          sorted(want - set(found)))


def _photo(bounds, cache):
    """The map's photo over NZTM bounds at PHOTO_RES_M, from LINZ Basemaps
    tiles at PHOTO_Z (cached under data/case_snapshots/tiles). The build's
    reader (register_imagery._Tiles) is fixed at zoom 20 and 0.1 m, which is
    coarser than the map shows a roof at."""
    import math
    import time
    import urllib.request
    import numpy as np
    import pyproj
    from PIL import Image
    from rasterio.transform import from_bounds, from_origin
    from rasterio.warp import reproject, Resampling
    import config
    x0, y0, x1, y1 = bounds
    to84 = pyproj.Transformer.from_crs(2193, 4326, always_xy=True).transform
    n = 2 ** PHOTO_Z
    txy = []
    for x, y in ((x0, y0), (x0, y1), (x1, y0), (x1, y1)):      # all four corners: NZTM is turned against the tile grid
        lon, lat = to84(x, y)
        txy.append(((lon + 180) / 360 * n,
                    (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n))
    xs = range(int(min(t[0] for t in txy)), int(max(t[0] for t in txy)) + 1)
    ys = range(int(min(t[1] for t in txy)), int(max(t[1] for t in txy)) + 1)
    cache.mkdir(parents=True, exist_ok=True)

    def tile(tx, ty):
        f = cache / f"{PHOTO_Z}_{tx}_{ty}.webp"
        if not f.exists():
            url = (f"https://basemaps.linz.govt.nz/v1/tiles/{config.LINZ_BASEMAPS_LAYER}/WebMercatorQuad/"
                   f"{PHOTO_Z}/{tx}/{ty}.webp?api={config.LINZ_BASEMAPS_KEY}")
            for attempt in range(3):
                try:
                    f.write_bytes(urllib.request.urlopen(urllib.request.Request(
                        url, headers={"User-Agent": "rewiring-solar-map"}), timeout=30).read())
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(2 * (attempt + 1))
        return np.array(Image.open(f).convert("RGB"))
    a = np.concatenate([np.concatenate([tile(tx, ty) for tx in xs], axis=1) for ty in ys], axis=0).transpose(2, 0, 1)
    R = 6378137.0
    mx = lambda t: t / n * 2 * math.pi * R - math.pi * R
    my = lambda t: math.pi * R - t / n * 2 * math.pi * R
    src_t = from_bounds(mx(xs[0]), my(ys[-1] + 1), mx(xs[-1] + 1), my(ys[0]), a.shape[2], a.shape[1])
    h, w = int(round((y1 - y0) / PHOTO_RES_M)), int(round((x1 - x0) / PHOTO_RES_M))
    out = np.zeros((3, h, w), dtype=np.uint8)
    dst_t = from_origin(x0, y1, PHOTO_RES_M, PHOTO_RES_M)
    for k in range(3):
        reproject(a[k], out[k], src_transform=src_t, src_crs="EPSG:3857",
                  dst_transform=dst_t, dst_crs="EPSG:2193", resampling=Resampling.bilinear)
    return np.moveaxis(out, 0, -1)


def _draw(ax, entry, bounds, tiles, T):
    import numpy as np
    from shapely.affinity import translate
    from shapely.geometry import shape
    from shapely.ops import transform
    x0, y0, x1, y1 = bounds
    img = _photo(bounds, SNAPS / "tiles")
    ax.imshow(img, extent=(x0, x1, y0, y1))
    n = 0
    feats = [(f["properties"], transform(T, shape(f["geometry"]))) for f in (entry or {}).get("features", [])]
    cap = HOME_CAP_PANELS if sum(g.area for q, g in feats if q["kind"] == "facet") < HOME_CAP_ROOF_M2 else 10 ** 9
    for q, g in feats:
        g = translate(g, *entry["shift"])
        for p in getattr(g, "geoms", [g]):
            x, y = p.exterior.xy
            if q["kind"] == "facet":
                ax.plot(x, y, color="red", lw=1.3)
            elif q["kind"] == "obstruction":
                ax.fill(x, y, color="orange", alpha=.55)
            elif q["kind"] == "panel":
                n += 1
                if (q.get("fill_order") or 0) <= cap:
                    ax.fill(x, y, color="#1f4fff", alpha=.5, lw=.8, ec="white")
                else:
                    ax.plot(x, y, color="#7fd4ff", lw=1.0)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_axis_off()
    return n


def sheet(out_dir, before, now):
    """One image per reported roof (<id>.jpg: before left, now right, at the
    map tiles' own resolution or finer) and index.html listing them with
    what was reported. A sheet of several roofs at 55 dpi was too coarse to
    judge a roof line on; each roof gets its own file."""
    import html
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pyproj
    from shapely.geometry import shape
    from shapely.ops import transform, unary_union
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    A = json.loads((SNAPS / f"{before}.json").read_text())["buildings"]
    B = json.loads((SNAPS / f"{now}.json").read_text())["buildings"]
    T = pyproj.Transformer.from_crs(4326, 2193, always_xy=True).transform
    rows = [c for c in _cases() if str(c["id"]) in A or str(c["id"]) in B]
    rows.sort(key=lambda c: c["first_flagged"], reverse=True)
    page = [f"<!doctype html><meta charset=utf-8><title>Reported roofs: {before} and {now}</title>",
            "<style>body{font:15px system-ui;margin:16px auto;max-width:" + ("1100px" if before == now else "1800px") + "}img{width:100%;height:auto}"
            "h2{margin:28px 0 4px}p{margin:2px 0 8px;color:#444}</style>",
            (f"<h1>Reported roofs: {html.escape(now)}</h1>" if before == now else
             f"<h1>Reported roofs: {html.escape(before)} (left) and {html.escape(now)} (right)</h1>"),
            "<p>Red: roof faces. Orange: obstructions. Solid blue: panels the map shows by default. "
            "Outlined: panels the slider can add. Newest reports first.</p>"]
    for c in rows:
        ea, eb = A.get(str(c["id"])), B.get(str(c["id"]))
        reg = (eb or ea)["region"]
        geom = unary_union([transform(T, shape(f["geometry"])) for e in (ea, eb) if e
                            for f in e["features"] if f["properties"]["kind"] == "facet"]
                           or [transform(T, shape(f["geometry"])) for e in (ea, eb) if e for f in e["features"]])
        x0, y0, x1, y1 = geom.buffer(PAD_M).bounds
        s = max(x1 - x0, y1 - y0)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        bounds = (cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2)
        if before == now:                                  # one version: one drawing a roof
            fig, a1 = plt.subplots(1, 1, figsize=(14, 14.8))
            na = nb = _draw(a1, eb, bounds, None, T)
            ax = [a1, a1]
        else:
            fig, ax = plt.subplots(1, 2, figsize=(24, 12.6))
            na = _draw(ax[0], ea, bounds, None, T)
            nb = _draw(ax[1], eb, bounds, None, T)
        ax[0].set_title(f"{before.upper()}   {na} panels" if ea else f"{before.upper()}: no layout", fontsize=18)
        ax[1].set_title(f"{now.upper()}   {nb} panels" if eb else f"{now.upper()}: no layout", fontsize=18)
        fig.suptitle(f"#{c['id']}  {c['defect']}", fontsize=20)
        plt.tight_layout(rect=(0, 0, 1, 0.95))
        plt.savefig(out_dir / f"{c['id']}.jpg", dpi=IMAGE_DPI, pil_kwargs={"quality": 90})
        plt.close(fig)
        page.append(f"<h2>#{c['id']} &nbsp; {html.escape(c['defect'])}</h2>"
                    f"<p>Reported {c['first_flagged']}{' on ' + html.escape(c['flagged_on']) if c.get('flagged_on') else ''}"
                    f" &middot; {html.escape(reg)} &middot; {na} &rarr; {nb} panels<br>"
                    + "<br>".join("&ldquo;" + html.escape(q) + "&rdquo;" for q in c.get("quotes", [])[-2:])
                    + f"</p><a href='{c['id']}.jpg'><img loading=lazy src='{c['id']}.jpg'></a>")
    (out_dir / "index.html").write_text("\n".join(page))
    print(len(rows), "reported roofs, one image each, and index.html in", out_dir)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "snap":
        snap(sys.argv[2], sys.argv[4] if len(sys.argv) > 4 and sys.argv[3] == "--layouts" else None)
    elif len(sys.argv) == 5 and sys.argv[1] == "sheet":
        sheet(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        sys.exit(__doc__)
