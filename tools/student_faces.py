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
the roof the LiDAR sees (point_roof.register) with the slide recorded, so
the build and the map treat it as they do a vision reading.

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
BRIDGE_M = 0.15
_W = {}


def _init(region):
    import rasterio
    from src.region_build import area_paths
    from src.pointcloud_source import PointCloudSource
    _W["img"] = rasterio.open(area_paths(region)["imagery"])
    _W["pc"] = PointCloudSource(max_cached_tiles=3)


def _one(job):
    bid, wkb, out = job
    from shapely import wkb as _wkb
    from shapely.ops import unary_union
    from src import face_regions as fr, point_roof as pr
    try:
        geom = _wkb.loads(wkb)
        x0, y0, x1, y1 = geom.bounds
        pts = _W["pc"].points_in_bbox(x0 - 2, y0 - 2, x1 + 2, y1 + 2, building_only=True)
        dx, dy = pr.register(geom)
        faces = [f for f in fr.straight_faces(geom, _W["img"], pts, (dx, dy))
                 if f.is_valid and f.geom_type == "Polygon" and f.area >= 1.0]
        if not faces:
            return bid, 0
        edge = unary_union([f.buffer(BRIDGE_M) for f in faces]).buffer(-BRIDGE_M)
        edge = max(edge.geoms, key=lambda q: q.area) if hasattr(edge, "geoms") else edge
        faces = [f for f in faces if f.representative_point().within(edge.buffer(0.05))]
        ring = lambda q: [[round(x + dx, 2), round(y + dy, 2)] for x, y in q.exterior.coords]
        doc = {"source": "student", "score": 1.0, "shift": [dx, dy], "outline": ring(edge),
               "faces": [ring(f) for f in faces], "obstructions": []}
        tmp = Path(out) / f"{bid}.json.tmp"
        tmp.write_text(json.dumps(doc))
        os.replace(tmp, Path(out) / f"{bid}.json")
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
    done = empty = failed = 0
    with mp.get_context("spawn").Pool(a.jobs, initializer=_init, initargs=(a.region,)) as pool:
        for n, (bid, r) in enumerate(pool.imap_unordered(_one, jobs, chunksize=4), 1):
            if isinstance(r, str):
                failed += 1
                if failed <= 5:
                    print(f"  #{bid}: {r}", flush=True)
            elif r:
                done += 1
            else:
                empty += 1
            if n % 500 == 0:
                print(f"  {n}...", flush=True)
    print(f"{a.region}: {done} roofs read -> {out}; {empty} with no faces, {failed} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
