"""Find buildings in a region's photo that LINZ has no outline for.

    python tools/detect_new_buildings.py arrowtown_east            # writes building_outlines_detected.geojson
    python tools/detect_new_buildings.py arrowtown_east --apply    # ...and adds them to the region's outlines
    python tools/detect_new_buildings.py arrowtown_east --screen-only   # re-screen existing candidates

A house built after LINZ traced its outlines has no outline, so nothing can
be estimated for it (bug form, 8 Sep 2026). The building finder
(tools/train_footprints.py) is run across the region's map photo
(imagery_mosaic.tif, the newest capture); every building it sees that no
LINZ outline already covers becomes a candidate outline:

  * at least MIN_M2 and at most MAX_M2 (sheds and stray blobs below, car
    parks and fields above),
  * mean confidence of at least MIN_SCORE,
  * not on an existing outline (less than OVERLAP_MAX of it on one),
  * tidied: a near-rectangular blob becomes its minimum rotated rectangle,
    anything else is simplified to straight edges.

Candidates get ids from DETECTED_ID_BASE up (no LINZ id is that large) and
"source": "photo_detected". --apply keeps LINZ's file as
building_outlines.linz.geojson the first time and writes the union, so the
rest of the pipeline treats them as ordinary buildings;
tools/predict_photo_shape.py then reads their roofs from the photo (the
LiDAR, flown earlier, shows only ground there).
"""

import argparse
import json
import sys
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from src import photo_shape as ps  # noqa: E402
from train_footprints import OUT as MODEL, TILE_PX_DEFAULT, RES_M_DEFAULT, to_input  # noqa: E402

REGIONS_DIR = ROOT / "data" / "regions"
MIN_M2, MAX_M2 = 25.0, 1500.0
MIN_SCORE = 0.6
OVERLAP_MAX = 0.2
RECT_FILL = 0.8
DETECTED_ID_BASE = 900_000_000

# SCREENING (17 Oct, from close-ups of 60 Arrowtown candidates: ~70% real
# houses and sheds LINZ lacks; the rest fell into three kinds, each with a
# plain signature in the photo):
#   * slivers along the black edge of the photo     -> touches no-data
#   * vineyards, gardens, trees, paddocks           -> green, and either the
#     finder was unsure or the shape is ragged (green Colorsteel roofs are
#     common here and are confident, regular shapes, so they stay)
#   * ponds and lakes                               -> almost no texture
#     inside, and the finder was not sure
NODATA_MAX = 0.05          # share of pixels within 3 m that are pure black
GREEN_MARGIN = 6           # green brighter than red and blue by this much
GREEN_SCORE_MIN = 0.8      # ...needs at least this confidence
GREEN_SOLIDITY_MIN = 0.75  # ...and this regular a shape (area / convex hull)
FLAT_STD_MAX = 9           # mean per-band pixel spread inside: water is flat
FLAT_SCORE_MIN = 0.9
# What survived those still held ~20% junk (a pond with birds on it, scrub,
# a garden path, play equipment), nearly all below 0.72 confidence, while
# real houses sat mostly above 0.8. A detected outline gets panels drawn on
# it, so precision wins over the last few real sheds.
KEEP_SCORE_MIN = 0.75
RAGGED_SOLIDITY_MAX = 0.6
# pools: clearly bluer than any roof colour sold here
BLUE_MARGIN = 20
# lawns and bowling greens: bright and clearly green (green steel roofs are
# dark, so brightness separates them)
LAWN_MARGIN, LAWN_BRIGHT = 12, 95


def detected_id(region, n):
    return DETECTED_ID_BASE + (zlib.crc32(region.encode()) % 9000) * 10000 + n


def probability_map(model, src, bounds, tile_px=TILE_PX_DEFAULT, res=RES_M_DEFAULT):
    """Building probability over `bounds` at `res`, tiles overlapping by a quarter."""
    import torch
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds as wfb
    x0, y0, x1, y1 = bounds
    W, H = int((x1 - x0) / res), int((y1 - y0) / res)
    acc = np.zeros((H, W), np.float32)
    cnt = np.zeros((H, W), np.float32)
    step = int(tile_px * 0.75)
    side = tile_px * res
    rows = list(range(0, max(H - tile_px, 0) + 1, step)) + ([H - tile_px] if H > tile_px else [])
    cols = list(range(0, max(W - tile_px, 0) + 1, step)) + ([W - tile_px] if W > tile_px else [])
    batch, where = [], []

    def flush():
        if not batch:
            return
        x = torch.from_numpy(np.stack(batch))
        with torch.no_grad():
            p = 0
            for k in (0, 1, 4, 5):           # four of the eight symmetries
                pk = torch.sigmoid(model(ps.dihedral_x(x, k))[:, 0])
                z = torch.zeros_like(pk)
                pk, _, _, _ = ps.undo_dihedral(pk, z, z, z, k)
                p = p + pk
            p = (p / 4).numpy()
        for (r, c), pi in zip(where, p):
            acc[r:r + tile_px, c:c + tile_px] += pi
            cnt[r:r + tile_px, c:c + tile_px] += 1
        batch.clear(); where.clear()

    for r in sorted(set(rows)):
        for c in sorted(set(cols)):
            tx0 = x0 + c * res
            ty1 = y1 - r * res
            img = src.read([1, 2, 3], window=wfb(tx0, ty1 - side, tx0 + side, ty1, src.transform),
                           out_shape=(3, tile_px, tile_px), resampling=Resampling.average, boundless=True)
            if img.dtype == np.uint16:
                img = img // 257
            img = np.clip(img, 0, 255).astype(np.uint8)
            if img.max() == 0:
                continue
            batch.append(to_input(img)); where.append((r, c))
            if len(batch) == 16:
                flush()
    flush()
    return np.where(cnt > 0, acc / np.maximum(cnt, 1), 0.0)


SPLIT_THR = 0.9   # neighbouring houses merged at 0.5 separate at this confidence


def _blobs(prob, thr, tr):
    """Outlines of the confident areas. A blob that a stricter threshold
    breaks into two or more building-sized parts was neighbouring houses
    run together (a row of townhouses as one 650 m2 outline, Millbrook,
    17 Oct): each part is grown back to the 0.5 edge within the blob."""
    from rasterio.features import shapes, rasterize
    from shapely.geometry import shape
    from scipy import ndimage
    lo = prob > thr
    lab, n = ndimage.label(lo)
    hi_lab, _ = ndimage.label(prob > SPLIT_THR)
    px_m2 = abs(tr.a * tr.e)
    boxes = ndimage.find_objects(lab)       # once: one box per blob
    out = []
    for g, v in shapes(lab.astype(np.int32), mask=lo, transform=tr):
        blob = int(v)
        sl = boxes[blob - 1]
        sub = lab[sl] == blob
        cores = np.unique(hi_lab[sl][sub & (hi_lab[sl] > 0)])
        big = [c for c in cores if (hi_lab[sl] == c).sum() * px_m2 >= MIN_M2]
        if len(big) < 2:
            out.append(shape(g).buffer(0))
            continue
        # grow each core back out to the blob's edge (nearest core wins)
        seeds = np.zeros(sub.shape, np.int32)
        for k, c in enumerate(big, 1):
            seeds[hi_lab[sl] == c] = k
        _, idx = ndimage.distance_transform_edt(seeds == 0, return_indices=True)
        part = np.where(sub, seeds[idx[0], idx[1]], 0)
        sub_tr = tr * tr.translation(sl[1].start, sl[0].start)
        for g2, v2 in shapes(part.astype(np.int32), mask=part > 0, transform=sub_tr):
            out.append(shape(g2).buffer(0))
    return out


def detect(region, model, thr=0.5, recut=False):
    import rasterio
    from rasterio.features import shapes
    from rasterio.transform import from_origin
    from shapely.geometry import box, shape
    from shapely.strtree import STRtree
    import config
    from pyproj import Transformer
    d = REGIONS_DIR / region
    linz_path = d / "building_outlines.linz.geojson"
    if not linz_path.exists():
        linz_path = d / "building_outlines.geojson"
    linz = [shape(f["geometry"]) for f in json.loads(linz_path.read_text())["features"]]
    linz = [g if g.is_valid else g.buffer(0) for g in linz]
    tree = STRtree(linz)
    b = config.REGIONS[region]
    t = Transformer.from_crs(4326, 2193, always_xy=True)
    x0, y0 = t.transform(b[0], b[1]); x1, y1 = t.transform(b[2], b[3])
    if recut:
        z = np.load(d / "detect_probability.npz")
        prob, bounds, res = z["prob"].astype(np.float32), tuple(z["bounds"]), float(z["res"])
    else:
        with rasterio.open(d / "imagery_mosaic.tif") as src:
            pb = src.bounds
            bounds = (max(x0, pb.left), max(y0, pb.bottom), min(x1, pb.right), min(y1, pb.top))
            prob = probability_map(model, src, bounds)
        res = RES_M_DEFAULT
        # kept so the outlines can be re-cut without re-running the finder
        np.savez_compressed(d / "detect_probability.npz", prob=prob.astype(np.float16),
                            bounds=np.array(bounds), res=res)
    tr = from_origin(bounds[0], bounds[3], res, res)
    out = []
    for poly in _blobs(prob, thr, tr):
        if poly.is_empty or not (MIN_M2 <= poly.area <= MAX_M2):
            continue
        hits = [linz[i] for i in tree.query(poly)]
        if any(h.intersection(poly).area > OVERLAP_MAX * poly.area for h in hits):
            continue
        from rasterio.features import geometry_mask
        inside = geometry_mask([poly], out_shape=prob.shape, transform=tr, invert=True)
        score = float(prob[inside].mean()) if inside.any() else 0.0
        if score < MIN_SCORE:
            continue
        mrr = poly.minimum_rotated_rectangle
        tidy = mrr if poly.area / max(mrr.area, 1e-9) >= RECT_FILL else poly.simplify(0.75, preserve_topology=True)
        if tidy.geom_type != "Polygon" or tidy.is_empty:
            continue
        out.append((tidy, score))
    feats = []
    for n, (poly, score) in enumerate(sorted(out, key=lambda p: (-p[0].centroid.y, p[0].centroid.x))):
        feats.append({"type": "Feature", "geometry": poly.__geo_interface__,
                      "properties": {"building_id": detected_id(region, n), "source": "photo_detected",
                                     "detect_score": round(score, 3), "area_m2": round(poly.area, 1),
                                     "capture_source_name": "detected in the region's photo (tools/detect_new_buildings.py)",
                                     "use": "Unknown", "name": ""}})
    doc = {"type": "FeatureCollection",
           "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::2193"}}, "features": feats}
    (d / "building_outlines_detected_raw.geojson").write_text(json.dumps(doc))
    (d / "building_outlines_detected.geojson").write_text(json.dumps(doc))
    print(f"[{region}] {len(feats)} buildings in the photo with no LINZ outline "
          f"(LINZ has {len(linz)}); median {np.median([f['properties']['area_m2'] for f in feats]) if feats else 0:.0f} m2")
    return doc


def screen(region):
    """Split the finder's candidates into likely buildings and rejects, by
    what the photo shows inside each one (see SCREENING above). Reads the
    raw candidates, so it can be re-run with new thresholds without
    re-running the finder; rejects are kept, with their reason, for review."""
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.windows import from_bounds
    from shapely.geometry import shape
    d = REGIONS_DIR / region
    raw = d / "building_outlines_detected_raw.geojson"
    if not raw.exists():
        raw.write_text((d / "building_outlines_detected.geojson").read_text())
    doc = json.loads(raw.read_text())
    keep, rejects, why = [], [], {}
    with rasterio.open(d / "imagery_mosaic.tif") as src:
        for f in doc["features"]:
            g = shape(f["geometry"])
            score = f["properties"].get("detect_score", 0)
            solidity = g.area / max(g.convex_hull.area, 1e-9)
            ring = g.buffer(3)
            win = from_bounds(*ring.bounds, src.transform)
            img = src.read([1, 2, 3], window=win, boundless=True).astype(float)
            if img.max() > 255:
                img = img / 257
            tr = src.window_transform(win)
            near = geometry_mask([ring], out_shape=img.shape[1:], transform=tr, invert=True)
            inside = geometry_mask([g], out_shape=img.shape[1:], transform=tr, invert=True)
            px = img[:, near]
            nodata = float((px.sum(0) == 0).mean()) if px.size else 1.0
            pin = img[:, inside]
            r, gr, b = pin.mean(1) if pin.size else (0, 0, 0)
            spread = float(pin.std(1).mean()) if pin.size else 0.0
            reason = None
            if nodata > NODATA_MAX:
                reason = "edge of the photo"
            elif score < KEEP_SCORE_MIN:
                reason = "finder not sure"
            elif solidity < RAGGED_SOLIDITY_MAX:
                reason = "ragged shape"
            elif b - max(r, gr) >= BLUE_MARGIN:
                reason = "water (pool)"
            elif gr - max(r, b) >= LAWN_MARGIN and (r + gr + b) / 3 >= LAWN_BRIGHT:
                reason = "lawn"
            elif gr - max(r, b) >= GREEN_MARGIN and (score < GREEN_SCORE_MIN or solidity < GREEN_SOLIDITY_MIN):
                reason = "vegetation"
            elif spread < FLAT_STD_MAX and score < FLAT_SCORE_MIN:
                reason = "flat (water or bare ground)"
            f["properties"].update({"solidity": round(solidity, 2), "photo_spread": round(spread, 1)})
            if reason:
                f["properties"]["rejected"] = reason
                rejects.append(f)
                why[reason] = why.get(reason, 0) + 1
            else:
                keep.append(f)
    out = dict(doc, features=keep)
    (d / "building_outlines_detected.geojson").write_text(json.dumps(out))
    (d / "building_outlines_detected_rejected.geojson").write_text(json.dumps(dict(doc, features=rejects)))
    print(f"[{region}] screened {len(doc['features'])} candidates: {len(keep)} kept, "
          f"{len(rejects)} rejected {why}")
    return out


def apply(region):
    d = REGIONS_DIR / region
    raw = d / "building_outlines.geojson"
    linz = d / "building_outlines.linz.geojson"
    if not linz.exists():
        linz.write_text(raw.read_text())
    base = json.loads(linz.read_text())
    det = json.loads((d / "building_outlines_detected.geojson").read_text())
    base["features"] = base["features"] + det["features"]
    raw.write_text(json.dumps(base))
    print(f"[{region}] outlines: {len(base['features']) - len(det['features'])} LINZ + {len(det['features'])} detected")


def main():
    import torch
    ap = argparse.ArgumentParser()
    ap.add_argument("regions", nargs="+")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--threads", type=int, default=14)
    ap.add_argument("--screen-only", action="store_true",
                    help="re-screen the existing candidates without re-running the finder")
    ap.add_argument("--recut", action="store_true",
                    help="re-cut outlines from the saved probability map, then screen")
    a = ap.parse_args()
    torch.set_num_threads(a.threads)
    model = None if (a.screen_only or a.recut) else ps.load(MODEL, out_ch=1, in_ch=3)
    for r in a.regions:
        if a.recut:
            detect(r, None, recut=True)
        elif not a.screen_only:
            detect(r, model)
        screen(r)
        if a.apply:
            apply(r)


if __name__ == "__main__":
    main()
