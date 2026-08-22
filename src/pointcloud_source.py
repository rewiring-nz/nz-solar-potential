"""
Read the raw LiDAR point cloud tiles (data/pointcloud/*.copc.laz) as a
higher-resolution stand-in for the 1m gridded DSM.

Confirmed directly on a real tile before building this: ~8.6-8.8 points/m2
overall, ~5.8 points/m2 restricted to LAS classification 6 ("building")
alone -- roughly 6-9x the density the 1m DSM grid implies (1 point/m2 by
construction), plus classification lets us throw out tree/ground returns
the DSM has no way to distinguish from roof.

Rather than rewrite roof_segmentation.py's RANSAC/shape/dedupe pipeline to
work on an irregular point set directly (a bigger, riskier rewrite), this
rasterizes the point cloud onto a finer regular grid (median z per cell)
and hands back the exact (window_array, window_transform) shape a DSM read
already produces -- everything downstream of points_from_window is reused
completely unchanged, exactly as this project's own earlier documentation
anticipated ("a drop-in upgrade to points_from_window without touching the
RANSAC/vectorize code").
"""
import sys
from pathlib import Path

import laspy
import numpy as np
from affine import Affine
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

POINTCLOUD_DIR = Path(__file__).resolve().parent.parent / "data" / "pointcloud"
BUILDING_CLASSIFICATION = 6  # LAS standard classification code
MIN_BUILDING_POINTS = 10  # below this, the classification filter isn't trustworthy -- use all points instead


class PointCloudSource:
    """Tile bounds are read from LAZ headers only (cheap) at construction;
    a tile's actual points are decoded from disk on first use and cached
    in memory after that."""

    def __init__(self, directory=POINTCLOUD_DIR):
        self.tile_paths = sorted(Path(directory).glob("*.copc.laz"))
        if not self.tile_paths:
            raise FileNotFoundError(f"No .copc.laz tiles found in {directory}")
        self._bounds = {}
        for path in self.tile_paths:
            with laspy.open(path) as f:
                h = f.header
                self._bounds[path] = (h.mins[0], h.mins[1], h.maxs[0], h.maxs[1])
        self._cache = {}

    def _tiles_overlapping(self, minx, miny, maxx, maxy):
        return [
            path for path, (tminx, tminy, tmaxx, tmaxy) in self._bounds.items()
            if tminx <= maxx and tmaxx >= minx and tminy <= maxy and tmaxy >= miny
        ]

    def _load_tile(self, path):
        if path not in self._cache:
            las = laspy.read(path)
            self._cache[path] = (
                np.asarray(las.x, dtype=np.float64), np.asarray(las.y, dtype=np.float64),
                np.asarray(las.z, dtype=np.float64), np.asarray(las.classification),
            )
        return self._cache[path]

    def points_in_bbox(self, minx, miny, maxx, maxy, building_only=True):
        """Returns Nx3 (x, y, z) array."""
        xs, ys, zs, classes = [], [], [], []
        for path in self._tiles_overlapping(minx, miny, maxx, maxy):
            tx, ty, tz, tc = self._load_tile(path)
            mask = (tx >= minx) & (tx <= maxx) & (ty >= miny) & (ty <= maxy)
            if not mask.any():
                continue
            xs.append(tx[mask]); ys.append(ty[mask]); zs.append(tz[mask]); classes.append(tc[mask])
        if not xs:
            return np.empty((0, 3))
        x, y, z, c = np.concatenate(xs), np.concatenate(ys), np.concatenate(zs), np.concatenate(classes)
        if building_only:
            building_mask = c == BUILDING_CLASSIFICATION
            if building_mask.sum() >= MIN_BUILDING_POINTS:
                return np.column_stack([x[building_mask], y[building_mask], z[building_mask]])
        return np.column_stack([x, y, z])


def rasterize_pointcloud_window(pc_source, building_geom, resolution, pad_m=2.0):
    """Bins point-cloud points inside (a small pad around) building_geom's
    bounds onto a regular grid at `resolution` -- median z per cell, same
    "one representative height per cell" idea a DSM already embodies, just
    at a finer grid and from real classified building points instead of
    whatever a coarser cell's highest return happened to be. Returns
    (window_array[1,H,W], window_transform, nodata) matching
    rasterio.mask.mask's return shape, so points_from_window can consume
    it exactly as it already does for a real DSM read. Returns
    (None, None, None) if too few points fall in the window to be useful."""
    minx, miny, maxx, maxy = building_geom.bounds
    minx, miny, maxx, maxy = minx - pad_m, miny - pad_m, maxx + pad_m, maxy + pad_m
    pts = pc_source.points_in_bbox(minx, miny, maxx, maxy, building_only=True)
    if len(pts) < MIN_BUILDING_POINTS:
        return None, None, None

    width = max(1, int(np.ceil((maxx - minx) / resolution)))
    height = max(1, int(np.ceil((maxy - miny) / resolution)))
    transform = Affine(resolution, 0, minx, 0, -resolution, maxy)

    col = np.clip(((pts[:, 0] - minx) / resolution).astype(np.int64), 0, width - 1)
    row = np.clip(((maxy - pts[:, 1]) / resolution).astype(np.int64), 0, height - 1)
    flat = row * width + col

    order = np.argsort(flat)
    flat_sorted, z_sorted = flat[order], pts[order, 2]
    unique_idx, start = np.unique(flat_sorted, return_index=True)
    bounds_idx = np.append(start, len(flat_sorted))
    medians = np.array([
        np.median(z_sorted[bounds_idx[i]:bounds_idx[i + 1]]) for i in range(len(unique_idx))
    ])

    nodata = -9999.0
    grid = np.full(height * width, nodata, dtype=np.float32).reshape(height, width)
    grid.flat[unique_idx] = medians

    # A real DSM is interpolated to be gapless; a naive per-cell bin of raw
    # points isn't -- confirmed directly: even at a resolution matched to
    # the point cloud's average density, individual cells routinely have
    # zero points (scan-pattern gaps, occlusion), so an unfilled grid comes
    # out mostly *empty* (measured 15% filled at 0.3m for a real building),
    # breaking the connected-component step downstream (ndimage.label sees
    # a field of disconnected single-cell islands instead of one
    # contiguous roof) -- which is why an earlier, unfilled version of this
    # function made segmentation dramatically *worse* despite denser input
    # data. Nearest-neighbour fill closes the gaps the same way DSM
    # production already does; cells that end up filled from far outside
    # the true roof get clipped away later when the facet is intersected
    # against the (accurate, imagery-derived) building outline anyway.
    invalid = grid == nodata
    if invalid.any() and not invalid.all():
        nearest_idx = ndimage.distance_transform_edt(invalid, return_distances=False, return_indices=True)
        grid = grid[tuple(nearest_idx)]

    window_array = grid.reshape(1, height, width)
    return window_array, transform, nodata
