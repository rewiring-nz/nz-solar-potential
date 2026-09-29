"""A stand-in surface model for towns no LiDAR survey covers.

WHY. Cromwell and Bannockburn have building outlines and good aerial photos
but no LiDAR: every published Otago survey stops short of the Cromwell basin
(LINZ Data Service tile indexes and the nz-elevation store were both checked,
29 Sep 2026). Everything this pipeline knows about a roof -- its faces, pitch,
aspect, obstructions standing proud of it, the shade neighbours cast -- is
read from an elevation surface, so without one a town cannot be modelled at
all.

WHAT THIS DOES. It builds that surface from what does exist, with the roof
shape GUESSED:

  ground   the national 8 m DEM (the wide DEM the horizon stages already use),
           resampled to the 1 m grid a LINZ DSM would have;
  roofs    every outline becomes a roof of one assumed pitch. Houses get a
           hip roof -- every point rises with its distance from the nearest
           wall, which is exactly a hipped roof on a rectangle and a close
           stand-in on anything else -- because a hip is the one shape that
           needs nothing but the outline. Footprints of FLAT_MIN_M2 and up are
           treated as flat commercial roofs.

The rest of the pipeline then runs unchanged on the surface, exactly as it
does for Kingston, which has a 1 m DSM and no point cloud: faces are read from
it, panels laid on them, terrain and neighbouring buildings shade them, and
the photo still finds vents and skylights.

WHAT IT CANNOT KNOW, and the map says so for every building built this way
(`pitch_guessed` on the building): the real pitch; gable versus hip, so gable
ends are modelled as hips; dormers and roof levels; and trees, so no tree
shade at all. Totals are an estimate of a different kind from the surveyed
towns, and are labelled as such.

Writes data/regions/<r>/dsm_mosaic.tif and data/regions/<r>/elevation_source.json.
Usage: python src/synthesize_dsm.py <region>
"""

import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from src.region_build import area_bbox_nztm, area_paths, write_json_atomic

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RES_M = 1.0                 # a LINZ DSM's grid, so every stage sees what it expects
PAD_M = 60.0                # ground beyond the region's edge, for neighbour shade
PITCH_DEG = getattr(config, "PHOTO_ONLY_PITCH_DEG", 20.0)
FLAT_MIN_M2 = getattr(config, "PHOTO_ONLY_FLAT_MIN_M2", 400.0)
EAVE_HOUSE_M = 3.0          # one storey to the eaves
EAVE_FLAT_M = 5.0           # a commercial or industrial shed
NODATA = -9999.0


def roof_model(area_m2):
    """(kind, pitch_deg, eave_m) guessed from the footprint alone."""
    if area_m2 >= FLAT_MIN_M2:
        return "flat", 0.0, EAVE_FLAT_M
    return "hip", PITCH_DEG, EAVE_HOUSE_M


def roof_heights(poly, xs, ys, base, kind, pitch_deg, eave_m):
    """Roof surface z at the points (xs, ys), all inside `poly`."""
    if kind == "flat":
        return np.full(len(xs), base + eave_m)
    import shapely
    d = shapely.distance(shapely.points(xs, ys), poly.boundary)
    return base + eave_m + math.tan(math.radians(pitch_deg)) * d


SIMPLIFY_M = 0.3            # outline jogs finer than this are not roof edges
MERGE_DEG = 15.0            # consecutive walls within this bearing are one roof edge
FACE_GRID_M = 0.25          # resolution the hip faces are labelled at
SELECTED_DIR = DATA_DIR / "selected_faces"


def roof_outline(poly):
    """The outline the roof is built on: tiny jogs removed, counter-clockwise."""
    from shapely.geometry.polygon import orient
    p = poly.simplify(SIMPLIFY_M, preserve_topology=True)
    if p.is_empty or p.geom_type != "Polygon":
        p = poly
    return orient(p, 1.0)


def _edge_groups(poly):
    """The walls a hip roof rises from: each ring's edges, consecutive ones
    of nearly the same bearing merged, as LineStrings."""
    from shapely.geometry import LineString
    groups = []
    for ring in [poly.exterior, *poly.interiors]:
        pts = list(ring.coords)[:-1]
        if len(pts) < 3:
            continue
        edges = [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
        bearing = [math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) for a, b in edges]
        # start at a corner, so a run is never split across the ring's seam
        turn = [abs((bearing[i] - bearing[i - 1] + 180) % 360 - 180) for i in range(len(edges))]
        start = max(range(len(edges)), key=lambda i: turn[i])
        run = [edges[start]]
        for k in range(1, len(edges) + 1):
            i = (start + k) % len(edges)
            if k < len(edges) and turn[i] < MERGE_DEG:
                run.append(edges[i])
                continue
            groups.append(LineString([run[0][0]] + [e[1] for e in run]))
            if k < len(edges):
                run = [edges[i]]
    return groups


def hip_faces(poly):
    """A hip roof's faces: the part of the footprint nearest each wall.
    Exactly the straight skeleton on a rectangle; its nearest-wall stand-in
    elsewhere. Returns [(polygon, wall LineString)]."""
    import shapely
    from rasterio.features import shapes
    from rasterio.transform import from_origin
    from shapely.geometry import shape as _shape
    from shapely.ops import unary_union
    groups = _edge_groups(poly)
    if len(groups) < 2:
        return []
    minx, miny, maxx, maxy = poly.bounds
    w = max(int(math.ceil((maxx - minx) / FACE_GRID_M)), 1)
    h = max(int(math.ceil((maxy - miny) / FACE_GRID_M)), 1)
    gx, gy = np.meshgrid(minx + (np.arange(w) + 0.5) * FACE_GRID_M,
                         maxy - (np.arange(h) + 0.5) * FACE_GRID_M)
    pts = shapely.points(gx.ravel(), gy.ravel())
    inside = shapely.contains(poly, pts)
    d = np.stack([shapely.distance(pts, g) for g in groups])
    label = np.where(inside, d.argmin(axis=0), -1).reshape(h, w).astype(np.int32)
    t = from_origin(minx, maxy, FACE_GRID_M, FACE_GRID_M)
    parts = {}
    for geom, v in shapes(label, mask=label >= 0, transform=t):
        parts.setdefault(int(v), []).append(_shape(geom))
    faces = []
    for v, ps in parts.items():
        f = unary_union(ps).simplify(FACE_GRID_M, preserve_topology=True).intersection(poly)
        for q in ([f] if f.geom_type == "Polygon" else getattr(f, "geoms", [])):
            if q.geom_type == "Polygon" and q.area >= 1.0:
                faces.append((q, groups[v]))
    return faces


def wall_plane(wall, base, eave_m, pitch_deg):
    """The plane z = a x + b y + c rising at the pitch away from `wall`, a
    wall of a ring oriented so the building's inside is on its left."""
    (x0, y0), (x1, y1) = wall.coords[0], wall.coords[-1]
    L = math.hypot(x1 - x0, y1 - y0) or 1.0
    nx, ny = -(y1 - y0) / L, (x1 - x0) / L          # inward normal
    t = math.tan(math.radians(pitch_deg))
    return [t * nx, t * ny, base + eave_m - t * (nx * x0 + ny * y0)]


def _write_faces(building_id, faces, planes):
    """The roof's faces where build_layout_geojson reads a building's chosen
    faces (roof_partition.facets_from_selected_faces), which fits each one's
    plane from this surface. Never over a reading that came from elsewhere."""
    f = SELECTED_DIR / f"{building_id}.json"
    if f.exists():
        try:
            if json.loads(f.read_text()).get("source") != "synthetic":
                return False
        except ValueError:
            pass
    SELECTED_DIR.mkdir(parents=True, exist_ok=True)
    rings = [[[round(x, 2), round(y, 2)] for x, y in q.exterior.coords] for q in faces]
    f.write_text(json.dumps({"source": "synthetic", "score": 1.0, "faces": rings,
                             "planes": [[round(v, 6) for v in pl] for pl in planes]}))
    return True


def synthesize(region, dem_path=None):
    """Build and write the region's surface. `dem_path` overrides the wide
    DEM, for trials on a machine that does not hold it."""
    import geopandas as gpd
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.transform import from_origin
    from rasterio.warp import reproject, Resampling

    paths = area_paths(region)
    t0 = time.time()
    raw = paths["dir"] / "building_outlines.geojson"
    gdf = gpd.read_file(raw if raw.exists() else paths["outlines"]).to_crs("EPSG:2193")
    x0, y0, x1, y1 = area_bbox_nztm(region)
    x0, y0 = math.floor(x0 - PAD_M), math.floor(y0 - PAD_M)
    x1, y1 = math.ceil(x1 + PAD_M), math.ceil(y1 + PAD_M)
    w, h = int((x1 - x0) / RES_M), int((y1 - y0) / RES_M)
    transform = from_origin(x0, y1, RES_M, RES_M)

    # ground: the wide DEM, bilinear onto the 1 m grid
    ground = np.full((h, w), NODATA, dtype=np.float32)
    with rasterio.open(dem_path or DATA_DIR / "dem_wide_mosaic.tif") as src:
        reproject(rasterio.band(src, 1), ground, dst_transform=transform, dst_crs="EPSG:2193",
                  dst_nodata=NODATA, resampling=Resampling.bilinear)
    empty = float((ground == NODATA).mean())
    if empty > 0.01:
        raise SystemExit(f"[{region}] the wide DEM does not cover this region "
                         f"({100 * empty:.0f}% empty) -- run python src/fetch_dem_wide.py first")
    dsm = ground.copy()

    roofs = {}
    counts = {"hip": 0, "flat": 0}
    for row in gdf.itertuples():
        g = row.geometry
        if g is None or g.is_empty:
            continue
        kind, pitch, eave = roof_model(g.area)
        if g.geom_type != "Polygon":
            g = max(getattr(g, "geoms", [g]), key=lambda q: q.area)
        g = roof_outline(g)
        parts = [g]
        c0 = max(int((g.bounds[0] - x0) / RES_M) - 1, 0)
        c1 = min(int((g.bounds[2] - x0) / RES_M) + 2, w)
        r0 = max(int((y1 - g.bounds[3]) / RES_M) - 1, 0)
        r1 = min(int((y1 - g.bounds[1]) / RES_M) + 2, h)
        if c1 <= c0 or r1 <= r0:
            continue
        sub_t = from_origin(x0 + c0 * RES_M, y1 - r0 * RES_M, RES_M, RES_M)
        inside = geometry_mask(parts, out_shape=(r1 - r0, c1 - c0), transform=sub_t, invert=True)
        if not inside.any():
            continue
        rr, cc = np.nonzero(inside)
        xs = x0 + (c0 + cc + 0.5) * RES_M
        ys = y1 - (r0 + rr + 0.5) * RES_M
        base = float(np.median(ground[r0 + rr, c0 + cc]))
        hips = hip_faces(g) if kind == "hip" else []
        if hips:
            faces = [q for q, _ in hips]
            planes = [wall_plane(wall, base, eave, pitch) for _, wall in hips]
        else:
            faces, planes = [g], [[0.0, 0.0, base + eave]]
            if kind == "hip":       # no usable walls: model it flat rather than guess
                kind, pitch = "flat", 0.0
        z = roof_heights(g, xs, ys, base, kind, pitch, eave)
        dsm[r0 + rr, c0 + cc] = np.maximum(dsm[r0 + rr, c0 + cc], z.astype(np.float32))
        _write_faces(int(row.building_id), faces, planes)
        roofs[str(int(row.building_id))] = {"roof": kind, "pitch_deg": pitch, "eave_m": eave,
                                            "faces": len(faces)}
        counts[kind] += 1

    out = paths["dsm"]
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp.tif")
    with rasterio.open(tmp, "w", driver="GTiff", width=w, height=h, count=1, dtype="float32",
                       crs="EPSG:2193", transform=transform, nodata=NODATA,
                       compress="deflate", tiled=True) as dst:
        dst.write(dsm, 1)
    tmp.replace(out)
    write_json_atomic(paths["dir"] / "elevation_source.json", {
        "source": "synthetic",
        "why": "no LiDAR survey covers this region",
        "ground": "NZ 8 m DEM, bilinear to 1 m",
        "roofs": f"hip at {PITCH_DEG:g} degrees under {FLAT_MIN_M2:g} m2, flat at and above",
        "pitch_deg": PITCH_DEG,
        "flat_min_m2": FLAT_MIN_M2,
        "buildings": roofs,
    })
    print(f"[{region}] synthetic surface: {counts['hip']} hip roofs at {PITCH_DEG:g} deg, "
          f"{counts['flat']} flat, {w} x {h} m, in {time.time() - t0:.0f}s")
    return out


def is_synthetic(region):
    """True when the region's surface was built here rather than surveyed."""
    f = area_paths(region)["dir"] / "elevation_source.json"
    try:
        return json.loads(f.read_text()).get("source") == "synthetic"
    except Exception:
        return False


def main():
    synthesize(sys.argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
