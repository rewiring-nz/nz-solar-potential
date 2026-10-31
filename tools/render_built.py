#!/usr/bin/env python3
"""Draw roofs as the build left them, before and after, over the photo.

Reads each region's built layouts (data/regions/<r>/panel_layouts.geojson) and
draws facets (red), obstructions (orange) and panels (blue) on that region's
imagery_mosaic.tif. With --before, each roof gets two tiles side by side: the
layouts in that folder (same <region>/panel_layouts.geojson tree, e.g. a copy
taken before a --force build) and the current ones.

It reads the build's output instead of rebuilding a roof, so it shows what
ships and needs no per-worker state.

    tools/render_built.py --cases --before ~/v67_layouts --out cases.jpg
    tools/render_built.py --movers 24 --before ~/v67_layouts --out movers.jpg
    tools/render_built.py --ids 4725633 5370368 --out beach.jpg
"""
import argparse
import json
import math
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ROOT / "data" / "regions"
warnings.filterwarnings("ignore")


def load_layouts(base):
    """{region: {building_id: [features]}} for every region under base."""
    out = {}
    for f in sorted(Path(base).glob("*/panel_layouts.geojson")):
        by = {}
        for ft in json.loads(f.read_text())["features"]:
            b = ft["properties"].get("building_id")
            if b is not None:
                by.setdefault(int(b), []).append(ft)
        out[f.parent.name] = by
    return out


def panels(feats):
    return sum(1 for f in feats if f["properties"].get("kind") == "panel")


def find(lay, bid):
    for r, by in lay.items():
        if bid in by:
            return r
    return None


def pick(a, now, before):
    """[(building_id, region, note)] in drawing order."""
    if a.cases:
        reg = json.loads((ROOT / "data/roof_cases.json").read_text())["cases"]
        out = []
        for c in reg:
            r = c.get("region") or find(now, c["id"]) or find(before, c["id"])
            out.append((c["id"], r, c.get("defect", "")[:60]))
        return out
    if a.movers:
        rows = []
        for r, by in now.items():
            old = before.get(r, {})
            for b in set(by) | set(old):
                n0, n1 = panels(old.get(b, [])), panels(by.get(b, []))
                if n0 != n1:
                    rows.append((abs(n1 - n0), b, r, f"{n0} -> {n1} panels"))
        rows.sort(reverse=True)
        return [(b, r, note) for _, b, r, note in rows[:a.movers]]
    return [(b, find(now, b) or find(before, b), "") for b in a.ids]


def draw(ax, feats, region, bounds):
    import numpy as np
    import pyproj
    import rasterio
    from rasterio.windows import from_bounds
    from shapely.geometry import shape
    from shapely.ops import transform
    x0, y0, x1, y1 = bounds
    with rasterio.open(REGIONS / region / "imagery_mosaic.tif") as src:
        img = src.read([1, 2, 3], window=from_bounds(x0, y0, x1, y1, src.transform),
                       boundless=True)
    ax.imshow(np.moveaxis(img, 0, -1), extent=(x0, x1, y0, y1))
    tf = pyproj.Transformer.from_crs(4326, 2193, always_xy=True).transform
    for f in feats:
        k = f["properties"].get("kind")
        g = transform(tf, shape(f["geometry"]))
        for p in getattr(g, "geoms", [g]):
            x, y = p.exterior.xy
            if k == "facet":
                ax.plot(x, y, color="red", lw=1.2)
            elif k == "panel":
                ax.fill(x, y, color="#1f4fff", alpha=.55, lw=.3, ec="white")
            elif k == "obstruction":
                ax.fill(x, y, color="orange", alpha=.5)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_axis_off()


def extent(feat_lists):
    import pyproj
    from shapely.geometry import shape
    from shapely.ops import transform
    tf = pyproj.Transformer.from_crs(4326, 2193, always_xy=True).transform
    bs = [transform(tf, shape(f["geometry"])).bounds for fl in feat_lists for f in fl]
    if not bs:
        return None
    x0, y0 = min(b[0] for b in bs) - 6, min(b[1] for b in bs) - 6
    x1, y1 = max(b[2] for b in bs) + 6, max(b[3] for b in bs) + 6
    s = max(x1 - x0, y1 - y0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--ids", type=int, nargs="+")
    g.add_argument("--cases", action="store_true", help="every roof in data/roof_cases.json")
    g.add_argument("--movers", type=int, help="the N roofs whose panel count moved most")
    ap.add_argument("--before", help="folder of earlier <region>/panel_layouts.geojson")
    ap.add_argument("--cols", type=int, default=0, help="roofs per row (default: 2 with --before, else 3)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.movers and not a.before:
        ap.error("--movers needs --before")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    now = load_layouts(REGIONS)
    before = load_layouts(Path(a.before).expanduser()) if a.before else {}
    todo = pick(a, now, before)
    per = 2 if a.before else 1
    cols = a.cols or (2 if a.before else 3)
    rows = math.ceil(len(todo) / cols)
    fig, axs = plt.subplots(rows, cols * per, figsize=(5 * cols * per, 5.3 * rows),
                            squeeze=False)
    for ax in axs.flat:
        ax.set_axis_off()
    for i, (bid, r, note) in enumerate(todo):
        row, col = divmod(i, cols)
        cells = axs[row][col * per:(col + 1) * per]
        if r is None:
            cells[0].set_title(f"{bid}: not in any region", fontsize=9)
            continue
        f1 = now.get(r, {}).get(bid, [])
        f0 = before.get(r, {}).get(bid, [])
        bounds = extent([f0, f1])
        if bounds is None:
            cells[0].set_title(f"{bid} {r}: no layout", fontsize=9)
            continue
        if a.before:
            draw(cells[0], f0, r, bounds)
            cells[0].set_title(f"{bid} {r} BEFORE  {panels(f0)} panels", fontsize=9)
        draw(cells[-1], f1, r, bounds)
        cells[-1].set_title(f"{bid} {'NOW' if a.before else r}  {panels(f1)} panels"
                            + (f"\n{note}" if note and not a.before else ""), fontsize=9)
        if a.before and note:
            cells[0].text(0.01, 0.01, note, transform=cells[0].transAxes, fontsize=8,
                          color="white", backgroundcolor="black")
    plt.tight_layout()
    plt.savefig(a.out, dpi=70)
    print(f"{len(todo)} roofs -> {a.out}")


if __name__ == "__main__":
    main()
