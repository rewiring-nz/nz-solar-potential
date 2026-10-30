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
photo, so a neighbour's lean is a good estimate of this building's. Before
borrowing, a weak match is measured twice more: over a wider window, then
with only the roof voting (ROOF_LOOK).

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
# A SECOND LOOK, WIDER. The neighbour check above overrode correct answers: a
# taller roof leans further than the houses round it, so its own sharp match
# "disagreed" and was replaced -- 13 Douglas Avenue (4746298) matched 1.6 m
# north at sharpness 8.2, steady at every window size, and shipped its
# neighbours' 0.8 m, leaving the panels a metre off the roof Josh sees. So a
# match that is out of line with its neighbours, or too weak to trust, is
# measured again over a WIDE_PAD_M window. Agreeing with itself within
# ROBUST_M at a trusted sharpness makes it robust: kept whatever the
# neighbours say. On 300 Arrowtown roofs the wide window reproduced trusted
# matches within 0.5 m 97% of the time and made 24% of weak ones trustworthy.
WIDE_PAD_M = 12.0
ROBUST_M = 0.3
# ...and never far: rendered on the photo, 10 of the 12 biggest second-look
# moves in Arrowtown sat visibly better, but one jumped 3.9 m onto the next
# roof. A second look further than this from its neighbours is not believed.
SECOND_LOOK_MAX_M = 2.5
# ...AND THEN THE ROOF ALONE. Both looks correlate the whole window, street
# and neighbours included, and the ground does not lean: on a big roof that
# repeats -- parallel ridges a few metres apart -- the street's vote and the
# roof's period put the peak one period short. 24 Beach St (1,688 m2, ridges
# ~4 m apart) matched 2.9 m at sharpness 4.6, borrowed its neighbours' 2.1 m,
# and the map drew every panel row over a ridge and every vent a metre and a
# half off. Weighting the survey photo's edges by the roof outline
# (grown ROOF_GROW_M) lets only the roof vote: 3.6 m at 5.3, with the vents
# on the vents. It is the last look before borrowing, not the first: on 60
# trusted roofs it agreed within 0.3 m on 52, and the misses were small roofs
# (37-218 m2) with too little inside the outline to match. On the pilot's
# 169 borrowed roofs it is trusted on 61; the eight with the biggest moves
# (32 Frankton Rd, 4735242, 4735613 ...) all sat on their roofs better,
# rendered on the map's photo. Same SHARP_MIN, never further than
# SECOND_LOOK_MAX_M from the neighbours. SOLAR_ROOF_LOOK=0 turns it off.
ROOF_GROW_M = 0.5
ROOF_LOOK = os.environ.get("SOLAR_ROOF_LOOK", "1") != "0"
# CHANGED SINCE THE SURVEY. The LiDAR and the survey-year photo are 2021; the
# map's photo is 2026. Where a building was rebuilt in between, every layout
# stage worked on a roof that is gone -- 35 Brecon St was a bare deck under
# construction in 2021 and has rooftop plant now, which the panels cover
# (Josh, 21 Oct). Inside the roof outline the two photos are compared: a
# brightness change of more than CHANGE_LUM and an edge structure that no
# longer matches (gradient correlation under CHANGE_STRUCT) flags it. On the
# CBD the top ten flagged were all rebuilt or demolished; in Arrowtown about
# 8 of 12 sampled were, the rest re-roofed in a new colour -- so the flag
# writes a note on the building, it does not withhold the layout.
CHANGE_LUM = 60.0
CHANGE_STRUCT = 0.25
CHANGE_ERODE_M = 0.8
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


def measure(geom, reference_ds, tiles, roof_only=False):
    """(dx_east, dy_north, sharpness) of the map photo against the reference, or None.
    roof_only: only the survey photo's edges inside the outline vote (ROOF_GROW_M)."""
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
    if roof_only:
        from rasterio.features import rasterize
        from rasterio.transform import from_origin
        A = A * rasterize([(geom.buffer(ROOF_GROW_M), 1)], out_shape=(h, w),
                          transform=from_origin(b[0], b[3], RES_M, RES_M))
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


def roof_change(geom, reference_ds, tiles, shift):
    """(brightness change, edge correlation) inside the roof outline between
    the survey-year photo and the map's photo, or None."""
    from rasterio.features import geometry_mask
    from rasterio.transform import from_origin
    from rasterio.windows import from_bounds
    inner = geom.buffer(-CHANGE_ERODE_M)
    if inner.is_empty:
        return None
    b = geom.bounds
    h, w = int((b[3] - b[1]) / RES_M), int((b[2] - b[0]) / RES_M)
    if h < 20 or w < 20:
        return None
    try:
        A = reference_ds.read([1, 2, 3], window=from_bounds(*b, reference_ds.transform), out_shape=(3, h, w))
        dx, dy = shift[0], shift[1]
        Bm = tiles.window((b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy), h, w)
    except Exception:
        return None
    m = geometry_mask([inner], out_shape=(h, w), transform=from_origin(b[0], b[3], RES_M, RES_M), invert=True)
    if m.sum() < 100:
        return None
    la, lb = A.astype(float).mean(axis=0)[m], Bm.astype(float).mean(axis=0)[m]
    ga, gb = _gradient(A)[m], _gradient(Bm)[m]
    if ga.std() < 1e-6 or gb.std() < 1e-6:
        return None
    return float(abs(la.mean() - lb.mean())), float(np.corrcoef(ga, gb)[0, 1])


def reconcile(raw, second_look=None, roof_look=None):
    """raw: {bid: (x, y, dx, dy, sharp)} -> {bid: [dx, dy, quality]} with weak or
    outlying matches replaced by their trusted neighbours' median.
    second_look(bid) -> (dx, dy, sharp) or None: the wide-window measurement,
    asked only of matches that would otherwise be replaced (see WIDE_PAD_M).
    roof_look(bid), the same with only the roof voting (ROOF_GROW_M), is asked
    of a weak match the second look did not settle, before borrowing."""
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
        wide = None
        if second_look is not None and not (trusted[i] and (med is None or np.hypot(*(V[i] - med)) <= AGREE_MAX_M)):
            wide = second_look(b)
        robust = (wide is not None and wide[2] >= SHARP_MIN
                  and (not trusted[i] or np.hypot(V[i][0] - wide[0], V[i][1] - wide[1]) <= ROBUST_M)
                  and (med is None or np.hypot(wide[0] - med[0], wide[1] - med[1]) <= SECOND_LOOK_MAX_M))
        if trusted[i] and (med is None or np.hypot(*(V[i] - med)) <= AGREE_MAX_M):
            dx, dy, q = V[i][0], V[i][1], raw[b][4]
            STATS["trusted"] += 1
        elif robust:
            dx, dy, q = (V[i][0], V[i][1], raw[b][4]) if trusted[i] else (wide[0], wide[1], wide[2])
            STATS["second_look"] = STATS.get("second_look", 0) + 1
        elif (not trusted[i] and roof_look is not None
              and (roof := roof_look(b)) is not None and roof[2] >= SHARP_MIN
              and (med is None or np.hypot(roof[0] - med[0], roof[1] - med[1]) <= SECOND_LOOK_MAX_M)):
            dx, dy, q = roof
            STATS["roof_look"] = STATS.get("roof_look", 0) + 1
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
    raw, geoms = {}, {}
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
        geoms[int(row.building_id)] = g

    def second_look(bid):
        global PAD_M
        keep = PAD_M
        try:
            PAD_M = WIDE_PAD_M
            return measure(geoms[bid], ref_ds, tiles)
        finally:
            PAD_M = keep
    def roof_look(bid):
        return measure(geoms[bid], ref_ds, tiles, roof_only=True) if ROOF_LOOK else None
    out = reconcile(raw, second_look, roof_look)
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
    # roofs whose photo has changed since the survey (see CHANGE_LUM)
    changed = {}
    for bid, g in geoms.items():
        if bid >= 900_000_000:          # read from the current photo already
            continue
        s = out.get(str(bid)) or markup.get(str(bid)) or [0.0, 0.0, 0.0]
        rc = roof_change(g, ref_ds, tiles, s)
        if rc and rc[0] > CHANGE_LUM and rc[1] < CHANGE_STRUCT:
            changed[str(bid)] = [round(rc[0]), round(rc[1], 2)]
    write_json_atomic(paths["dir"] / "image_change.json", changed)
    print(f"[{region}] {len(changed)} roofs look changed since the survey photo", flush=True)
    mags = [math.hypot(v[0], v[1]) for v in out.values()]
    print(f"[{region}] image lean: {len(out)}/{len(gdf)} buildings shifted "
          f"(median {np.median(mags) if mags else 0:.2f} m, p90 {np.percentile(mags, 90) if mags else 0:.2f} m; "
          f"{STATS['trusted']} own, {STATS.get('second_look', 0)} on a second look, "
          f"{STATS.get('roof_look', 0)} on the roof alone, "
          f"{STATS['borrowed']} from neighbours) in {time.time() - t0:.0f}s")
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
