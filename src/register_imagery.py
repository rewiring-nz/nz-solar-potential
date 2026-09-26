"""How far each roof leans in the map's photo, so the drawing can follow it.

WHAT IS MISALIGNED. The LiDAR, the LINZ outlines and Josh's markup agree on
where each roof is. The photo the live map shows (LINZ Basemaps' aerial,
the 2026 capture over Queenstown) is orthorectified to the ground, not the
rooftops, so every roof leans away from the camera by an amount that grows
with its height and its distance from where the photo was taken. On 32
Frankton Road and 10 Stanley Street the drawings sat 1.3-1.5 m off the roof
people see. Nothing computed is wrong; the picture is.

HOW IT IS MEASURED. Not against the LiDAR: a height grid and a photo are
different instruments, and matching their edges locked onto trees and
shadows (neighbouring roofs agreed on direction 38-49% of the time, against
33% for chance). Instead, against a PHOTO THAT SITS ON THE LIDAR -- the
survey's own-year capture (config SURVEYS `reference_imagery_layer`; 2021
for the 2021 Queenstown LiDAR). Photo against photo is the same instrument:
on 150 town-centre buildings the lean was a median 0.63 m (p90 1.67 m) and
89% of neighbouring pairs leaned the same way.

Each building's gradient image in the reference photo is cross-correlated
with the same window of the map's photo (the tiles themselves, fetched once
per region). A match whose correlation peak is sharp is trusted; measured on
that sample, sharpness >= SHARP_MIN put 87-95% of buildings within 0.75 m of
their neighbours' median, below it only 55%. A building whose own match is
weak, or disagrees with its trusted neighbours by more than
AGREE_MAX_M, takes their median instead: lean varies smoothly across a
photo, so a neighbour's lean is a good estimate of this building's.

emit_region moves the DRAWN geometry (outline, faces, panels, obstructions)
and build_heatmap_raster moves each building's heat-map pixels by the shift;
build_markup_lines moves the markup overlay. Every number stays where the
LiDAR computed it. Regions whose survey has no own-year photo (Kingston) get
no shift.

Writes data/regions/<r>/image_shift.json: {building_id: [dx_east_m, dy_north_m, quality]}
with quality = the peak sharpness, or 0 when the shift was borrowed.

Usage: python src/register_imagery.py <region>
"""

import json
import os
import math
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.region_build import area_paths, write_json_atomic

RES_M = 0.1              # comparison grid
PAD_M = 4.0              # context around each footprint
SEARCH_M = 4.0           # largest lean looked for, each way
SHARP_MIN = 5.0          # correlation peak / mean |correlation| to trust a match
AGREE_MAX_M = 0.75       # a trusted match further than this from its neighbours' median is replaced
NEIGHBOUR_M = 150.0
NEIGHBOUR_K = 8
MIN_NEIGHBOURS = 2
MIN_AREA_M2 = 20.0
TILE_Z = 20
STATS = {"measured": 0, "trusted": 0, "borrowed": 0}


class _Tiles:
    """The map's own photo: LINZ Basemaps tiles at TILE_Z, cached on disk."""

    def __init__(self, cache_dir):
        import config
        self.key = config.LINZ_BASEMAPS_KEY
        self.layer = config.LINZ_BASEMAPS_LAYER
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.mem = {}

    def tile(self, tx, ty):
        from PIL import Image
        k = (tx, ty)
        if k in self.mem:
            return self.mem[k]
        f = self.dir / f"{TILE_Z}_{tx}_{ty}.webp"
        if not f.exists():
            url = (f"https://basemaps.linz.govt.nz/v1/tiles/{self.layer}/WebMercatorQuad/"
                   f"{TILE_Z}/{tx}/{ty}.webp?api={self.key}")
            for attempt in range(3):
                try:
                    data = urllib.request.urlopen(urllib.request.Request(
                        url, headers={"User-Agent": "rewiring-solar-map"}), timeout=30).read()
                    f.write_bytes(data)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(2 * (attempt + 1))
        arr = np.array(Image.open(f).convert("RGB"))
        if len(self.mem) > 4000:
            self.mem.clear()
        self.mem[k] = arr
        return arr

    def window(self, bounds_2193, height, width):
        """The map's photo resampled onto a NZTM grid at RES_M."""
        import pyproj
        from rasterio.transform import from_origin, from_bounds
        from rasterio.warp import reproject, Resampling
        to84 = pyproj.Transformer.from_crs(2193, 4326, always_xy=True).transform
        lo0, la0 = to84(bounds_2193[0], bounds_2193[1])
        lo1, la1 = to84(bounds_2193[2], bounds_2193[3])
        x0, y1 = _tile_xy(lo0, la0)
        x1, y0 = _tile_xy(lo1, la1)
        xs = range(int(x0), int(x1) + 1)
        ys = range(int(y0), int(y1) + 1)
        a = np.concatenate([np.concatenate([self.tile(tx, ty) for tx in xs], axis=1)
                            for ty in ys], axis=0).transpose(2, 0, 1)
        R = 6378137.0
        n = 2 ** TILE_Z
        mx = lambda t: t / n * 2 * math.pi * R - math.pi * R
        my = lambda t: math.pi * R - t / n * 2 * math.pi * R
        src_t = from_bounds(mx(xs[0]), my(ys[-1] + 1), mx(xs[-1] + 1), my(ys[0]), a.shape[2], a.shape[1])
        out = np.zeros((3, height, width), dtype=np.uint8)
        dst_t = from_origin(bounds_2193[0], bounds_2193[3], RES_M, RES_M)
        for k in range(3):
            reproject(a[k], out[k], src_transform=src_t, src_crs="EPSG:3857",
                      dst_transform=dst_t, dst_crs="EPSG:2193", resampling=Resampling.bilinear)
        return out


def _tile_xy(lon, lat):
    n = 2 ** TILE_Z
    return ((lon + 180) / 360 * n,
            (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)


def _gradient(rgb):
    from scipy import ndimage
    lum = ndimage.gaussian_filter(rgb.astype(float).mean(axis=0), 1.0)
    g = np.hypot(ndimage.sobel(lum, 0), ndimage.sobel(lum, 1))
    return (g - g.mean()) / (g.std() + 1e-9)


def measure(geom, reference_ds, tiles):
    """(dx_east, dy_north, sharpness) of the map photo against the reference, or None."""
    from rasterio.windows import from_bounds
    b = geom.buffer(PAD_M).bounds
    h = int((b[3] - b[1]) / RES_M)
    w = int((b[2] - b[0]) / RES_M)
    s = int(round(SEARCH_M / RES_M))
    if h <= 2 * s + 4 or w <= 2 * s + 4:
        return None
    try:
        ref = reference_ds.read([1, 2, 3], window=from_bounds(*b, reference_ds.transform), out_shape=(3, h, w))
        disp = tiles.window(b, h, w)
    except Exception:
        return None
    if ref.std() < 2 or disp.std() < 2:
        return None
    A, B = _gradient(ref), _gradient(disp)
    cc = np.fft.fftshift(np.real(np.fft.ifft2(np.fft.fft2(A) * np.conj(np.fft.fft2(B)))))
    cy, cx = h // 2, w // 2
    win = cc[cy - s:cy + s + 1, cx - s:cx + s + 1]
    i = np.unravel_index(int(np.argmax(win)), win.shape)
    sharp = float(win[i] / (np.abs(win).mean() + 1e-9))
    dr, dc = i[0] - s, i[1] - s
    # Rolling the map photo by (dr, dc) pixels lines it up with the reference,
    # so the map photo shows the roof -dc pixels east and +dr pixels north of
    # where the reference (and the LiDAR) has it. The drawing moves there.
    return -dc * RES_M, dr * RES_M, sharp


def reconcile(raw):
    """raw: {bid: (x, y, dx, dy, sharp)} -> {bid: [dx, dy, quality]} with weak or
    outlying matches replaced by their trusted neighbours' median."""
    from scipy.spatial import cKDTree
    bids = list(raw)
    if not bids:
        return {}
    P = np.array([[raw[b][0], raw[b][1]] for b in bids])
    V = np.array([[raw[b][2], raw[b][3]] for b in bids])
    trusted = np.array([raw[b][4] >= SHARP_MIN for b in bids])
    out = {}
    if trusted.sum() == 0:
        return out
    tidx = np.nonzero(trusted)[0]
    tree = cKDTree(P[tidx])
    for i, b in enumerate(bids):
        d, j = tree.query(P[i], k=min(NEIGHBOUR_K + 1, len(tidx)))
        d, j = np.atleast_1d(d), np.atleast_1d(j)
        nb = [tidx[k] for k, dd in zip(j, d) if dd <= NEIGHBOUR_M and tidx[k] != i]
        med = np.median(V[nb], axis=0) if len(nb) >= MIN_NEIGHBOURS else None
        if trusted[i] and (med is None or np.hypot(*(V[i] - med)) <= AGREE_MAX_M):
            dx, dy, q = V[i][0], V[i][1], raw[b][4]
            STATS["trusted"] += 1
        elif med is not None:
            dx, dy, q = med[0], med[1], 0.0
            STATS["borrowed"] += 1
        else:
            continue
        if abs(dx) < 0.05 and abs(dy) < 0.05:
            continue
        out[str(b)] = [round(float(dx), 2), round(float(dy), 2), round(float(q), 1)]
    return out


def markup_in_lidar_frame():
    """True when markup traced on the map's photo is moved onto the LiDAR as
    it is read. Off until measured (BACKLOG, Imagery)."""
    return os.environ.get("SOLAR_MARKUP_FRAME", "map") == "lidar"


def drawing_shifts(region_dir):
    """{building_id: [dx, dy, q]} the drawing of a region moves by: every
    measured lean, plus -- when markup is read in the LiDAR frame -- the
    lean of the roofs whose markup was traced on the map's photo."""
    region_dir = Path(region_dir)
    if os.environ.get("SOLAR_IMAGE_SHIFT", "1") == "0":
        return {}
    out = {}
    for name, use in (("image_shift.json", True), ("markup_shift.json", markup_in_lidar_frame())):
        f = region_dir / name
        if use and f.exists():
            try:
                out.update(json.loads(f.read_text()))
            except Exception:
                pass
    return out


def markup_shifts(data_dir):
    """Every region's markup_shift.json, merged: the lean to take OFF markup
    traced on the map's photo."""
    out = {}
    for f in Path(data_dir).glob("regions/*/markup_shift.json"):
        try:
            out.update(json.loads(f.read_text()))
        except Exception:
            pass
    return out


def _labelled_ids():
    f = Path(__file__).resolve().parents[1] / "data" / "roof_labels.json"
    try:
        B = json.loads(f.read_text())["buildings"]
    except Exception:
        return set()
    return {int(b) for b, v in B.items() if v.get("faces") or v.get("lines") or v.get("obstructions")}


def register(region):
    import rasterio
    import geopandas as gpd
    from src.surveys import survey_for
    from src.region_build import area_bbox_wgs84
    paths = area_paths(region)
    out_path = paths["dir"] / "image_shift.json"
    try:
        sv = survey_for(area_bbox_wgs84(region), region)
    except Exception:
        sv = {}
    ref_layer = sv.get("reference_imagery_layer")
    if not ref_layer:
        print(f"[{region}] survey has no own-year photo -- no image shifts")
        write_json_atomic(out_path, {})
        return {}
    ref_path = paths["reference_imagery"] if ref_layer != sv.get("imagery_layer") else paths["imagery"]
    if not ref_path.exists():
        print(f"[{region}] no reference photo at {ref_path.name} -- no image shifts")
        write_json_atomic(out_path, {})
        return {}
    t0 = time.time()
    dd = paths["dir"] / "building_outlines_dedup.geojson"
    gdf = gpd.read_file(dd if dd.exists() else paths["outlines"]).to_crs("EPSG:2193")
    ref_ds = rasterio.open(ref_path)
    tiles = _Tiles(paths["dir"] / "display_tiles")
    raw = {}
    for row in gdf.itertuples():
        g = row.geometry
        if g is None or g.is_empty or g.area < MIN_AREA_M2:
            continue
        m = measure(g, ref_ds, tiles)
        if m is None:
            continue
        STATS["measured"] += 1
        c = g.centroid
        raw[int(row.building_id)] = (c.x, c.y, m[0], m[1], m[2])
    out = reconcile(raw)
    # MARKUP DRAWN ON THE LEANING PHOTO ALREADY SITS WHERE THE MAP SHOWS THE
    # ROOF. The labelling tool shows the region's pipeline photo; where that
    # is not the reference (every Queenstown region but pilot, 2026 vs 2021),
    # a labelled roof's faces were traced on the leaned roof and shifting
    # them would double the lean. Those roofs keep their drawn position.
    # markup_shift.json keeps their lean, so SOLAR_MARKUP_FRAME=lidar can move
    # that markup back onto the LiDAR when it is read (roof_line_source) and
    # the drawing forward again with everything else (drawing_shifts).
    markup = {}
    if ref_path.resolve() != paths["imagery"].resolve():
        labelled = _labelled_ids()
        for b in [b for b in out if int(b) in labelled]:
            markup[b] = out.pop(b)
        if markup:
            print(f"[{region}] {len(markup)} labelled roofs left unshifted (drawn on the map's photo)")
    write_json_atomic(paths["dir"] / "markup_shift.json", markup)
    write_json_atomic(out_path, out)
    mags = [math.hypot(v[0], v[1]) for v in out.values()]
    print(f"[{region}] image lean: {len(out)}/{len(gdf)} buildings shifted "
          f"(median {np.median(mags) if mags else 0:.2f} m, p90 {np.percentile(mags, 90) if mags else 0:.2f} m; "
          f"{STATS['trusted']} own, {STATS['borrowed']} from neighbours) in {time.time() - t0:.0f}s")
    return out


def main():
    region = sys.argv[1]
    from src.preflight import preflight
    preflight("register_imagery", region)
    # A failure here only means the drawing stays where the LiDAR is; it
    # must never stop a build.
    try:
        register(region)
    except Exception as exc:
        print(f"[{region}] WARNING: image registration failed ({exc!r}) -- drawing unshifted")
        write_json_atomic(area_paths(region)["dir"] / "image_shift.json", {})
    return 0


if __name__ == "__main__":
    sys.exit(main())
