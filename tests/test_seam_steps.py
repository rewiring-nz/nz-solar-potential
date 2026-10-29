"""Seam steps (tools/layout_health.py): a plane gap the DSM does not show is
counted; a real step between roof levels is not."""
import math
import sys
from pathlib import Path

import numpy as np
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import layout_health as LH

PITCH = 20.0
T = math.tan(math.radians(PITCH))


def _dsm(surface):
    """A 1 m DSM of surface(x, y) over 0..30 m, each cell the mean of 10 x 10
    sub-samples (a cell averages the returns in it), on 2 m ground."""
    n, sub = 30, 10
    c = (np.arange(n * sub) + 0.5) / sub
    X, Y = np.meshgrid(c, c[::-1])
    z = surface(X, Y).reshape(n, sub, n, sub).mean(axis=(1, 3)).astype("float32")
    mem = MemoryFile()
    ds = mem.open(driver="GTiff", width=n, height=n, count=1, dtype="float32", crs="EPSG:2193",
                  transform=from_origin(0, n, 1, 1), nodata=-9999)
    ds.write(z, 1)
    return ds


def _gable(X, Y, crest=15.0):
    """East-west ridge at y = crest, house 5..25 x 8..22."""
    on = (X >= 5) & (X <= 25) & (Y >= 8) & (Y <= 22)
    return np.where(on, 10.0 - T * np.abs(Y - crest), 2.0)


def _faces(edge_y, levels=False):
    south = (box(5, 8, 25, edge_y), {"slope_deg": PITCH, "aspect_deg": 180.0})
    north = (box(5, edge_y, 25, 22), {"slope_deg": PITCH, "aspect_deg": 0.0})
    if levels:
        # two north-facing slopes, the southern one a storey lower
        south = (box(5, 8, 25, edge_y), {"slope_deg": PITCH, "aspect_deg": 0.0})
    return [south, north]


def _steps(surface, faces):
    ds = _dsm(surface)
    H = LH._dsm_sampler(ds, box(5, 8, 25, 22))
    return LH._seam_steps(faces, H)


def test_ridge_on_the_crest_is_clean():
    (s,) = _steps(_gable, _faces(15.0))
    assert abs(s["gap"]) < 0.15, s
    assert s["miss"] <= LH.STEP_OFF_M, s


def test_ridge_off_the_crest_is_counted():
    # drawn 1.5 m south of the crest: each plane still fits its own side, but
    # they cross 1.5 m from the edge and step ~2 * 1.5 * tan(20) = 1.1 m there
    (s,) = _steps(_gable, _faces(13.5))
    assert s["crease"] > 0.5, s
    assert s["miss"] > LH.STEP_OFF_M, s
    assert 1.0 < s["shift"] < 2.0, s


def test_real_step_between_levels_is_not_counted():
    # a north-facing roof whose southern half is a storey (1.2 m) lower,
    # stepping at y = 15: the plane gap is real
    def levels(X, Y):
        on = (X >= 5) & (X <= 25) & (Y >= 8) & (Y <= 22)
        z = 12.0 - T * (Y - 8) - np.where(Y < 15, 1.2, 0.0)
        return np.where(on, z, 2.0)
    (s,) = _steps(levels, _faces(15.0, levels=True))
    assert abs(abs(s["gap"]) - 1.2) < 0.3, s
    assert s["miss"] == 0.0, s


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
