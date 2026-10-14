"""Training pairs for reading roof shape from a photo: LiDAR roofs as the answers.

    python tools/photo_shape_dataset.py fetch                 # the 0.3 m photo for every LiDAR region
    python tools/photo_shape_dataset.py export [--regions a b] [--out data/photo_shape]

WHY. Towns with no LiDAR (Glenorchy, Kinloch, Cromwell) and buildings newer
than their LiDAR (Arrowtown) are drawn from footprint size alone: hip roofs at
20 degrees (src/synthesize_dsm.py). 128,000 LiDAR roof faces say what roofs of
each look actually are; this pairs each of those roofs with its photo so a
model can learn the look-to-shape mapping (tools/train_photo_shape.py).

THE PHOTO. LINZ layer 104460, Otago 0.3m Rural Aerial Photos (2017-2019), is
the only photo of Glenorchy and Kinloch, and it also covers every Queenstown,
Arrowtown, Frankton, Wanaka and Hawea region. Training on that same photo
removes the gap between the photos a model learns from and the photos it is
used on. The 2026 map photo (imagery_mosaic.tif, 0.1 m) is exported as a
second view of every roof, for buildings newer than their LiDAR, which only
the 2026 photo shows.

THE ANSWERS. Per pixel inside the footprint, from the LiDAR facets in the
region's panel_layouts.geojson: slope (degrees) and the downslope direction
as a unit vector; pixels no facet covers are left unlabelled. Roofs are kept
only where the facets are trustworthy (mean roof_confidence >= 0.8, facets
over >= 70% of the footprint). Slopes are capped at MAX_SLOPE_DEG: 99.5% of
LiDAR roof area is at or under 45 degrees, and the rest is mostly walls and
fitting artefacts.

ONE SAMPLE. North-up (direction matters for sunshine), centred on the
footprint, side max(SIDE_MIN_M, 1.15 x the footprint's extent) resampled to
SIDE_PX: 0.3 m per pixel for a house, coarser for a big building, recorded as
m_per_px. Stored in shards of uint8 photo + mask and float16 answers.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

REGIONS_DIR = ROOT / "data" / "regions"
PHOTO_NAME = "photo03"          # data/regions/<r>/photo03_mosaic.vrt
SIDE_PX = 96
SIDE_MIN_M = SIDE_PX * 0.3
MAX_SLOPE_DEG = 45.0
FLAT_DEG = 5.0
MIN_CONFIDENCE = 0.8
MIN_COVER = 0.7
SHARD = 2000


def lidar_regions():
    """Regions built from LiDAR (not the synthetic no-LiDAR surveys)."""
    from src.surveys import survey_for
    out = []
    for name, bbox in sorted(config.REGIONS.items()):
        try:
            sv = survey_for(bbox, name)
        except LookupError:
            continue
        if sv.get("elevation") == "synthetic":
            continue
        if (REGIONS_DIR / name / "panel_layouts.geojson").exists():
            out.append(name)
    return out


S3_COLLECTION = ("https://nz-imagery.s3.ap-southeast-2.amazonaws.com/"
                 "otago/otago_2017-2019_0.3m/rgb/2193/")
TILES_DIR = ROOT / "data" / "photo03_tiles"


def _tile_index():
    """{tile id: (lon/lat bbox, tiff url)} for the 0.3 m photo, cached.

    LINZ publishes every aerial survey as open cloud-optimised GeoTIFFs on
    S3 (no API key, no export queue); this is layer 104460's copy.
    """
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor
    cache = TILES_DIR / "index.json"
    if cache.exists():
        return json.loads(cache.read_text())
    TILES_DIR.mkdir(parents=True, exist_ok=True)
    coll = json.load(urllib.request.urlopen(S3_COLLECTION + "collection.json", timeout=60))
    hrefs = [l["href"][2:] for l in coll["links"] if l["rel"] == "item"]

    def item(h):
        d = json.load(urllib.request.urlopen(S3_COLLECTION + h, timeout=60))
        return d["id"], (d["bbox"], S3_COLLECTION + d["assets"]["visual"]["href"][2:])
    with ThreadPoolExecutor(32) as ex:
        idx = dict(ex.map(item, hrefs))
    cache.write_text(json.dumps(idx))
    return idx


def cmd_fetch(regions):
    """Download the 0.3 m photo tiles under each region; a VRT per region."""
    import subprocess
    import urllib.request
    from shapely.geometry import box
    idx = _tile_index()
    for name in regions:
        rb = box(*config.REGIONS[name])
        tiles = [(tid, url) for tid, (bb, url) in idx.items() if box(*bb).intersects(rb)]
        paths = []
        for tid, url in tiles:
            p = TILES_DIR / f"{tid}.tiff"
            if not p.exists():
                tmp = p.with_suffix(".part")
                urllib.request.urlretrieve(url, tmp)
                tmp.rename(p)
            paths.append(str(p))
        if not paths:
            print(f"[{name}] no 0.3 m photo covers it")
            continue
        vrt = REGIONS_DIR / name / f"{PHOTO_NAME}_mosaic.vrt"
        subprocess.run(["gdalbuildvrt", "-q", str(vrt), *paths], check=True)
        print(f"[{name}] 0.3 m photo: {len(paths)} tiles -> {vrt.name}", flush=True)


def _facets_by_building(region):
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform
    to_nztm = Transformer.from_crs(4326, 2193, always_xy=True).transform
    fc = json.loads((REGIONS_DIR / region / "panel_layouts.geojson").read_text())
    by = {}
    for f in fc["features"]:
        p = f["properties"]
        if p.get("kind") != "facet" or p.get("building_id") is None:
            continue
        slope = p.get("plane_slope_deg", p.get("slope_deg"))
        aspect = p.get("plane_aspect_deg", p.get("aspect_deg"))
        if slope is None or aspect is None:
            continue
        g = transform(to_nztm, shape(f["geometry"]))
        if not g.is_valid:
            g = g.buffer(0)
        by.setdefault(int(p["building_id"]), []).append(
            (g, float(slope), float(aspect), float(p.get("roof_confidence") or 0.0)))
    return by


def _outlines(region):
    from shapely.geometry import shape
    d = REGIONS_DIR / region
    f = d / "building_outlines_dedup.geojson"
    if not f.exists():          # no-LiDAR towns keep only the raw outlines
        f = d / "building_outlines.geojson"
    fc = json.loads(f.read_text())
    out = {}
    for f in fc["features"]:
        p = f["properties"]
        if p.get("building_id") is None:
            continue
        g = shape(f["geometry"])
        if not g.is_valid:
            g = g.buffer(0)
        out[int(p["building_id"])] = (g, p.get("capture_source_from", ""))
    return out


def _crop(src, cx, cy, side_m):
    """RGB uint8 [3,SIDE_PX,SIDE_PX], north-up, centred on (cx, cy) in NZTM; None off the photo."""
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds
    h = side_m / 2
    win = from_bounds(cx - h, cy - h, cx + h, cy + h, src.transform)
    if (win.col_off < 0 or win.row_off < 0 or win.col_off + win.width > src.width
            or win.row_off + win.height > src.height):
        return None
    a = src.read([1, 2, 3], window=win, out_shape=(3, SIDE_PX, SIDE_PX),
                 resampling=Resampling.average)
    if a.max() == 0 or (a.sum(axis=0) == 0).mean() > 0.05:
        return None
    if a.dtype == np.uint16:
        # Newer LINZ captures (the 2025-26 Otago RGBNIR set) store 16-bit
        # colour; cast straight to uint8 they wrap into noise.
        a = a // 257
    return np.clip(a, 0, 255).astype(np.uint8)


def _rasterise(geoms_values, cx, cy, side_m, fill=0.0, dtype=np.float32):
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds as tfb
    h = side_m / 2
    t = tfb(cx - h, cy - h, cx + h, cy + h, SIDE_PX, SIDE_PX)
    if not geoms_values:
        return np.full((SIDE_PX, SIDE_PX), fill, dtype)
    return rasterize(geoms_values, out_shape=(SIDE_PX, SIDE_PX), transform=t,
                     fill=fill, dtype=dtype)


def export_region(region, out_dir, stats):
    import rasterio
    outlines = _outlines(region)
    facets = _facets_by_building(region)
    photos = {}
    for view, fname in (("p03", f"{PHOTO_NAME}_mosaic.vrt"), ("p26", "imagery_mosaic.tif")):
        p = REGIONS_DIR / region / fname
        if p.exists():
            photos[view] = rasterio.open(p)
    if not photos:
        print(f"  {region}: no photo, skipped")
        return
    rows = {k: [] for k in ("photo", "mask", "slope", "dirx", "diry", "labelled",
                            "m_per_px", "bid", "view", "region", "captured")}
    shard_i = [0]

    def flush():
        if not rows["bid"]:
            return
        path = out_dir / f"{region}_{shard_i[0]:03d}.npz"
        np.savez_compressed(
            path,
            photo=np.stack(rows["photo"]), mask=np.stack(rows["mask"]),
            slope=np.stack(rows["slope"]).astype(np.float16),
            dirx=np.stack(rows["dirx"]).astype(np.float16),
            diry=np.stack(rows["diry"]).astype(np.float16),
            labelled=np.stack(rows["labelled"]),
            m_per_px=np.array(rows["m_per_px"], np.float32),
            bid=np.array(rows["bid"], np.int64), view=np.array(rows["view"]),
            region=np.array(rows["region"]), captured=np.array(rows["captured"]))
        shard_i[0] += 1
        for v in rows.values():
            v.clear()

    for bid, (fp, captured) in outlines.items():
        fs = facets.get(bid)
        if not fs or fp.area < 15:
            stats["no_facets"] += 1
            continue
        conf = np.average([c for _, _, _, c in fs], weights=[g.area for g, _, _, _ in fs])
        covered = sum(g.intersection(fp).area for g, _, _, _ in fs) / fp.area
        if conf < MIN_CONFIDENCE or covered < MIN_COVER:
            stats["untrusted"] += 1
            continue
        minx, miny, maxx, maxy = fp.bounds
        side_m = max(SIDE_MIN_M, 1.15 * max(maxx - minx, maxy - miny))
        cx, cy = fp.centroid.x, fp.centroid.y
        mask = _rasterise([(fp, 1)], cx, cy, side_m, 0, np.uint8)
        slope_v = [(g, min(s, MAX_SLOPE_DEG)) for g, s, _, _ in fs]
        slope = _rasterise(slope_v, cx, cy, side_m, -1.0)
        a = [np.radians(asp) for _, _, asp, _ in fs]
        dirx = _rasterise([(g, float(np.sin(t))) for (g, _, _, _), t in zip(fs, a)], cx, cy, side_m)
        diry = _rasterise([(g, float(np.cos(t))) for (g, _, _, _), t in zip(fs, a)], cx, cy, side_m)
        labelled = ((slope >= 0) & (mask > 0)).astype(np.uint8)
        flat = slope < FLAT_DEG
        dirx[flat] = 0
        diry[flat] = 0
        for view, src in photos.items():
            img = _crop(src, cx, cy, side_m)
            if img is None:
                stats[f"off_photo_{view}"] += 1
                continue
            rows["photo"].append(img)
            rows["mask"].append(mask)
            rows["slope"].append(np.clip(slope, -1, MAX_SLOPE_DEG))
            rows["dirx"].append(dirx)
            rows["diry"].append(diry)
            rows["labelled"].append(labelled)
            rows["m_per_px"].append(side_m / SIDE_PX)
            rows["bid"].append(bid)
            rows["view"].append(view)
            rows["region"].append(region)
            rows["captured"].append(str(captured)[:10])
            stats[f"samples_{view}"] += 1
            if len(rows["bid"]) >= SHARD:
                flush()
    flush()
    for s in photos.values():
        s.close()


def cmd_export(regions, out_dir):
    import collections
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = collections.Counter()
    for r in regions:
        before = dict(stats)
        export_region(r, out_dir, stats)
        print(f"  {r}: " + ", ".join(f"{k} +{stats[k] - before.get(k, 0)}" for k in sorted(stats)),
              flush=True)
    (out_dir / "export_stats.json").write_text(json.dumps(dict(stats), indent=1))
    print("total:", dict(stats))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "export"])
    ap.add_argument("--regions", nargs="*")
    ap.add_argument("--out", default=str(ROOT / "data" / "photo_shape"))
    a = ap.parse_args()
    regions = a.regions or lidar_regions()
    print(f"{len(regions)} LiDAR regions: {' '.join(regions)}")
    if a.cmd == "fetch":
        cmd_fetch(regions)
    else:
        cmd_export(regions, Path(a.out))


if __name__ == "__main__":
    main()
