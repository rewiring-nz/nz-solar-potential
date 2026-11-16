"""Point roof against the free model, each allowed its own best slide.

Every earlier comparison slid the markup by the outline overlap, which is
insensitive to a metre of slide (BACKLOG, 16 Nov), so a method drawn in the
photo's frame and one in the LiDAR's were not compared on equal terms. Here
each method's faces are slid as one piece to where their lines best match
the markup's faces and strokes, then scored at 0.3 m and 0.75 m. Roofs: the
35 pinned benchmark roofs the free model never trained on
(data/face_regions/val), less any held-back roof.

Usage: python tools/experiments/fair_compare.py out.json
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
import numpy as np


def main():
    import geopandas as gpd
    import rasterio
    from shapely.geometry import Polygon, LineString
    from shapely.ops import unary_union
    from shapely.affinity import translate
    import markup_benchmark as M
    from src import face_regions as fr
    from src import point_roof as pr
    from src.region_build import area_paths
    from src.pointcloud_source import PointCloudSource
    B = json.loads(Path("data/roof_labels.json").read_text())["buildings"]
    hold = {int(h["id"]) for h in json.load(open("data/roof_set.json"))["holdout"]}
    p = area_paths("pilot")
    gdf = gpd.read_file(p["dir"] / "building_outlines_dedup.geojson").set_index("building_id", drop=False)
    img = rasterio.open(p["imagery"])
    pc = PointCloudSource(max_cached_tiles=3)
    O = pr.outlines("pilot")
    close = lambda ps: unary_union([q.buffer(0.15) for q in ps]).buffer(-0.15)
    rows = []
    for f in sorted(os.listdir("data/face_regions/val")):
        bid = int(f[:-4])
        v = B.get(str(bid))
        if bid in hold or not v or not v.get("faces") or bid not in O:
            continue
        drawn = [q for q in (Polygon(fc["ring"]).buffer(0) for fc in v["faces"]) if q.area > 0.5]
        o = close(drawn)
        dl = M._lines(drawn, o)
        st = [LineString([(q[0], q[1]) for q in l["points"]]) for l in (v.get("lines") or [])
              if len(l.get("points") or []) >= 2]
        if st:
            u = unary_union(st).difference(o.boundary.buffer(M.OUTLINE_BAND_M))
            if dl is not None:
                u = u.difference(dl.buffer(0.6))
            if not u.is_empty:
                dl = u if dl is None else unary_union([dl, u])
        if dl is None or dl.length < 2:
            continue

        def best(polys):
            polys = [q for q in polys if q.is_valid and q.area > 0.5]
            if not polys:
                return dict(f03=0.0, f075=0.0, n=0)
            gl = M._lines(polys, close(polys))
            if gl is None:
                return dict(f03=0.0, f075=0.0, n=len(polys))

            def f1(d, tol):
                rr = d.intersection(gl.buffer(tol)).length / d.length
                pp = gl.intersection(d.buffer(tol)).length / gl.length
                return 2 * rr * pp / max(rr + pp, 1e-9)
            b = (f1(dl, 0.3), 0.0, 0.0)
            for dx in np.arange(-3, 3.01, 0.25):
                for dy in np.arange(-3, 3.01, 0.25):
                    s = f1(translate(dl, dx, dy), 0.3)
                    if s > b[0]:
                        b = (s, dx, dy)
            return dict(f03=b[0], f075=f1(translate(dl, b[1], b[2]), 0.75), n=len(polys), slide=[float(b[1]), float(b[2])])

        geom = gdf.loc[bid].geometry
        if geom.geom_type == "MultiPolygon":
            geom = max(geom.geoms, key=lambda g: g.area)
        x0, y0, x1, y1 = geom.bounds
        pts = pc.points_in_bbox(x0 - 2, y0 - 2, x1 + 2, y1 + 2, building_only=True)
        raw = [c for c in fr.learned_faces(geom, img, pts) if c.is_valid and c.area > 0.5]
        sh = pr.register(unary_union(raw).buffer(0.15).buffer(-0.15)) if raw else (0, 0)
        stu = fr.straight_faces(geom, img, pts, sh)
        r = pr.reconstruct(bid, O[bid])
        row = dict(id=bid, nd=len(drawn), student=best(stu), point=best([fc["poly"] for fc in r["faces"]]))
        rows.append(row)
        print(bid, "drawn", len(drawn), "| free model", row["student"]["n"], round(row["student"]["f03"], 2), round(row["student"]["f075"], 2),
              "| point roof", row["point"]["n"], round(row["point"]["f03"], 2), round(row["point"]["f075"], 2), flush=True)
    json.dump(rows, open(sys.argv[1], "w"))
    for name in ("student", "point"):
        a = np.array([r[name]["f03"] for r in rows]); b = np.array([r[name]["f075"] for r in rows])
        print(f"{name:8s} n={len(rows)} line F at 0.3 m median {np.median(a):.2f}, roofs >= 0.7: {int((a >= .7).sum())} | at 0.75 m median {np.median(b):.2f}, roofs >= 0.7: {int((b >= .7).sum())}")
    w = sum(r["point"]["f03"] > r["student"]["f03"] + 0.05 for r in rows); l = sum(r["point"]["f03"] < r["student"]["f03"] - 0.05 for r in rows)
    print("point roof better on", w, "roofs, free model better on", l, "same on", len(rows) - w - l)
    small = [r for r in rows if r["nd"] <= 12]
    w = sum(r["point"]["f03"] > r["student"]["f03"] + 0.05 for r in small); l = sum(r["point"]["f03"] < r["student"]["f03"] - 0.05 for r in small)
    print("roofs with 12 faces or fewer:", len(small), "| point better", w, "| free model better", l)


if __name__ == "__main__":
    main()
