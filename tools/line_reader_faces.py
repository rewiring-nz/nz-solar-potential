#!/usr/bin/env python3
"""Roof faces read by the roof-line reader (tools/train_roof_lines.py), written
as readings the build takes (data/student_faces format).

The reader was trained in the frame of the map's photo, so each roof is read
there: the roof edge (the edge fitted to the returns where there is one, else
the surveyed outline) and the LiDAR returns are both moved by the roof's
drawing shift onto the photo, the faces are read, and they are moved back and
stored with that slide, as a photo reading is.

    SOLAR_FACE_REGION_ARCH=dino SOLAR_FACE_REGION_MODEL=<model.pt> \\
        python tools/line_reader_faces.py <region> --out <dir> [--ids ...] [--jobs N]

SOLAR_LINE_READER=graph SOLAR_LINE_READER_MODEL=<model.pt> reads with the
typed reader instead (src/line_reader.py): the map's display photo, the roof
read twice, faces from joins and straight lines.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
MIN_AREA_M2 = 30.0
MIN_RETURNS_PER_M2 = 1.0
BRIDGE_M = 0.15
GRAPH = os.environ.get("SOLAR_LINE_READER", "") == "graph"
_W = {}


def _init(region):
    import rasterio
    from src import point_roof as pr, register_imagery as ri
    from src.region_build import area_paths
    from src.pointcloud_source import PointCloudSource
    p = area_paths(region)
    _W["img"] = rasterio.open(p["imagery"])
    _W["tiles"] = ri._Tiles(p["dir"] / "display_tiles")
    _W["O"] = pr.outlines(region)
    _W["shift"] = ri.drawing_shifts(p["dir"])
    _W["pc"] = PointCloudSource(max_cached_tiles=3)


def _one(job):
    bid, out = job
    import numpy as np
    import shapely
    from shapely.affinity import translate
    from shapely.ops import unary_union
    from src import face_regions as fr, point_roof as pr
    try:
        geom = pr.point_outline(bid) or _W["O"][bid]
        if geom.area < MIN_AREA_M2:
            return bid, -3
        sx, sy = (_W["shift"].get(str(bid)) or [0.0, 0.0])[:2]
        x0, y0, x1, y1 = geom.bounds
        pts = _W["pc"].points_in_bbox(x0 - 6, y0 - 6, x1 + 6, y1 + 6, building_only=True)
        on_roof = 0 if pts is None or not len(pts) else int(shapely.contains_xy(geom, pts[:, 0], pts[:, 1]).sum())
        if on_roof < MIN_RETURNS_PER_M2 * geom.area:
            return bid, -2
        pts = pts + np.array([sx, sy, 0.0])
        if GRAPH:
            # the typed reader, read twice (src/line_reader.py)
            from src import line_reader as lr
            faces, lines = lr.roof_faces(translate(geom, sx, sy), _W["tiles"], pts)
            faces = [f for f in faces if f.is_valid and f.geom_type == "Polygon" and f.area >= 1.0]
        else:
            faces = [f for f in fr.straight_faces(translate(geom, sx, sy), _W["img"], pts, (0.0, 0.0))
                     if f.is_valid and f.geom_type == "Polygon" and f.area >= 1.0]
        if not faces:
            return bid, 0
        # square joins: a round join gives every corner of the roof edge a dozen points
        edge = unary_union([f.buffer(BRIDGE_M, join_style=2) for f in faces]).buffer(-BRIDGE_M, join_style=2).simplify(0.05)
        edge = max(edge.geoms, key=lambda q: q.area) if hasattr(edge, "geoms") else edge
        faces = [f for f in faces if f.representative_point().within(edge.buffer(0.05))]
        ring = lambda q: [[round(x - sx, 2), round(y - sy, 2)] for x, y in q.exterior.coords]
        doc = {"source": "student", "reader": "lines", "score": 1.0, "shift": [-sx, -sy], "outline": ring(edge),
               "faces": [ring(f) for f in faces], "obstructions": [],
               # the reader's lines, for a markup draft (build_label_bundle --seed-lines)
               "lines": [{"a": [round(a[0] - sx, 2), round(a[1] - sy, 2)], "b": [round(q[0] - sx, 2), round(q[1] - sy, 2)], "kind": k}
                         for a, q, k in (lines if GRAPH else [])]}
        dst = Path(out) / f"{bid}.json"
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
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    Path(a.out).mkdir(parents=True, exist_ok=True)
    from src import point_roof as pr
    O = pr.outlines(a.region)
    ids = [b for b in (a.ids or sorted(O)) if b in O]
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(a.jobs, initializer=_init, initargs=(a.region,)) as ex:
        res = list(ex.map(_one, [(b, a.out) for b in ids], chunksize=4))
    bad = [r for r in res if not isinstance(r[1], int)]
    print(f"line_reader_faces {a.region}: {sum(1 for r in res if isinstance(r[1], int) and r[1] > 0)} of {len(ids)} read; "
          f"{sum(1 for r in res if r[1] == 0)} no faces, {sum(1 for r in res if r[1] == -2)} too few returns, {len(bad)} errors")
    for b, e in bad[:5]:
        print("  ", b, e)


if __name__ == "__main__":
    main()
