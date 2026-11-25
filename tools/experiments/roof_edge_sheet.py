"""Unmarked houses with the surveyed outline against the roof edge from the
returns, each placed on the map's photo by its own lean.

Per house, left: point faces on the surveyed outline (SOLAR_ROOF_EDGE=0),
right: run out to the fitted edge. Yellow is the surveyed outline as
surveyed, red the faces at their lean (one move for the building,
photo_lean.drawing_shift). Houses: outline 120-400 m2, no markup, not held
back, fixed seed.

  .venv/bin/python tools/experiments/roof_edge_sheet.py <out.jpg> <region> [n] [--ids ...]
"""
import json
import random
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")

if __name__ == "__main__":
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from shapely.affinity import translate
    from shapely.geometry import Polygon
    from src import photo_lean, point_roof as pr, roof_edge
    from src.outline_fit import _marked
    from src.register_imagery import RES_M, _Tiles

    out, region = sys.argv[1], sys.argv[2]
    args = sys.argv[3:]
    ids = [int(a) for a in args[args.index("--ids") + 1:]] if "--ids" in args else None
    n = int(args[0]) if args and args[0].isdigit() else 8
    O = pr.outlines(region)
    if ids is None:
        hold = {int(h["id"]) for h in json.load(open("data/roof_set.json"))["holdout"]}
        skip = hold | set(_marked())
        random.seed(17)
        ids = random.sample(sorted(b for b, g in O.items() if 120 <= g.area <= 400 and b not in skip), n)
    tiles = _Tiles(Path("data/regions") / region / "display_tiles")

    def photo(x0, y0, x1, y1):
        return tiles.window((x0, y0, x1, y1), int(round((y1 - y0) / RES_M)), int(round((x1 - x0) / RES_M)))

    rows = (len(ids) + 1) // 2
    fig, ax = plt.subplots(rows, 4, figsize=(26, 6.6 * rows), squeeze=False)
    for a in ax.ravel():
        a.axis("off")
    for r, b in enumerate(ids):
        docs = []
        for use in (False, True):
            roof_edge.USE_ROOF_EDGE = use
            with tempfile.TemporaryDirectory() as d:
                ok = pr.write_faces(b, O[b], Path(d))
                docs.append(json.loads((Path(d) / f"{b}.json").read_text()) if ok else None)
        x0, y0, x1, y1 = O[b].buffer(5).bounds
        img = np.moveaxis(np.asarray(photo(x0, y0, x1, y1)), 0, -1)
        for c, doc in enumerate(docs):
            a = ax[r // 2][2 * (r % 2) + c]; a.imshow(img, extent=(x0, x1, y0, y1)); a.axis("off")
            a.set_xlim(x0, x1); a.set_ylim(y0, y1)
            for p in getattr(O[b], "geoms", [O[b]]):
                a.plot(*p.exterior.xy, color="yellow", lw=1.3)
            if not doc:
                a.set_title(f"{b}: no faces"); continue
            s = photo_lean.drawing_shift(doc, photo) or [0, 0, 0]
            for ring in doc["faces"]:
                a.plot(*translate(Polygon(ring), s[0], s[1]).exterior.xy, color="red", lw=1.8)
            area = sum(Polygon(f).area for f in doc["faces"])
            a.set_title(f"{b}  {'roof edge from returns' if c else 'surveyed outline'}  "
                        f"{area:.0f} m2  lean {s[0]:+.2f},{s[1]:+.2f} m (sharpness {s[2]})", fontsize=10)
            print(b, "edge" if c else "surveyed", round(area), s, "outline" in doc, flush=True)
    plt.tight_layout(); plt.savefig(out, dpi=70)
