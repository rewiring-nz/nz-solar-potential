"""The outline moved onto the roof the LiDAR sees, where it is clearly off.

THE PROBLEM. Every face, panel and 3D wall is clipped to the LINZ outline.
Where the outline is offset from the roof, the faces hang over the ground on
one side and miss roof on the other. 41 Ballarat St (4735359): its pyramid's
apex in the returns sits 1.4 m north-west of the outline's centre, and the
south-east panels sat over the yard. LINZ outlines here are traced on the 2021
photo, and a traced roof edge carries the photo's lean and the tracer's error.

THE RULE. Per building, the cells (CELL_M) holding roof-height returns
(MIN_HEIGHT_M above the local ground, LAS class 6, not on a mapped
neighbour, connected to this roof) are the roof. The outline is slid over
them in SHIFT_STEP_M steps up to SHIFT_MAX_M; the slide that best covers them
(intersection over union) is taken when it is a clear win: at least
MIN_SHIFT_M, raising the IoU by MIN_GAIN, to at least MIN_FIT_IOU, leaving behind a strip that is mostly not roof (VACATED_MAX).
Anything
less leaves the outline where it is -- tools/measure_outline_offset.py found
on 30 marked roofs that most outlines are the right place and the wrong
shape (eaves), which a slide cannot fix and should not try to.

The shape is not changed: growing outlines to the eaves fragmented faces
(geometry lessons), and a roof the LiDAR reads thinly would shrink. Roofs
with hand-drawn markup are not moved: the markup governs their geometry.

Writes data/regions/<r>/outline_fit.json:
    {building_id: [dx_east_m, dy_north_m, iou_before, iou_after, vacated_roof_share]}
for every building measured. The stages that clip to the outline read it
through fitted_outlines() when SOLAR_OUTLINE_FIT=1.

Usage: python src/outline_fit.py <region> [--ids ...]
"""

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CELL_M = 0.25
PAD_M = 3.0
MIN_HEIGHT_M = 2.0       # gate_panels' bar for roof, not ground
SHIFT_MAX_M = 2.0        # traced offsets on the pilot renders were 0.5-1.4 m; every slide
# found past 1.75 m (5370967, 4735215, 4726056) was chasing an extension LINZ
# drew short, not an offset outline
SHIFT_STEP_M = 0.25
MIN_SHIFT_M = 0.75       # eaves overhang 0.45 m (median, tools/measure_outline_offset.py),
# and slides of 0.5-0.7 m moved two of six identical townhouses on
# Hallenstein St and not the other four: eave noise, not an offset
MIN_GAIN = 0.05          # 41 Ballarat St: 0.80 -> 0.92
MIN_FIT_IOU = 0.80       # the slid outline must then fit the roof well
MIN_ROOF_CELLS = 200     # 12.5 m2 of returns
VACATED_MAX = 0.35       # share of the vacated strip that may be roof
USE = os.environ.get("SOLAR_OUTLINE_FIT", "0") == "1"


def roof_cells(pc, geom, others, ground_z=None):
    """(roof, inside, origin): boolean grids over the padded outline."""
    import shapely
    from scipy import ndimage
    minx, miny, maxx, maxy = geom.bounds
    x0, y0 = minx - PAD_M, miny - PAD_M
    nx = int((maxx - minx + 2 * PAD_M) / CELL_M) + 1
    ny = int((maxy - miny + 2 * PAD_M) / CELL_M) + 1
    pts = pc.points_in_bbox(x0, y0, x0 + nx * CELL_M, y0 + ny * CELL_M, building_only=True)
    if ground_z is None:
        g = pc.ground_points_in_bbox(x0 - 5, y0 - 5, x0 + nx * CELL_M + 5, y0 + ny * CELL_M + 5)
        if g is None or len(g) < 20:
            return None
        ground_z = float(np.percentile(g[:, 2], 50))
    gx = x0 + (np.arange(nx) + 0.5) * CELL_M
    gy = y0 + (np.arange(ny) + 0.5) * CELL_M
    XX, YY = np.meshgrid(gx, gy)
    inside = shapely.contains_xy(geom, XX.ravel(), YY.ravel()).reshape(ny, nx)
    roof = np.zeros((ny, nx), dtype=bool)
    if pts is not None and len(pts):
        hi = pts[pts[:, 2] >= ground_z + MIN_HEIGHT_M]
        ix = ((hi[:, 0] - x0) / CELL_M).astype(int)
        iy = ((hi[:, 1] - y0) / CELL_M).astype(int)
        ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
        roof[iy[ok], ix[ok]] = True
    # sparse returns leave holes a cell wide: close them
    roof = ndimage.binary_closing(roof, structure=np.ones((3, 3)), iterations=1)
    if others is not None and not others.is_empty:
        roof &= ~shapely.contains_xy(others, XX.ravel(), YY.ravel()).reshape(ny, nx)
    seed = roof & inside
    if seed.any():
        lab, _ = ndimage.label(roof, structure=np.ones((3, 3), dtype=int))
        roof = np.isin(lab, list(set(np.unique(lab[seed])) - {0}))
    return roof, inside


def best_slide(roof, inside):
    """(dx, dy, iou) of the slide of `inside` that best covers `roof`."""
    steps = int(round(SHIFT_MAX_M / CELL_M))
    every = int(round(SHIFT_STEP_M / CELL_M))
    best = (0.0, 0.0, -1.0)
    for sy in range(-steps, steps + 1, every):
        for sx in range(-steps, steps + 1, every):
            sh = np.roll(np.roll(inside, sy, axis=0), sx, axis=1)
            u = (sh | roof).sum()
            iou = (sh & roof).sum() / u if u else 0.0
            if iou > best[2] + 1e-9 or (abs(iou - best[2]) < 1e-9
                                         and sx * sx + sy * sy < (best[0] ** 2 + best[1] ** 2) / CELL_M ** 2):
                best = (sx * CELL_M, sy * CELL_M, iou)
    return best


def measure(pc, geom, others):
    """[dx, dy, iou_before, iou_after] for one outline, or None."""
    r = roof_cells(pc, geom, others)
    if r is None:
        return None
    roof, inside = r
    if roof.sum() < MIN_ROOF_CELLS:
        return None
    u = (roof | inside).sum()
    iou0 = (roof & inside).sum() / u if u else 0.0
    dx, dy, iou1 = best_slide(roof, inside)
    sh = np.roll(np.roll(inside, int(round(dy / CELL_M)), axis=0), int(round(dx / CELL_M)), axis=1)
    left = inside & ~sh
    vacated = float((left & roof).sum() / left.sum()) if left.any() else 0.0
    return [round(dx, 2), round(dy, 2), round(float(iou0), 3), round(float(iou1), 3),
            round(vacated, 3)]


def accepted(entry):
    """The slide (dx, dy) to apply, or None."""
    if not entry:
        return None
    dx, dy, iou0, iou1 = entry[:4]
    if (dx * dx + dy * dy) ** 0.5 < MIN_SHIFT_M or iou1 - iou0 < MIN_GAIN or iou1 < MIN_FIT_IOU:
        return None
    # An offset outline leaves ground behind it. Where the strip it vacates is
    # itself roof, the roof runs on past the outline -- an extension or a
    # neighbour LINZ did not split off (5370967, a 2 m slide along a long
    # roof that continues south-east) -- and sliding only trades one edge
    # for the other.
    if len(entry) > 4 and entry[4] > VACATED_MAX:
        return None
    # A slide that runs to the edge of the search is chasing roof that is not
    # this outline's: 4735215's best slide (2.5, -2.25 m) dragged the whole
    # outline onto a lean-to beyond its south-east wall. An offset outline has
    # its best fit inside the search.
    if max(abs(dx), abs(dy)) >= SHIFT_MAX_M - SHIFT_STEP_M / 2:
        return None
    return dx, dy


def _marked():
    p = Path(__file__).resolve().parent.parent / "data" / "roof_labels.json"
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return set()
    return {int(k) for k in (d.get("buildings", d) or {}) if str(k).isdigit()}


def fitted_outlines(gdf, region_dir, use=None):
    """`gdf` with each accepted slide applied to its outline (in place, and
    returned). A no-op unless SOLAR_OUTLINE_FIT=1 or `use`."""
    if not (USE if use is None else use):
        return gdf
    fp = Path(region_dir) / "outline_fit.json"
    try:
        fits = json.loads(fp.read_text())
    except (OSError, ValueError):
        return gdf
    from shapely.affinity import translate
    marked = _marked()
    col = gdf.geometry.name
    ids = gdf["building_id"] if "building_id" in gdf.columns else gdf.index
    new = []
    for bid, g in zip(ids, gdf.geometry):
        s = None if int(bid) in marked else accepted(fits.get(str(int(bid))))
        new.append(translate(g, s[0], s[1]) if s else g)
    gdf[col] = new
    return gdf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("region")
    ap.add_argument("--ids", nargs="*", type=int, default=None)
    ap.add_argument("--out", default=None, help="write here instead of the region's outline_fit.json")
    a = ap.parse_args()
    import geopandas as gpd
    from shapely.ops import unary_union
    from shapely.strtree import STRtree
    from src.region_build import area_paths, write_json_atomic
    from src.pointcloud_source import PointCloudSource
    p = area_paths(a.region)
    gdf = gpd.read_file(p["outlines"])
    pc = PointCloudSource(max_cached_tiles=4)
    geoms = list(gdf.geometry)
    bids = [int(b) for b in gdf["building_id"]]
    tree = STRtree(geoms)
    want = set(a.ids) if a.ids else None
    out = {}
    for k, (bid, g) in enumerate(zip(bids, geoms)):
        if want and bid not in want:
            continue
        near = [geoms[int(j)] for j in tree.query(g.buffer(PAD_M + 1)) if int(j) != k]
        try:
            m = measure(pc, g, unary_union(near) if near else None)
        except Exception:
            m = None
        if m:
            out[str(bid)] = m
    write_json_atomic(Path(a.out) if a.out else p["dir"] / "outline_fit.json", out)
    moved = [v for v in out.values() if accepted(v)]
    print(f"{a.region}: {len(out)} outlines measured, {len(moved)} slid onto the roof "
          f"(median {np.median([np.hypot(v[0], v[1]) for v in moved]) if moved else 0:.2f} m)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
