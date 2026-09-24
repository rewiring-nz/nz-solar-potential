"""Fabricate a small synthetic region so the real build stages can run end to
end without LINZ data: point cloud (LAZ), 1 m DSM, RGB imagery, wide DEM and
building outlines for ten roofs of known shape.

Usage: python make_region.py <repo_root>   (writes into <repo_root>/data)
Called by tests/synthetic/run.py, which never points it at a real checkout.
"""
import json
import sys
from pathlib import Path

import numpy as np
import laspy
import rasterio
from rasterio.transform import from_origin
import geopandas as gpd
from shapely.geometry import Polygon, box

ROOT = Path(sys.argv[1])
DATA = ROOT / "data"
REGION = "zz_harness"
RD = DATA / "regions" / REGION
RD.mkdir(parents=True, exist_ok=True)
(DATA / "pointcloud").mkdir(parents=True, exist_ok=True)
(DATA / "selected_faces").mkdir(parents=True, exist_ok=True)

X0, Y0 = 1258400.0, 5003900.0   # inside the Queenstown pilot bbox
G = 300.0                        # ground level
EXT = (-25.0, -25.0, 160.0, 75.0)  # local extent (minx, miny, maxx, maxy)
rng = np.random.default_rng(12345)


def ground(x, y):
    return G + 0.02 * x + 0.01 * y


def gable_y(x, y, x0, x1, eave, pitch):      # ridge runs along y
    c = (x0 + x1) / 2
    return eave + np.tan(np.radians(pitch)) * ((x1 - x0) / 2 - np.abs(x - c))


def gable_x(x, y, y0, y1, eave, pitch):      # ridge runs along x
    c = (y0 + y1) / 2
    return eave + np.tan(np.radians(pitch)) * ((y1 - y0) / 2 - np.abs(y - c))


def hip(x, y, x0, y0, x1, y1, eave, pitch):
    d = np.minimum.reduce([x - x0, x1 - x, y - y0, y1 - y])
    return eave + np.tan(np.radians(pitch)) * d


# (id, footprint, height function)
B = []
B.append((990000001, box(0, 0, 10, 16), lambda x, y: G + 5 + gable_y(x, y, 0, 10, 0, 25)))
B.append((990000002, box(20, 0, 36, 10), lambda x, y: G + 5 + gable_x(x, y, 0, 10, 0, 30)))
B.append((990000003, box(45, 0, 63, 12), lambda x, y: G + 5 + hip(x, y, 45, 0, 63, 12, 0, 22)))
L_fp = Polygon([(0, 30), (8, 30), (8, 42), (24, 42), (24, 50), (0, 50)])
B.append((990000004, L_fp, lambda x, y: G + 5 + np.maximum(
    np.where(x <= 8, gable_y(x, y, 0, 8, 0, 28), -99),
    np.where(y >= 42, gable_x(x, y, 42, 50, 0, 28), -99))))


def flat_ac(x, y):
    z = np.full_like(x, G + 8.0)
    ac = (x >= 42) & (x <= 44) & (y >= 36) & (y <= 37.5)
    z = np.where(ac, z + 1.2, z)
    vent = (x >= 50) & (x <= 50.6) & (y >= 40) & (y <= 40.6)
    return np.where(vent, z + 0.8, z)


B.append((990000005, box(35, 30, 55, 45), flat_ac))
B.append((990000006, box(65, 30, 73, 42), lambda x, y: G + 4 + np.tan(np.radians(10)) * (42 - y)))
B.append((990000007, box(80, 5, 83, 8), lambda x, y: np.full_like(x, G + 2.5)))
B.append((990000008, box(80, 30, 92, 40), lambda x, y: G + 5 + gable_x(x, y, 30, 40, 0, 35)))
# A survey corner where nothing was classified (class 1): its roof has no
# building-class returns, so queries fall back to every class, and there is no
# ground class either, so the gate falls back to the lowest returns round each
# panel. The two cases where points AWAY from a roof change the answer --
# which is what makes the pack test (src/pack_region.py) able to fail.
B.append((990000009, box(125, 45, 133, 55), lambda x, y: G + 5 + gable_y(x, y, 125, 133, 0, 25)))
UNCLASSIFIED = lambda x, y: x > 100   # >20 m of unclassified all round building 9


# A flat commercial roof crowded with plant (17 Church Street, #4726056): ~30%
# of it under units and ducts standing 0.8-2.5 m proud. The plane is right; the
# plant is obstructions, and the roof has plenty of clear space between them.
_PLANT = [(62, 55, 66, 58, 1.8), (70, 54, 72, 64, 0.9), (75, 56, 80, 59, 2.4),
          (61, 62, 64, 66, 1.2), (66, 63, 70, 64, 0.8), (77, 62, 80, 66, 1.6),
          (68, 58, 69, 62, 1.0), (62, 59, 63, 61, 2.0), (73, 60, 76, 62, 1.4)]


def plant_roof(x, y):
    z = np.full_like(x, G + 7.0)
    for x0, y0, x1, y1, h in _PLANT:
        z = np.where((x >= x0) & (x <= x1) & (y >= y0) & (y <= y1), G + 7.0 + h, z)
    return z


B.append((990000010, box(60, 53, 82, 68), plant_roof))
TREE = (86.0, 46.0, 3.5, 12.0)   # x, y, radius, height -- north of building 8


def surface(x, y):
    """(z, class) for arrays of local x, y."""
    z = ground(x, y)
    cls = np.full(x.shape, 2, dtype=np.uint8)
    import shapely
    for bid, fp, f in B:
        inside = shapely.contains_xy(fp, x, y)
        if inside.any():
            zz = f(x[inside], y[inside])
            z[inside] = zz
            cls[inside] = 6
    tx, ty, tr, th = TREE
    d = np.hypot(x - tx, y - ty)
    t = d < tr
    z[t] = ground(x[t], y[t]) + th * np.sqrt(1 - (d[t] / tr) ** 2)
    cls[t] = 5
    cls[UNCLASSIFIED(x, y)] = 1
    return z, cls


# --- point cloud: 8 pts/m2 roofs, same everywhere for simplicity -----------
minx, miny, maxx, maxy = EXT
n = int((maxx - minx) * (maxy - miny) * 8)
px = rng.uniform(minx, maxx, n)
py = rng.uniform(miny, maxy, n)
pz, pc = surface(px, py)
pz = pz + rng.normal(0, 0.03, n)
hdr = laspy.LasHeader(point_format=1, version="1.2")
hdr.scales = np.array([0.001, 0.001, 0.001])
hdr.offsets = np.array([X0, Y0, 0.0])
las = laspy.LasData(hdr)
las.x = px + X0
las.y = py + Y0
las.z = pz
las.classification = pc
las.write(str(DATA / "pointcloud" / "zz_harness_tile.laz"))

# --- DSM 1 m ----------------------------------------------------------------
res = 1.0
cols, rows = int((maxx - minx) / res), int((maxy - miny) / res)
gx, gy = np.meshgrid(minx + (np.arange(cols) + 0.5) * res, maxy - (np.arange(rows) + 0.5) * res)
dz, _ = surface(gx.ravel(), gy.ravel())
dsm = dz.reshape(rows, cols).astype("float32")
prof = dict(driver="GTiff", width=cols, height=rows, count=1, dtype="float32",
            crs="EPSG:2193", transform=from_origin(X0 + minx, Y0 + maxy, res, res), nodata=-9999.0)
with rasterio.open(RD / "dsm_mosaic.tif", "w", **prof) as ds:
    ds.write(dsm, 1)

# --- imagery 0.2 m RGB ------------------------------------------------------
res = 0.2
cols, rows = int((maxx - minx) / res), int((maxy - miny) / res)
gx, gy = np.meshgrid(minx + (np.arange(cols) + 0.5) * res, maxy - (np.arange(rows) + 0.5) * res)
iz, ic = surface(gx.ravel(), gy.ravel())
iz = iz.reshape(rows, cols)
ic = ic.reshape(rows, cols)
gyy, gxx = np.gradient(iz, res)
shade = np.clip(0.6 + 0.8 * (-gxx * 0.5 + gyy * 0.5), 0.2, 1.0)
img = np.zeros((3, rows, cols), dtype=np.uint8)
img[0] = np.where(ic == 6, 150 * shade, np.where(ic == 5, 40, 90))
img[1] = np.where(ic == 6, 60 * shade, np.where(ic == 5, 110, 130))
img[2] = np.where(ic == 6, 50 * shade, np.where(ic == 5, 40, 70))
lx, ly = gx.reshape(rows, cols), gy.reshape(rows, cols)
ac = (lx >= 42) & (lx <= 44) & (ly >= 36) & (ly <= 37.5)
img[:, ac] = 235
# a flush skylight on building 1's west face: bright in the photo, nothing in
# the LiDAR -- the case only the bright-object detector can see
SKYLIGHT = (2.0, 6.0, 3.2, 7.0)
sky = (lx >= SKYLIGHT[0]) & (lx <= SKYLIGHT[2]) & (ly >= SKYLIGHT[1]) & (ly <= SKYLIGHT[3])
img[:, sky] = 250
prof = dict(driver="GTiff", width=cols, height=rows, count=3, dtype="uint8",
            crs="EPSG:2193", transform=from_origin(X0 + minx, Y0 + maxy, res, res))
with rasterio.open(RD / "imagery_mosaic.tif", "w", **prof) as ds:
    ds.write(img)

# --- wide DEM 8 m with a mountain to the south-east -------------------------
res = 8.0
half = 4000.0
cols = rows = int(2 * half / res)
gx, gy = np.meshgrid(-half + (np.arange(cols) + 0.5) * res, half - (np.arange(rows) + 0.5) * res)
dem = ground(gx, gy) + 900 * np.exp(-(((gx - 1800) / 700) ** 2 + ((gy + 1500) / 700) ** 2))
prof = dict(driver="GTiff", width=cols, height=rows, count=1, dtype="float32",
            crs="EPSG:2193", transform=from_origin(X0 - half, Y0 + half, res, res), nodata=-9999.0)
with rasterio.open(DATA / "dem_wide_mosaic.tif", "w", **prof) as ds:
    ds.write(dem.astype("float32"), 1)

# --- outlines ---------------------------------------------------------------
from shapely import affinity
gdf = gpd.GeoDataFrame(
    {"building_id": [b[0] for b in B], "use": ["Residential"] * len(B), "name": [None] * len(B)},
    geometry=[affinity.translate(b[1], X0, Y0) for b in B], crs="EPSG:2193")
gdf.to_file(RD / "building_outlines.geojson", driver="GeoJSON")

import pyproj
tr = pyproj.Transformer.from_crs("EPSG:2193", "EPSG:4326", always_xy=True)
w, s = tr.transform(X0 + minx, Y0 + miny)
e, nn = tr.transform(X0 + maxx, Y0 + maxy)
(RD / "task.json").write_text(json.dumps({"bbox": [w, s, e, nn]}))
print("synthetic region written", RD)
