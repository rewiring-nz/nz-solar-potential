"""Houses as the build draws them beside the paid reader's reading of the
photo (src/vision_roof.py), over the map's photo.

Left of each pair: the region's panel_layouts faces (red) and panels (cyan)
at the building's image shift. Right: the reader's faces (red), its roof
edge (thick red) and the obstructions it listed (orange), drawn where they
were traced: the reading is stored slid onto the LiDAR and is moved back by
that slide. Yellow is the surveyed outline. A house with no usable reading
says so.

  .venv/bin/python tools/experiments/reader_vs_live.py <out dir> <reader faces dir> <ids.json>

ids.json: [{"region": ..., "id": ...}, ...] (held-back roofs are skipped).
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
PAD_M = 6.0

if __name__ == "__main__":
    import geopandas as gpd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pyproj
    from shapely.affinity import translate
    from shapely.geometry import Polygon, shape
    from shapely.ops import transform as shp_t
    from src.register_imagery import RES_M, _Tiles

    out_dir, faces_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    hold = {int(h["id"]) for h in json.load(open("data/roof_set.json"))["holdout"]}
    want = [r for r in json.load(open(sys.argv[3])) if int(r["id"]) not in hold]
    T = pyproj.Transformer.from_crs(4326, 2193, always_xy=True).transform
    picks = []
    for reg in sorted({r["region"] for r in want}):
        d = Path("data/regions") / reg
        ids = {int(r["id"]) for r in want if r["region"] == reg}
        g = gpd.read_file(d / "building_outlines.geojson")
        geoms = {int(b): gm for b, gm in zip(g["building_id"], g.geometry) if int(b) in ids}
        sh = json.load(open(d / "image_shift.json"))
        live = {}
        for f in json.load(open(d / "panel_layouts.geojson"))["features"]:
            p = f["properties"]
            if p["building_id"] in ids and p["kind"] in ("facet", "panel"):
                live.setdefault(p["building_id"], []).append((p["kind"], shp_t(T, shape(f["geometry"]))))
        tiles = _Tiles(d / "display_tiles")
        for r in want:
            if r["region"] == reg and int(r["id"]) in geoms:
                b = int(r["id"])
                picks.append((reg, b, r.get("name", ""), geoms[b], live.get(b, []), sh.get(str(b), [0, 0, 0]), tiles))
    n_read = 0
    for page in range(0, len(picks), 8):
        fig, ax = plt.subplots(4, 4, figsize=(24, 24))
        for a in ax.ravel():
            a.axis("off")
        for n, (reg, b, name, geom, lv, s, tiles) in enumerate(picks[page:page + 8]):
            x0, y0, x1, y1 = geom.buffer(PAD_M).bounds
            img = np.moveaxis(np.asarray(tiles.window((x0, y0, x1, y1), int(round((y1 - y0) / RES_M)),
                                                      int(round((x1 - x0) / RES_M)))), 0, -1)
            fp = faces_dir / f"{b}.json"
            doc = json.loads(fp.read_text()) if fp.exists() else None
            for c in (0, 1):
                a = ax.ravel()[2 * n + c]
                a.imshow(img, extent=(x0, x1, y0, y1))
                for p in getattr(geom, "geoms", [geom]):
                    a.plot(*p.exterior.xy, color="yellow", lw=1.0)
                a.set_xlim(x0, x1); a.set_ylim(y0, y1)
                if c == 0:
                    for kind, gm in lv:
                        gm = translate(gm, s[0], s[1])
                        for p in getattr(gm, "geoms", [gm]):
                            a.plot(*p.exterior.xy, color="red" if kind == "facet" else "cyan",
                                   lw=1.6 if kind == "facet" else 0.5)
                    a.set_title(f"{page + n + 1}  LIVE  {name or reg} #{b}", fontsize=11)
                elif doc is None:
                    a.set_title(f"{page + n + 1}  READER: no usable reading", fontsize=11)
                else:
                    dx, dy = [-float(v) for v in (doc.get("shift") or [0.0, 0.0])]
                    for ring in doc.get("faces") or []:
                        a.plot(*translate(Polygon(ring), dx, dy).exterior.xy, color="red", lw=1.3)
                    if doc.get("outline"):
                        a.plot(*translate(Polygon(doc["outline"]), dx, dy).exterior.xy, color="red", lw=2.4)
                    for ring in doc.get("obstructions") or []:
                        a.fill(*translate(Polygon(ring), dx, dy).exterior.xy, color="orange", alpha=0.6)
                    a.set_title(f"{page + n + 1}  READER  {len(doc.get('faces') or [])} faces, "
                                f"{len(doc.get('obstructions') or [])} obstructions, confidence {doc.get('confidence')}",
                                fontsize=11)
            n_read += doc is not None
        plt.tight_layout()
        plt.savefig(out_dir / f"reader_vs_live_{page // 8 + 1}.jpg", dpi=55)
        plt.close(fig)
    print(len(picks), "houses;", n_read, "with a reading")
