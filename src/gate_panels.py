"""
Placement quality gate, applied as a post-filter over built panel layouts.

Drops any placed panel whose underlying LiDAR contradicts "flat roof
surface here" -- the failure classes from the 23-building field-report set
(docs/bugdoc-2026-08-22.md):

- too few building-class returns beneath it  -> carpark / air / demolished
  (19 Industrial Pl, 10/16 Kent St, 61 Ballarat St)
- surface not meaningfully above bare earth  -> ground-level slab/yard
- points disagree with the local panel plane -> covers vents/plant/level
  changes the obstruction pass missed (17 Marine Pde; audit's 3,459 lumpy)

Runs per region on panel_layouts.geojson IN PLACE (before rerank/deciles/
shrink/tile). The same checks move into fit time with the Wave-1 rebuild;
this post-filter exists so the worst placements leave the live map a
rebuild-cycle earlier.

Usage: python src/gate_panels.py [region ...]   (default: all areas)
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
import pyproj
import rasterio
import shapely.vectorized
from shapely.geometry import shape
from shapely.ops import transform as shp_transform

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.pointcloud_source import PointCloudSource
from src.region_build import all_areas, area_paths

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TO_NZTM = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2193", always_xy=True).transform

MIN_EVIDENCE_PTS = 8        # fewer total returns than this under a panel = survey too thin
# to judge here at all -> keep. (7 Cedar Dr: a real roof at 2.0 pts/m2 total
# had 63 of 69 fitted panels executed by absolute-count thresholds.)
MIN_BUILDING_FRACTION = 0.4  # of the returns that DO exist under a panel, at least this share
# must be building-class for it to count as roof -- a carpark/yard/demolition
# site is well-sampled but its returns are ground/vegetation, a thin-but-real
# roof has few returns that are ALL building. Ratio, not absolute density.
MIN_HEIGHT_ABOVE_DEM = 1.8  # roof surface must clear bare earth by this (m)
MAX_LOCAL_RMS = 0.28        # points under one 2m panel should fit their own plane this well


def panel_ok(poly, pc, dem, dem_transform_inv):
    minx, miny, maxx, maxy = poly.bounds
    area = poly.area
    # A veto requires EVIDENCE AGAINST a roof, never mere absence of data:
    # a LiDAR coverage gap (zero returns of ANY class) means "unknown" and
    # the panel stays. Cost of this lesson: a cropped-tile coverage hole
    # made the first gate run execute 96 healthy houses.
    pts_all = pc.points_in_bbox(minx - 0.3, miny - 0.3, maxx + 0.3, maxy + 0.3, building_only=False)
    if len(pts_all) == 0:
        return True, "no_coverage_kept"
    pts = pc.points_in_bbox(minx - 0.3, miny - 0.3, maxx + 0.3, maxy + 0.3, building_only=True)
    inside_all = shapely.vectorized.contains(poly, pts_all[:, 0], pts_all[:, 1])
    n_all = int(inside_all.sum())
    if n_all < MIN_EVIDENCE_PTS:
        return True, "thin_coverage_kept"  # not enough returns of ANY class to judge
    inside = shapely.vectorized.contains(poly, pts[:, 0], pts[:, 1]) if len(pts) else np.zeros(0, bool)
    pp = pts[inside] if len(pts) else np.empty((0, 3))
    if len(pp) == 0:
        # adequately sampled, zero building returns: carpark, air, demolition
        return False, "sparse"
    # Height-windowed evidence: only returns AT ROOF LEVEL argue about the
    # roof. Canopy metres above a real roof floods the raw all-class count
    # and vetoed 6,759 pilot panels (v1 ratio rule) -- so the denominator is
    # returns within a window of the building surface, not everything in
    # the column.
    roof_z = float(np.median(pp[:, 2]))
    all_in = pts_all[shapely.vectorized.contains(poly, pts_all[:, 0], pts_all[:, 1])]
    near = all_in[np.abs(all_in[:, 2] - roof_z) < 1.2]
    if len(pp) < MIN_BUILDING_FRACTION * max(len(near), 1):
        # what exists at this height is mostly NOT building surface
        return False, "sparse"
    # NO height-above-DEM test. The wide DEM is 8m-resolution smoothed bare
    # earth: on sloping ground its cell averages uphill terrain, so a real
    # single-storey roof can sit <1m above it (4 Abbottswood Ln: roof 392.9,
    # DEM 391.4 -> every panel wrongly read as ground-level, including the
    # north face that carries REAL installed panels in the photo). Height is
    # already implied by LAS building classification, which is per-return and
    # far more reliable here; a rooftop parking deck is an exclusion-list case,
    # not a height-rule case.
    # local planarity: the points under one panel must fit their own plane
    if len(pp) >= 6:
        x0, y0 = pp[:, 0].mean(), pp[:, 1].mean()
        A = np.column_stack([pp[:, 0] - x0, pp[:, 1] - y0, np.ones(len(pp))])
        try:
            coeffs, *_ = np.linalg.lstsq(A, pp[:, 2], rcond=None)
            rms = float(np.sqrt(np.mean((A @ coeffs - pp[:, 2]) ** 2)))
            if rms > MAX_LOCAL_RMS:
                return False, "lumpy"
        except np.linalg.LinAlgError:
            pass
    return True, "ok"


def gate_area(name, pc, dem, dem_inv):
    import config
    non_roof = getattr(config, "NON_ROOF_BUILDING_IDS", set())
    path = area_paths(name)["panel_layouts"]
    if not path.exists():
        print(f"{name}: no layouts, skipping")
        return
    d = json.loads(path.read_text())
    kept, dropped = [], {"no_points": 0, "sparse": 0, "ground_level": 0, "lumpy": 0}
    for f in d["features"]:
        if f["properties"].get("kind") != "panel" or f["geometry"]["type"] != "Polygon":
            kept.append(f)
            continue
        if f["properties"].get("building_id") in non_roof:
            dropped["sparse"] += 1
            continue
        try:
            poly = shp_transform(TO_NZTM, shape(f["geometry"]))
            ok, why = panel_ok(poly, pc, dem, dem_inv)
        except Exception:
            ok, why = True, "error-kept"  # never drop a panel on a gate crash
        if ok:
            kept.append(f)
        else:
            dropped[why] += 1
    n_dropped = sum(dropped.values())
    d["features"] = kept
    path.write_text(json.dumps(d))
    print(f"{name}: dropped {n_dropped} panels "
          f"(air/no-points {dropped['no_points']}, sparse {dropped['sparse']}, "
          f"ground-level {dropped['ground_level']}, lumpy {dropped['lumpy']})")


def main():
    pc = PointCloudSource()
    with rasterio.open(DATA_DIR / "dem_wide_mosaic.tif") as ds:
        dem = ds.read(1)
        dem_inv = ~ds.transform
    for name in (sys.argv[1:] or all_areas()):
        gate_area(name, pc, dem, dem_inv)


if __name__ == "__main__":
    main()
