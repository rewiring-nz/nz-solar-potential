"""Read a roof's faces from the model trained on the drawn faces.

The fourth candidate generator, and the first that learns the OUTPUT we
actually want. sam/line/lidar/hypothesis all produce polygons by assembling
or assuming: SAM segments appearance, the line net finds crease pixels that
a partition then has to close into cells, LiDAR grows planar regions, and
hypothesis fits a closed vocabulary of shapes. Each fails in its own way and
the failures are visible on the map -- a dropped line, an invented flat top.

Here the model predicts, per pixel, BOUNDARY and CORE; a watershed turns the
pair into regions that tile the roof exactly once. A region cannot fail to
close, and nothing is assumed about what shape a roof "should" be.

Over-segmentation -- too many lines, or invented ones -- is controlled by
MIN_FACE_FRAC and the
seed threshold, both measurable against his face counts rather than tuned by
eye.

STATUS 18 Sep: MEASURED, NOT SHIPPING. On the 35 held-out roofs it has
never trained on (tools/eval_face_sources.py), against the drawn faces:

    reading                    agree   matched   faces vs drawn
    the chain that ships now   0.714     77.6%   +1.9 (over on 19 roofs)
    learned from his markup    0.577     55.9%   -1.0 (under on 14)

It is not close enough to replace anything, so nothing in the build calls
it. But note HOW each one fails: the shipping chain INVENTS faces, which is
the recurring defect, and this one draws too few. Every polygon
it does draw is closed and straight -- there is no junk to clean up, only
detail to gain.

TWO MEASURED DEAD ENDS, so neither is retried blind:
  * RID2 pretraining. 1,819 roof-centred German roofs reach 0.624 boundary
    F1 on their OWN held-out split -- the architecture learns creases well
    given data -- but fine-tuned onto the marked roofs it scores 0.544/48.8%,
    WORSE than training on the 96 alone (0.567/54.0%). RID has no LiDAR
    (four of seven channels neutral), its masks are azimuth classes rather
    than face instances, and German roofs are not NZ roofs. This is the
    same corpus that was a dead end for the line detector; it is now a
    measured dead end twice, for different targets.
  * Watershed knobs. Swept core threshold 0.50/0.70/0.85 against minimum
    face 0.012/0.005: the best pairing (0.50, 0.005) buys 0.567 -> 0.577.
    The shortfall is not in the post-processing.

WHAT WOULD ACTUALLY MOVE IT: more marked roofs. 96 training roofs is the
binding constraint, and unlike the shape-fitting path, this one converts
marking effort directly into the output -- the only lever measured to work.
"""

import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
# face_regions_v3.pt: the EncoderNet trained on 96 drawn roofs and 472
# readings of the photo by a vision model (tools/train_face_regions.py).
MODEL = Path(os.environ.get("SOLAR_FACE_REGION_MODEL",
                            ROOT / "data" / "models" / "face_regions_v3.pt"))
SIZE = 256
FILL_PX = 208
MIN_FACE_FRAC = float(os.environ.get("SOLAR_FR_MIN_FACE", "0.005"))
CORE_THR = float(os.environ.get("SOLAR_FR_CORE_THR", "0.50"))
# Averaging over the 8 symmetries is off: on the 35 held-out roofs it blurred
# the boundaries (line F 0.626 -> 0.553, 14 Nov). SOLAR_FR_TTA=1 turns it on.
TTA = os.environ.get("SOLAR_FR_TTA", "0") == "1"
_NET = [None]


def _net():
    if _NET[0] is None:
        import torch
        sys.path.insert(0, str(ROOT / "tools"))
        from train_face_regions import EncoderNet, UNet
        if not MODEL.exists():
            return None
        torch.set_num_threads(1)      # one roof at a time; the callers run a process per core
        ck = torch.load(MODEL, map_location="cpu", weights_only=False)
        n = EncoderNet() if ck.get("arch") == "resnet18" else UNet(cin=ck.get("cin", 7), cout=ck.get("cout", 2))
        n.load_state_dict(ck["state_dict"])
        n.eval()
        _NET[0] = n
    return _NET[0]


def _frame(geom):
    minx, miny, maxx, maxy = geom.bounds
    span = max(maxx - minx, maxy - miny)
    m_per_px = span / FILL_PX
    half = SIZE / 2 * m_per_px
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    return (cx - half, cy - half, cx + half, cy + half), m_per_px


def predict_maps(geom, img_ds, pts):
    """(boundary, core) probability maps for this roof, or None."""
    import torch
    import rasterio.windows
    from scipy.ndimage import gaussian_filter
    from scipy.spatial import cKDTree
    net = _net()
    if net is None:
        return None
    b, m_per_px = _frame(geom)
    win = rasterio.windows.from_bounds(*b, img_ds.transform)
    rgb = np.moveaxis(img_ds.read([1, 2, 3], window=win, boundless=True,
                                  fill_value=0, out_shape=(3, SIZE, SIZE)),
                      0, -1).astype(np.float32) / 255.0
    z = np.zeros((SIZE, SIZE), np.float32)
    if pts is not None and len(pts) >= 50:
        gx, gy = np.meshgrid(
            np.linspace(b[0] + m_per_px / 2, b[2] - m_per_px / 2, SIZE),
            np.linspace(b[3] - m_per_px / 2, b[1] + m_per_px / 2, SIZE))
        tree = cKDTree(pts[:, :2])
        dist, idx = tree.query(np.c_[gx.ravel(), gy.ravel()], k=1)
        z = pts[idx, 2].reshape(SIZE, SIZE).astype(np.float32)
        z[dist.reshape(SIZE, SIZE) > 2.5] = np.nan
    base = np.nanpercentile(z, 5) if np.isfinite(z).any() else 0.0
    zf = np.nan_to_num(z - base, nan=0.0)
    zf = gaussian_filter(zf, max(2.0, 1.2 / m_per_px))
    dzy, dzx = np.gradient(zf, m_per_px)
    slope = np.degrees(np.arctan(np.hypot(dzx, dzy)))
    mag = np.hypot(dzx, dzy) + 1e-9
    x = np.dstack([rgb, np.clip(zf / 10.0, 0, 1), np.clip(slope / 45.0, 0, 1),
                   (-dzx / mag + 1) / 2, (dzy / mag + 1) / 2]).astype(np.float32)
    with torch.no_grad():
        t = torch.from_numpy(x).permute(2, 0, 1)[None]
        if not TTA:
            p = torch.sigmoid(net(t))[0].numpy()
        else:
            # the reading averaged over the picture's 8 symmetries, each
            # turned back before averaging (the trainer's own dihedral, so
            # the downhill-direction channels turn with the picture)
            sys.path.insert(0, str(ROOT / "tools"))
            from train_face_regions import dihedral
            acc = torch.zeros(2, SIZE, SIZE)
            for k in range(8):
                xk, _, _ = dihedral(t.clone(), t[:, :1], t[:, :1], k)
                pk = torch.sigmoid(net(xk))[0]
                r = k & 3
                if r:
                    pk = torch.rot90(pk, -r, (-2, -1))
                if k & 4:
                    pk = pk.flip(-1)
                acc += pk
            p = (acc / 8).numpy()
    return p[0], p[1], b, m_per_px


def region_labels(geom, img_ds, pts):
    """(label map, face count, frame bounds, m per px, minimum face px): the
    model's regions as a raster over the frame, 0 off the roof. None if
    unavailable."""
    from scipy import ndimage
    from shapely.geometry import Polygon
    try:
        from skimage.segmentation import watershed
    except Exception:
        return None
    out = predict_maps(geom, img_ds, pts)
    if out is None:
        return None
    bnd, core, b, m_per_px = out

    # roof mask in frame pixels
    from PIL import Image, ImageDraw
    im = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(im).polygon(
        [((x - b[0]) / (b[2] - b[0]) * SIZE,
          (1 - (y - b[1]) / (b[3] - b[1])) * SIZE)
         for x, y in geom.exterior.coords], fill=255)
    roof = np.array(im) > 0
    if roof.sum() < 400:
        return None

    seeds, nseed = ndimage.label((core > CORE_THR) & roof)
    if nseed == 0:
        return None
    # drop specks before they become faces -- this is the over-segmentation
    # control, expressed as a share of THIS roof
    min_px = max(30, int(MIN_FACE_FRAC * roof.sum()))
    keep = [i for i in range(1, nseed + 1) if (seeds == i).sum() >= min_px]
    if not keep:
        return None
    relabel = np.zeros(nseed + 1, np.int32)
    for n, i in enumerate(keep, start=1):
        relabel[i] = n
    seeds = relabel[seeds]
    lab = watershed(bnd, markers=seeds, mask=roof)
    return lab, len(keep), b, m_per_px, min_px


def learned_faces(geom, img_ds, pts):
    """Face polygons in NZTM, read from the model. [] if unavailable."""
    from shapely.geometry import Polygon
    got = region_labels(geom, img_ds, pts)
    if got is None:
        return []
    lab, nface, b, m_per_px, min_px = got
    inv_x = lambda px: b[0] + px / SIZE * (b[2] - b[0])
    inv_y = lambda py: b[1] + (1 - py / SIZE) * (b[3] - b[1])
    faces = []
    for i in range(1, nface + 1):
        m = lab == i
        if m.sum() < min_px:
            continue
        try:
            from rasterio.features import shapes as rio_shapes
            polys = [Polygon(g["coordinates"][0])
                     for g, v in rio_shapes(m.astype("uint8"),
                                            mask=m.astype(bool))
                     if v == 1 and len(g["coordinates"][0]) >= 4]
        except Exception:
            continue
        if not polys:
            continue
        p = max(polys, key=lambda q: q.area)
        # frame px -> world, then straighten: a learned region has a ragged
        # raster edge, and a roof face does not.
        ring = [(inv_x(px), inv_y(py)) for px, py in p.exterior.coords]
        poly = Polygon(ring)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.geom_type != "Polygon":
            continue
        poly = poly.simplify(max(0.25, 1.2 * m_per_px), preserve_topology=True)
        if poly.area >= MIN_FACE_FRAC * geom.area:
            faces.append(poly)
    return faces


# ---- straight faces ---------------------------------------------------------
# A watershed region has a raster edge: every line wobbles, and one plane the
# model cut in two stays cut. Blind check, 14 Nov, 24 unseen homes: the six
# the owner gave to production had wobbling lines, a plain face split or two
# faces merged. The regions are redrawn from straight lines:
#   * two neighbours whose LiDAR returns lie on one plane (90% of them within
#     ONE_PLANE_P90_M of a single fitted plane) are one face;
#   * where their planes differ and the boundary lies along the planes'
#     intersection (FOLD_SHARE of it within FOLD_NEAR_M), the line is that
#     intersection;
#   * otherwise the line is fitted to the boundary, in straight runs, and
#     turned onto a wall direction (or 45 degrees to one, the plan direction
#     of a hip between equal pitches) when it is close.
# The outline cut by every line gives cells; a cell takes the region most of
# it lay in; cells of one region are one face.
# Measured on the 35 held-out drawn roofs (line F / lines right / face IoU /
# faces right): raster regions 0.626 / 21 / 0.697 / 13; straight 0.619 / 20 /
# 0.716 / 16. The intersection rule is strict because a looser one (0.75 m,
# half the boundary) moved lines off the drawn ridge: 0.560 / 16. The drawn
# ridge is where the photo shows it, and a ridge leans further than the eaves.
ONE_PLANE_P90_M = 0.12
FOLD_NEAR_M = 0.4
FOLD_SHARE = 0.7
FOLD_MIN_SLOPE = 0.06      # 3.4 degrees between the two planes' gradients
MIN_BOUNDARY_M = 0.8
WALL_SNAP_DEG = 15.0
HIP_SNAP_DEG = 8.0
MIN_PLANE_PTS = 8
MIN_STRAIGHT_FACE_M2 = 1.0


def _plane(p):
    """z = ax + by + c through the points, refitted once on the inliers."""
    if len(p) < MIN_PLANE_PTS:
        return None
    A = np.c_[p[:, 0], p[:, 1], np.ones(len(p))]
    eq = np.linalg.lstsq(A, p[:, 2], rcond=None)[0]
    r = np.abs(A @ eq - p[:, 2])
    ok = r <= max(0.10, 2.5 * np.median(r))
    if ok.sum() >= MIN_PLANE_PTS:
        eq = np.linalg.lstsq(A[ok], p[ok, 2], rcond=None)[0]
    return eq


def straight_faces(geom, img_ds, pts, shift=(0.0, 0.0)):
    """The model's faces with straight shared edges, in the photo frame.
    `pts` are LiDAR returns; `shift` is the photo -> LiDAR slide of this
    roof, so a return sits at its position less the slide. [] if
    unavailable."""
    import math
    from scipy import ndimage
    from shapely.ops import unary_union
    from src import point_roof as pr
    got = region_labels(geom, img_ds, pts)
    if got is None:
        return []
    lab, nface, b, m_per_px, min_px = got
    ox, oy = b[0], b[1]                       # local origin: keeps the fits well conditioned
    sx, sy = (b[2] - b[0]) / SIZE, (b[3] - b[1]) / SIZE
    wx = lambda px: px * sx
    wy = lambda py: (SIZE - py) * sy

    # boundary midpoints of every pair of touching regions
    mids = {}
    for A, B, fx, fy in ((lab[:, :-1], lab[:, 1:], 1.0, 0.5), (lab[:-1, :], lab[1:, :], 0.5, 1.0)):
        m = (A > 0) & (B > 0) & (A != B)
        ys, xs = np.nonzero(m)
        for a, c, x, y in zip(A[m], B[m], xs, ys):
            mids.setdefault((min(a, c), max(a, c)), []).append((wx(x + fx), wy(y + fy)))

    # a plane per region, from the returns well inside it
    planes, inner_pts = {}, {}
    if pts is not None and len(pts):
        inner = (lab == ndimage.minimum_filter(lab, 5)) & (lab == ndimage.maximum_filter(lab, 5))
        lx = pts[:, 0] - shift[0] - ox
        ly = pts[:, 1] - shift[1] - oy
        ix = (lx / sx).astype(int)
        iy = (SIZE - ly / sy).astype(int)
        ok = (ix >= 0) & (ix < SIZE) & (iy >= 0) & (iy < SIZE)
        pl = np.zeros(len(pts), np.int32)
        pl[ok] = np.where(inner[iy[ok], ix[ok]], lab[iy[ok], ix[ok]], 0)
        for i in range(1, nface + 1):
            q = np.c_[lx[pl == i], ly[pl == i], pts[pl == i, 2]]
            eq = _plane(q)
            if eq is not None:
                planes[i] = eq
                inner_pts[i] = q

    parent = list(range(nface + 1))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    local = __import__("shapely.affinity", fromlist=["translate"]).translate(geom, -ox, -oy)
    walls = pr.dominant_dirs(local)
    snaps = [(w, WALL_SNAP_DEG) for w in walls] + [((w + 45 * k) % 180, HIP_SNAP_DEG) for w in walls for k in (1, 3)]
    lines = []
    for (a, c), m in mids.items():
        m = np.array(m)
        if len(m) * m_per_px < MIN_BOUNDARY_M:
            continue
        A, B = planes.get(a), planes.get(c)
        if A is not None and B is not None:
            dn = A[:2] - B[:2]
            both = np.vstack([inner_pts[a], inner_pts[c]])
            J = np.c_[both[:, 0], both[:, 1], np.ones(len(both))]
            res = np.abs(J @ np.linalg.lstsq(J, both[:, 2], rcond=None)[0] - both[:, 2])
            if np.percentile(res, 90) < ONE_PLANE_P90_M:
                parent[find(a)] = find(c)
                continue
            if np.hypot(*dn) >= FOLD_MIN_SLOPE:
                nrm = dn / np.hypot(*dn)
                off = (A[2] - B[2]) / np.hypot(*dn)
                near = np.abs(m @ nrm + off) < FOLD_NEAR_M
                if near.mean() >= FOLD_SHARE:
                    d = np.array([-nrm[1], nrm[0]])
                    t = (m[near] + off * nrm) @ d
                    lines.append({"p": -off * nrm + d * np.median(t), "d": d, "kind": "fold",
                                  "pair": (a, c), "n": int(near.sum())})
                    m = m[~near]
                    if len(m) * m_per_px < MIN_BOUNDARY_M:
                        continue
        for seg in pr.split_runs(m):
            if len(seg) * m_per_px < MIN_BOUNDARY_M:
                continue
            ctr = seg.mean(0)
            _, v = np.linalg.eigh(np.cov((seg - ctr).T))
            d = v[:, 1]
            ang = math.degrees(math.atan2(d[1], d[0])) % 180
            for w, tol in snaps:
                if min(abs(ang - w), 180 - abs(ang - w)) < tol:
                    d = np.array([math.cos(math.radians(w)), math.sin(math.radians(w))])
                    break
            t = (seg - ctr) @ d
            if t.max() - t.min() < MIN_BOUNDARY_M:
                continue
            lines.append({"p": ctr, "d": d, "kind": "step", "pair": (a, c), "n": len(seg)})
    cells = pr.arrangement(local, pr.merge_lines(lines)) if lines else [local]
    if not cells:
        return []

    # each cell takes the region most of it lay in
    from rasterio.features import rasterize
    from rasterio.transform import Affine
    cid = rasterize([(c, k + 1) for k, c in enumerate(cells)], out_shape=(SIZE, SIZE),
                    transform=Affine(sx, 0, 0, 0, -sy, SIZE * sy), fill=0, dtype="int32")
    root = np.array([find(i) for i in range(nface + 1)])
    rl = root[lab]
    owner = []
    for k in range(len(cells)):
        v = rl[(cid == k + 1) & (rl > 0)]
        owner.append(int(np.bincount(v).argmax()) if len(v) else 0)
    parts = []
    for r in sorted(set(owner) - {0}):
        u = unary_union([c for c, o in zip(cells, owner) if o == r]).buffer(0)
        parts += [g for g in (u.geoms if hasattr(u, "geoms") else [u]) if g.geom_type == "Polygon"]
    loose = [c for c, o in zip(cells, owner) if o == 0]
    floor = max(MIN_STRAIGHT_FACE_M2, MIN_FACE_FRAC * geom.area)
    keep = [g for g in parts if g.area >= floor]
    loose += [g for g in parts if g.area < floor]
    if not keep:
        return []
    # a scrap joins the face it shares the most edge with
    for g in sorted(loose, key=lambda q: -q.area):
        best, blen = None, 0.05
        for k, f in enumerate(keep):
            ln = g.boundary.intersection(f.boundary).length
            if ln > blen:
                best, blen = k, ln
        if best is not None:
            u = unary_union([keep[best], g]).buffer(0)
            if u.geom_type == "Polygon":
                keep[best] = u
    from shapely.affinity import translate
    return [translate(f, ox, oy) for f in keep]
