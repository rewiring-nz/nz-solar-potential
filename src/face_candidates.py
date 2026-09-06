"""Two independent readings of a roof's faces, and the evidence to choose one.

A day of Josh's verdicts established that no single front-end wins everywhere:

  LINE NETWORK  best on crease-textured hip houses -- "much better" on
                7 Anderson Heights -- because the detector fires on folds
  SAM           best where faces differ in tone or material -- "clearly
                better than any of your lines" on #4735106 -- because it
                segments surfaces, not folds

Every attempt to MERGE them degraded the winner: each stage's failure modes
multiplied. So they are not merged. Each generator produces a complete
candidate face-set, a scorer measures each against the imagery and LiDAR
evidence, and the better one is used -- per roof. Roofs where both score
poorly belong in Josh's markup queue, not in a guess.

The scorer's authority is not taken on faith: tools/select_faces.py measures,
on the benchmark roofs Josh has drawn, whether the scorer picks the candidate
that agrees better with HIS faces. Selection accuracy against his markup is
the only accepted validation here.
"""

import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SAM_MAX_PROMPTS = 14
GRID_RES_M = 0.2
CLUTTER_MAX_M2 = 15.0
CLUTTER_STEP_M = 0.45
MERGE_SLOPE_DEG = 3.0
MERGE_ASPECT_DEG = 15.0


# ----------------------------------------------------------- shared helpers

def _grid(geom, bounds):
    import shapely
    gxs = np.arange(bounds[0], bounds[2] + GRID_RES_M, GRID_RES_M)
    gys = np.arange(bounds[1], bounds[3] + GRID_RES_M, GRID_RES_M)
    GX, GY = np.meshgrid(gxs, gys)
    inside = shapely.contains_xy(geom, GX.ravel(), GY.ravel()).reshape(GX.shape)
    return GX, GY, gys, inside


def _tile(faces, geom, bounds):
    """Assign a grid of the footprint to faces -> clean partition polygons."""
    import shapely
    import rasterio.features
    from rasterio.transform import from_origin
    from shapely.geometry import Polygon
    from scipy.spatial import cKDTree

    GX, GY, gys, inside = _grid(geom, bounds)
    lab = np.full(GX.shape, -1, dtype=int)
    for i, f in enumerate(faces):
        m = shapely.contains_xy(f, GX.ravel(), GY.ravel()).reshape(GX.shape)
        lab[m & inside & (lab < 0)] = i
    un = inside & (lab < 0)
    if un.any() and (lab >= 0).any():
        tree = cKDTree(np.c_[GX[lab >= 0], GY[lab >= 0]])
        _, nn = tree.query(np.c_[GX[un], GY[un]])
        lab[un] = lab[lab >= 0][nn]

    tr = from_origin(bounds[0] - GRID_RES_M / 2, gys[-1] + GRID_RES_M / 2,
                     GRID_RES_M, GRID_RES_M)
    out = []
    for rid in np.unique(lab):
        if rid < 0:
            continue
        mask = np.flipud(lab == rid).astype("uint8")
        best = None
        for geo, val in rasterio.features.shapes(mask, transform=tr):
            if val != 1:
                continue
            poly = Polygon(geo["coordinates"][0])
            if best is None or poly.area > best.area:
                best = poly
        if best is None:
            continue
        best = best.intersection(geom).simplify(0.3)
        for g in getattr(best, "geoms", [best]):
            if g.geom_type == "Polygon" and g.area >= 2.0:
                out.append(g)
    return out


def _plane(poly, pts):
    from src.roof_partition import _points_in, _fit_plane_robust
    sub = _points_in(poly, pts) if pts is not None and len(pts) else []
    if len(sub) < 10:
        return None, sub
    return _fit_plane_robust(sub), sub


# ------------------------------------------------------------- SAM faces

def sam_faces(predictor, rgb, geom, bounds, pts):
    """Coverage-completion SAM + LiDAR clutter/merge. Returns (faces, obs)."""
    import rasterio.features
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    from src.roof_partition import _slope_aspect

    h, w = rgb.shape[:2]
    b = bounds
    predictor.set_image(rgb)

    def to_world(ring_px):
        return [(b[0] + x / w * (b[2] - b[0]),
                 b[1] + (1 - y / h) * (b[3] - b[1])) for x, y in ring_px]

    def px_of(pt):
        return np.array([[(pt.x - b[0]) / (b[2] - b[0]) * w,
                          (1 - (pt.y - b[1]) / (b[3] - b[1])) * h]],
                        dtype=np.float32)

    def mask_to_poly(mask):
        best = None
        for geo, val in rasterio.features.shapes(mask.astype("uint8")):
            if val != 1:
                continue
            poly = Polygon(to_world(geo["coordinates"][0]))
            if not poly.is_valid:
                poly = poly.buffer(0)
            if not poly.is_empty and (best is None or poly.area > best.area):
                best = poly
        return best

    faces = []
    uncovered = geom
    for _ in range(SAM_MAX_PROMPTS):
        if uncovered.is_empty or uncovered.area < 3.0:
            break
        probe = max(getattr(uncovered, "geoms", [uncovered]),
                    key=lambda g: g.area)
        if probe.area < 3.0:
            break
        seed = probe.representative_point()
        mk, sc, _ = predictor.predict(point_coords=px_of(seed),
                                      point_labels=np.array([1]),
                                      multimask_output=True)
        pick = None
        for i in np.argsort(-sc):
            poly = mask_to_poly(mk[i])
            if poly is None:
                continue
            clipped = poly.intersection(geom)
            if clipped.is_empty or clipped.area < 2.0:
                continue
            if clipped.area > 0.85 * geom.area:
                continue
            pick = clipped
            break
        if pick is None:
            uncovered = uncovered.difference(seed.buffer(0.8))
            continue
        fresh = pick.difference(unary_union(faces)) if faces else pick
        for g in getattr(fresh, "geoms", [fresh]):
            if g.geom_type == "Polygon" and g.area >= 2.0:
                faces.append(g)
        uncovered = uncovered.difference(pick.buffer(0.05))

    if not faces:
        return [], []
    polys = _tile(faces, geom, bounds)

    # clutter: small and sitting above what surrounds it
    obs = []
    kept = []
    for poly in polys:
        if poly.area <= CLUTTER_MAX_M2 and pts is not None and len(pts):
            pl, sub = _plane(poly, pts)
            ring = poly.buffer(1.2).difference(poly).intersection(geom)
            _, around = _plane(ring, pts)
            if (pl is not None and len(around) >= 10
                    and float(np.median(sub[:, 2]))
                    - float(np.median(around[:, 2])) > CLUTTER_STEP_M):
                obs.append(poly)
                continue
        kept.append(poly)
    polys = _tile(kept, geom, bounds) if obs else polys

    # merge neighbours whose planes agree
    from src.roof_partition import _slope_aspect as _sa
    changed = True
    while changed and len(polys) > 1:
        changed = False
        for i in range(len(polys)):
            for j in range(i + 1, len(polys)):
                pi, pj = polys[i], polys[j]
                if pi.buffer(0.3).intersection(pj).is_empty:
                    continue
                pl1, _ = _plane(pi, pts)
                pl2, _ = _plane(pj, pts)
                if pl1 is None or pl2 is None:
                    continue
                s1, a1 = _sa(pl1)
                s2, a2 = _sa(pl2)
                da = abs(a1 - a2) % 360
                da = min(da, 360 - da)
                if abs(s1 - s2) < MERGE_SLOPE_DEG and \
                        (da < MERGE_ASPECT_DEG or max(s1, s2) < 4):
                    merged = pi.union(pj).buffer(0.02).buffer(-0.02)
                    if merged.geom_type == "Polygon":
                        polys = ([p for k, p in enumerate(polys)
                                  if k not in (i, j)] + [merged])
                        changed = True
                        break
            if changed:
                break
    return [p.simplify(0.3) for p in polys if p.area >= 2.0], obs


# ------------------------------------------------------------ line faces

def line_faces(line_model, device, rgb, geom, bounds, pts, building_id=0):
    """The line-network reading: detect -> extract -> polygonize -> faces."""
    import torch
    from src.line_extract import extract, clip_to
    from src.roof_partition import line_facets

    h, w = rgb.shape[:2]
    b = bounds
    ph, pw = (-h) % 16, (-w) % 16
    arr = np.pad(rgb, ((0, ph), (0, pw), (0, 0)))
    x = torch.from_numpy(arr).float().permute(2, 0, 1)[None] / 255.0
    with torch.no_grad():
        pr = torch.sigmoid(line_model(x.to(device)))[0].cpu().numpy()[:, :h, :w]

    def tw(px, py):
        return (b[0] + px / w * (b[2] - b[0]),
                b[1] + (1 - py / h) * (b[3] - b[1]))

    segs = [r["seg"] for r in clip_to(extract(pr, tw), geom)
            if r["score"] >= 0.5]
    if not segs:
        return [], pr
    facets = line_facets(building_id, geom, pts, segs) or []
    return [f["geometry"] for f in facets], pr


# --------------------------------------------------------------- scorer

def score_candidate(faces, geom, prob_max, to_px, pts, inv_px=None):
    """How well a face-set fits the evidence. Higher is better.

    Two terms, one per instrument, both bounded:
      EDGES  interior boundaries should lie on imagery activation -- measured
             as mean activation along them. Exterior edges are the outline's
             business and score nothing either way.
      PLANES each face should be one plane -- mean LiDAR inlier fraction,
             area-weighted.
    A candidate with no interior edges (one big face) earns only its plane
    term, so a genuinely multi-face roof rewards the reading that found its
    folds.
    """
    from src.line_extract import _line_mean
    from src.roof_partition import _inlier_fraction

    if not faces:
        return 0.0
    rim = geom.exterior.buffer(0.5)
    edge_len = edge_sup = 0.0
    for f in faces:
        coords = list(f.exterior.coords)
        for a, bb in zip(coords, coords[1:]):
            import shapely.geometry as sg
            seg = sg.LineString([a, bb])
            inner = seg.difference(rim)
            L = inner.length
            if L < 0.5:
                continue
            pa = np.array(to_px(*a))
            pb = np.array(to_px(*bb))
            edge_len += L
            edge_sup += L * _line_mean(prob_max, pa, pb)
    edge_term = (edge_sup / edge_len) if edge_len > 3.0 else 0.35

    # RECALL of the activation: edge precision alone is biased toward the line
    # reading, whose edges lie on activation by construction -- the scorer
    # chose LINE on roofs where SAM agreed far better with Josh's faces
    # (#4735316: 0.90 vs 0.61, picked LINE). A reading that MISSES a fold the
    # imagery clearly shows must pay for it, whichever family it came from.
    ys, xs = np.nonzero(prob_max > 0.5)
    recall_term = 0.5
    if len(xs) > 30:
        import shapely.geometry as sg
        from shapely.ops import unary_union
        rings = [sg.LineString(list(f.exterior.coords)) for f in faces]
        net = unary_union(rings).buffer(3.0)   # px frame? no -- world; convert
        # boundaries are in world coords; activation in px. Sample activation
        # pixels back to world for the test.
        pxs = np.c_[xs, ys][np.random.default_rng(7).permutation(len(xs))[:400]]
        hit = 0
        for x2, y2 in pxs:
            wpt = sg.Point(inv_px(x2, y2))
            if net.distance(wpt) < 0.45:
                hit += 1
        recall_term = hit / len(pxs)

    # STEP RECALL. Josh, on 8 Isle Street: "This missed a roof plane" -- the
    # selector shipped the reading that ran one face across an annex sitting a
    # storey lower. A height step is the one boundary LiDAR sees decisively,
    # and no term looked at it: a reading that separates faces across a big
    # step earns credit, one that papers over it pays.
    step_term = 0.5
    if pts is not None and len(pts) > 80:
        from scipy.spatial import cKDTree
        import shapely.geometry as sg
        from shapely.ops import unary_union
        xy = pts[:, :2]
        tree = cKDTree(xy)
        rng = np.random.default_rng(11)
        idx = rng.permutation(len(pts))[:250]
        steps = []
        for i in idx:
            nb = tree.query_ball_point(xy[i], 1.0)
            if len(nb) < 4:
                continue
            z = pts[nb, 2]
            if z.max() - z.min() > 0.7:
                steps.append(xy[i])
        if len(steps) >= 8:
            net = unary_union(
                [sg.LineString(list(f.exterior.coords)) for f in faces])
            hit = sum(1 for q in steps
                      if net.distance(sg.Point(q)) < 0.8)
            step_term = hit / len(steps)

    plane_num = plane_den = 0.0
    for f in faces:
        pl, sub = _plane(f, pts)
        if pl is None:
            continue
        plane_num += f.area * _inlier_fraction(sub, pl)
        plane_den += f.area
    plane_term = (plane_num / plane_den) if plane_den else 0.0
    # A reading is also answerable for the roof it left unexplained. Josh's
    # faces tile the footprint; a candidate of two clean faces covering 40%
    # scored best of all before this factor -- quality of what it kept, no
    # charge for what it dropped (#4734678: score 0.77, agreement 0.28).
    coverage = min(1.0, sum(f.area for f in faces) / max(geom.area, 1e-9))
    return (0.3 * edge_term + 0.2 * recall_term + 0.2 * step_term
            + 0.3 * plane_term) * coverage
