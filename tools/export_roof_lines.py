"""Training set for the roof-line reader: one sample per roof in the frame of
the map's photo, from three label sources: hand-drawn faces (kept apart, for
validation), paid photo readings, and LiDAR point-roof readings whose planes
fit their returns (free, thousands). Held-back roofs are never read.

    python export_lines.py <out dir> [--per-region 500] [--jobs 12] [--regions a b]
"""
import argparse, json, os, sys, random, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
ROOT = Path.home() / "solar-map"
os.chdir(ROOT); sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_erosion, gaussian_filter
from scipy.spatial import cKDTree
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.affinity import translate

SIZE, FILL_PX, BOUNDARY_PX, CORE_ERODE_PX, EAVE_ERASE_PX = 384, 304, 3, 5, 6
FIT_M, FIT_SHARE, MIN_DENSITY = 0.15, 0.95, 2.5


def sample(faces, tiles, pc, shift):
    """faces: shapely polygons in the photo frame. LiDAR is read `shift` back."""
    roof = unary_union([f.buffer(0.02) for f in faces]).buffer(-0.02)
    if roof.is_empty:
        return None
    minx, miny, maxx, maxy = roof.bounds
    span = max(maxx - minx, maxy - miny)
    if span < 4:
        return None
    mpp = span / FILL_PX
    half = SIZE / 2 * mpp
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    b = (cx - half, cy - half, cx + half, cy + half)
    n = max(int(round((b[2] - b[0]) / 0.1)), 8)        # the tiles come on a 0.1 m grid
    arr = np.moveaxis(np.asarray(tiles.window(b, n, n)), 0, -1)
    rgb = np.asarray(Image.fromarray(arr).resize((SIZE, SIZE), Image.BILINEAR)).astype(np.float32) / 255.0
    sx, sy = shift
    pts = pc.points_in_bbox(b[0] - sx, b[1] - sy, b[2] - sx, b[3] - sy, building_only=True)
    z = np.full((SIZE, SIZE), np.nan, np.float32)
    if pts is not None and len(pts) >= 50:
        gx, gy = np.meshgrid(np.linspace(b[0] + mpp / 2, b[2] - mpp / 2, SIZE),
                             np.linspace(b[3] - mpp / 2, b[1] + mpp / 2, SIZE))
        tree = cKDTree(pts[:, :2] + [sx, sy])
        dist, idx = tree.query(np.c_[gx.ravel(), gy.ravel()], k=1)
        z = pts[idx, 2].reshape(SIZE, SIZE).astype(np.float32)
        z[dist.reshape(SIZE, SIZE) > 2.5] = np.nan
    base = np.nanpercentile(z, 5) if np.isfinite(z).any() else 0.0
    zf = gaussian_filter(np.nan_to_num(z - base, nan=0.0), max(2.0, 1.2 / mpp))
    dzy, dzx = np.gradient(zf, mpp)
    slope = np.degrees(np.arctan(np.hypot(dzx, dzy)))
    mag = np.hypot(dzx, dzy) + 1e-9
    from src import line_reader as LR                 # the build's picture has the same channels
    chans = np.dstack([rgb, np.clip(zf / 10.0, 0, 1), np.clip(slope / 45.0, 0, 1),
                       (-dzx / mag + 1) / 2, (dzy / mag + 1) / 2] + (LR.sharp_channels(z, base, mpp) if LR.SHARP else []))

    def px(x, y):
        return ((x - b[0]) / (b[2] - b[0]) * SIZE, (1 - (y - b[1]) / (b[3] - b[1])) * SIZE)
    wim = Image.new("L", (SIZE, SIZE), 0); wd = ImageDraw.Draw(wim)
    bim = Image.new("L", (SIZE, SIZE), 0); bd = ImageDraw.Draw(bim)
    for f in faces:
        for g in getattr(f, "geoms", [f]):
            ring = [px(x, y) for x, y in g.exterior.coords]
            wd.polygon(ring, fill=255)
            bd.line(ring + [ring[0]], fill=255, width=BOUNDARY_PX)
    for g in getattr(roof, "geoms", [roof]):
        fr = [px(x, y) for x, y in g.exterior.coords]
        bd.line(fr + [fr[0]], fill=0, width=BOUNDARY_PX + EAVE_ERASE_PX)
    weight = np.array(wim) > 0
    if weight.sum() < 900:
        return None
    boundary = np.array(bim) > 0
    core = binary_erosion(weight & ~boundary, np.ones((3, 3), bool), iterations=CORE_ERODE_PX)
    return (chans * 255).astype(np.uint8), np.dstack([boundary, core]), weight, mpp, b


def point_ok(doc, pr):
    """A LiDAR reading good enough to be a label: its planes hold its returns."""
    faces = [Polygon(r).buffer(0) for r in doc.get("faces") or []]
    planes = doc.get("planes") or []
    if not (2 <= len(faces) <= 12) or len(planes) != len(faces):
        return False
    roof = unary_union(faces)
    if not (40 <= roof.area <= 500):
        return False
    try:
        pts, _, ground = pr.all_points(roof.convex_hull, buf=0.0)
    except Exception:
        return False
    if len(pts) < MIN_DENSITY * roof.area:
        return False
    from shapely import contains_xy
    ox, oy = doc["origin"]
    res = []
    for f, (a, b, c) in zip(faces, planes):
        m = contains_xy(f, pts[:, 0], pts[:, 1])
        if m.sum():
            res.append(pts[m, 2] - ground - (a * (pts[m, 0] - ox) + b * (pts[m, 1] - oy) + c))
    if not res:
        return False
    r = np.concatenate(res); r = r - np.median(r)
    return float((np.abs(r) < FIT_M).mean()) >= FIT_SHARE


def region_job(job):
    region, out, per_region, seed = job
    import src.point_roof as PR, src.register_imagery as RI
    from src.region_build import area_paths
    from src.vision_roof import VISION_FACES_DIR
    out = Path(out)
    try:
        paths = area_paths(region)
        O = PR.outlines(region)
        tiles = RI._Tiles(paths["dir"] / "display_tiles")
        sh = RI.drawing_shifts(paths["dir"])
    except Exception as exc:
        return region, f"skipped ({exc!r})"
    pc = PR.pc()
    hold = {int(h["id"] if isinstance(h, dict) else h) for h in json.loads((ROOT / "data/roof_set.json").read_text())["holdout"]}
    B = json.loads((ROOT / "data/roof_labels.json").read_text())["buildings"]
    n = {"hand": 0, "vision": 0, "points": 0, "points_rejected": 0}

    def save(kind, bid, faces, s):
        try:
            r = sample(faces, tiles, pc, s)
        except Exception:
            return
        if r is None:
            return
        img, tgt, w, mpp, b = r
        (out / kind).mkdir(parents=True, exist_ok=True)
        np.savez_compressed(out / kind / f"{bid}.npz", image=img, target=tgt, weight=w,
                            meta=np.array([mpp, *b, *s]), region=region)
        n[kind] += 1
    ids = sorted(O)
    random.Random(seed).shuffle(ids)
    taken = 0
    for bid in ids:
        if bid in hold:
            continue
        s = tuple((sh.get(str(bid)) or [0.0, 0.0])[:2])
        v = B.get(str(bid))
        if v and v.get("faces") and v.get("area") == region and not v.get("problem"):
            rings = [Polygon(f["ring"]).buffer(0) for f in v["faces"] if f.get("ring") and len(f["ring"]) >= 3]
            if rings:
                save("hand", bid, [translate(g, *s) for g in rings] if region == "pilot" else rings, s)
            continue
        f = VISION_FACES_DIR / f"{bid}.json"
        if f.exists():
            try:
                d = json.loads(f.read_text())
                if (d.get("confidence") or 0) >= 0.5:
                    save("vision", bid, [translate(Polygon(r).buffer(0), *s) for r in d["faces"] if len(r) >= 3], s)
                    continue
            except Exception:
                pass
        if taken >= per_region:
            continue
        f = PR.POINT_FACES_DIR / f"{bid}.json"
        if f.exists():
            try:
                d = json.loads(f.read_text())
                if point_ok(d, PR):
                    save("points", bid, [translate(Polygon(r).buffer(0), *s) for r in d["faces"]], s)
                    taken += 1
                else:
                    n["points_rejected"] += 1
            except Exception:
                pass
    return region, n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out"); ap.add_argument("--per-region", type=int, default=500)
    ap.add_argument("--jobs", type=int, default=12); ap.add_argument("--regions", nargs="*")
    a = ap.parse_args()
    import config
    regions = a.regions or (["pilot"] + [r for r in config.REGIONS if not r.startswith("akl")])
    regions = [r for r in dict.fromkeys(regions) if (ROOT / "data/regions" / r).exists()]
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(a.jobs) as ex:
        for region, n in ex.map(region_job, [(r, a.out, a.per_region, 7) for r in regions]):
            print(region, n, flush=True)
