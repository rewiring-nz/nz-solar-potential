"""
Find roofs whose served panel layout has a visible fault.

Reads a served layout set (panel_layouts.pmtiles, the file the public map
draws) and LINZ's open 1 m LiDAR surface and ground model, and measures each
roof for the faults reviewers tag on blind pages:

  ridge       a panel sits on more than one roof plane: the LiDAR surface
              under it departs from its array's fitted plane.
  off_roof    a panel hangs over open air: the surface under it is near the
              ground, or drops well below the array's plane.
  lines       a roof face does not follow the LiDAR: its own plane fits the
              surface badly, or its stated slope and direction disagree with
              the surface's.
  messy       an array mixes portrait and landscape panels, its rows are
              offset from each other, or it holds only one or two panels.
  face shape  the least convex face carrying panels, and inward corners per
              face: drawn faces a real roof plane would not have.

The served drawing is moved onto the map photo by each building's lean
(img_dx/img_dy in buildings.pmtiles); it is moved back before any LiDAR
measure. Panels are grouped by the build's array_id and judged by the median
of the 1 m pixels inside them, so one eave pixel decides nothing.

THESE MEASURES DO NOT YET FIND BAD LAYOUTS. Checked against the owner's
blind scores and fault tags on the 40 random v72 roofs
(data/live_review/v72/answers.json):
  - the LiDAR measures barely track the score (best rank correlation -0.26)
    and the ridge measure predicts the "ridge" tag at chance (AUC 0.53, 9
    roofs). The build fits faces and panels to this same LiDAR, so the
    faults reviewers see on the photo are mostly not LiDAR misfits;
  - face shape tracked the score on those 40 (min convexity + inward corners:
    rank correlation 0.50, 8 of the 16 roofs scored 5 or less in its worst
    10), but those measures were chosen on the same roofs, and on the 25
    frame-fix roofs (data/blind_check/frame_fix) it ranked tagged roofs
    below untagged ones (AUC 0.39).
Use the output to inspect a roof, not to rank or withhold roofs.

Every measure is in metres or degrees and the thresholds are named below, so
a flag can be checked by hand against the LiDAR. The build's own surface is
the same 1 m LINZ product, so no build data is needed: this runs anywhere
with the served pmtiles and internet access.

    python tools/layout_faults.py --pmtiles data/panel_layouts.pmtiles \
        --ids 4747641 5370340 --out faults.json
    python tools/layout_faults.py --pmtiles ... --sample data/live_review/v72/sample.json

The rasters for each roof are cached under data/layout_faults/rasters/.
"""
import argparse
import gzip
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import geometry_mask
from shapely.geometry import shape, Polygon, MultiPolygon, box
from shapely.ops import transform as shp_transform, unary_union
from shapely import affinity

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CACHE = ROOT / "data" / "layout_faults"
TO_NZTM = Transformer.from_crs(4326, 2193, always_xy=True).transform

ELEV = "https://nz-elevation.s3.ap-southeast-2.amazonaws.com/otago/"
# Tried in order; the first whose tiles cover the roof is used. Queenstown 2021
# and Wanaka 2022-23 are the surveys the build reads (config.SURVEYS).
SURVEYS = ["queenstown_2021", "wanaka_2022-2023", "kingston_2025",
           "central-otago_2022-2023", "central-otago_2021", "cardrona_2025",
           "queenstown_2016", "otago_2016"]

# --- thresholds (metres / degrees) -------------------------------------------
RIDGE_RESID_M = 0.35      # a panel's surface sits this far off its array's plane
OFF_ROOF_AGL_M = 1.5      # surface under a panel this close to the ground: open air
OFF_ROOF_DROP_M = 1.0     # or this far below the array's plane
LINES_RMS_M = 0.30        # a face's own plane fits the surface this badly
LINES_SLOPE_DEG = 8.0     # stated slope differs from the surface's by this much
LINES_ASPECT_DEG = 30.0   # stated direction differs by this much (slopes over 10 deg)
STAGGER_FRAC = 0.2        # a row offset by this fraction of a panel width
TINY_ARRAY = 3            # arrays with fewer panels than this


# --- reading the served tiles -------------------------------------------------
def _tile_xy(lon, lat, z):
    n = 2 ** z
    return (int((lon + 180) / 360 * n),
            int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n))


def _decode(reader, z, x, y):
    import mapbox_vector_tile as mvt
    t = reader.get(z, x, y)
    if not t:
        return []
    if t[:2] == b"\x1f\x8b":
        t = gzip.decompress(t)
    out = []
    for layer in mvt.decode(t, default_options={"y_coord_down": True}).values():
        ext = layer["extent"]
        n = 2 ** z

        def to_ll(px, py, ext=ext):
            lon = (x + px / ext) / n * 360 - 180
            lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + py / ext) / n))))
            return lon, lat
        for f in layer["features"]:
            g = shape(f["geometry"])
            g = shp_transform(lambda xs, ys: tuple(zip(*[TO_NZTM(*to_ll(a, b)) for a, b in zip(xs, ys)])), g)
            out.append((f["properties"], g))
    return out


class Served:
    """Panels, faces and obstructions of one served layout set, by building."""

    def __init__(self, pmtiles, z=16):
        from pmtiles.reader import Reader, MmapSource
        self.r = Reader(MmapSource(open(pmtiles, "rb")))
        self.z = z
        self.seen = {}

    def roof(self, bid, lon, lat):
        x, y = _tile_xy(lon, lat, self.z)
        feats = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                k = (x + dx, y + dy)
                if k not in self.seen:
                    self.seen[k] = _decode(self.r, self.z, *k)
                feats += [(p, g) for p, g in self.seen[k] if p.get("building_id") == bid]
        panels, faces, obst = {}, defaultdict(list), []
        for p, g in feats:
            if g.is_empty:
                continue
            kind = p.get("kind")
            if kind == "panel":
                key = (round(g.centroid.x, 1), round(g.centroid.y, 1))
                if key not in panels or g.area > panels[key][1].area:
                    panels[key] = (p, g)
            elif kind == "facet":
                faces[(p.get("slope_deg"), p.get("aspect_deg"), p.get("panel_count"))].append((p, g))
            elif kind == "obstruction":
                obst.append(g)
        faces = [(v[0][0], unary_union([g for _, g in v]).buffer(0)) for v in faces.values()]
        return list(panels.values()), faces, unary_union(obst) if obst else None


# --- LiDAR around a roof ------------------------------------------------------
def _ll_bbox(geom, pad=15):
    x0, y0, x1, y1 = geom.bounds
    inv = Transformer.from_crs(2193, 4326, always_xy=True).transform
    a, b = inv(x0 - pad, y0 - pad)
    c, d = inv(x1 + pad, y1 + pad)
    return [a, b, c, d]


def _read(collection_url, bbox_wgs84):
    """The bbox cut from the collection's 1 m COG tiles as (array, transform,
    nodata), read in windows over HTTP. Needs only rasterio, not the GDAL
    command-line tools s3_rasters.fetch uses."""
    from src import s3_rasters
    import rasterio.merge
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    urls = s3_rasters.tiles_touching(collection_url, bbox_wgs84)
    if not urls:
        return None
    srcs = [rasterio.open("/vsicurl/" + u) for u in urls]
    try:
        a, tr = rasterio.merge.merge(srcs, bounds=s3_rasters._nztm_bounds(bbox_wgs84))
        return a[0], tr, srcs[0].nodata, srcs[0].crs
    finally:
        for d in srcs:
            d.close()


def rasters(bid, geom):
    """(dsm, dem) as (array, transform, nodata), or None where no survey covers
    the roof."""
    CACHE.joinpath("rasters").mkdir(parents=True, exist_ok=True)
    out = []
    for kind in ("dsm_1m", "dem_1m"):
        path = CACHE / "rasters" / f"{bid}_{kind}.tif"
        if not path.exists():
            for s in SURVEYS:
                try:
                    got = _read(f"{ELEV}{s}/{kind}/2193/", _ll_bbox(geom))
                except Exception as e:
                    print(f"  {bid} {s} {kind}: {e}", file=sys.stderr)
                    continue
                if got is None:
                    continue
                a, tr, nod, crs = got
                valid = np.isfinite(a) & (a != nod) if nod is not None else np.isfinite(a)
                if valid.mean() < 0.9:            # survey edge: try the next
                    continue
                with rasterio.open(path, "w", driver="GTiff", height=a.shape[0], width=a.shape[1],
                                   count=1, dtype=a.dtype, crs=crs, transform=tr, nodata=nod) as d:
                    d.write(a, 1)
                break
        if not path.exists():
            return None
        with rasterio.open(path) as d:
            out.append((d.read(1).astype(float), d.transform, d.nodata))
    return out


def _pixels(geom, arr, tr):
    """(x, y, z) of the 1 m pixels whose centres fall inside geom."""
    m = ~geometry_mask([geom], arr.shape, tr, all_touched=False)
    rows, cols = np.nonzero(m)
    xs, ys = rasterio.transform.xy(tr, rows, cols)
    return np.column_stack([xs, ys, arr[rows, cols]])


def _plane(pts):
    """Least-squares z = ax + by + c; (coef, rms)."""
    if len(pts) < 4:
        return None, None
    A = np.column_stack([pts[:, 0] - pts[:, 0].mean(), pts[:, 1] - pts[:, 1].mean(), np.ones(len(pts))])
    coef, *_ = np.linalg.lstsq(A, pts[:, 2], rcond=None)
    rms = float(np.sqrt(np.mean((A @ coef - pts[:, 2]) ** 2)))
    return (coef, pts[:, 0].mean(), pts[:, 1].mean()), rms


def _zat(pl, x, y):
    (a, b, c), mx, my = pl
    return a * (x - mx) + b * (y - my) + c


def _slope_aspect(pl):
    (a, b, _), _, _ = pl
    slope = math.degrees(math.atan(math.hypot(a, b)))
    aspect = (math.degrees(math.atan2(-a, -b)) + 360) % 360   # downhill direction, 0 = north
    return slope, aspect


# --- panel geometry -----------------------------------------------------------
def _rect(g):
    """(long-axis angle deg, long side, short side, centre) of a panel."""
    r = g.minimum_rotated_rectangle
    c = list(r.exterior.coords)[:4]
    e = [(c[i], c[(i + 1) % 4]) for i in range(4)]
    L = [math.dist(*s) for s in e]
    i = int(np.argmax(L))
    (x0, y0), (x1, y1) = e[i]
    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180, max(L), min(L), g.centroid


def _arrays(panels):
    """Panels grouped by the build's own array_id (one array lies on one face
    by construction). Touching panels are joined only where a tile carries
    no array_id."""
    groups = defaultdict(list)
    loose = []
    for i, (p, _) in enumerate(panels):
        if p.get("array_id") is not None:
            groups[p["array_id"]].append(i)
        else:
            loose.append(i)
    geoms = {i: panels[i][1].buffer(0.15) for i in loose}
    parent = {i: i for i in loose}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for n, i in enumerate(loose):
        for j in loose[n + 1:]:
            if geoms[i].intersects(geoms[j]):
                parent[find(i)] = find(j)
    for i in loose:
        groups[("loose", find(i))].append(i)
    return list(groups.values())


def messy(panels):
    """Mixed orientation, offset rows and tiny arrays, per roof."""
    out = {"mixed_orientation": 0, "stagger": 0, "tiny_arrays": 0, "angle_spread_deg": 0.0}
    if not panels:
        return out
    rects = [_rect(g) for _, g in panels]
    arrays = _arrays(panels)
    angles = []
    for arr in arrays:
        if len(arr) < TINY_ARRAY:
            out["tiny_arrays"] += 1
        # orientation: the long axis must be parallel or perpendicular to the
        # array's dominant axis; a mix of both inside one array is "mixed".
        base = rects[arr[0]][0]
        par = [abs(((rects[i][0] - base + 90) % 180) - 90) < 20 for i in arr]
        if 0 < sum(par) < len(par):
            out["mixed_orientation"] += 1
        angles.append(base)
        # rows: project centres onto the array's axes; panels in one row share
        # the across-row coordinate, and a row's along-row phase should match
        # its neighbours' (mod the panel's along-row width).
        if len(arr) >= 4 and all(par):
            th = math.radians(base)
            u = np.array([math.cos(th), math.sin(th)])
            v = np.array([-u[1], u[0]])
            cs = np.array([[rects[i][3].x, rects[i][3].y] for i in arr])
            w = float(np.median([rects[i][2] for i in arr]))       # along-row width (short side)
            h = float(np.median([rects[i][1] for i in arr]))
            a = cs @ v
            b = cs @ u
            # rows by across-coordinate clustering at half a panel length
            order = np.argsort(b)
            row_id, cur, last = np.zeros(len(b), int), 0, b[order[0]]
            for k in order:
                if b[k] - last > h * 0.5:
                    cur += 1
                last = b[k]
                row_id[k] = cur
            phases = []
            for rid in set(row_id):
                ph = np.mod(a[row_id == rid] - a.min(), w)
                phases.append(float(np.median(ph)))
            if len(phases) > 1:
                d = [min(abs(p - phases[0]), w - abs(p - phases[0])) for p in phases[1:]]
                if max(d) > STAGGER_FRAC * w:
                    out["stagger"] += 1
    if len(angles) > 1:
        dif = [abs(((x - angles[0] + 45) % 90) - 45) for x in angles[1:]]
        out["angle_spread_deg"] = round(max(dif), 1)
    return out


def _reflex_corners(poly):
    """Inward corners sharper than 20 degrees on a polygon's outer ring."""
    c = np.array(poly.exterior.coords)[:-1]
    if len(c) < 4:
        return 0
    ccw = poly.exterior.is_ccw
    v = np.roll(c, -1, 0) - c
    w = np.roll(v, -1, 0)
    cr = v[:, 0] * w[:, 1] - v[:, 1] * w[:, 0]
    ang = np.abs(np.degrees(np.arctan2(cr, (v * w).sum(1))))
    return int((((cr < 0) if ccw else (cr > 0)) & (ang > 20)).sum())


def face_shape(faces):
    """How plausible the drawn faces look as roof planes: the least convex
    face that carries panels (area over convex-hull area; a real roof plane
    is close to 1) and inward corners per face, after a 0.3 m simplify."""
    convex = [g.area / g.convex_hull.area for p, g in faces
              if (p.get("panel_count") or 0) > 0 and g.area > 2]
    corners = sum(_reflex_corners(q) for _, g in faces
                  for q in ([g] if g.geom_type == "Polygon" else getattr(g, "geoms", []))
                  if q.geom_type == "Polygon" for q in [q.simplify(0.3)])
    return {"min_panel_face_convexity": round(min(convex), 2) if convex else None,
            "inward_corners_per_face": round(corners / len(faces), 2) if faces else 0}


# --- one roof -----------------------------------------------------------------
def measure(served, bid, lon, lat, img_dx=0.0, img_dy=0.0):
    panels, faces, obst = served.roof(bid, lon, lat)
    # The served drawing sits on the map photo, moved from the LiDAR by the
    # photo's lean (emit_region "THE DRAWING FOLLOWS THE PHOTO"; median
    # 0.57 m, p90 1.28 m). Measured against the LiDAR it must move back, or
    # a lean alone reads as panels over a ridge or over the edge.
    if img_dx or img_dy:
        back = lambda g: affinity.translate(g, -img_dx, -img_dy)
        panels = [(p, back(g)) for p, g in panels]
        faces = [(p, back(g)) for p, g in faces]
        obst = back(obst) if obst is not None else None
    res_shift = {"img_shift_m": round(math.hypot(img_dx, img_dy), 2)} if (img_dx or img_dy) else {}
    res = {"id": bid, "panels": len(panels), "faces": len(faces), **res_shift}
    if not panels and not faces:
        res["missing"] = True
        return res
    res.update(messy(panels))
    res.update(face_shape(faces))
    footprint = unary_union([g for _, g in faces] + [g for _, g in panels])
    r = rasters(bid, footprint)
    if r is None:
        res["no_lidar"] = True
        return res
    (dsm, tr, nd), (dem, tr2, nd2) = r
    if nd is not None:
        dsm[dsm == nd] = np.nan
    if nd2 is not None:
        dem[dem == nd2] = np.nan

    # faces: does each follow the LiDAR?
    bad_faces, worst_rms = [], 0.0
    for p, g in faces:
        pts = _pixels(g.buffer(-0.5), dsm, tr)
        pts = pts[np.isfinite(pts[:, 2])]
        if len(pts) < 6:
            continue
        pl, rms = _plane(pts)
        s, a = _slope_aspect(pl)
        ds = abs(s - (p.get("slope_deg") or 0))
        da = abs(((a - (p.get("aspect_deg") or 0) + 180) % 360) - 180) if s > 10 else 0
        worst_rms = max(worst_rms, rms)
        if rms > LINES_RMS_M or ds > LINES_SLOPE_DEG or da > LINES_ASPECT_DEG:
            bad_faces.append({"area": round(g.area), "rms": round(rms, 2), "dslope": round(ds, 1),
                              "daspect": round(da), "panels": p.get("panel_count", 0)})
    res["bad_faces"] = bad_faces
    res["bad_face_area"] = sum(f["area"] for f in bad_faces)
    res["face_area"] = round(sum(g.area for _, g in faces))
    res["bad_faces_with_panels"] = sum(1 for f in bad_faces if f["panels"])

    # panels: on their array's plane, and over roof?
    ridge, off, stats = 0, 0, []
    for arr in _arrays(panels):
        geom = unary_union([panels[i][1] for i in arr])
        pts = _pixels(geom.buffer(-0.3), dsm, tr)
        pts = pts[np.isfinite(pts[:, 2])]
        pl, _ = _plane(pts) if len(pts) >= 6 else (None, None)
        for i in arr:
            g = panels[i][1]
            # the 1 m pixels whose centres fall inside the panel (a panel is
            # about 1.7 x 1.1 m, so 1-3 of them); judged by the median, so a
            # single eave pixel that sees the ground decides nothing
            pp = _pixels(g, dsm, tr)
            if not len(pp):
                pp = _pixels(g.centroid.buffer(0.71), dsm, tr)
            gp = _pixels(g, dem, tr2) if len(pp) else []
            if len(gp) != len(pp):
                gp = None
            ok = np.isfinite(pp[:, 2]) if len(pp) else np.array([], bool)
            if not ok.any():
                continue
            agl = float(np.median((pp[:, 2] - gp[:, 2])[ok])) if gp is not None else None
            d = pp[ok, 2] - _zat(pl, pp[ok, 0], pp[ok, 1]) if pl is not None else None
            # how far the panel sits from the array's edge: the 1 m surface
            # blurs ridges and eaves, so edge panels read off-plane on a
            # clean roof
            edge = float(geom.boundary.distance(g.centroid))
            stats.append({"agl": None if agl is None else round(agl, 2),
                          "drop": None if d is None else round(float(np.median(d)), 2),
                          "res": None if d is None else round(float(np.median(np.abs(d))), 2),
                          "edge": round(edge, 2), "n": int(len(arr))})
            if agl is not None and agl < OFF_ROOF_AGL_M:
                off += 1
            elif d is not None and np.median(d) < -OFF_ROOF_DROP_M:
                off += 1
            elif d is not None and np.median(np.abs(d)) > RIDGE_RESID_M:
                ridge += 1
    res["panel_stats"] = stats
    res["ridge_panels"] = ridge
    res["off_roof_panels"] = off
    res["worst_face_rms"] = round(worst_rms, 2)
    return res


def locate(ids):
    """{id: (lon, lat, img_dx, img_dy)} from the served buildings.pmtiles:
    the outline's centroid, and the metres emit_region moved the building's
    drawing east and north onto the map photo (0 where it did not)."""
    from pmtiles.reader import Reader, MmapSource, all_tiles
    import mapbox_vector_tile as mvt
    want, out = set(ids), {}
    r = Reader(MmapSource(open(ROOT / "data" / "buildings.pmtiles", "rb")))
    z = r.header()["max_zoom"]
    for (tz, x, y), t in all_tiles(r.get_bytes):
        if tz != z:
            continue
        for p, g in _decode(r, z, x, y):
            b = p.get("building_id")
            if b in want and b not in out:
                inv = Transformer.from_crs(2193, 4326, always_xy=True).transform
                out[b] = [*inv(g.centroid.x, g.centroid.y),
                          float(p.get("img_dx") or 0), float(p.get("img_dy") or 0)]
        if len(out) == len(want):
            break
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pmtiles", required=True)
    ap.add_argument("--ids", nargs="*", type=int, default=[])
    ap.add_argument("--sample", help="json with an 'ids' list of [region, id] pairs")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    ids = list(a.ids)
    if a.sample:
        ids += [i for _, i in json.load(open(a.sample))["ids"]]
    loc_cache = CACHE / "locations_v2.json"
    CACHE.mkdir(parents=True, exist_ok=True)
    known = {int(k): v for k, v in json.load(open(loc_cache)).items()} if loc_cache.exists() else {}
    need = [i for i in ids if i not in known]
    if need:
        known.update(locate(need))
        json.dump(known, open(loc_cache, "w"))
    served = Served(a.pmtiles)
    out = []
    for i in ids:
        if i not in known:
            out.append({"id": i, "missing": True})
            continue
        try:
            out.append(measure(served, i, *known[i]))
        except Exception as e:
            out.append({"id": i, "error": repr(e)})
        print(json.dumps(out[-1]), flush=True)
    json.dump(out, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
