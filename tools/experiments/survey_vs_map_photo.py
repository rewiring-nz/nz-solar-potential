"""Per street roof: the survey-year photo, the map's photo and the LiDAR
returns by height, on one grid, with the surveyed outline; and the change
measures register_imagery.roof_change gives."""
import json, sys, os
from pathlib import Path
ROOT = Path.home() / "solar-map"
os.chdir(ROOT); sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import numpy as np, matplotlib, rasterio
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from rasterio.windows import from_bounds
from scipy.spatial import cKDTree
import src.point_roof as PR, src.register_imagery as RI
from src.region_build import area_paths, area_bbox_wgs84
from src.surveys import survey_for
out = Path.home() / "change"; out.mkdir(exist_ok=True)
snap = json.loads((ROOT / "data/case_snapshots/street_r4.json").read_text())["buildings"]
marks = {}
for f in sorted((ROOT / "data/fault_marks").glob("street_r*.json")):
    for r in json.loads(f.read_text())["roofs"]:
        marks.setdefault(str(r["id"]), []).extend(m for m in r["spots"] if m["fault"] == "outline_wrong")
ctx = {}
rows = []
for bid, e in snap.items():
    reg = e["region"]
    if reg not in ctx:
        paths = area_paths(reg)
        sv = survey_for(area_bbox_wgs84(reg), reg)
        refp = paths["reference_imagery"] if sv.get("reference_imagery_layer") != sv.get("imagery_layer") else paths["imagery"]
        print(reg, "survey", sv.get("name") or sv.get("dsm_layer"), "reference photo layer", sv.get("reference_imagery_layer"), refp.name, refp.exists(), flush=True)
        ctx[reg] = (rasterio.open(refp), RI._Tiles(paths["dir"] / "display_tiles"), PR.outlines(reg))
    ref, tiles, outl = ctx[reg]
    poly = outl[int(bid)]
    x0, y0, x1, y1 = poly.buffer(7).bounds
    s = max(x1 - x0, y1 - y0); cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    b = (cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2)
    n = int(s / 0.1)
    A = np.moveaxis(ref.read([1, 2, 3], window=from_bounds(*b, ref.transform), out_shape=(3, n, n)), 0, 2)
    B = np.moveaxis(tiles.window(b, n, n), 0, 2) if tiles.window(b, n, n).shape[0] == 3 else tiles.window(b, n, n)
    ch = RI.roof_change(poly, ref, tiles, e["shift"])
    P = PR.pc(); xs, ys, zs, cs = [], [], [], []
    for path in P._tiles_overlapping(*b):
        tx, ty, tz, tc = P._load_tile(path)
        m = (tx >= b[0]) & (tx <= b[2]) & (ty >= b[1]) & (ty <= b[3])
        xs.append(tx[m]); ys.append(ty[m]); zs.append(tz[m]); cs.append(tc[m])
    x, y, z, c = map(np.concatenate, (xs, ys, zs, cs))
    g = c == 2
    _, gi = cKDTree(np.column_stack([x[g], y[g]])).query(np.column_stack([x, y]), k=5)
    hag = z - np.median(z[g][gi], axis=1)
    veg = (c >= 3) & (c <= 5)
    from shapely import contains_xy
    ins = contains_xy(poly, x, y) & ~veg
    high = float((hag[ins] > 2.0).mean()) if ins.sum() else 0.0
    fig, ax = plt.subplots(1, 3, figsize=(30, 10.5))
    ax[0].imshow(A, extent=(b[0], b[2], b[1], b[3])); ax[0].set_title(f"#{bid}  survey-year photo", fontsize=16)
    ax[1].imshow(B, extent=(b[0], b[2], b[1], b[3])); ax[1].set_title("the map's photo (current)", fontsize=16)
    ax[2].set_facecolor("black")
    ax[2].scatter(x[veg], y[veg], s=3, c="#1c5c1c")
    ax[2].scatter(x[~veg], y[~veg], s=7, c=np.clip(hag[~veg], 0, 9), cmap="turbo", vmin=0, vmax=9)
    ax[2].set_title(f"LiDAR returns by height (0-9 m); {high:.0%} of the outline at roof height", fontsize=16)
    for a in ax:
        a.plot(*poly.exterior.xy, color="white", lw=2, ls="--")
        for mk in marks.get(bid, []):
            a.plot(mk["xy"][0] - e["shift"][0], mk["xy"][1] - e["shift"][1], "o", ms=12, mfc="yellow", mec="black", mew=1.5)
        a.set_xlim(b[0], b[2]); a.set_ylim(b[1], b[3]); a.set_aspect("equal"); a.set_xticks([]); a.set_yticks([])
    plt.tight_layout(); plt.savefig(out / f"{bid}.jpg", dpi=60, pil_kwargs={"quality": 85}); plt.close(fig)
    rows.append((bid, ch, high, round(poly.area)))
    print(bid, "change (brightness, edge corr)", None if ch is None else (round(ch[0], 1), round(ch[1], 2)), "roof-height share", round(high, 2), "outline m2", round(poly.area), flush=True)
