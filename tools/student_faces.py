#!/usr/bin/env python3
"""Roof faces for every home in a region, read by the face-region model.

The model (src/face_regions.py, data/models/face_regions_v3.pt) reads the
photo and the LiDAR surface together and costs nothing per roof, where a
vision-model reading costs about 17 cents (src/vision_roof.py). It was
trained on that reader's roofs and the drawn ones. Owner's blind check,
14 Nov, 24 unseen central Queenstown homes, model / production / same:
lines 13 / 6 / 5, panels 13 / 6 / 5.

Homes only (zone class "home", tools/fetch_zoning.py): the training roofs
and both blind checks were homes. Roofs with hand-drawn markup are skipped
(the markup governs), as are roofs that already hold a vision reading.

Writes data/student_faces/<id>.json in the vision reading's format: faces
with straight shared edges, their union as the roof edge, all slid onto
the roof the LiDAR sees with the slide recorded, so the build and the map
treat it as they do a vision reading. The slide is the build's own outline
fit (src/outline_fit.py, accepted slides only), not point_roof.register:
the model reads inside the surveyed outline, so its roof moves exactly as
that outline does. register slid 87 of 368 central Queenstown homes and
differed from the outline fit by over 1 m on 41; under trees it slid roofs
off the building (4735042, 4735619: a face over the wall, 57-67 degrees,
no panels).

Usage: python tools/student_faces.py <region> [--ids ...] [--jobs N] [--all-zones]
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MIN_AREA_M2 = 30.0     # a shed is not read
# The reading gives way to the one the build would otherwise use
# (data/repeat_faces, else data/selected_faces) when it explains clearly
# less of the roof's LiDAR returns: the share within PLANE_BAND_M of their
# own face's plane, both readings on the same returns. In the 15 Nov build
# Wanaka homes whose faces missed the planes were withheld as low
# confidence (5374704: a gabled house read as one face; 4720927: a pyramid
# at 45-52 degrees). On the 24 homes of the owner's blind check a margin of
# 0.20 gives way on 5: 2 the owner gave to production, 2 called the same,
# 1 given to the model; of 368 central Queenstown homes, 68.
YIELD_MARGIN = 0.20
PLANE_BAND_M = 0.15    # roof_partition.INLIER_BAND_M
MIN_SHARE_PTS = 30
BRIDGE_M = 0.15
_W = {}


def _init(region):
    import rasterio
    from src.region_build import area_paths
    from src.pointcloud_source import PointCloudSource
    _W["img"] = rasterio.open(area_paths(region)["imagery"])
    try:
        _W["fits"] = json.loads((area_paths(region)["dir"] / "outline_fit.json").read_text())
    except (OSError, ValueError):
        _W["fits"] = {}
    _W["pc"] = PointCloudSource(max_cached_tiles=3)


def _share(faces, pts):
    """Share of the returns inside `faces` within PLANE_BAND_M of the plane
    fitted to their own face."""
    import numpy as np
    import shapely
    from src.face_regions import _plane
    n = ok = 0
    for f in faces:
        q = pts[shapely.contains_xy(f, pts[:, 0], pts[:, 1])]
        n += len(q)
        eq = _plane(q)
        if eq is not None:
            ok += int((np.abs(q[:, 0] * eq[0] + q[:, 1] * eq[1] + eq[2] - q[:, 2]) < PLANE_BAND_M).sum())
    return ok / n if n else 0.0


def _incumbent(bid, slide):
    """Faces of the reading the build uses without this one, as stored and
    slid with the outline; [] when there is none."""
    from shapely.affinity import translate
    from shapely.geometry import Polygon
    from src.roof_partition import REPEAT_FACES_DIR, SELECTED_FACES_DIR
    for d in (REPEAT_FACES_DIR, SELECTED_FACES_DIR):
        try:
            rings = json.loads((d / f"{bid}.json").read_text()).get("faces") or []
        except (OSError, ValueError):
            continue
        faces = []
        for r in rings:
            try:
                p = Polygon(r).buffer(0)
            except (ValueError, TypeError):
                continue
            if p.geom_type == "Polygon" and p.area >= 1.0:
                faces.append(p)
        sets = [faces]
        if slide[0] or slide[1]:
            sets.append([translate(f, slide[0], slide[1]) for f in faces])
        return sets
    return []


def _one(job):
    bid, wkb, out = job
    from shapely import wkb as _wkb
    from shapely.ops import unary_union
    from src import face_regions as fr
    from src.outline_fit import accepted
    try:
        geom = _wkb.loads(wkb)
        x0, y0, x1, y1 = geom.bounds
        pts = _W["pc"].points_in_bbox(x0 - 2, y0 - 2, x1 + 2, y1 + 2, building_only=True)
        dx, dy = accepted(_W["fits"].get(str(bid))) or (0.0, 0.0)
        faces = [f for f in fr.straight_faces(geom, _W["img"], pts, (dx, dy))
                 if f.is_valid and f.geom_type == "Polygon" and f.area >= 1.0]
        if not faces:
            return bid, 0
        edge = unary_union([f.buffer(BRIDGE_M) for f in faces]).buffer(-BRIDGE_M)
        edge = max(edge.geoms, key=lambda q: q.area) if hasattr(edge, "geoms") else edge
        faces = [f for f in faces if f.representative_point().within(edge.buffer(0.05))]
        ring = lambda q: [[round(x + dx, 2), round(y + dy, 2)] for x, y in q.exterior.coords]
        from shapely.affinity import translate
        dst = Path(out) / f"{bid}.json"
        if pts is not None and len(pts) >= MIN_SHARE_PTS:
            mine = _share([translate(f, dx, dy) for f in faces], pts)
            theirs = max((_share(fs, pts) for fs in _incumbent(bid, (dx, dy))), default=0.0)
            if mine < theirs - YIELD_MARGIN:
                dst.unlink(missing_ok=True)
                return bid, -1
        doc = {"source": "student", "score": 1.0, "shift": [dx, dy], "outline": ring(edge),
               "faces": [ring(f) for f in faces], "obstructions": []}
        tmp = Path(out) / f"{bid}.json.tmp"
        tmp.write_text(json.dumps(doc))
        os.replace(tmp, dst)
        return bid, len(faces)
    except Exception as e:                       # one roof never stops a region
        return bid, f"{type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("region")
    ap.add_argument("--ids", nargs="*", type=int)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--all-zones", action="store_true", help="read every building, not homes only")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    import multiprocessing as mp
    import geopandas as gpd
    from src.region_build import area_paths
    from src.emit_region import _zones_by_id
    from src.outline_fit import _marked
    from src.vision_roof import STUDENT_FACES_DIR, VISION_FACES_DIR
    from src import face_regions as fr
    if not fr.MODEL.exists():
        print(f"no model at {fr.MODEL}: nothing read")
        return 1
    out = Path(a.out) if a.out else STUDENT_FACES_DIR
    out.mkdir(parents=True, exist_ok=True)
    p = area_paths(a.region)
    dd = p["dir"] / "building_outlines_dedup.geojson"
    gdf = gpd.read_file(dd if dd.exists() else p["outlines"])
    zones = _zones_by_id(p)
    if not zones and "zone_class" in gdf.columns:
        zones = {int(b): z for b, z in zip(gdf["building_id"], gdf["zone_class"])}
    marked = _marked()
    want = set(a.ids) if a.ids else None
    jobs = []
    for bid, g in zip(gdf["building_id"], gdf.geometry):
        bid = int(bid)
        if (want is not None and bid not in want) or g is None or g.is_empty:
            continue
        if g.geom_type == "MultiPolygon":
            g = max(g.geoms, key=lambda q: q.area)
        if bid in marked or (VISION_FACES_DIR / f"{bid}.json").exists() or g.area < MIN_AREA_M2:
            continue
        if not a.all_zones and zones.get(bid) != "home":
            continue
        jobs.append((bid, g.wkb, str(out)))
    print(f"{a.region}: {len(jobs)} of {len(gdf)} buildings to read ({len(zones)} zoned)", flush=True)
    done = empty = failed = gave = 0
    with mp.get_context("spawn").Pool(a.jobs, initializer=_init, initargs=(a.region,)) as pool:
        for n, (bid, r) in enumerate(pool.imap_unordered(_one, jobs, chunksize=4), 1):
            if isinstance(r, str):
                failed += 1
                if failed <= 5:
                    print(f"  #{bid}: {r}", flush=True)
            elif r == -1:
                gave += 1
            elif r:
                done += 1
            else:
                empty += 1
            if n % 500 == 0:
                print(f"  {n}...", flush=True)
    print(f"{a.region}: {done} roofs read -> {out}; {gave} gave way, {empty} with no faces, {failed} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
