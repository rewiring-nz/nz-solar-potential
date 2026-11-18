"""Random unmarked houses, the build's roof as the map draws it beside the
fused roof edge (tools/experiments/fused_edge.py), over the map's photo.

Left of each pair: the region's panel_layouts faces at the building's image
shift (what is live when the region's layout is the served one). Right: the
fused edge (thick) and its leaned faces. Yellow is the surveyed outline.
Houses: outline 120-400 m2, no markup, not held back, PER_REGION from each
region, fixed seed.

  .venv/bin/python tools/experiments/fused_vs_live.py <out dir> [region ...]
"""
import json
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "tools/experiments")
PER_REGION = 5
PAD_M = 6.0
REGIONS = ["pilot", "town_west_fernhill", "kelvin_heights", "frankton_arm",
           "lake_hayes_est_west", "hanleys_farm", "jacks_point", "arrowtown_millbrook"]


def one(job):
    reg, b, geom = job
    import fused_edge as FE
    from src import photo_lean as PL, point_roof as pr, roof_edge as RE
    from src.register_imagery import RES_M, _Tiles
    tiles = _Tiles(Path("data/regions") / reg / "display_tiles")

    def photo(x0, y0, x1, y1):
        return tiles.window((x0, y0, x1, y1), int(round((y1 - y0) / RES_M)), int(round((x1 - x0) / RES_M)))
    def neighbours_k():
        """Median lean of the sharp point roofs within 150 m (their stored faces)."""
        import geopandas as gpd
        g = gpd.read_file(Path("data/regions") / reg / "building_outlines.geojson")
        c = geom.centroid
        near = g[(g.geometry.centroid.distance(c) < 150) & (g["building_id"] != b)]
        near = near.assign(d=near.geometry.centroid.distance(c)).sort_values("d").head(16)
        ks = []
        for nb in near["building_id"]:
            f = Path(f"data/point_faces/{int(nb)}.json")
            if not f.exists():
                continue
            faces, origin = PL.doc_faces(json.loads(f.read_text()))
            k = PL.photo_lean(faces, origin, photo) if faces else None
            if k and k[2] >= 3.5 and max(abs(k[0]), abs(k[1])) < PL.K_MAX - 1e-6:
                ks.append(k[:2])
        return tuple(np.median(np.array(ks), axis=0)) if len(ks) >= 4 else None
    try:
        r = FE.fuse(pr, RE, PL, b, geom, photo, neighbours_k=neighbours_k)
        if r is None:
            return b, None
        return b, dict(fused=r["fused"], k=r["k"], moved=len(r["moved"]), n_walls=r["n_walls"], notes=r["notes"],
                       faces=PL.leaned(r["faces"], r["origin"], r["k"][0], r["k"][1]))
    except Exception as exc:
        print("failed", b, repr(exc), flush=True)
        return b, None


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
    skip = {int(h["id"]) for h in json.load(open("data/roof_set.json"))["holdout"]} | set(_marked())
    random.seed(29)
    picks, live = [], {}
    extra = {}
    if "--ids" in regions:
        i = regions.index("--ids")
        for t in regions[i + 1:]:
            extra.setdefault(t.split(":")[0], []).append(int(t.split(":")[1]))
        regions = regions[:i] + [r for r in extra if r not in regions[:i]]
    for reg in regions:
        d = Path("data/regions") / reg
        g = gpd.read_file(d / "building_outlines.geojson")
        ok = [(int(b), geom) for b, geom in zip(g["building_id"], g.geometry)
              if 120 <= geom.area <= 400 and int(b) not in skip]
        chosen = random.sample(ok, min(PER_REGION, len(ok))) if reg in REGIONS or not extra else []
        geoms = {int(b): geom for b, geom in zip(g["building_id"], g.geometry)}
        chosen += [(b, geoms[b]) for b in extra.get(reg, []) if b in geoms]
        want = {b for b, _ in chosen}
        sh = json.load(open(d / "image_shift.json"))
        for f in json.load(open(d / "panel_layouts.geojson"))["features"]:
            p = f["properties"]
            if p["building_id"] in want and p["kind"] in ("facet", "panel"):
                live.setdefault(p["building_id"], []).append((p["kind"], shp_t(T, shape(f["geometry"]))))
        for b, geom in chosen:
            picks.append((reg, b, geom, sh.get(str(b), [0, 0, 0])))
    with ProcessPoolExecutor(8) as ex:
        res = dict(ex.map(one, [(reg, b, geom) for reg, b, geom, _ in picks]))
    listing = []
    for page in range(0, len(picks), 8):
        fig, ax = plt.subplots(4, 4, figsize=(24, 24))
        for a in ax.ravel():
            a.axis("off")
        for n, (reg, b, geom, s) in enumerate(picks[page:page + 8]):
            num = page + n + 1
            x0, y0, x1, y1 = geom.buffer(PAD_M).bounds
            tiles = _Tiles(Path("data/regions") / reg / "display_tiles")
            img = np.moveaxis(np.asarray(tiles.window((x0, y0, x1, y1), int(round((y1 - y0) / RES_M)),
                                                      int(round((x1 - x0) / RES_M)))), 0, -1)
            r = res.get(b)
            for c in (0, 1):
                a = ax.ravel()[2 * n + c]
                a.imshow(img, extent=(x0, x1, y0, y1))
                for p in getattr(geom, "geoms", [geom]):
                    a.plot(*p.exterior.xy, color="yellow", lw=1.0)
                a.set_xlim(x0, x1); a.set_ylim(y0, y1)
                if c == 0:
                    for kind, gm in live.get(b, []):
                        gm = translate(gm, s[0], s[1])
                        for p in getattr(gm, "geoms", [gm]):
                            a.plot(*p.exterior.xy, color="red" if kind == "facet" else "cyan",
                                   lw=1.6 if kind == "facet" else 0.5)
                    a.set_title(f"{num}  LIVE  {reg} #{b}", fontsize=11)
                elif r is None:
                    a.set_title(f"{num}  FUSED: no faces from the returns", fontsize=11)
                else:
                    for p in r["faces"]:
                        a.plot(*p.exterior.xy, color="red", lw=0.9)
                    if r["fused"] is not None:
                        a.plot(*r["fused"].exterior.xy, color="red", lw=2.4)
                    a.set_title(f"{num}  FUSED  " + ("; ".join(r["notes"]) if r["notes"] else
                                f"lean sharpness {r['k'][2]:.1f}"), fontsize=11)
            listing.append(dict(n=num, region=reg, id=b, fused=bool(r and r["fused"] is not None), notes=(r or {}).get("notes"),
                                sharp=round(r["k"][2], 1) if r else None))
        plt.tight_layout()
        plt.savefig(out_dir / f"fused_vs_live_{page // 8 + 1}.jpg", dpi=55)
        plt.close(fig)
    json.dump(listing, open(out_dir / "fused_vs_live.json", "w"))
    print(len(picks), "houses;", sum(1 for r in listing if r["fused"]), "with a fused edge")
