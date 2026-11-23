"""Roof faces from the reader's maps, the way a roof is drawn by hand.

A person marks a roof as points joined by straight lines: the outline's
corners, the ends of each ridge, valley and drop edge. The reader
(tools/train_roof_lines.py) predicts those points (the join map), the lines
by kind and the roof itself. This turns them back into the drawing:

1. joins: the peaks of the join map;
2. the outline: the edge of the roof map, simplified, its corners moved onto
   the joins beside them;
3. lines: a straight segment between two joins is kept when the line maps
   hold along it (mean LINE_MEAN or more, and no stretch of it empty), it
   does not run through a third join, and it crosses no better-supported line;
4. faces: the areas the outline and the kept lines enclose.

Every line is straight and every line ends on a join, so there are no
doubled or wandering lines to tidy afterwards. Pixel coordinates throughout
(x right, y down); the caller maps to metres.
"""
import os

import numpy as np
from scipy.ndimage import maximum_filter, label as cc_label, map_coordinates
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

JOIN_MIN, JOIN_NMS_PX = 0.35, 4
LINE_MEAN, LINE_LOW, LINE_LOW_SHARE = 0.45, 0.15, 0.2
THROUGH_PX, SNAP_PX, OUTLINE_SIMPLIFY_PX = 2.5, 6.0, 1.5
MIN_FACE_PX2 = 60.0
OUTLINE_JOIN_IOU = 0.93
SKELETON_JOIN_PX = 7.0
STRAIGHT_SIMPLIFY_PX, STRAIGHT_SNAP_DEG, STRAIGHT_MERGE_PX, STRAIGHT_MIN_IOU = 4.0, 15.0, 3.0, 0.85
STRAIGHT_DROP_PX = 20.0
EXTEND_PX, EXTEND_SHARE = 24.0, 0.35
KINDS = ("ridge", "valley", "cliff")
STRAIGHTEN = True
EDGE_BAND_PX = 8


def join_points(jmap):
    from scipy.ndimage import gaussian_filter
    jmap = gaussian_filter(jmap.astype(np.float32), 1.0)      # one peak per join, not a flat top
    peak = (jmap == maximum_filter(jmap, size=2 * JOIN_NMS_PX + 1)) & (jmap >= JOIN_MIN)
    ys, xs = np.nonzero(peak)
    pts = sorted(zip(jmap[ys, xs], xs.astype(float), ys.astype(float)), reverse=True)
    out = []
    for _, x, y in pts:                       # a flat-topped peak gives several pixels: keep one
        if all(np.hypot(x - a, y - b) > JOIN_NMS_PX for a, b in out):
            out.append((x, y))
    return out


def roof_outline(roof, edge_map=None):
    """The largest roof area as a simplified polygon.

    With `edge_map` (the reader's map of the roof's outer edge) the area's
    boundary is first carried onto the crest of that map: the roof map is
    soft at its rim and its 0.5 level wanders, while the edge map is a thin
    line on the edge itself. The sure inside (the area shrunk by
    EDGE_BAND_PX) and the sure outside (everything beyond the area grown by
    it) flood toward each other over the edge map and meet on its crest."""
    from skimage import measure
    lab, n = cc_label(roof >= 0.5)
    if not n:
        return None
    big = lab == (1 + np.argmax(np.bincount(lab.ravel())[1:]))
    if edge_map is not None:
        from scipy.ndimage import binary_dilation, binary_erosion, binary_fill_holes
        from skimage.segmentation import watershed
        inner = binary_erosion(big, iterations=EDGE_BAND_PX)
        if inner.any():
            marks = np.where(inner, 1, 0) + np.where(~binary_dilation(big, iterations=EDGE_BAND_PX), 2, 0)
            # the roof map breaks ties where the edge map is flat
            big = watershed(edge_map + 0.05 * (1 - np.clip(np.abs(roof - 0.5) * 2, 0, 1)), markers=marks) == 1
            lab, n = cc_label(big)
            big = binary_fill_holes(lab == (1 + np.argmax(np.bincount(lab.ravel())[1:]))) if n else big
    cs = measure.find_contours(np.pad(big, 1).astype(float), 0.5)
    if not cs:
        return None
    c = max(cs, key=len) - 1.0
    p = Polygon([(x, y) for y, x in c]).buffer(0)
    if p.geom_type == "MultiPolygon":
        p = max(p.geoms, key=lambda g: g.area)
    return straighten(p) if STRAIGHTEN else p.simplify(OUTLINE_SIMPLIFY_PX)


def straighten(poly, tol=STRAIGHT_SIMPLIFY_PX):
    """The polygon with straight edges in the roof's two main directions.

    A roof's outer edges are straight and nearly all run in one direction or
    at right angles to it. The edge of a predicted roof map wobbles and has
    rounded corners. Rule: simplify to `tol`; the main direction is the
    length-weighted peak of the edge directions (mod 90 degrees); an edge
    within STRAIGHT_SNAP_DEG of it or of its right angle is turned onto it
    about its midpoint; neighbouring edges on the same line are merged; the
    corners are where consecutive edge lines cross. The result is kept only
    if it covers the same area as before (STRAIGHT_MIN_IOU)."""
    q = poly.simplify(tol)
    c = np.asarray(q.exterior.coords)[:-1]
    if len(c) < 4:
        return q
    a, b2 = c, np.roll(c, -1, axis=0)
    d = b2 - a; ln = np.hypot(d[:, 0], d[:, 1]); ang = np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 180.0
    hist = np.zeros(90)
    for g, l in zip(ang, ln):
        hist[int(g % 90.0) % 90] += l
    sm = sum(np.roll(hist, k) for k in (-2, -1, 0, 1, 2))
    k = int(np.argmax(sm))
    w = np.array([hist[(k + j) % 90] for j in range(-2, 3)])
    dom = (k + 0.5 + float((w * np.arange(-2, 3)).sum() / max(w.sum(), 1e-9))) % 90.0
    edges = []                                        # (direction in degrees, point on the line, length)
    for i in range(len(c)):
        g = ang[i]
        for t in (dom, dom + 90.0):
            diff = abs((g - t + 90.0) % 180.0 - 90.0)
            if diff <= STRAIGHT_SNAP_DEG:
                g = t % 180.0
                break
        if g not in (dom % 180.0, (dom + 90.0) % 180.0) and ln[i] < STRAIGHT_DROP_PX:
            continue                                  # a short slanted edge is a rounded corner: its neighbours meet instead
        edges.append([g, (a[i] + b2[i]) / 2.0, ln[i]])
    merged = []
    for e in edges:                                   # neighbours on one line become one edge
        if merged:
            m = merged[-1]
            if abs((e[0] - m[0] + 90.0) % 180.0 - 90.0) < 1e-6:
                n = np.array([-np.sin(np.radians(m[0])), np.cos(np.radians(m[0]))])
                if abs(float(np.dot(e[1] - m[1], n))) <= STRAIGHT_MERGE_PX:
                    m[1] = (m[1] * m[2] + e[1] * e[2]) / (m[2] + e[2]); m[2] += e[2]
                    continue
        merged.append(e)
    if len(merged) < 3:
        return q
    out = []
    for i in range(len(merged)):
        g1, p1, _ = merged[i - 1]; g2, p2, _ = merged[i]
        u = np.array([np.cos(np.radians(g1)), np.sin(np.radians(g1))])
        v = np.array([np.cos(np.radians(g2)), np.sin(np.radians(g2))])
        den = u[0] * v[1] - u[1] * v[0]
        if abs(den) < 0.17:                           # nearly parallel: a step, joined at its midpoint
            mid = (p1 + p2) / 2.0
            out.append(p1 + u * float(np.dot(mid - p1, u))); out.append(p2 + v * float(np.dot(mid - p2, v)))
            continue
        t = ((p2[0] - p1[0]) * v[1] - (p2[1] - p1[1]) * v[0]) / den
        out.append(p1 + u * t)
    r = Polygon(out)
    if not r.is_valid or r.is_empty or r.geom_type != "Polygon":
        return q
    return r if r.intersection(poly).area / r.union(poly).area >= STRAIGHT_MIN_IOU else q


def _along(m, a, b):
    n = max(int(np.hypot(b[0] - a[0], b[1] - a[1])), 2)
    t = np.linspace(0.08, 0.92, n)            # the ends sit on joins, where lines of other kinds cross
    xs, ys = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
    return map_coordinates(m, [ys, xs], order=1, mode="nearest")


def _extend_dangling(lines, edge):
    """Lines whose loose ends are carried on to what they point at.

    A line divides a roof only if both its ends meet the outline or another
    line. The reader's lines stop where its confidence does, often a little
    short of the eave, and the build's roof edge sits outside the photo's
    eave by its overhang: 1 Sunrise Lane (street L5) had a clean line map
    and came out as one face. Rule: an end that touches nothing is carried
    straight on, up to EXTEND_PX or EXTEND_SHARE of the line's length, to
    the first outline edge or line it reaches; if it reaches nothing it is
    left as it is."""
    segs = [LineString([a, b]) for a, b, _ in lines]
    out = []
    for i, (a, b, kind) in enumerate(lines):
        ends = []
        for p, q in ((a, b), (b, a)):                 # p: the end looked at, q: the other end
            pt = Point(p)
            if edge.distance(pt) < 1.0 or any(j != i and s.distance(pt) < 1.0 for j, s in enumerate(segs)):
                ends.append(p); continue
            d = np.array(p) - np.array(q); ln = float(np.hypot(*d))
            reach = max(EXTEND_PX, EXTEND_SHARE * ln)
            far = tuple(np.array(p) + d / max(ln, 1e-6) * reach)
            ray = LineString([p, far])
            hits = [ray.intersection(g) for g in [edge] + [s for j, s in enumerate(segs) if j != i]]
            pts = [h for hit in hits if not hit.is_empty for h in getattr(hit, "geoms", [hit]) if h.geom_type == "Point"]
            if pts:
                h = min(pts, key=lambda h: h.distance(pt))
                # a hair past the meeting point, so the two lines cross and the face closes
                ends.append(tuple(np.array(h.coords[0]) + d / max(ln, 1e-6) * 0.01))
            else:
                ends.append(p)
        out.append((ends[0], ends[1], kind))
    return out


def faces_from_maps(p, names, outline=None):
    """p: C x H x W probabilities; names: the map names in order. `outline`:
    the roof's outer edge in pixels when the caller has one (on fold 0 the
    reader's own roof map matched the drawn outline at F 0.62 within 0.5 m,
    no better than the build's outline); else the roof map's edge.
    Returns (faces, lines, joins, outline): faces as polygons, lines as
    (a, b, kind) with kind ridge, valley or cliff."""
    ch = {n: p[i] for i, n in enumerate(names)}
    given = outline is not None
    if not given:
        outline = roof_outline(ch["roof"])
    if outline is None or outline.is_empty:
        return [], [], [], None
    joins = join_points(ch["join"])
    n_pred = len(joins)
    # The join map misses points (0.49 F on held-out drawn roofs, fold 0):
    # the ends and forks of the line map's own skeleton are joins too, where
    # no predicted join is already beside them.
    from skimage.morphology import skeletonize
    from scipy.ndimage import convolve
    from shapely import contains_xy
    yy, xx = np.mgrid[0:p.shape[1], 0:p.shape[2]]
    inside = contains_xy(outline, xx + 0.5, yy + 0.5)
    sk = skeletonize((np.maximum.reduce([ch[k] for k in ("line",) + KINDS]) >= 0.5) & inside)
    nb = convolve(sk.astype(int), np.ones((3, 3), int), mode="constant") - 1
    ys, xs = np.nonzero(sk & ((nb == 1) | (nb >= 3)))
    for x, y in zip(xs.astype(float), ys.astype(float)):
        if all(np.hypot(x - a, y - b) > SKELETON_JOIN_PX for a, b in joins):
            joins.append((x, y))
    near = outline.buffer(SNAP_PX)
    keep = [near.contains(Point(j)) for j in joins]
    n_pred = sum(keep[:n_pred])
    joins = [j for j, k in zip(joins, keep) if k]
    # outline corners move onto the join beside them; joins on the outline
    # that are not corners are added to it, so lines can end mid-edge
    # The outline drawn through the joins that sit on the roof's edge, in
    # order round it: straight edges between real corners. It is used when
    # it covers the same area as the roof map (OUTLINE_JOIN_IOU); otherwise
    # the map's own edge, with its corners moved onto the joins beside them.
    ext = outline.exterior
    on_edge = sorted((ext.project(Point(j)), j) for j in joins[:n_pred] if ext.distance(Point(j)) <= SNAP_PX)
    drawn = Polygon([j for _, j in on_edge]).buffer(0) if len(on_edge) >= 3 and not given else None
    if given:
        pass
    elif drawn is not None and drawn.geom_type == "Polygon" and not drawn.is_empty and \
            drawn.intersection(outline).area / drawn.union(outline).area >= OUTLINE_JOIN_IOU:
        outline = drawn
    else:
        ring = list(ext.coords)[:-1]
        for i, v in enumerate(ring):
            d = [np.hypot(v[0] - j[0], v[1] - j[1]) for j in joins]
            if d and min(d) <= SNAP_PX:
                ring[i] = joins[int(np.argmin(d))]
        ring = [v for i, v in enumerate(ring) if v != ring[i - 1]]
        if len(ring) < 3:
            return [], [], joins, outline
        outline = Polygon(ring).buffer(0)
        if outline.geom_type == "MultiPolygon":
            outline = max(outline.geoms, key=lambda g: g.area)
    edge = outline.exterior
    nodes = []
    for j in joins:
        d = edge.distance(Point(j))
        nodes.append(tuple(edge.interpolate(edge.project(Point(j))).coords[0]) if 0 < d <= SNAP_PX else j)
    anyline = np.maximum.reduce([ch[k] for k in ("line",) + KINDS])
    inside_edge = outline.buffer(1.5)
    cand = []
    for i in range(len(nodes)):
        for k in range(i + 1, len(nodes)):
            a, b = nodes[i], nodes[k]
            seg = LineString([a, b])
            if seg.length < 3:
                continue
            if any(m not in (i, k) and seg.distance(Point(nodes[m])) < THROUGH_PX for m in range(len(nodes))):
                continue                      # the two shorter pieces are tested on their own
            if not inside_edge.contains(seg):
                continue                      # a roof line does not leave the roof
            mid = seg.interpolate(0.5, normalized=True)
            if edge.distance(mid) < 2.0 and edge.distance(Point(a)) < 1.0 and edge.distance(Point(b)) < 1.0:
                continue                      # along the outline: already a line
            v = _along(anyline, a, b)
            if v.mean() >= LINE_MEAN and (v < LINE_LOW).mean() <= LINE_LOW_SHARE:
                kind = max(KINDS, key=lambda n: _along(ch[n], a, b).mean())
                cand.append((float(v.mean()), seg, a, b, kind))
    # roof lines do not cross except at a join: the best-supported line is
    # kept and any that would cross it is dropped
    lines, kept = [], []
    for _, seg, a, b, kind in sorted(cand, key=lambda c: -c[0]):
        if any(seg.crosses(k) for k in kept):
            continue
        kept.append(seg); lines.append((a, b, kind))
    lines = _extend_dangling(lines, edge)
    net = unary_union([LineString(list(edge.coords))] + [LineString([a, b]) for a, b, _ in lines])
    faces = [f for f in polygonize(net)
             if f.area >= MIN_FACE_PX2 and outline.buffer(0.5).contains(f.representative_point())]
    return faces, lines, nodes, outline


# ---------------------------------------------------------------- regions
CORE_MIN, SEED_MIN_PX2, MERGE_BARRIER, REGION_SIMPLIFY_PX = 0.5, 150, 0.35, 8.0
BASIN_SIGMA_PX, BASIN_STEP = 2.0, 0.05
COMPLETE = int(os.environ.get("SOLAR_LINE_COMPLETE", "0"))
COMPLETE_STRONG, COMPLETE_MIN_PX, COMPLETE_GAP_PX = 0.5, 14, 3
COMPLETE_REACH_PX, COMPLETE_REACH_SHARE, COMPLETE_MEET_PX, COMPLETE_SIDE = 30.0, 1.0, 5, 0.3


def complete_lines(barrier, inside):
    """The line map with every clear straight line carried on to where it
    must end.

    A roof line ends on the roof edge or on another line; it never stops in
    the middle of a face. The reader's lines fade where the photo gives no
    edge (a ridge in shadow, a valley under a tree), and a line that stops
    short divides nothing: the faces either side join, or close along a
    slanted cut. Rule: the straight pieces of the map at COMPLETE_STRONG or
    more, COMPLETE_MIN_PX or longer, are found (a Hough transform of the
    thinned map); an end that is not on the roof edge and has no other line
    beside it (nothing at COMPLETE_SIDE or more off its axis within
    COMPLETE_MEET_PX: a ridge end where two hips leave is complete) is
    carried straight on to the first strong line or the roof edge it
    reaches, within COMPLETE_REACH_PX or COMPLETE_REACH_SHARE of its own
    length. Returns the map with the carried parts drawn in at 1."""
    from skimage.morphology import skeletonize
    from skimage.transform import probabilistic_hough_line
    from skimage.draw import line as draw_line
    from scipy.ndimage import binary_dilation, binary_erosion
    h, w = barrier.shape
    strong = (barrier >= COMPLETE_STRONG) & inside
    segs = probabilistic_hough_line(skeletonize(strong), threshold=8, line_length=COMPLETE_MIN_PX, line_gap=COMPLETE_GAP_PX, rng=0)
    if not segs:
        return barrier
    rim = inside & ~binary_erosion(inside, iterations=3)
    out = barrier.copy()
    wide = binary_dilation(strong, iterations=1)
    for (x0, y0), (x1, y1) in segs:
        ln = float(np.hypot(x1 - x0, y1 - y0))
        for (px, py), (qx, qy) in (((x1, y1), (x0, y0)), ((x0, y0), (x1, y1))):       # p: the end, q: the other end
            ux, uy = (px - qx) / ln, (py - qy) / ln
            reach = int(min(max(COMPLETE_REACH_PX, COMPLETE_REACH_SHARE * ln), 4 * COMPLETE_REACH_PX))
            # the end is complete if another line leaves it: strong map off the line's own axis, near the end
            ys, xs = np.mgrid[max(0, py - COMPLETE_MEET_PX):min(h, py + COMPLETE_MEET_PX + 1), max(0, px - COMPLETE_MEET_PX):min(w, px + COMPLETE_MEET_PX + 1)]
            off = np.abs((xs - px) * -uy + (ys - py) * ux) > 2.5
            if (barrier[ys, xs][off] >= COMPLETE_SIDE).any() or rim[max(0, py - 3):py + 4, max(0, px - 3):px + 4].any():
                continue
            hit = None
            for t in range(3, reach + 1):
                x, y = int(round(px + ux * t)), int(round(py + uy * t))
                if not (0 <= x < w and 0 <= y < h) or not inside[y, x]:
                    hit = (int(round(px + ux * (t - 1))), int(round(py + uy * (t - 1)))); break
                if t > COMPLETE_MEET_PX and wide[y, x]:
                    hit = (x, y); break
            if hit is None:
                continue
            rr, cc = draw_line(py, px, hit[1], hit[0])
            ok = (rr >= 0) & (rr < h) & (cc >= 0) & (cc < w)
            out[rr[ok], cc[ok]] = 1.0
            out[np.clip(rr[ok] + 1, 0, h - 1), cc[ok]] = 1.0; out[rr[ok], np.clip(cc[ok] + 1, 0, w - 1)] = 1.0
    return out


def _basins(barrier, inside):
    """The roof cut wherever the line maps have a crest, then joined back
    across every cut with no line under it.

    Seeds taken from the face-interior map (its connected pieces) give one
    seed to two faces whenever the line between them has a gap, and a seed
    cannot be divided: on the 157 held-out drawn roofs, 202 of 488 faces lost
    into a neighbour had a line the reader saw (mean 0.5 or more) on the
    missing edge. So the cut comes first: every basin of the smoothed line
    map is a region (far more than there are faces), and two regions become
    one where the boundary between them carries no line (mean below
    MERGE_BARRIER), weakest boundary first; a region under SEED_MIN_PX2
    joins its weakest neighbour whatever the boundary."""
    from scipy.ndimage import gaussian_filter
    from skimage.segmentation import watershed
    h, w = barrier.shape
    smooth = np.round(gaussian_filter(barrier, BASIN_SIGMA_PX) / BASIN_STEP) * BASIN_STEP
    lab = watershed(smooth, mask=inside)
    if lab.max() < 1:
        return None
    S, N = {}, {}
    for a, b, v in ((lab[:, :-1], lab[:, 1:], np.maximum(barrier[:, :-1], barrier[:, 1:])),
                    (lab[:-1, :], lab[1:, :], np.maximum(barrier[:-1, :], barrier[1:, :]))):
        m = (a != b) & (a > 0) & (b > 0)
        lo, hi = np.minimum(a[m], b[m]).astype(np.int64), np.maximum(a[m], b[m]).astype(np.int64)
        key = lo * (lab.max() + 1) + hi
        u, inv = np.unique(key, return_inverse=True)
        sm, ct = np.bincount(inv, weights=v[m]), np.bincount(inv)
        for k, x, c in zip(u, sm, ct):
            k = (int(k // (lab.max() + 1)), int(k % (lab.max() + 1)))
            S[k] = S.get(k, 0.0) + float(x); N[k] = N.get(k, 0) + int(c)
    size = dict(enumerate(np.bincount(lab.ravel())))
    nb = {}
    for (i, j) in S:
        nb.setdefault(i, set()).add(j); nb.setdefault(j, set()).add(i)
    parent = {}
    key = lambda i, j: (min(i, j), max(i, j))

    def join(i, j):                                   # j into i; boundary sums add
        for k in nb.pop(j, ()):
            if k == i:
                continue
            S[key(i, k)] = S.get(key(i, k), 0.0) + S.pop(key(j, k)); N[key(i, k)] = N.get(key(i, k), 0) + N.pop(key(j, k))
            nb[k].discard(j); nb[k].add(i); nb.setdefault(i, set()).add(k)
        nb.get(i, set()).discard(j); S.pop(key(i, j), None); N.pop(key(i, j), None)
        size[i] += size.pop(j); parent[j] = i
    while S:
        small = [(S[key(i, k)] / N[key(i, k)], i, k) for i in nb if size[i] < SEED_MIN_PX2 for k in nb[i]]
        if small:
            _, j, i = min(small)
        else:
            v, (i, j) = min((S[k] / N[k], k) for k in S)
            if v >= MERGE_BARRIER:
                break
        join(i, j)
    def root(i):
        while i in parent:
            i = parent[i]
        return i
    lut = np.arange(lab.max() + 1)
    for i in parent:
        lut[i] = root(i)
    return lut[lab]


SNAP = int(os.environ.get("SOLAR_LINE_SNAP", "0"))
SNAP_SIMPLIFY_PX = float(os.environ.get("SOLAR_LINE_SNAP_TOL", "8"))
SNAP_DEG = float(os.environ.get("SOLAR_LINE_SNAP_DEG", "20"))
SNAP_HOLD = float(os.environ.get("SOLAR_LINE_SNAP_HOLD", "0.05"))
SNAP_EXTEND_PX = 5.0


def main_direction(segs):
    """The length-weighted peak of segment directions, mod 90 degrees.
    `segs` is rows of (x1, y1, x2, y2)."""
    segs = np.asarray(segs, float)
    d = segs[:, 2:] - segs[:, :2]; ln = np.hypot(d[:, 0], d[:, 1])
    ang = np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 90.0
    hist = np.zeros(90)
    np.add.at(hist, ang.astype(int) % 90, ln)
    sm = sum(np.roll(hist, k) for k in (-2, -1, 0, 1, 2))
    k = int(np.argmax(sm))
    w = np.array([hist[(k + j) % 90] for j in range(-2, 3)])
    return (k + 0.5 + float((w * np.arange(-2, 3)).sum() / max(w.sum(), 1e-9))) % 90.0


def snap_lines(inner, outline):
    """The lines between faces made straight and turned onto the roof's two
    main directions.

    A roof's ridges, valleys and steps are straight, and most run along the
    roof's main direction or at right angles to it. The boundary between two
    read regions wobbles and drifts off that direction. Rule: each line is
    simplified to SNAP_SIMPLIFY_PX; the main direction is the length-weighted
    peak (mod 90 degrees) of the outline's and the lines' segment directions;
    a segment within SNAP_DEG of it or of its right angle is made exactly
    parallel by moving its two ends (least squares over all segments at once,
    each end held weakly at its read position so shared ends stay shared);
    other segments (hips) stay straight between their moved ends. A line's
    loose end is carried on along its last segment to cross the roof edge."""
    if not inner:
        return inner
    lines = [list(g.simplify(SNAP_SIMPLIFY_PX).coords) for g in inner]
    oc = list(outline.simplify(STRAIGHT_SIMPLIFY_PX).exterior.coords)
    segs = [(a[0], a[1], b[0], b[1]) for c in lines + [oc] for a, b in zip(c[:-1], c[1:])]
    t = np.radians(main_direction(segs))
    R = np.array([[np.cos(t), np.sin(t)], [-np.sin(t), np.cos(t)]])      # into the roof's frame
    idx, pts = {}, []
    def node(q):
        k = (round(q[0], 2), round(q[1], 2))
        if k not in idx:
            idx[k] = len(pts); pts.append(q)
        return idx[k]
    chains = [[node(q) for q in c] for c in lines]
    P = np.asarray(pts, float) @ R.T
    n = len(P)
    A = [np.eye(n) * SNAP_HOLD, np.eye(n) * SNAP_HOLD]; B = [P[:, 0] * SNAP_HOLD, P[:, 1] * SNAP_HOLD]
    for c in chains:
        for i, j in zip(c[:-1], c[1:]):
            d = P[j] - P[i]; ln = float(np.hypot(*d))
            if ln < 1e-6:
                continue
            g = np.degrees(np.arctan2(d[1], d[0])) % 180.0
            for ax, target in ((1, 0.0), (0, 90.0)):          # along the main direction: equal across it, and the reverse
                if abs((g - target + 90.0) % 180.0 - 90.0) <= SNAP_DEG:
                    w = ln / 20.0
                    A[ax][i, i] += w; A[ax][j, j] += w; A[ax][i, j] -= w; A[ax][j, i] -= w
    Q = np.stack([np.linalg.solve(A[0], B[0]), np.linalg.solve(A[1], B[1])], axis=1) @ R
    deg = np.zeros(n, int)
    for c in chains:
        if c[0] != c[-1]:
            deg[c[0]] += 1; deg[c[-1]] += 1
    out = []
    for c in chains:
        q = [Q[i].copy() for i in c]
        if c[0] != c[-1] and len(q) >= 2:
            for end, nxt, i in ((0, 1, c[0]), (-1, -2, c[-1])):
                if deg[i] == 1:                                   # a loose end: on to the roof edge
                    d = q[end] - q[nxt]; ln = float(np.hypot(*d))
                    if ln > 1e-6:
                        q[end] = q[end] + d / ln * SNAP_EXTEND_PX
        g = LineString([tuple(v) for v in q])
        if g.length > 0.5:
            out.append(g)
    return out


def faces_from_regions(p, names, outline):
    """Closed faces: one per face interior the reader sees.

    The joins-and-lines drawing keeps only lines whose two ends are found,
    and a line that reaches nothing divides nothing: on the 157 held-out
    drawn roofs it left most roofs short of faces. Here every face starts
    from its CORE (the reader's map of face interiors), so a face exists
    wherever the reader sees one, and the lines only decide where two faces
    meet:

    1. seeds: the connected pieces of the core map inside the roof edge;
    2. each seed grows over the roof until it meets its neighbours, the
       meeting held on the crest of the line maps (a watershed);
    3. two regions whose shared boundary has no line under it (mean below
       MERGE_BARRIER) are one face;
    4. the shared boundaries are straightened together (a coverage
       simplification, so neighbours keep one common edge) and tied to the
       roof edge, and the faces are the areas they enclose.

    `outline` is the roof edge in pixels. Returns polygons in pixels."""
    import shapely
    from rasterio import features
    from skimage.segmentation import watershed
    ch = {n: p[i] for i, n in enumerate(names)}
    h, w = p.shape[1:]
    inside = features.rasterize([outline], out_shape=(h, w)).astype(bool)
    barrier = np.maximum.reduce([ch[k] for k in ("line",) + KINDS])
    if COMPLETE:
        barrier = complete_lines(barrier, inside)
    lab = _basins(barrier, inside)
    if lab is None:
        return [outline]
    polys = []
    for geom, v in features.shapes(lab.astype(np.int32), mask=lab > 0):
        if v:
            polys.append(shapely.geometry.shape(geom))
    by = {}
    for q, v in zip(polys, [v for _, v in features.shapes(lab.astype(np.int32), mask=lab > 0) if v]):
        by.setdefault(int(v), []).append(q)
    regs = [unary_union(v) for v in by.values()]
    regs = [max(r.geoms, key=lambda g: g.area) if r.geom_type == "MultiPolygon" else r for r in regs]
    if len(regs) < 2:
        return [outline]
    regs = list(shapely.coverage_simplify(regs, REGION_SIMPLIFY_PX))
    # 4. the lines between regions, tied to the roof edge
    edge = outline.exterior
    cover = unary_union(regs).exterior if unary_union(regs).geom_type == "Polygon" else None
    inner = []
    for i in range(len(regs)):
        for j in range(i + 1, len(regs)):
            s = regs[i].boundary.intersection(regs[j].boundary)
            for g in getattr(s, "geoms", [s]):
                if g.geom_type in ("LineString", "MultiLineString") and g.length > 0.5:
                    inner.extend(getattr(g, "geoms", [g]))
    merged = shapely.ops.linemerge(inner) if inner else None
    inner = list(getattr(merged, "geoms", [merged])) if merged is not None and not merged.is_empty else []
    if SNAP:
        inner = snap_lines(inner, outline)
    ties = []
    for g in inner:
        for end in (g.coords[0], g.coords[-1]):
            pt = Point(end)
            if sum(1 for o in inner if o is not g and o.distance(pt) < 0.5) == 0:      # a loose end: it met the roof edge
                near = edge.interpolate(edge.project(pt))
                # carried a hair past the edge so the two cross
                d = np.array(near.coords[0]) - np.array(end); ln = float(np.hypot(*d))
                far = np.array(near.coords[0]) + (d / ln * 0.5 if ln > 1e-6 else 0)
                ties.append(LineString([end, tuple(far)]))
    net = unary_union([LineString(list(edge.coords))] + inner + ties)
    faces = [f for f in polygonize(net) if f.area >= MIN_FACE_PX2 and outline.buffer(0.5).contains(f.representative_point())]
    return faces or [outline]
