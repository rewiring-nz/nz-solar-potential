"""
Keep what the geometry needs, so a region can be rebuilt after its inputs go.

WHY. publish_region deletes a region's inputs once its outputs are in the
bucket -- they are most of the disk, and nationally terabytes. But geometry is
the layer that keeps improving: every fix to segmentation, obstructions or
panel fitting needs the LiDAR and imagery again, and re-fetching the country
from LINZ and OpenTopography is ~26 TB through one service. So before the
inputs are deleted, this keeps the part of them any build stage can actually
read: the points and pixels near buildings. Most of a survey is roads,
paddocks and bush.

WHAT IS KEPT, and why each margin is what it is (every one is measured from
the building's bounding box, because every query in src/ is a bounding box):

  points, every class, within PAD_ALL_M (4 m)
      segmentation pads by 2 m, obstruction and heat-map queries by 1 m, the
      gate by 0.3 m around a panel; drawn faces can reach ~2 m past a
      footprint. 4 m covers all of them.
  ground-class points within PAD_GROUND_M (23.5 m)
      the gate's local ground is the median ground return in a 20 m box
      round each panel's centre, and a panel centre can sit up to ~3 m
      outside the footprint box on a drawn roof.
  every class within PAD_GROUND_M, only for buildings that need it
      when a 20 m box holds fewer than 20 ground returns the gate falls back
      to the lowest returns of ANY class in it. For each building this checks
      a 2 m grid of possible panel centres (footprint box + 3 m) against the
      ground returns in a box 1.5 m smaller than the gate's -- a lower bound
      on every real box -- and keeps all classes out to 23.5 m wherever the
      bound could fall under 20.
  imagery within PAD_IMAGERY_M (13 m), lossless
      register_imagery reads the footprint + 12 m; everything else reads
      less. Pixels further out are zeroed, which compresses to almost
      nothing. Lossless because a JPEG would move the colour evidence the
      obstruction detector reads.
  the DSM, whole (1 m, small); outlines; the region's task/bbox.

EXACTNESS. Points are written per source tile, as a subset of that tile, with
the tile's own scale, offset, point format and file name, in the original
order. So any query inside the kept area returns the same float64 values in
the same order from the pack as from the survey -- and RANSAC, which samples
by index, samples the same points. tests/synthetic proves it: the synthetic
region is built, packed, stripped of its inputs, restored from the pack and
rebuilt, and every output is byte-identical.

The one reader that goes further is tools/ (research scripts such as
roof_extent's ground search); the build stages do not.

Usage:
    python src/pack_region.py <region>              # write data/regions/<r>/pack/
    python src/pack_region.py <region> --restore    # put a pack back as inputs
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.region_build import area_paths, DATA_DIR

PAD_ALL_M = 4.0
PAD_GROUND_M = 23.5
PAD_IMAGERY_M = 13.0
GATE_GROUND_R_M = 20.0        # gate_panels.GROUND_SEARCH_RADIUS_M
GATE_MIN_GROUND = 20          # gate_panels: len(ground_cls) >= 20
CENTRE_REACH_M = 3.0          # a panel centre can sit this far outside the footprint box
PROBE_STEP_M = 2.0
CELL_M = 1.0                  # mask grid; cells are included whole (a superset)
GROUND_CLASS = 2
POINTCLOUD_DIR = DATA_DIR / "pointcloud"


def _pack_dir(region):
    return area_paths(region)["dir"] / "pack"


def _boxes(outlines_path):
    import geopandas as gpd
    g = gpd.read_file(outlines_path)
    if g.crs is not None and g.crs.to_epsg() != 2193:
        g = g.to_crs(2193)
    return np.array([geom.bounds for geom in g.geometry if geom is not None and not geom.is_empty])


class _Grid:
    """A boolean mask over the region at CELL_M, filled from boxes."""

    def __init__(self, x0, y0, x1, y1):
        self.x0, self.y0 = np.floor(x0), np.floor(y0)
        self.nx = int(np.ceil((x1 - self.x0) / CELL_M)) + 1
        self.ny = int(np.ceil((y1 - self.y0) / CELL_M)) + 1
        self.m = np.zeros((self.ny, self.nx), bool)

    def add(self, box, pad):
        minx, miny, maxx, maxy = box
        c0 = max(0, int(np.floor((minx - pad - self.x0) / CELL_M)))
        c1 = min(self.nx, int(np.floor((maxx + pad - self.x0) / CELL_M)) + 1)
        r0 = max(0, int(np.floor((miny - pad - self.y0) / CELL_M)))
        r1 = min(self.ny, int(np.floor((maxy + pad - self.y0) / CELL_M)) + 1)
        self.m[r0:r1, c0:c1] = True

    def contains(self, x, y):
        c = np.floor((x - self.x0) / CELL_M).astype(np.int64)
        r = np.floor((y - self.y0) / CELL_M).astype(np.int64)
        ok = (c >= 0) & (c < self.nx) & (r >= 0) & (r < self.ny)
        out = np.zeros(len(x), bool)
        out[ok] = self.m[r[ok], c[ok]]
        return out


def _ground_sat(ground_xy, x0, y0, nx, ny, cell=0.5):
    """Summed-area table of ground-return counts at `cell` metres."""
    h = np.zeros((ny, nx), np.int64)
    c = np.floor((ground_xy[:, 0] - x0) / cell).astype(np.int64)
    r = np.floor((ground_xy[:, 1] - y0) / cell).astype(np.int64)
    ok = (c >= 0) & (c < nx) & (r >= 0) & (r < ny)
    np.add.at(h, (r[ok], c[ok]), 1)
    sat = np.zeros((ny + 1, nx + 1), np.int64)
    sat[1:, 1:] = h.cumsum(0).cumsum(1)
    return sat


def _needs_fallback(box, sat, x0, y0, cell=0.5):
    """True unless every possible gate box round this building is certain to
    hold >= GATE_MIN_GROUND ground returns (see the module docstring)."""
    minx, miny, maxx, maxy = box
    half = GATE_GROUND_R_M - PROBE_STEP_M * 0.75       # 18.5: a lower-bound box
    xs = np.arange(minx - CENTRE_REACH_M, maxx + CENTRE_REACH_M + PROBE_STEP_M, PROBE_STEP_M)
    ys = np.arange(miny - CENTRE_REACH_M, maxy + CENTRE_REACH_M + PROBE_STEP_M, PROBE_STEP_M)
    ny, nx = sat.shape[0] - 1, sat.shape[1] - 1
    for px in xs:
        # cells FULLY inside [px - half, px + half]: an under-count, never over
        c0 = int(np.ceil((px - half - x0) / cell)); c1 = int(np.floor((px + half - x0) / cell))
        for py in ys:
            r0 = int(np.ceil((py - half - y0) / cell)); r1 = int(np.floor((py + half - y0) / cell))
            c0c, c1c, r0c, r1c = max(0, c0), min(nx, c1), max(0, r0), min(ny, r1)
            if c1c <= c0c or r1c <= r0c:
                return True
            n = sat[r1c, c1c] - sat[r0c, c1c] - sat[r1c, c0c] + sat[r0c, c0c]
            if n < GATE_MIN_GROUND:
                return True
    return False


def pack(region):
    import laspy
    t0 = time.time()
    paths = area_paths(region)
    boxes = _boxes(paths["outlines"])
    if not len(boxes):
        raise SystemExit(f"[{region}] no outlines to pack around")
    out = _pack_dir(region)
    if out.exists():
        shutil.rmtree(out)
    (out / "points").mkdir(parents=True)
    ext = (boxes[:, 0].min() - PAD_GROUND_M - 1, boxes[:, 1].min() - PAD_GROUND_M - 1,
           boxes[:, 2].max() + PAD_GROUND_M + 1, boxes[:, 3].max() + PAD_GROUND_M + 1)

    tiles = []
    for p in sorted(POINTCLOUD_DIR.glob("*.laz")):
        if p.name.startswith("."):
            continue
        with laspy.open(p) as f:
            h = f.header
            if h.maxs[0] >= ext[0] and h.mins[0] <= ext[2] and h.maxs[1] >= ext[1] and h.mins[1] <= ext[3]:
                tiles.append(p)

    # ground returns across the region, for the fallback test
    g_parts = []
    for p in tiles:
        las = laspy.read(p)
        m = np.asarray(las.classification) == GROUND_CLASS
        g_parts.append(np.column_stack([np.asarray(las.x)[m], np.asarray(las.y)[m]]))
    ground = np.concatenate(g_parts) if g_parts else np.empty((0, 2))
    scell = 0.5
    snx = int(np.ceil((ext[2] - ext[0]) / scell)) + 1
    sny = int(np.ceil((ext[3] - ext[1]) / scell)) + 1
    sat = _ground_sat(ground, ext[0], ext[1], snx, sny, scell)

    keep_all, keep_ground = _Grid(*ext), _Grid(*ext)
    n_fallback = 0
    for b in boxes:
        keep_all.add(b, PAD_ALL_M)
        keep_ground.add(b, PAD_GROUND_M)
        if _needs_fallback(b, sat, ext[0], ext[1], scell):
            keep_all.add(b, PAD_GROUND_M)
            n_fallback += 1

    n_in = n_kept = bytes_in = bytes_out = 0
    for p in tiles:
        las = laspy.read(p)
        x, y = np.asarray(las.x), np.asarray(las.y)
        cls = np.asarray(las.classification)
        m = keep_all.contains(x, y) | ((cls == GROUND_CLASS) & keep_ground.contains(x, y))
        n_in += len(x)
        n_kept += int(m.sum())
        bytes_in += p.stat().st_size
        if not m.any():
            continue
        hdr = laspy.LasHeader(point_format=las.header.point_format, version=las.header.version)
        hdr.scales = las.header.scales
        hdr.offsets = las.header.offsets
        for v in las.header.vlrs:
            if getattr(v, "user_id", "") != "copc":
                hdr.vlrs.append(v)
        sub = laspy.LasData(hdr)
        sub.points = las.points[m].copy()
        dest = out / "points" / p.name
        sub.write(str(dest))
        bytes_out += dest.stat().st_size

    img_bytes = _pack_imagery(paths["imagery"], boxes, out / "imagery_pack.tif")
    dsm_bytes = _copy_raster(paths["dsm"], out / "dsm_pack.tif")
    manifest = {
        "region": region, "packed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pads_m": {"all_classes": PAD_ALL_M, "ground": PAD_GROUND_M, "imagery": PAD_IMAGERY_M},
        "buildings": int(len(boxes)), "buildings_with_ground_fallback": n_fallback,
        "tiles": [p.name for p in tiles], "points_in": n_in, "points_kept": n_kept,
        "point_bytes_in": bytes_in, "point_bytes_kept": bytes_out,
        "imagery_bytes": img_bytes, "dsm_bytes": dsm_bytes,
    }
    (out / "pack.json").write_text(json.dumps(manifest, indent=1))
    print(f"[{region}] packed {n_kept:,} of {n_in:,} points "
          f"({bytes_out / 1e6:.1f} of {bytes_in / 1e6:.1f} MB LAZ), imagery {img_bytes / 1e6:.1f} MB, "
          f"{n_fallback} building(s) with the all-class ground margin, {time.time() - t0:.0f}s",
          flush=True)
    return manifest


def _pack_imagery(src, boxes, dest):
    """Lossless copy of the imagery with every pixel beyond PAD_IMAGERY_M of a
    building box set to zero, block by block."""
    if not Path(src).exists():
        return 0
    import rasterio
    from rasterio.windows import Window
    with rasterio.open(src) as ds:
        prof = ds.profile.copy()
        prof.update(driver="GTiff", tiled=True, blockxsize=512, blockysize=512,
                    compress="deflate", predictor=2, BIGTIFF="IF_SAFER")
        inv = ~ds.transform
        with rasterio.open(dest, "w", **prof) as out:
            for r0 in range(0, ds.height, 512):
                for c0 in range(0, ds.width, 512):
                    w = Window(c0, r0, min(512, ds.width - c0), min(512, ds.height - r0))
                    arr = ds.read(window=w)
                    keep = np.zeros(arr.shape[1:], bool)
                    # only the boxes that reach this block (a city has
                    # thousands of buildings and a mosaic thousands of blocks)
                    bx0, by1 = ds.transform * (c0, r0)
                    bx1, by0 = ds.transform * (c0 + w.width, r0 + w.height)
                    bx0, bx1 = sorted((bx0, bx1)); by0, by1 = sorted((by0, by1))
                    near = boxes[(boxes[:, 2] + PAD_IMAGERY_M >= bx0) & (boxes[:, 0] - PAD_IMAGERY_M <= bx1)
                                 & (boxes[:, 3] + PAD_IMAGERY_M >= by0) & (boxes[:, 1] - PAD_IMAGERY_M <= by1)]
                    for minx, miny, maxx, maxy in near:
                        ca, ra = inv * (minx - PAD_IMAGERY_M, maxy + PAD_IMAGERY_M)
                        cb, rb = inv * (maxx + PAD_IMAGERY_M, miny - PAD_IMAGERY_M)
                        ca, cb = sorted((ca, cb)); ra, rb = sorted((ra, rb))
                        a0 = max(0, int(np.floor(ca)) - 1 - c0); a1 = min(w.width, int(np.ceil(cb)) + 1 - c0)
                        b0 = max(0, int(np.floor(ra)) - 1 - r0); b1 = min(w.height, int(np.ceil(rb)) + 1 - r0)
                        if a1 > a0 and b1 > b0:
                            keep[b0:b1, a0:a1] = True
                    arr[:, ~keep] = 0
                    out.write(arr, window=w)
    return Path(dest).stat().st_size


def _copy_raster(src, dest):
    if not Path(src).exists():
        return 0
    import rasterio
    with rasterio.open(src) as ds:
        prof = ds.profile.copy()
        prof.update(driver="GTiff", tiled=True, blockxsize=256, blockysize=256,
                    compress="deflate", BIGTIFF="IF_SAFER")
        with rasterio.open(dest, "w", **prof) as out:
            out.write(ds.read())
    return Path(dest).stat().st_size


PACKED_REGISTRY = POINTCLOUD_DIR / ".packed.json"   # hidden: PointCloudSource skips dotfiles


def packed_tiles():
    """Names of point tiles on disk that are pack subsets, not full tiles."""
    try:
        return set(json.loads(PACKED_REGISTRY.read_text()))
    except (OSError, ValueError):
        return set()


def forget_packed(name):
    names = packed_tiles() - {name}
    PACKED_REGISTRY.write_text(json.dumps(sorted(names)))


def restore(region):
    """Put a pack back as the region's inputs. Refuses to mix a pack with the
    survey it came from: the point tiles keep their names, and a pack tile
    next to its full original would count every kept point twice."""
    src = _pack_dir(region)
    if not (src / "pack.json").exists():
        # published regions keep their pack only in the bucket
        from src.gcs_queue import url, _run
        src.mkdir(parents=True, exist_ok=True)
        r = _run(["rsync", "-r", url("regions", region) + "/pack", str(src)], check=False)
        if r.returncode != 0 or not (src / "pack.json").exists():
            raise SystemExit(f"[{region}] no pack at {src} and none in the bucket")
    paths = area_paths(region)
    clashes = [p.name for p in (src / "points").glob("*.laz") if (POINTCLOUD_DIR / p.name).exists()]
    if clashes:
        raise SystemExit(f"[{region}] {len(clashes)} point tile(s) already in {POINTCLOUD_DIR} "
                         f"(e.g. {clashes[0]}) -- the survey is still here, nothing to restore")
    for name, dest in (("imagery_pack.tif", paths["imagery"]), ("dsm_pack.tif", paths["dsm"])):
        if (src / name).exists() and Path(dest).exists():
            raise SystemExit(f"[{region}] {dest} exists -- refusing to overwrite it with the pack")
    POINTCLOUD_DIR.mkdir(parents=True, exist_ok=True)
    for p in sorted((src / "points").glob("*.laz")):
        shutil.copy2(p, POINTCLOUD_DIR / p.name)
    for name, dest in (("imagery_pack.tif", paths["imagery"]), ("dsm_pack.tif", paths["dsm"])):
        if (src / name).exists():
            shutil.copy2(src / name, dest)
    tiles = sorted(p.name for p in (src / "points").glob("*.laz"))
    PACKED_REGISTRY.write_text(json.dumps(sorted(packed_tiles() | set(tiles))))
    (paths["dir"] / "pointcloud_tiles.txt").write_text("\n".join(tiles) + "\n")
    print(f"[{region}] restored {len(tiles)} point tile(s), imagery and DSM from the pack", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("region")
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args()
    restore(a.region) if a.restore else pack(a.region)


if __name__ == "__main__":
    main()
