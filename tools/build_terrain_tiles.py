"""Terrain-RGB tiles from the DSM, so the map can be viewed in 3D.

Josh, 19 Sep: "Is there an ability to add a 3D option to the map? So you can
see a 3D view of terrain, trees, and the household?"

All three at once, from one surface. The DSM is the top of everything the
LiDAR hit -- ground where there is ground, tree canopy where there are
trees, roof where there is a roof -- so encoding IT rather than the bare
earth gives terrain, trees and buildings in a single layer. It is also
exactly the surface the shading model reads, so the 3D view shows what the
estimates were computed from rather than a decorative extrusion beside it.

Output is the Mapbox terrain-RGB encoding MapLibre expects:
    height = -10000 + (R*65536 + G*256 + B) * 0.1
written as an XYZ pyramid of PNGs under data/terrain/.

No GDAL command line and no rio-rgbify on this machine, so the reprojection
is done through rasterio's WarpedVRT, which reads the NZTM raster directly
into web-mercator tiles.

Usage:
    python tools/build_terrain_tiles.py                 # every region
    python tools/build_terrain_tiles.py pilot --max-zoom 17
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import from_bounds, Window
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "terrain"
TILE = 256
R_EARTH = 6378137.0
ORIGIN = math.pi * R_EARTH          # 20037508.342789244


def lonlat_to_merc(lon, lat):
    x = R_EARTH * math.radians(lon)
    y = R_EARTH * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y


def tile_bounds(z, x, y):
    n = 2 ** z
    span = 2 * ORIGIN / n
    return (-ORIGIN + x * span, ORIGIN - (y + 1) * span,
            -ORIGIN + (x + 1) * span, ORIGIN - y * span)


def merc_to_tile(z, mx, my):
    n = 2 ** z
    span = 2 * ORIGIN / n
    return int((mx + ORIGIN) / span), int((ORIGIN - my) / span)


def _overlaps(win, vrt):
    return not (win.col_off + win.width <= 0 or win.row_off + win.height <= 0
                or win.col_off >= vrt.width or win.row_off >= vrt.height)


def encode(h):
    """Mapbox terrain-RGB. NaN becomes the encoding's own zero, which reads
    as -10000 m; MapLibre never shows it because the tile is clipped to the
    data extent, and a nodata hole is better than a spike."""
    v = np.where(np.isfinite(h), (h + 10000.0) * 10.0, 0.0)
    v = np.clip(np.rint(v), 0, 256 ** 3 - 1).astype(np.uint32)
    rgb = np.empty(v.shape + (3,), np.uint8)
    rgb[..., 0] = (v >> 16) & 255
    rgb[..., 1] = (v >> 8) & 255
    rgb[..., 2] = v & 255
    return rgb


def build(region, min_z, max_z, paths):
    src_path = paths["dsm"]
    if not Path(src_path).exists():
        print(f"{region}: no DSM"); return 0
    written = 0
    with rasterio.open(src_path) as src:
        with WarpedVRT(src, crs="EPSG:3857",
                       resampling=Resampling.bilinear,
                       src_nodata=src.nodata, nodata=np.nan,
                       dtype="float32") as vrt:
            left, bottom, right, top = vrt.bounds
            for z in range(min_z, max_z + 1):
                x0, y0 = merc_to_tile(z, left, top)
                x1, y1 = merc_to_tile(z, right, bottom)
                for x in range(x0, x1 + 1):
                    for y in range(y0, y1 + 1):
                        b = tile_bounds(z, x, y)
                        # A WarpedVRT refuses boundless reads, and a tile at
                        # the edge of the survey always overhangs it. So the
                        # window is clipped to the VRT and the result is
                        # pasted into a NaN tile at the right offset --
                        # which is what boundless would have done.
                        win = from_bounds(*b, transform=vrt.transform)
                        clipped = win.intersection(
                            Window(0, 0, vrt.width, vrt.height)) \
                            if _overlaps(win, vrt) else None
                        if clipped is None or clipped.width < 1 or clipped.height < 1:
                            continue
                        sx = TILE / win.width
                        sy = TILE / win.height
                        ow = max(1, int(round(clipped.width * sx)))
                        oh = max(1, int(round(clipped.height * sy)))
                        try:
                            part = vrt.read(1, window=clipped,
                                            out_shape=(oh, ow),
                                            resampling=Resampling.bilinear)
                        except Exception as exc:
                            print(f"    z{z}/{x}/{y}: {exc!r}", flush=True)
                            continue
                        a = np.full((TILE, TILE), np.nan, "float32")
                        ox = int(round((clipped.col_off - win.col_off) * sx))
                        oy = int(round((clipped.row_off - win.row_off) * sy))
                        ox = max(0, min(TILE - 1, ox))
                        oy = max(0, min(TILE - 1, oy))
                        ow = min(ow, TILE - ox)
                        oh = min(oh, TILE - oy)
                        if ow < 1 or oh < 1:
                            continue
                        a[oy:oy + oh, ox:ox + ow] = part[:oh, :ow]
                        if not np.isfinite(a).any():
                            continue
                        d = OUT / str(z) / str(x)
                        d.mkdir(parents=True, exist_ok=True)
                        Image.fromarray(encode(a)).save(
                            d / f"{y}.png", optimize=True)
                        written += 1
    print(f"{region}: {written} tiles z{min_z}-{max_z}", flush=True)
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("regions", nargs="*")
    ap.add_argument("--min-zoom", type=int, default=11)
    # 1 m data is fully resolved by about z17 at this latitude (0.84 m/px);
    # asking for more just interpolates and multiplies the file count by four.
    ap.add_argument("--max-zoom", type=int, default=17)
    a = ap.parse_args()
    from src.region_build import area_paths
    d = ROOT / "data/regions"
    regions = a.regions or sorted(p.name for p in d.iterdir() if p.is_dir())
    OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    bounds = {}
    for r in regions:
        try:
            paths = area_paths(r)
        except Exception:
            continue
        total += build(r, a.min_zoom, a.max_zoom, paths)
    (OUT / "meta.json").write_text(json.dumps(
        {"encoding": "mapbox", "tileSize": TILE,
         "minzoom": a.min_zoom, "maxzoom": a.max_zoom,
         "source": "LINZ 1 m DSM (surface: ground, trees and roofs)"}, indent=1))
    print(f"TOTAL {total} terrain tiles -> {OUT}")


if __name__ == "__main__":
    sys.exit(main() or 0)
