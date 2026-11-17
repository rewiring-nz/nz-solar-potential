"""Sheets of random unmarked houses as the map draws them: the surveyed
outline (yellow), the roof faces (red) and the panels (cyan) at the
building's image shift, over the map's own photo. For sorting each miss by
cause: wrong lines, wrong place, panels over the edge.

Houses: outline 120-400 m2, at least one panel, no markup, not held back;
PER_REGION from each region, fixed seed.

SHEET_LEAN=1 draws each house at the lean of its own point faces read off
the map's tiles (src/photo_lean.py) instead of the shift on file: what the
build would give a region whose image_shift.json is empty.

  .venv/bin/python tools/experiments/placement_sheet.py <out dir> [region ...]
"""
import os
import json
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
PER_REGION = 5
PAD_M = 5.0
REGIONS = ["hanleys_farm", "lake_hayes_est_west", "kelvin_heights", "arrowtown_east",
           "frankton_flats", "wanaka_town", "jacks_point", "town_west_fernhill"]

if __name__ == "__main__":
    import geopandas as gpd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pyproj
    from shapely.affinity import translate
    from shapely.geometry import shape
    from shapely.ops import transform as shp_t
    from src.outline_fit import _marked
    from src.register_imagery import RES_M, _Tiles

    out_dir = Path(sys.argv[1]); out_dir.mkdir(parents=True, exist_ok=True)
    regions = sys.argv[2:] or REGIONS
    T = pyproj.Transformer.from_crs(4326, 2193, always_xy=True).transform
    hold = {int(h["id"]) for h in json.load(open("data/roof_set.json"))["holdout"]}
    skip = hold | set(_marked())
    random.seed(17)
    picks = []
    for reg in regions:
        d = Path("data/regions") / reg
        lay = json.load(open(d / "panel_layouts.geojson"))["features"]
        by = {}
        for f in lay:
            by.setdefault(int(f["properties"]["building_id"]), []).append(f)
        g = gpd.read_file(d / "building_outlines.geojson")
        ok = [(int(b), geom) for b, geom in zip(g["building_id"], g.geometry)
              if 120 <= geom.area <= 400 and int(b) not in skip
              and any(f["properties"]["kind"] == "panel" for f in by.get(int(b), []))]
        sh = json.load(open(d / "image_shift.json"))
        tiles = _Tiles(d / "display_tiles")
        for b, geom in random.sample(ok, min(PER_REGION, len(ok))):
            s = sh.get(str(b), [0, 0, 0])
            pf = Path(f"data/point_faces/{b}.json")
            if os.environ.get("SHEET_LEAN") == "1" and pf.exists():
                from src import photo_lean
                s = photo_lean.drawing_shift(json.loads(pf.read_text()), lambda x0, y0, x1, y1, t=tiles: t.window(
                    (x0, y0, x1, y1), int(round((y1 - y0) / RES_M)), int(round((x1 - x0) / RES_M)))) or s
            picks.append((reg, b, geom, by[b], s, tiles))
    listing = []
    for page in range(0, len(picks), 8):
        fig, ax = plt.subplots(2, 4, figsize=(22, 11.5))
        for a in ax.ravel():
            a.axis("off")
        for n, (reg, b, geom, feats, s, tiles) in enumerate(picks[page:page + 8]):
            a = ax.ravel()[n]
            x0, y0, x1, y1 = geom.buffer(PAD_M).bounds
            img = np.moveaxis(np.asarray(tiles.window((x0, y0, x1, y1), int(round((y1 - y0) / RES_M)),
                                                      int(round((x1 - x0) / RES_M)))), 0, -1)
            a.imshow(img, extent=(x0, x1, y0, y1))

            def draw(gm, **kw):
                gm = translate(gm, s[0], s[1])
                for p in getattr(gm, "geoms", [gm]):
                    a.plot(*p.exterior.xy, **kw)
            draw(geom, color="yellow", lw=1.0)
            for f in feats:
                k = f["properties"]["kind"]
                if k in ("facet", "panel"):
                    draw(shp_t(T, shape(f["geometry"])), color="red" if k == "facet" else "cyan",
                         lw=1.3 if k == "facet" else 0.6)
            num = page + n + 1
            a.set_title(f"{num}  {reg} #{b}  shift {s[0]:+.1f},{s[1]:+.1f} m", fontsize=11)
            a.set_xlim(x0, x1); a.set_ylim(y0, y1)
            listing.append(dict(n=num, region=reg, id=b, shift=s))
        plt.tight_layout()
        plt.savefig(out_dir / f"placement_{page // 8 + 1}.jpg", dpi=75)
        plt.close(fig)
    json.dump(listing, open(out_dir / "placement.json", "w"))
    print(len(picks), "houses on", (len(picks) + 7) // 8, "sheets")
