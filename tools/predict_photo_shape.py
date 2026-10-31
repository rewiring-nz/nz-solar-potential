"""Roof faces read from the photo, for buildings the LiDAR cannot describe.

    python tools/predict_photo_shape.py glenorchy kinloch     # every building (no-LiDAR towns)
    python tools/predict_photo_shape.py arrowtown_east        # LiDAR town: only its no_lidar buildings

Writes data/regions/<r>/photo_roofs.json:
    {"model": ..., "photo": ..., "buildings": {bid: [{"ring": [[x, y], ...], "slope": s, "aspect": a|null}]}}
rings in NZTM, slope in degrees (<= 45), aspect the downslope bearing (null =
flat). src/synthesize_dsm.py builds a no-LiDAR town's roofs from it, and the
layout stage uses it for buildings in LiDAR towns that the LiDAR shows only
as ground (newer than the survey, or in its gaps).

WHICH BUILDINGS. In a synthetic (no-LiDAR) region, all of them. In a LiDAR
region, those its last build marked no_estimate / no_lidar -- the roof is
not in the point cloud, so the photo is the only evidence.

WHICH PHOTO. The region's imagery_mosaic.tif: in Glenorchy and Kinloch that
is the 2017-19 0.3 m photo the model trained on; in a LiDAR town it is the
2026 map photo, the one that shows buildings newer than the LiDAR (the model
trained on both).
"""

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from src import photo_shape as ps  # noqa: E402
from photo_shape_dataset import SIDE_MIN_M, _crop, _outlines, _rasterise  # noqa: E402
from src.synthesize_dsm import is_synthetic  # noqa: E402

REGIONS_DIR = ROOT / "data" / "regions"
MODEL = ROOT / "data" / "models" / "photo_shape_v1.pt"
OBS_MODEL = ROOT / "data" / "models" / "photo_obstruct_v1.pt"
MIN_OBS_M2 = 0.2
# When a building is off the region's own photo (a town's urban capture often
# stops short of its edges), the newest open LINZ captures, in order.
FALLBACK_PHOTOS = [
    "https://nz-imagery.s3.ap-southeast-2.amazonaws.com/otago/otago_2025_0.2m/rgb/2193/",
    "https://nz-imagery.s3.ap-southeast-2.amazonaws.com/otago/otago_2025-2026_0.15m/rgbnir/2193/",
]


def targets(region, outlines):
    if is_synthetic(region):
        return list(outlines)
    pl = REGIONS_DIR / region / "panel_layouts.geojson"
    if not pl.exists():
        return []
    out = []
    for f in json.loads(pl.read_text())["features"]:
        p = f["properties"]
        if p.get("kind") == "no_estimate" and p.get("no_estimate_reason") == "no_lidar":
            out.append(int(p["building_id"]))
    # ...buildings found in the photo where LINZ had no outline
    # (tools/detect_new_buildings.py): the LiDAR predates them too...
    for f in json.loads((REGIONS_DIR / region / "building_outlines.geojson").read_text())["features"]:
        if f["properties"].get("source") == "photo_detected":
            out.append(int(f["properties"]["building_id"]))
    # ...and every roof read from the photo before, so a better reading
    # replaces an older one (the build no longer lists those as no_lidar).
    prev = REGIONS_DIR / region / "photo_roofs.json"
    if prev.exists():
        out += [int(b) for b in json.loads(prev.read_text()).get("buildings", {})]
    return sorted({b for b in out if b in outlines})


def _obstruction_rings(prob, thr, mask, t, fp):
    from rasterio.features import shapes
    from shapely.geometry import shape
    sel = (prob > thr) & (mask > 0)
    out = []
    for g, v in shapes(sel.astype(np.uint8), mask=sel, transform=t):
        q = shape(g).buffer(0).intersection(fp)
        if q.is_empty or q.area < MIN_OBS_M2:
            continue
        for part in getattr(q, "geoms", [q]):
            if part.geom_type == "Polygon" and part.area >= MIN_OBS_M2:
                out.append([[round(x, 2), round(y, 2)] for x, y in part.exterior.coords])
    return out


def predict_region(region, model, photo_name="imagery_mosaic.tif", obs_model=None, obs_thr=0.5):
    import rasterio
    from rasterio.transform import from_bounds
    outlines = _outlines(region)
    want = targets(region, outlines)
    photo = REGIONS_DIR / region / photo_name
    if not want or not photo.exists():
        print(f"[{region}] {len(want)} buildings to read; photo {'present' if photo.exists() else 'missing'} -- skipped")
        return None
    import config
    from src import s3_rasters
    sources = [rasterio.open(photo)]
    names = [photo_name]
    bbox = config.REGIONS.get(region)
    for i, coll in enumerate(FALLBACK_PHOTOS if bbox else []):
        v = s3_rasters.remote_vrt(coll, bbox, REGIONS_DIR / region / f"photo_fallback{i}.vrt")
        if v is not None:
            sources.append(rasterio.open(v))
            names.append(coll.split("amazonaws.com/")[-1])
    rows, used = [], {}
    try:
        for bid in want:
            fp = outlines[bid][0]
            minx, miny, maxx, maxy = fp.bounds
            side_m = max(SIDE_MIN_M, 1.15 * max(maxx - minx, maxy - miny))
            cx, cy = fp.centroid.x, fp.centroid.y
            img = None
            for name, src in zip(names, sources):
                img = _crop(src, cx, cy, side_m)
                if img is not None:
                    used[name] = used.get(name, 0) + 1
                    break
            if img is None:
                continue
            mask = _rasterise([(fp, 1)], cx, cy, side_m, 0, np.uint8)
            rows.append((bid, fp, cx, cy, side_m, img, mask))
    finally:
        for src in sources:
            src.close()
    out, obstructions, forms = {}, {}, {}
    for i0 in range(0, len(rows), 128):
        chunk = rows[i0:i0 + 128]
        imgs, masks = np.stack([r[5] for r in chunk]), np.stack([r[6] for r in chunk])
        mpp = np.array([r[4] / ps.SIDE_PX for r in chunk])
        F, X, Y, S = ps.predict(model, imgs, masks, mpp)
        P = predict_obstructions(obs_model, imgs, masks, mpp) if obs_model is not None else None
        for j, (bid, fp, cx, cy, side_m, _, mask) in enumerate(chunk):
            h = side_m / 2
            t = from_bounds(cx - h, cy - h, cx + h, cy + h, ps.SIDE_PX, ps.SIDE_PX)
            faces, form = ps.roof_faces(F[j], X[j], Y[j], S[j], mask, t, side_m / ps.SIDE_PX, fp)
            forms[form] = forms.get(form, 0) + 1
            keep = []
            for poly, slope, asp in faces:
                g = poly.intersection(fp)
                if g.is_empty or g.area < ps.MIN_FACE_M2 * 0.5:
                    continue
                if g.geom_type != "Polygon":
                    g = max(getattr(g, "geoms", [g]), key=lambda q: q.area)
                if g.geom_type != "Polygon":
                    continue
                keep.append({"ring": [[round(x, 2), round(y, 2)] for x, y in g.exterior.coords],
                             "slope": slope, "aspect": asp})
            if keep:
                out[str(bid)] = keep
            if P is not None:
                rings = _obstruction_rings(P[j], obs_thr, mask, t, fp)
                if rings:
                    obstructions[str(bid)] = rings
    doc = {"model": str(MODEL.name), "photo": photo_name, "photos_used": used, "forms": forms, "buildings": out,
           "obstruction_model": OBS_MODEL.name if obs_model is not None else None,
           "obstructions": obstructions}
    (REGIONS_DIR / region / "photo_roofs.json").write_text(json.dumps(doc))
    if not is_synthetic(region):
        # A LiDAR town: the surface stays the survey's; the faces go where the
        # layout stage reads them, on the ground the LiDAR does show.
        _write_lidar_town_faces(region, out, outlines)
    slopes = [f["slope"] for fs in out.values() for f in fs]
    print(f"[{region}] {len(out)} of {len(want)} buildings read from the photo, "
          f"{sum(len(v) for v in obstructions.values())} obstructions on {len(obstructions)}; "
          f"face slope median {np.median(slopes) if slopes else 0:.0f} deg, max {max(slopes) if slopes else 0:.0f}; "
          f"photos {used}; forms {forms}")
    return doc


def predict_obstructions(model, imgs, masks, mpp):
    """Obstruction probability per pixel, averaged over the 8 symmetries."""
    import torch
    x = torch.from_numpy(ps.make_input(imgs, masks, mpp))
    acc = 0
    with torch.no_grad():
        for k in range(8):
            p = torch.sigmoid(model(ps.dihedral_x(x, k))[:, 0])
            z = torch.zeros_like(p)
            f, _, _, _ = ps.undo_dihedral(p, z, z, z, k)
            acc = acc + f
    return (acc / 8).numpy()


def _write_lidar_town_faces(region, out, outlines):
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.windows import from_bounds
    from src.synthesize_dsm import (EAVE_FLAT_M, EAVE_HOUSE_M, FLAT_MIN_M2, _write_faces,
                                    photo_roof)
    written = 0
    dsm_path = REGIONS_DIR / region / "dsm_mosaic.tif"
    dsm = rasterio.open(dsm_path) if dsm_path.exists() else None
    # Where the survey has no surface at all (a region outside its edge), the
    # ground comes from the national 8 m DEM, as in the no-LiDAR towns.
    dem = rasterio.open(ROOT / "data" / "dem_wide_mosaic.tif")
    try:
        for bid, faces_doc in out.items():
            fp = outlines[int(bid)][0]
            vals = []
            if dsm is not None:
                win = from_bounds(*fp.bounds, dsm.transform).round_offsets().round_lengths()
                a = dsm.read(1, window=win, boundless=True, fill_value=dsm.nodata or -9999)
                m = geometry_mask([fp], out_shape=a.shape, transform=dsm.window_transform(win), invert=True)
                vals = a[m & (a > -1000)]
            if len(vals):
                base = float(np.median(vals))
            else:
                from pyproj import Transformer
                x, y = fp.centroid.x, fp.centroid.y
                if dem.crs.to_epsg() != 2193:
                    x, y = Transformer.from_crs(2193, dem.crs, always_xy=True).transform(x, y)
                base = float(next(dem.sample([(x, y)]))[0])
                if not np.isfinite(base) or base < -1000:
                    continue
            eave = EAVE_FLAT_M if fp.area >= FLAT_MIN_M2 else EAVE_HOUSE_M
            faces, planes = photo_roof(faces_doc, base, eave)
            if faces and _write_faces(int(bid), faces, planes, replace=True):
                written += 1
    finally:
        if dsm is not None:
            dsm.close()
        dem.close()
    print(f"[{region}] {written} roofs written for the layout stage")


def _check_owned(region):
    """Refuse when a building in the region's outlines is owned by no region
    yet (the dedupe has not seen it)."""
    ids = lambda f: {int(x["properties"]["building_id"]) for x in json.loads(f.read_text())["features"]}
    d = REGIONS_DIR / region
    raw, dd = d / "building_outlines.geojson", d / "building_outlines_dedup.geojson"
    if not (raw.exists() and dd.exists()):
        return
    missing = ids(raw) - ids(dd)
    if not missing:
        return
    from src.region_ids import list_regions
    for r in list_regions(REGIONS_DIR, sentinel="building_outlines_dedup.geojson"):
        other = REGIONS_DIR / r / "building_outlines_dedup.geojson"
        if other != dd:
            missing -= ids(other)
            if not missing:
                return
    import config
    missing -= set(getattr(config, "DEMOLISHED_BUILDING_IDS", set()))
    if missing:
        raise SystemExit(f"[{region}] {len(missing)} buildings in the outlines are owned by no region "
                         f"yet -- run src/region_build.py over the built regions first")


def main():
    import torch
    ap = argparse.ArgumentParser()
    ap.add_argument("regions", nargs="+")
    ap.add_argument("--model", default=str(MODEL))
    a = ap.parse_args()
    torch.set_num_threads(16)
    model = ps.load(a.model)
    obs_model, obs_thr = None, 0.5
    if OBS_MODEL.exists() and os.environ.get("SOLAR_PHOTO_OBSTRUCTIONS", "1") != "0":
        obs_model = ps.load(OBS_MODEL, out_ch=1)
        obs_thr = float(torch.load(OBS_MODEL, map_location="cpu").get("threshold", 0.5))
    for r in a.regions:
        # The buildings to read come from the deduplicated outlines. Outlines
        # changed since the last dedupe (new buildings added by
        # tools/detect_new_buildings.py --apply) are not in it yet, and
        # reading the stale file skipped all 330 detected Arrowtown buildings
        # (v50, 18 Oct). Dedupe first: src/region_build.py <built regions>.
        _check_owned(r)
        predict_region(r, model, obs_model=obs_model, obs_thr=obs_thr)


if __name__ == "__main__":
    main()
