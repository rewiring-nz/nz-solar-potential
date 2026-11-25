"""RoofVIP scenes as training samples for the roof-line reader.

RoofVIP 1.0.3.0 (DLR, Zenodo 22015175, CC BY 4.0) is 10,245 scenes of
256 x 256 px at 0.2 m over Munich: an RGB orthophoto, a DSM and hand-drawn
roof-plane polygons. Each building wholly inside a scene becomes one sample
in the reader's own form (tools/export_roof_lines.py and
tools/line_targets.py): the roof framed to FILL_PX of a SIZE picture, the
photo and four height channels, and the maps a plane drawing can teach
(line, core, outline, join, roof; not the line kinds).

The photo is 0.2 m against 0.1 m here and the roofs are Bavarian, so these
samples are for pre-training only; the drawn Queenstown roofs stay the test.
The polygon files are pickled arrays: load them only after checking they
hold nothing but numpy arrays (pickletools), as was done on download.

    python tools/public_roofvip.py data/sources/public/roofvip/x data/line_set/roofvip
"""
import json, sys
from multiprocessing import Pool
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_dilation, binary_erosion, gaussian_filter
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
from line_targets import corners, JOIN_PX, NEAR_PX
SIZE, FILL_PX, BOUNDARY_PX, CORE_ERODE_PX, EAVE_ERASE_PX = 384, 304, 3, 5, 6
GSD, MIN_M2, MAX_M2, EDGE_PX = 0.2, 40.0, 800.0, 3.0
SRC = OUT = None
REC = {}


def scene(sid):
    polys = [Polygon(p).buffer(0) for p in np.load(SRC / "vec" / f"{sid}.npy", allow_pickle=True) if len(p) >= 4]
    polys = [max(p.geoms, key=lambda g: g.area) if p.geom_type == "MultiPolygon" else p for p in polys if not p.is_empty]
    polys = [p for p in polys if p.geom_type == "Polygon" and p.area > 4]
    if not polys:
        return []
    groups = unary_union([p.buffer(0.75) for p in polys])
    rgb = Image.open(SRC / "img" / f"{sid}.png").convert("RGB")
    lo, hi = REC[sid]
    dsm = np.asarray(Image.open(SRC / "dsm" / f"{sid}.png").convert("L"), np.float32) / 255.0 * (hi - lo) + lo
    all_roof = np.zeros((256, 256), np.uint8)
    im = Image.fromarray(all_roof); dr = ImageDraw.Draw(im)
    for p in polys:
        dr.polygon(list(p.exterior.coords), fill=1)
    onroof = binary_dilation(np.array(im) > 0, iterations=5)
    dsm_im = Image.fromarray(np.where(onroof, dsm, np.nan).astype(np.float32), mode="F")
    tile = box(EDGE_PX, EDGE_PX, 256 - EDGE_PX, 256 - EDGE_PX)
    tf = json.loads((SRC / "meta" / f"{sid}.json").read_text())["tile_transform"]
    out = []
    for g in getattr(groups, "geoms", [groups]):
        faces = [p for p in polys if p.representative_point().within(g)]
        roof = unary_union([f.buffer(0.1) for f in faces]).buffer(-0.1)
        if roof.geom_type != "Polygon" or not (MIN_M2 <= roof.area * GSD * GSD <= MAX_M2) or not roof.within(tile):
            continue
        minx, miny, maxx, maxy = roof.bounds
        span = max(maxx - minx, maxy - miny); mpp = span / FILL_PX; half = SIZE / 2 * mpp
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        b = (cx - half, cy - half, cx + half, cy + half)          # tile pixels, y down
        if b[0] < -8 or b[1] < -8 or b[2] > 264 or b[3] > 264:
            continue
        pic = np.asarray(rgb.transform((SIZE, SIZE), Image.EXTENT, b, Image.BILINEAR), np.float32) / 255.0
        z = np.asarray(dsm_im.transform((SIZE, SIZE), Image.EXTENT, b, Image.NEAREST, fillcolor=float("nan")), np.float32)
        m = mpp * GSD                                             # metres per output pixel
        base = np.nanpercentile(z, 5) if np.isfinite(z).any() else 0.0
        zf = gaussian_filter(np.nan_to_num(z - base, nan=0.0), max(2.0, 1.2 / m))
        dzy, dzx = np.gradient(zf, m)
        # rows run south in both pictures, so the downhill channels mean the same
        slope = np.degrees(np.arctan(np.hypot(dzx, dzy))); mag = np.hypot(dzx, dzy) + 1e-9
        chans = np.dstack([pic, np.clip(zf / 10.0, 0, 1), np.clip(slope / 45.0, 0, 1), (-dzx / mag + 1) / 2, (dzy / mag + 1) / 2])
        px = lambda x, y: ((x - b[0]) / mpp, (y - b[1]) / mpp)
        wim = Image.new("L", (SIZE, SIZE), 0); wd = ImageDraw.Draw(wim)
        bim = Image.new("L", (SIZE, SIZE), 0); bd = ImageDraw.Draw(bim)
        for f in faces:
            ring = [px(x, y) for x, y in f.exterior.coords]
            wd.polygon(ring, fill=255); bd.line(ring + [ring[0]], fill=255, width=BOUNDARY_PX)
        fr = [px(x, y) for x, y in roof.exterior.coords]
        bd.line(fr + [fr[0]], fill=0, width=BOUNDARY_PX + EAVE_ERASE_PX)
        weight = np.array(wim) > 0
        if weight.sum() < 900:
            continue
        line = np.array(bim) > 0
        core = binary_erosion(weight & ~line, np.ones((3, 3), bool), iterations=CORE_ERODE_PX)
        t = np.zeros((SIZE, SIZE, 8), bool)
        t[..., 0], t[..., 1], t[..., 7] = line, core, weight
        t[..., 5] = binary_dilation(weight) & ~binary_erosion(weight)
        ends = []
        for f in faces + [roof]:
            ends += [tuple(c) for c in corners([(x * GSD, y * GSD) for x, y in f.exterior.coords])]
        joins = []
        for e in ends:
            if all(np.hypot(e[0] - j[0], e[1] - j[1]) > 0.3 for j in joins):
                joins.append(e)
        jim = Image.new("L", (SIZE, SIZE), 0); jd = ImageDraw.Draw(jim)
        for j in joins:
            x, y = px(j[0] / GSD, j[1] / GSD)
            jd.ellipse([x - JOIN_PX, y - JOIN_PX, x + JOIN_PX, y + JOIN_PX], fill=255)
        t[..., 6] = np.array(jim) > 0
        gx, gy = tf[2] + tf[0] * cx, tf[5] + tf[4] * cy          # where the roof is, to drop repeats between overlapping scenes
        name = f"{int(round(gx))}_{int(round(gy))}"
        np.savez_compressed(OUT / "pre" / f"{name}.npz", image=(chans * 255).astype(np.uint8), target=t,
                            known=np.array([1, 1, 0, 0, 0, 1, 1, 1], bool), near=binary_dilation(weight, iterations=NEAR_PX),
                            weight=weight, meta=np.array([m, b[0], b[1], b[2], b[3], 0, 0], float), region="roofvip")
        out.append(name)
    return out


def setup(src, out):
    global SRC, OUT, REC
    SRC, OUT = Path(src), Path(out)
    recs = json.loads((SRC / "modalities_summary.json").read_text())["records"]
    REC = {k: (v["modalities"]["dsm"]["geographic_min"], v["modalities"]["dsm"]["geographic_max"]) for k, v in recs.items()}


def main():
    setup(sys.argv[1], sys.argv[2])
    (OUT / "pre").mkdir(parents=True, exist_ok=True)
    ids = sorted(REC)[: int(sys.argv[3])] if len(sys.argv) > 3 else sorted(REC)
    with Pool(6, initializer=setup, initargs=(sys.argv[1], sys.argv[2])) as pool:
        n = sum(len(r) for r in pool.imap_unordered(scene, ids, chunksize=20))
    print(n, "written;", len(list((OUT / "pre").glob("*.npz"))), "distinct roofs")


if __name__ == "__main__":
    main()
