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
    # a fixed prompt budget starves big buildings: #4726050's whole west wing
    # went unpanelled because fourteen prompts ran out before coverage did
    budget = max(SAM_MAX_PROMPTS, int(geom.area / 120))
    for _ in range(budget):
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

    segs = network_lines(pr, geom, b, w, h, pts)
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


# ------------------------------------------------- line-network generator

def _medial_axis(poly):
    """Interior medial-axis segments of one polygon, straightened."""
    from scipy.spatial import Voronoi
    from shapely.geometry import Point, LineString, MultiLineString
    from shapely.ops import linemerge
    ring = list(poly.exterior.coords)[:-1]
    dense = []
    for a2, b2 in zip(ring, ring[1:] + ring[:1]):
        L = np.hypot(b2[0] - a2[0], b2[1] - a2[1])
        n2 = max(int(L / 0.4), 1)
        for t in range(n2):
            dense.append((a2[0] + (b2[0] - a2[0]) * t / n2,
                          a2[1] + (b2[1] - a2[1]) * t / n2))
    if len(dense) < 8:
        return []
    vor = Voronoi(np.array(dense))
    shrunk = poly.buffer(-0.25)
    if shrunk.is_empty:
        return []
    raw = []
    for v1, v2 in vor.ridge_vertices:
        if v1 < 0 or v2 < 0:
            continue
        p1, p2 = vor.vertices[v1], vor.vertices[v2]
        if shrunk.contains(Point(*p1)) and shrunk.contains(Point(*p2)):
            raw.append(LineString([p1, p2]))
    if not raw:
        return []
    merged = linemerge(MultiLineString(raw))
    segs = []
    for g2 in getattr(merged, "geoms", [merged]):
        c = list(g2.coords)

        def rec(i, j):
            a3 = np.array(c[i]); b3 = np.array(c[j])
            d = b3 - a3; L = np.hypot(*d)
            if L < 1e-9 or j - i < 2:
                segs.append((a3, b3)); return
            nv = np.array([-d[1], d[0]]) / L
            devs = [abs((np.array(c[k]) - a3) @ nv) for k in range(i, j + 1)]
            k = int(np.argmax(devs))
            if devs[k] > 0.35:
                rec(i, i + k); rec(i + k, j)
            else:
                segs.append((a3, b3))
        rec(0, len(c) - 1)
    return [(a3, b3) for a3, b3 in segs if np.hypot(*(b3 - a3)) >= 0.9]


def network_lines(prob3, geom, bounds, w, h, pts):
    """The line network Josh rated best: candidate nets scored whole, winner
    snapped to the activation and junction-cleaned. Ported from the preview
    where it lived while he judged it; the selector was still feeding
    line_facets the RAW extraction, which reads 7 Anderson Heights as one
    blob face -- "This is still broken", and it was.
    """
    from src.line_extract import (extract, clip_to, _line_mean,
                                  _junction_cleanup, _colinear_merge)
    import shapely.affinity as aff
    from shapely.ops import unary_union

    b = bounds
    P = prob3.max(axis=0)

    def w2p(x, y):
        return np.array([(x - b[0]) / (b[2] - b[0]) * w,
                         (1 - (y - b[1]) / (b[3] - b[1])) * h])

    def p2w(a):
        return (b[0] + a[0] / w * (b[2] - b[0]),
                b[1] + (1 - a[1] / h) * (b[3] - b[1]))

    def tw(px, py):
        return p2w(np.array([px, py]))

    ext = clip_to(extract(prob3, tw), geom)
    cand_A = [(w2p(r["seg"][0], r["seg"][1]), w2p(r["seg"][2], r["seg"][3]))
              for r in ext]
    blobs = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    cand_B, cand_C, cand_D = [], [], []
    for blob in blobs:
        for a3, b3 in _medial_axis(blob):
            cand_B.append((w2p(*a3), w2p(*b3)))
        mrr = blob.minimum_rotated_rectangle
        cc = list(mrr.exterior.coords)[:4]
        e = sorted(((np.hypot(cc[(k + 1) % 4][0] - cc[k][0],
                              cc[(k + 1) % 4][1] - cc[k][1]), k)
                    for k in range(4)), reverse=True)
        k0 = e[0][1]
        mid_a = ((np.array(cc[k0]) + np.array(cc[(k0 + 3) % 4])) / 2)
        mid_b = ((np.array(cc[(k0 + 1) % 4]) + np.array(cc[(k0 + 2) % 4])) / 2)
        cand_C.append((w2p(*mid_a), w2p(*mid_b)))
        if e[0][0] / max(e[2][0], 1e-6) < 1.5:
            inner = aff.scale(mrr, 0.35, 0.35, origin="centroid")
            ic = list(inner.exterior.coords)[:4]
            for k in range(4):
                cand_D.append((w2p(*ic[k]), w2p(*ic[(k + 1) % 4])))
                cand_D.append((w2p(*ic[k]), w2p(*cc[k])))

    def snap(a2, b2):
        d2 = b2 - a2
        L = np.hypot(*d2)
        if L < 1e-6:
            return a2, b2
        u2 = d2 / L
        nrm = np.array([-u2[1], u2[0]])
        best, bo = -1.0, 0.0
        for o in np.arange(-5.0, 5.01, 0.5):
            m2 = _line_mean(P, a2 + o * nrm, b2 + o * nrm)
            if m2 > best:
                best, bo = m2, o
        return a2 + bo * nrm, b2 + bo * nrm

    def score_net(net):
        tot = 0.0
        for a2, b2 in net:
            a3, b3 = snap(a2, b2)
            tot += np.hypot(*(b3 - a3)) * (_line_mean(P, a3, b3) - 0.25)
        return tot

    best_net, best_sc = [], -1e9
    for c in (cand_A, cand_B, cand_C, cand_D):
        if not c:
            continue
        sc = score_net(c)
        if sc > best_sc:
            best_sc, best_net = sc, c
    cleaned = _junction_cleanup([snap(a2, b2) for a2, b2 in best_net])

    # COMPLETE THE WINNER'S JUNCTIONS. Josh, on the first Anderson reading he
    # called much better: "you are missing a few ridge lines and therefore
    # faces missing" -- the short links between the dormer pyramid and the end
    # hips. One candidate family rarely carries every line, but the missing
    # ones have a structural signature: both endpoints already ARE nodes of
    # the winning network. A line from any family may join the winner if the
    # activation supports it, it duplicates nothing, and it connects two
    # existing nodes -- nothing free-floating is ever added.
    from src.line_extract import _line_mean as _lm
    nodes = []
    for a2, b2 in cleaned:
        nodes.extend([a2, b2])
    extra = []
    for fam in (cand_A, cand_B, cand_C, cand_D):
        for a2, b2 in fam:
            a3, b3 = snap(a2, b2)
            if np.hypot(*(b3 - a3)) < 12:
                continue
            if _lm(P, a3, b3) < 0.45:
                continue
            near_a = any(np.hypot(*(a3 - n)) < 12 for n in nodes)
            near_b = any(np.hypot(*(b3 - n)) < 12 for n in nodes)
            if not (near_a and near_b):
                continue
            d3 = (b3 - a3) / max(np.hypot(*(b3 - a3)), 1e-9)
            dup = False
            for ka, kb in cleaned + extra:
                kd = kb - ka
                kL = np.hypot(*kd)
                if kL < 1e-9:
                    continue
                kd = kd / kL
                ang = abs(np.degrees(np.arctan2(kd[1], kd[0])
                                     - np.arctan2(d3[1], d3[0]))) % 180
                if min(ang, 180 - ang) > 12:
                    continue
                nrm = np.array([-kd[1], kd[0]])
                if (abs((a3 - ka) @ nrm) < 6 and abs((b3 - ka) @ nrm) < 6):
                    dup = True
                    break
            if not dup:
                extra.append((a3, b3))
    # Node-pair SYNTHESIS -- inventing a connector between two existing
    # junctions -- was built and measured in four variants (activation-gated,
    # dual-evidence, crossing-guarded, plane-intersection-tested) and every
    # one lowered mean agreement with Josh's faces below this configuration
    # (0.737 / 0.711 / 0.731 against 0.744). It found the one ridge he named
    # on Anderson only in the variant that hurt most elsewhere. Not kept: a
    # connector invisible to both instruments routes its roof to the markup
    # queue instead of being guessed.
    if extra:
        cleaned = _junction_cleanup(cleaned + extra)

    # ROOF-GRAPH CLOSURE. Josh, three times on the same missing Anderson
    # ridge -- and topology says he is right to insist. Where two hips meet
    # and stop, the roof planes either side are still separated, so the fold
    # MUST continue until it meets another line: a junction whose edges all
    # leave on one side is not a place a fold can end. The continuation's
    # existence and direction are both forced -- down the open gap -- and it
    # terminates only on the network. No evidence gate is needed for what
    # geometry mandates; four evidence-gated syntheses of this same line each
    # cost the global mean, because they also admitted lines topology forbids.
    def closure(net):
        nodes2 = []
        for a2, b2 in net:
            for q in (a2, b2):
                if not any(np.hypot(*(q - m)) < 3 for m in nodes2):
                    nodes2.append(q)
        added = []
        for nd in nodes2:
            dirs = []
            for a2, b2 in net:
                for q, o in ((a2, b2), (b2, a2)):
                    if np.hypot(*(q - nd)) < 3:
                        v = o - q
                        L = np.hypot(*v)
                        if L > 1e-6:
                            dirs.append(np.arctan2(v[1], v[0]))
            if len(dirs) == 1:
                # A DANGLING FOLD END MID-ROOF IS THE CANONICAL MUST-CONTINUE
                # CASE -- a fold cannot end on a plane -- and this rule
                # originally skipped exactly those nodes. Anderson's missing
                # right ridge was a short stub off the pyramid corner whose
                # far end dangled here, invisible to a junction-gap test.
                # Continue along the stub's own direction; direction is never
                # invented, only length, and only to a network hit.
                bis = dirs[0] + np.pi
                u2 = np.array([np.cos(bis), np.sin(bis)])
                best_t = None
                for a2, b2 in net:
                    d2 = b2 - a2
                    den = d2[0] * u2[1] - d2[1] * u2[0]
                    if abs(den) < 1e-9:
                        continue
                    t = ((a2 - nd)[0] * d2[1] - (a2 - nd)[1] * d2[0]) / -den
                    r2 = ((a2 - nd)[0] * u2[1] - (a2 - nd)[1] * u2[0]) / -den
                    if t > 6 and -0.05 <= r2 <= 1.05:
                        if best_t is None or t < best_t:
                            best_t = t
                for m in nodes2:
                    v = m - nd
                    t = v @ u2
                    if t > 6 and np.hypot(*(v - t * u2)) < 5:
                        if best_t is None or t < best_t:
                            best_t = t
                if best_t is not None and best_t <= 140:
                    added.append((nd.copy(), nd + best_t * u2))
                continue
            dirs.sort()
            gaps = [(dirs[(i + 1) % len(dirs)] - dirs[i]) % (2 * np.pi)
                    for i in range(len(dirs))]
            gi = int(np.argmax(gaps))
            if gaps[gi] < np.radians(200):
                continue          # edges leave all around: a closed junction
            bis = dirs[gi] + gaps[gi] / 2
            u2 = np.array([np.cos(bis), np.sin(bis)])
            # march down the gap to the first thing the network offers
            best_t = None
            for a2, b2 in net:
                d2 = b2 - a2
                den = d2[0] * u2[1] - d2[1] * u2[0]
                if abs(den) < 1e-9:
                    continue
                t = ((a2 - nd)[0] * d2[1] - (a2 - nd)[1] * d2[0]) / -den
                r2 = ((a2 - nd)[0] * u2[1] - (a2 - nd)[1] * u2[0]) / -den
                if t > 6 and -0.05 <= r2 <= 1.05:
                    if best_t is None or t < best_t:
                        best_t = t
            for m in nodes2:
                v = m - nd
                t = v @ u2
                if t > 6 and np.hypot(*(v - t * u2)) < 5:
                    if best_t is None or t < best_t:
                        best_t = t
            if best_t is not None and best_t <= 100:
                added.append((nd.copy(), nd + best_t * u2))
        return added

    def cluster(net, tol=6.0):
        """Weld near-coincident junctions into one point.

        The probe on Anderson showed why closure misfired: four separate
        nodes within ~8 px around one pyramid corner, degree-1 fragments
        hanging off them. Topology rules cannot read a soup. Every endpoint
        joins the centroid of its cluster; zero-length remains are dropped.
        """
        reps = []
        def find(q):
            for r in reps:
                if np.hypot(*(q - r["c"])) <= tol:
                    return r
            return None
        for a2, b2 in net:
            for q in (a2, b2):
                r = find(q)
                if r is None:
                    reps.append({"c": q.copy(), "m": [q]})
                else:
                    r["m"].append(q)
                    r["c"] = np.mean(r["m"], axis=0)
        out2 = []
        for a2, b2 in net:
            ra, rb = find(a2), find(b2)
            na = ra["c"] if ra is not None else a2
            nb = rb["c"] if rb is not None else b2
            if np.hypot(*(nb - na)) >= 8:
                out2.append((na, nb))
        return out2

    cleaned = cluster(cleaned)
    grown = closure(cleaned)
    if grown:
        cleaned = cluster(cleaned + grown)

    # STRAIGHTEN JOGS. Josh, on the one line still wrong on Anderson: "A
    # misplaced line in the middle, which should be at the point of the
    # triangle" -- a 1.9 m skewed splice between two runs of one straight
    # ridge, turning it just short of the apex. When a short segment's two
    # neighbours run nearly collinear THROUGH it, the jog is an artefact of
    # tracing, not a fold: project its endpoints onto the neighbours' common
    # line so the collinear merge can absorb all three into one.
    def straighten_jogs(net):
        for i, (a2, b2) in enumerate(net):
            L = np.hypot(*(b2 - a2))
            if not (1.0 < L < 30.0):
                continue
            def longest_other_at(pt):
                best = None
                for j, (c2, e2) in enumerate(net):
                    if j == i:
                        continue
                    for q, o in ((c2, e2), (e2, c2)):
                        if np.hypot(*(q - pt)) < 3:
                            Ln2 = np.hypot(*(o - q))
                            if Ln2 > 2 * L and (best is None or Ln2 > best[0]):
                                best = (Ln2, q, o)
                return best
            na = longest_other_at(a2)
            nb = longest_other_at(b2)
            if na is None or nb is None:
                continue
            da = na[2] - na[1]
            db = nb[2] - nb[1]
            ang = abs(np.degrees(np.arctan2(da[1], da[0])
                                 - np.arctan2(db[1], db[0]))) % 180
            if min(ang, 180 - ang) > 12:
                continue
            u2 = da / max(np.hypot(*da), 1e-9)
            # both neighbours on one line? check B's anchor against A's line
            nrm = np.array([-u2[1], u2[0]])
            if abs((nb[1] - na[1]) @ nrm) > 3:
                continue
            base = na[1]
            net[i] = (base + ((a2 - base) @ u2) * u2,
                      base + ((b2 - base) @ u2) * u2)
        return net

    cleaned = straighten_jogs(list(cleaned))
    merged4 = True
    while merged4:
        merged4 = False
        for i in range(len(cleaned)):
            if cleaned[i] is None:
                continue
            for j in range(i + 1, len(cleaned)):
                if cleaned[j] is None:
                    continue
                m4 = _colinear_merge(cleaned[i], cleaned[j])
                if m4 is not None:
                    cleaned[i] = m4
                    cleaned[j] = None
                    merged4 = True
        cleaned = [c for c in cleaned if c is not None]

    out = []
    for a2, b2 in cleaned:
        x1, y1 = p2w(a2)
        x2, y2 = p2w(b2)
        out.append([x1, y1, x2, y2])

    # NEAR-DUPLICATE SEGMENTS SHATTER THE POLYGONIZATION. The Anderson autopsy:
    # 13 of Josh's 14 lines present, polygonize yields 9 cells matching his 9
    # faces -- and one is an 83 m2 leak beside a 2.6 m2 SLIVER. Two almost-
    # coincident lines survive the pixel weld, polygonize builds a thin
    # corridor between them, and the faces either side connect around its
    # ends. Keep the longer of any coincident pair, in metres, at the end.
    ded = []
    for seg in sorted(out, key=lambda s2: -np.hypot(s2[2] - s2[0],
                                                    s2[3] - s2[1])):
        a3 = np.array(seg[:2]); b3 = np.array(seg[2:])
        L = np.hypot(*(b3 - a3))
        if L < 1e-6:
            continue
        u3 = (b3 - a3) / L
        dup = False
        for k in ded:
            ka = np.array(k[:2]); kb = np.array(k[2:])
            kd = kb - ka
            kL = np.hypot(*kd)
            kd = kd / kL
            ang = abs(np.degrees(np.arctan2(kd[1], kd[0])
                                 - np.arctan2(u3[1], u3[0]))) % 180
            if min(ang, 180 - ang) > 10:
                continue
            nrm = np.array([-kd[1], kd[0]])
            if abs((a3 - ka) @ nrm) > 0.4 or abs((b3 - ka) @ nrm) > 0.4:
                continue
            t = sorted(((a3 - ka) @ kd, (b3 - ka) @ kd))
            if min(t[1], kL) - max(t[0], 0.0) > 0.5 * L:
                dup = True
                break
        if not dup:
            ded.append(seg)

    # AN ALMOST-CLOSED MOUTH LEAKS A WHOLE FACE. Two hips welded to each
    # other half a metre above the eave corner are both degree-2 there, so the
    # degree-based sealer never extends them, and polygonize leaks two of
    # Josh's faces into one 83 m2 cell through the gap. Snapping every
    # near-outline endpoint onto the ring was measured and REVERTED -- it
    # dragged legitimate interior ends sideways and cost 0.06 of mean
    # agreement. The mouth case is specific: an endpoint near a footprint
    # VERTEX belongs on that corner, and only that is done.
    corners = [np.array(c) for c in list(geom.exterior.coords)[:-1]]
    snapped = []
    for seg in ded:
        pts2 = [np.array(seg[:2]), np.array(seg[2:])]
        for i2 in range(2):
            for c2 in corners:
                if 1e-6 < np.hypot(*(pts2[i2] - c2)) < 0.8:
                    pts2[i2] = c2.copy()
                    break
        if np.hypot(*(pts2[1] - pts2[0])) >= 0.8:
            snapped.append([pts2[0][0], pts2[0][1], pts2[1][0], pts2[1][1]])
    return snapped


def rect_parts(geom, depth=0):
    """Split a footprint at reflex corners into near-rectangular parts.

    Public here because flatness is a PER-PART question: #5371128 is a flat
    block joined to a gabled hall, and a whole-building plane fit read the
    pair as flat, deferring a roof the selector should own.
    """
    from shapely.geometry import LineString
    from shapely.ops import split as shp_split
    ring = list(geom.exterior.coords)[:-1]
    n = len(ring)
    area2 = sum(ring[i][0] * ring[(i + 1) % n][1]
                - ring[(i + 1) % n][0] * ring[i][1] for i in range(n))
    if depth >= 6:
        return [geom]
    for i in range(n):
        ax, ay = ring[i - 1]
        bx, by = ring[i]
        cx, cy = ring[(i + 1) % n]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if (cross < 0) != (area2 > 0):
            continue
        best = None
        for ox, oy in ((bx - ax, by - ay), (bx - cx, by - cy)):
            L = np.hypot(ox, oy)
            if L < 1e-9:
                continue
            u = (ox / L, oy / L)
            cut = LineString([(bx - u[0] * 0.05, by - u[1] * 0.05),
                              (bx + u[0] * 200, by + u[1] * 200)])
            inner = cut.intersection(geom)
            ln = sum(g2.length for g2 in getattr(inner, "geoms", [inner])
                     if g2.geom_type == "LineString")
            if ln > 0.5 and (best is None or ln < best[0]):
                best = (ln, cut)
        if best is None:
            continue
        try:
            pieces = [g2 for g2 in shp_split(geom, best[1]).geoms
                      if g2.geom_type == "Polygon" and g2.area > 4.0]
        except Exception:
            continue
        if len(pieces) >= 2:
            out = []
            for pc2 in pieces:
                out.extend(rect_parts(pc2, depth + 1))
            return out
    return [geom]
