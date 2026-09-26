"""Small crisp objects are measured as contrasting; narrow edge drops become keepouts."""
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from shapely.geometry import Point, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import obstruction_detection as od


def _image(vent_value, roof_value=120, stain=False):
    """10 x 10 m roof at 0.1 m, a 0.5 m vent at (5, 5)."""
    n = 100
    arr = np.full((3, n, n), roof_value, dtype=np.uint8)
    yy, xx = np.mgrid[0:n, 0:n]
    d = np.hypot(xx - 50, yy - 50)
    if stain:   # a soft gradient patch
        arr[:, :, :] = np.clip(roof_value + 20 * np.exp(-(d / 8.0) ** 2), 0, 255).astype(np.uint8)
    else:
        arr[:, d <= 2.5] = vent_value
    mem = MemoryFile()
    ds = mem.open(driver="GTiff", width=n, height=n, count=3, dtype="uint8",
                  transform=from_origin(0, 10, 0.1, 0.1), crs="EPSG:2193")
    ds.write(arr)
    ds.close()
    return mem.open()


def test_vent_is_crisp():
    img = _image(240)
    assert od._crisp_contrast(img, Point(5, 5).buffer(0.25)) >= od.SMALL_OBJECT_MIN_CONTRAST


def test_dark_vent_is_crisp():
    img = _image(20)
    assert od._crisp_contrast(img, Point(5, 5).buffer(0.25)) >= od.SMALL_OBJECT_MIN_CONTRAST


def test_soft_stain_is_not():
    img = _image(0, stain=True)
    assert od._crisp_contrast(img, Point(5, 5).buffer(0.25)) < od.SMALL_OBJECT_MIN_CONTRAST


def test_roof_corner_is_compared_with_the_roof():
    # dark roof on bright ground: a patch at the corner is roof-coloured, so
    # against the roof around it there is no contrast at all
    n = 100
    arr = np.full((3, n, n), 230, dtype=np.uint8)
    arr[:, 20:80, 20:80] = 40
    mem = MemoryFile()
    ds = mem.open(driver="GTiff", width=n, height=n, count=3, dtype="uint8",
                  transform=from_origin(0, 10, 0.1, 0.1), crs="EPSG:2193")
    ds.write(arr)
    ds.close()
    img = mem.open()
    roof = box(2, 2, 8, 8)
    corner = box(2, 2, 2.6, 2.6)
    assert od._crisp_contrast(img, corner) >= od.SMALL_OBJECT_MIN_CONTRAST   # unclipped: fooled
    assert od._crisp_contrast(img, corner, roof=roof) < od.SMALL_OBJECT_MIN_CONTRAST


def test_no_imagery_is_zero():
    assert od._crisp_contrast(None, Point(5, 5).buffer(0.25)) == 0.0


def test_min_width():
    assert abs(od._min_width(box(0, 0, 1.2, 9)) - 1.2) < 1e-6


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("  pass ", name)
            except AssertionError as e:
                fails += 1
                print("  FAIL ", name, e)
    sys.exit(1 if fails else 0)
