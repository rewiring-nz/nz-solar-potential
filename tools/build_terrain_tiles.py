"""Terrain-RGB tiles from the DSM, so the map can be viewed in 3D.

A 3D option for the map: terrain, trees and the building.

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


def despike(h, m_per_px, rise_m=8.0, window_m=9.0):
    """Remove thin spikes that are not real structures.

    The DSM carries returns from things that are not surfaces -- a crane,
    a mast, a bird, a stray multipath return -- a town-centre building came
    out way too high and spiky in 3D.
    Measured there: cells sitting 45-47 m above the median of everything
    within 15 m of them.

    A median filter over a WINDOW IN METRES is the discriminator, because a
    real object is wider than its height error is tall. Inside a building or
    a tree crown the local median is the building or the crown, so the cell
    barely differs from it and nothing is touched; a pole or a noise return
    is one or two cells wide, so the median ignores it and the difference is
    large. Only cells exceeding the local median by `rise_m` are pulled back
    to it, which leaves every genuine roof and tree standing.

    Skipped where a pixel is already coarser than the window: at district
    zooms the filter would be smoothing hills, not spikes.
    """
    if m_per_px <= 0 or window_m / m_per_px < 3:
        return h
    from scipy.ndimage import median_filter
    w = int(max(3, min(15, round(window_m / m_per_px))))
    if w % 2 == 0:
        w += 1
    med = median_filter(h, size=w, mode="nearest")
    return np.where(h - med > rise_m, med, h)


OVERVIEW_MAX_Z = 14   # at and below this zoom the tiles are the district DEM alone
FEATHER_M = 30.0      # the survey's edge fades into the DEM over this distance


def _read_full(vrt, b):
    """The whole tile from one source (NaN outside it), or None."""
    win = from_bounds(*b, transform=vrt.transform)
    if not _overlaps(win, vrt):
        return None
    try:
        return vrt.read(1, window=win, out_shape=(TILE, TILE), boundless=True,
                        fill_value=np.nan, resampling=Resampling.bilinear).astype("float32")
    except Exception:
        return None


ROOF_RING_M = 2.5      # around a roof, nothing stands above its eaves
EAVE_PCT = 10         # the eave height: this percentile of the roof's plane heights


def clean_roofs(region, area_paths, tmp_dir):
    """The DSM with every modelled roof replaced by its own planes, and the
    strip around it held to the eaves. Returns the path of the cleaned
    raster, or None to use the DSM as it is.

    WHY. Panels are drawn ON this surface in 3D, and the raw DSM carries
    whatever the laser hit: tree crowns overhanging a roof, and tree walls
    right against its edge. Draped over that, rows of panels ran up into
    the trees (29 Park Street, 18 Oct). Inside each roof face the surface is
    now the face's own plane -- its tilt and direction from the layout, its
    height the median fit to the LiDAR inside it, so overhanging branches
    (a minority of the returns) do not move it. The rest of the footprint
    is capped at the roof's top; a 2.5 m strip around it, at the eaves, so
    nothing beside the roof rises over the panels. Trees further out, and
    other buildings, are left as surveyed.
    """
    p = area_paths(region)
    lay = Path(p["panel_layouts"]) if "panel_layouts" in p else None
    if not lay or not lay.exists() or not Path(p["dsm"]).exists():
        return None
    import geopandas as gpd
    from rasterio.features import rasterize
    from shapely.geometry import shape
    from shapely.ops import transform as shp_transform, unary_union
    from pyproj import Transformer
    to_nztm = Transformer.from_crs(4326, 2193, always_xy=True).transform
    faces = {}
    for f in json.loads(lay.read_text())["features"]:
        q = f["properties"]
        if q.get("kind") != "facet":
            continue
        try:
            g = shp_transform(to_nztm, shape(f["geometry"]))
        except Exception:
            continue
        faces.setdefault(int(q["building_id"]), []).append(
            (g, float(q.get("plane_slope_deg", q.get("slope_deg", 0)) or 0),
                float(q.get("plane_aspect_deg", q.get("aspect_deg", 0)) or 0)))
    if not faces:
        return None
    outl = Path(p["outlines"])
    if not outl.exists():
        outl = Path(p["dir"]) / "building_outlines.geojson"
    fp = gpd.read_file(outl).to_crs(2193)
    fp = fp[fp["building_id"].isin(list(faces))].set_index("building_id")
    with rasterio.open(p["dsm"]) as src:
        z = src.read(1).astype("float32")
        prof = src.profile
        tr, nd = src.transform, src.nodata
    if nd is not None:
        z[z == nd] = np.nan
    all_fp = list(fp.geometry)
    from shapely.strtree import STRtree
    tree = STRtree(all_fp) if all_fp else None
    done = 0
    for bid, flist in faces.items():
        if bid not in fp.index:
            continue
        foot = fp.loc[bid].geometry
        if isinstance(foot, gpd.GeoSeries):
            foot = foot.iloc[0]
        ring = foot.buffer(ROOF_RING_M)
        minx, miny, maxx, maxy = ring.bounds
        c0, r0 = ~tr * (minx, maxy)
        c1, r1 = ~tr * (maxx, miny)
        c0, r0 = max(0, int(c0)), max(0, int(r0))
        c1, r1 = min(z.shape[1], int(c1) + 1), min(z.shape[0], int(r1) + 1)
        if c1 <= c0 or r1 <= r0:
            continue
        sub = z[r0:r1, c0:c1]
        wtr = tr * tr.translation(c0, r0)
        cols, rows = np.meshgrid(np.arange(c0, c1) + 0.5, np.arange(r0, r1) + 0.5)
        X = tr.c + cols * tr.a
        Y = tr.f + rows * tr.e
        out = sub.copy()
        heights = []
        for g, slope, aspect in flist:
            m = rasterize([(g, 1)], out_shape=sub.shape, transform=wtr, fill=0, dtype="uint8").astype(bool)
            if m.sum() < 4:
                continue
            t = math.tan(math.radians(min(slope, 60)))
            gx, gy = -t * math.sin(math.radians(aspect)), -t * math.cos(math.radians(aspect))
            base = gx * X + gy * Y
            vals = sub[m] - base[m]
            vals = vals[np.isfinite(vals)]
            if vals.size < 4:
                continue
            z0 = float(np.median(vals))
            out[m] = (base + z0)[m]
            heights.append((base + z0)[m])
        if not heights:
            continue
        h = np.concatenate(heights)
        top, eave = float(np.max(h)), float(np.percentile(h, EAVE_PCT))
        fm = rasterize([(foot, 1)], out_shape=sub.shape, transform=wtr, fill=0, dtype="uint8").astype(bool)
        modelled = np.zeros_like(fm)
        for g, _, _ in flist:
            modelled |= rasterize([(g, 1)], out_shape=sub.shape, transform=wtr, fill=0, dtype="uint8").astype(bool)
        rest = fm & ~modelled
        out[rest] = np.fmin(sub[rest], top)
        # the strip around the roof, but never over another building
        others = [all_fp[i] for i in tree.query(ring)] if tree is not None else []
        others = [o for o in others if not o.equals(foot)]
        rm = rasterize([(ring, 1)], out_shape=sub.shape, transform=wtr, fill=0, dtype="uint8").astype(bool) & ~fm
        if others:
            rm &= ~rasterize([(unary_union(others), 1)], out_shape=sub.shape, transform=wtr, fill=0, dtype="uint8").astype(bool)
        out[rm] = np.fmin(sub[rm], eave)
        z[r0:r1, c0:c1] = out
        done += 1
    dst = Path(tmp_dir) / f"{region.replace('/', '__')}_dsm_roofs.tif"
    prof.update(dtype="float32", nodata=np.nan, compress="deflate", tiled=True)
    with rasterio.open(dst, "w", **prof) as d:
        d.write(z, 1)
    print(f"  [{region}] {done:,} roofs set to their planes", flush=True)
    return dst


FLAT_PAD_M = 1.0       # past the outline: the DSM's roof edge spills ~1 m over it
GROUND_RING_M = (2.0, 5.0)
GROUND_Q = 25          # as buildings3d.js GROUND_Q: skips hedges, cars, neighbours


def flatten_buildings(region, area_paths, tmp_dir):
    """The DSM with every building pressed flat to the ground beside it, for
    the 3D view's terrain UNDER its solid buildings. Returns the raster's
    path, or None.

    WHY (Josh, 27 Oct: "You have removed trees from the 3D view ... we need
    the other 3D terrain back"). The solid buildings (buildings3d.js, 21 Oct)
    replaced the draped surface model, and with it went the hills, the trees
    and their shade. The surface comes back as the ground the solids stand
    on: everything the laser hit -- hills, trees, hedges -- except the
    buildings, whose footprints (plus FLAT_PAD_M, where the DSM's roof edge
    spills over the outline) are set to the ground beside them: the
    GROUND_Q percentile of the DSM GROUND_RING_M outside the outline, other
    buildings excluded -- the same rule buildings3d.js stands each solid on,
    so a building and the ground under it agree.
    """
    p = area_paths(region)
    if not Path(p["dsm"]).exists():
        return None
    import geopandas as gpd
    from rasterio.features import rasterize
    from shapely.ops import unary_union
    from shapely.strtree import STRtree
    outl = Path(p["outlines"])
    if not outl.exists():
        outl = Path(p["dir"]) / "building_outlines.geojson"
    if not outl.exists():
        return None
    fp = [g for g in gpd.read_file(outl).to_crs(2193).geometry if g is not None and not g.is_empty]
    if not fp:
        return None
    with rasterio.open(p["dsm"]) as src:
        z = src.read(1).astype("float32")
        prof = src.profile
        tr, nd = src.transform, src.nodata
    if nd is not None:
        z[z == nd] = np.nan
    orig = z.copy()
    tree = STRtree(fp)
    done = 0
    for foot in fp:
        pad = foot.buffer(FLAT_PAD_M)
        ring = foot.buffer(GROUND_RING_M[1]).difference(foot.buffer(GROUND_RING_M[0]))
        near = [fp[i] for i in tree.query(ring)]
        near = [o for o in near if not o.equals(foot)]
        if near:
            ring = ring.difference(unary_union(near).buffer(FLAT_PAD_M))
        minx, miny, maxx, maxy = foot.buffer(GROUND_RING_M[1]).bounds
        c0, r0 = ~tr * (minx, maxy)
        c1, r1 = ~tr * (maxx, miny)
        c0, r0 = max(0, int(c0)), max(0, int(r0))
        c1, r1 = min(z.shape[1], int(c1) + 1), min(z.shape[0], int(r1) + 1)
        if c1 <= c0 or r1 <= r0 or ring.is_empty:
            continue
        wtr = tr * tr.translation(c0, r0)
        shp = (r1 - r0, c1 - c0)
        rm = rasterize([(ring, 1)], out_shape=shp, transform=wtr, fill=0, dtype="uint8").astype(bool)
        vals = orig[r0:r1, c0:c1][rm]
        vals = vals[np.isfinite(vals)]
        if vals.size < 4:
            continue
        g = float(np.percentile(vals, GROUND_Q))
        fm = rasterize([(pad, 1)], out_shape=shp, transform=wtr, fill=0, dtype="uint8").astype(bool)
        sub = z[r0:r1, c0:c1]
        sub[fm] = g
        done += 1
    dst = Path(tmp_dir) / f"{region.replace('/', '__')}_dsm_ground.tif"
    prof.update(dtype="float32", nodata=np.nan, compress="deflate", tiled=True)
    with rasterio.open(dst, "w", **prof) as d:
        d.write(z, 1)
    print(f"  [{region}] {done:,} buildings pressed to the ground", flush=True)
    return dst


def _overlaps(win, vrt):
    return not (win.col_off + win.width <= 0 or win.row_off + win.height <= 0
                or win.col_off >= vrt.width or win.row_off >= vrt.height)


def encode(h):
    """Mapbox terrain-RGB.

    NODATA MUST NOT BE A CLIFF. The first version encoded NaN as the
    encoding's zero, which decodes to -10000 m, and asserted in this
    docstring that MapLibre would never show it. It shows it: every hole and
    every tile edge became a 10 km chasm and the live map rendered two
    thirds of the view as vertical smears. Holes are filled from the nearest
    measured height instead, so a gap is flat ground at about the right
    level rather than a hole through the planet.
    """
    if not np.isfinite(h).all():
        finite = np.isfinite(h)
        if not finite.any():
            h = np.zeros_like(h)
        else:
            from scipy.ndimage import distance_transform_edt
            idx = distance_transform_edt(~finite, return_distances=False,
                                         return_indices=True)
            h = h[tuple(idx)]
    v = (h + 10000.0) * 10.0
    v = np.clip(np.rint(v), 0, 256 ** 3 - 1).astype(np.uint32)
    rgb = np.empty(v.shape + (3,), np.uint8)
    rgb[..., 0] = (v >> 16) & 255
    rgb[..., 1] = (v >> 8) & 255
    rgb[..., 2] = v & 255
    return rgb


def build_all(regions, min_z, max_z, area_paths, out_dir=OUT, clean=True, ground=False):
    """Tile by TILE, not region by region.

    Writing one region at a time meant a tile covered by two regions got
    whichever wrote last, and a tile whose neighbour came from a different
    region filled its gaps from different data -- so the two disagreed along
    their shared edge and the 3D view showed stepped cliffs at every seam.
    Here each tile gathers from EVERY region that covers it before anything
    is filled, so a gap is only ever filled when no survey has the answer.
    """
    srcs = []
    import tempfile
    tmp_dir = tempfile.mkdtemp(prefix="terrain_roofs_")
    for r in regions:
        try:
            p = area_paths(r)
        except Exception:
            continue
        if not Path(p["dsm"]).exists():
            continue
        cleaned = (flatten_buildings(r, area_paths, tmp_dir) if ground
                   else clean_roofs(r, area_paths, tmp_dir) if clean else None)
        src = rasterio.open(cleaned or p["dsm"])
        vrt = WarpedVRT(src, crs="EPSG:3857", resampling=Resampling.bilinear,
                        src_nodata=src.nodata, nodata=np.nan, dtype="float32")
        srcs.append((r, vrt))
    if not srcs:
        print("no DSMs found")
        return 0
    # A COARSE FLOOR UNDER THE DETAIL. The 1 m DSM exists only in patches
    # over the built-up areas, so between and around them a tile had holes,
    # and MapLibre draws the boundary between terrain and no-terrain as a
    # vertical curtain -- the walls standing over Lake Wakatipu in the first
    # 3D builds. The district DEM is 8 m bare earth over 29 x 38 km, which
    # is everywhere the DSM is not. It fills what the DSM cannot answer:
    # detail where we surveyed it, honest coarse ground elsewhere, and no
    # cliff where one ends and the other begins.
    dem = None
    dem_path = ROOT / "data" / "dem_wide_mosaic.tif"
    if dem_path.exists():
        dsrc = rasterio.open(dem_path)
        dem = WarpedVRT(dsrc, crs="EPSG:3857", resampling=Resampling.bilinear,
                        src_nodata=dsrc.nodata, nodata=np.nan, dtype="float32")
    print(f"{len(srcs)} region surfaces"
          f"{', plus the wide DEM as a floor' if dem is not None else ''}",
          flush=True)

    written = 0
    for z in range(min_z, max_z + 1):
        want = {}
        for _, vrt in srcs:
            l, b, r2, t = vrt.bounds
            x0, y0 = merc_to_tile(z, l, t)
            x1, y1 = merc_to_tile(z, r2, b)
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    want.setdefault((x, y), []).append(vrt)
        span_m = 2 * ORIGIN / (2 ** z) * math.cos(math.radians(-45.03))
        m_per_px = span_m / TILE
        # ZOOMED OUT, ONE SMOOTH SURFACE. Below z15 a tile used to mix the
        # 1 m DSM patches with the 8 m district DEM, and every place one met
        # the other was a step -- a "drop off in the lake" and a terrain that
        # looked broken when the camera moved (18 Oct). At these zooms a tree
        # or a roof is a few pixels anyway, so the DEM alone draws them.
        dem_only = dem is not None and z <= OVERVIEW_MAX_Z
        if dem_only:
            want = {}
            l, b0, r2, t = dem.bounds
            for (_, vrt) in srcs:
                vl, vb, vr, vt = vrt.bounds
                x0, y0 = merc_to_tile(z, max(l, vl - 20000), min(t, vt + 20000))
                x1, y1 = merc_to_tile(z, min(r2, vr + 20000), max(b0, vb - 20000))
                for x in range(x0, x1 + 1):
                    for y in range(y0, y1 + 1):
                        want.setdefault((x, y), [])
        for (x, y), vrts in want.items():
            b = tile_bounds(z, x, y)
            acc = np.full((TILE, TILE), np.nan, "float32")
            for vrt in ([] if dem_only else vrts):
                win = from_bounds(*b, transform=vrt.transform)
                if not _overlaps(win, vrt):
                    continue
                clipped = win.intersection(Window(0, 0, vrt.width, vrt.height))
                if clipped.width < 1 or clipped.height < 1:
                    continue
                sx, sy = TILE / win.width, TILE / win.height
                ow = max(1, int(round(clipped.width * sx)))
                oh = max(1, int(round(clipped.height * sy)))
                # A ROOF EDGE IS A CLIFF, NOT A RAMP. Bilinear resampling
                # smears the 1 m step at a building's edge across a couple
                # of metres, and anything draped on the terrain -- the
                # panels especially -- then runs down that ramp and appears
                # to hang off the side of the building: unrealistic
                # panels drooped over the sides of buildings on walls
                # rather than rooftops." At detail zooms the tile grid is
                # already near the 1 m source, so nearest keeps the step
                # sharp; at overview zooms bilinear still avoids aliasing.
                rs = (Resampling.nearest if m_per_px <= 1.5
                      else Resampling.bilinear)
                try:
                    part = vrt.read(1, window=clipped, out_shape=(oh, ow),
                                    resampling=rs)
                except Exception:
                    continue
                ox = max(0, min(TILE - 1, int(round((clipped.col_off - win.col_off) * sx))))
                oy = max(0, min(TILE - 1, int(round((clipped.row_off - win.row_off) * sy))))
                ow = min(ow, TILE - ox)
                oh = min(oh, TILE - oy)
                if ow < 1 or oh < 1:
                    continue
                tgt = acc[oy:oy + oh, ox:ox + ow]
                acc[oy:oy + oh, ox:ox + ow] = np.where(
                    np.isfinite(tgt), tgt, part[:oh, :ow])
            if dem is not None and not dem_only and np.isfinite(acc).any() and not np.isfinite(acc).all():
                # FEATHER THE EDGE OF THE SURVEY into the DEM over ~30 m, so
                # where the 1 m data stops there is a slope, not a cliff
                demt = _read_full(dem, b)
                if demt is not None:
                    from scipy.ndimage import distance_transform_edt
                    fin = np.isfinite(acc)
                    dist = distance_transform_edt(fin) * (span_m / TILE)
                    w = np.clip(dist / FEATHER_M, 0.0, 1.0)
                    blend = np.where(np.isfinite(demt), w * np.nan_to_num(acc) + (1 - w) * demt, acc)
                    acc = np.where(fin, blend, demt)
            if dem is not None and not np.isfinite(acc).all():
                win = from_bounds(*b, transform=dem.transform)
                if _overlaps(win, dem):
                    clipped = win.intersection(Window(0, 0, dem.width, dem.height))
                    if clipped.width >= 1 and clipped.height >= 1:
                        sx, sy = TILE / win.width, TILE / win.height
                        ow = max(1, int(round(clipped.width * sx)))
                        oh = max(1, int(round(clipped.height * sy)))
                        try:
                            part = dem.read(1, window=clipped, out_shape=(oh, ow),
                                            resampling=Resampling.bilinear)
                            ox = max(0, min(TILE - 1, int(round((clipped.col_off - win.col_off) * sx))))
                            oy = max(0, min(TILE - 1, int(round((clipped.row_off - win.row_off) * sy))))
                            ow = min(ow, TILE - ox); oh = min(oh, TILE - oy)
                            if ow >= 1 and oh >= 1:
                                tgt = acc[oy:oy + oh, ox:ox + ow]
                                acc[oy:oy + oh, ox:ox + ow] = np.where(
                                    np.isfinite(tgt), tgt, part[:oh, :ow])
                        except Exception:
                            pass
            if not np.isfinite(acc).any():
                continue
            acc = despike(acc, span_m / TILE)
            d = Path(out_dir) / str(z) / str(x)
            d.mkdir(parents=True, exist_ok=True)
            Image.fromarray(encode(acc)).save(d / f"{y}.png", optimize=True)
            written += 1
        print(f"  z{z}: {written} tiles so far", flush=True)
    for _, vrt in srcs:
        vrt.close()
    if dem is not None:
        dem.close()
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("regions", nargs="*")
    # overview zooms come from the district DEM alone (OVERVIEW_MAX_Z), so 3D
    # works zoomed out as far as a whole district
    ap.add_argument("--min-zoom", type=int, default=8)
    # 1 m data is fully resolved by about z17 at this latitude (0.84 m/px);
    # asking for more just interpolates and multiplies the file count by four.
    ap.add_argument("--max-zoom", type=int, default=17)
    ap.add_argument("--out", type=Path, default=None,
                    help="tile output directory (defaults to data/terrain, or data/terrain_ground with --ground)")
    ap.add_argument("--ground", action="store_true",
                    help="buildings pressed flat to the ground beside them: the terrain under the 3D view's solid buildings")
    a = ap.parse_args()
    if a.out is None:
        a.out = OUT.parent / "terrain_ground" if a.ground else OUT
    from src.region_build import area_paths
    from src.region_ids import list_regions
    regions = a.regions or list_regions(ROOT / "data/regions")
    a.out.mkdir(parents=True, exist_ok=True)
    total = build_all(regions, a.min_zoom, a.max_zoom, area_paths, a.out, ground=a.ground)
    (a.out / "meta.json").write_text(json.dumps(
        {"encoding": "mapbox", "tileSize": TILE,
         "minzoom": a.min_zoom, "maxzoom": a.max_zoom,
         "source": ("LINZ 1 m DSM, buildings pressed to the ground beside them (ground and trees)" if a.ground
                    else "LINZ 1 m DSM (surface: ground, trees and roofs)")}, indent=1))
    print(f"TOTAL {total} terrain tiles -> {a.out}")


if __name__ == "__main__":
    sys.exit(main() or 0)
