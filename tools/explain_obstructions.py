"""Which detector drew each obstruction on a roof, and on what evidence.

WHY. "Obstructions over clear space" (9 Marine Parade, 8 Sydney Street) and
"panels over unmarked obstructions" (1 Ballarat Street) are threshold
questions, but the shipped layout cannot answer them: the five detectors
(colour, height, height confirmed by colour, bright/skylight, sunken) are
merged into plain shapes before anything is written. This rebuilds one
building with the real layout stage, keeps each detector's shapes apart
(detect_obstructions_combined's `explain`), and attributes every final
obstruction to the detectors that drew it, with the LiDAR under it: how far
its returns stand off the face's plane. Tuning a threshold starts from this
table, not from a screenshot.

Run on the build VM (needs the region's point cloud and imagery):
    python tools/explain_obstructions.py 5370328 [--region queenstown_cbd]
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def explain(bid, region=None, log=print):
    import geopandas as gpd
    import numpy as np
    import shapely
    from src.region_build import area_paths, area_centroid_wgs84
    from src.solar_model import SolarModel
    import src.build_layout_geojson as blg

    if region is None:
        from tools.cases import _find_region
        region = _find_region(bid)
    if not region:
        raise SystemExit(f"#{bid}: not in any region on this machine")
    p = area_paths(region)
    dd = p["dir"] / "building_outlines_dedup.geojson"
    gdf = gpd.read_file(dd if dd.exists() else p["outlines"]).set_index("building_id", drop=False)
    if bid not in gdf.index:
        raise SystemExit(f"#{bid}: not in {region}'s outlines")
    c = area_centroid_wgs84(region)
    blg._init_worker(region, SolarModel(*c) if c else SolarModel())

    facets = []   # (facet geometry, plane, {detector: [shapes]})
    real = blg.detect_obstructions_combined

    def spy(imagery_ds, pc_source, facet_geom, plane, **kw):
        ex = {}
        out = real(imagery_ds, pc_source, facet_geom, plane, explain=ex, **kw)
        facets.append((facet_geom, plane, ex, pc_source))
        return out
    blg.detect_obstructions_combined = spy
    try:
        feats = blg._build_one(bid)
    finally:
        blg.detect_obstructions_combined = real

    import pyproj
    from shapely.geometry import shape
    from shapely.ops import transform
    to_nztm = pyproj.Transformer.from_crs(4326, 2193, always_xy=True).transform
    shipped = [transform(to_nztm, shape(f["geometry"])) for f in feats
               if f["properties"]["kind"] == "obstruction"]
    roof = sum(g.area for g, *_ in facets)
    log(f"#{bid} ({region}): {len(facets)} modelled faces, {roof:.0f} m2; "
        f"{len(shipped)} obstructions, {sum(o.area for o in shipped):.1f} m2")
    totals = defaultdict(float)
    rows = []
    for o in shipped:
        by = defaultdict(float)
        stand, best_ov = None, 0.0
        for g, plane, ex, pcs in facets:
            if not g.intersects(o):
                continue
            for name, shapes in ex.items():
                for s in shapes:
                    if s.intersects(o):
                        by[name] += s.intersection(o).area
            # against the plane of the face it mostly sits on, not the first it touches
            ov = g.intersection(o).area
            if pcs is not None and ov > best_ov:
                best_ov = ov
                minx, miny, maxx, maxy = o.bounds
                pts = pcs.points_in_bbox(minx, miny, maxx, maxy, building_only=True)
                if len(pts):
                    pts = pts[shapely.contains_xy(o, pts[:, 0], pts[:, 1])]
                if len(pts) >= 5:
                    a, b, cc = plane
                    r = pts[:, 2] - (a * pts[:, 0] + b * pts[:, 1] + cc)
                    stand = (len(pts), float(np.median(r)), float(np.percentile(r, 90)))
        for k, v in by.items():
            totals[k] += v
        src = ", ".join(f"{k} {v / o.area:.0%}" for k, v in sorted(by.items(), key=lambda t: -t[1])) or "drawn"
        rect = o.area / o.minimum_rotated_rectangle.area if o.area else 0.0
        lid = (f"{stand[0]} returns, median {stand[1]:+.2f} m, p90 {stand[2]:+.2f} m"
               if stand else "no returns")
        rows.append((o.area, f"  {o.area:7.1f} m2  fill {rect:.2f}  [{src}]  {lid}"))
    for _, line in sorted(rows, reverse=True):
        log(line)
    if totals:
        log("  by detector: " + ", ".join(f"{k} {v:.1f} m2" for k, v in
                                          sorted(totals.items(), key=lambda t: -t[1])))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("building_id", type=int, nargs="+")
    ap.add_argument("--region")
    a = ap.parse_args()
    for bid in a.building_id:
        explain(bid, a.region)


if __name__ == "__main__":
    main()
