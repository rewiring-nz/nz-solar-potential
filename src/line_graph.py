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
STRAIGHT_SIMPLIFY_PX, STRAIGHT_SNAP_DEG, STRAIGHT_MERGE_PX, STRAIGHT_MIN_IOU = 4.0, 15.0, 3.0, 0.9
EXTEND_PX, EXTEND_SHARE = 24.0, 0.35
KINDS = ("ridge", "valley", "cliff")
STRAIGHTEN = True


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


def roof_outline(roof):
    """The largest roof area as a simplified polygon."""
    from skimage import measure
    lab, n = cc_label(roof >= 0.5)
    if not n:
        return None
    big = lab == (1 + np.argmax(np.bincount(lab.ravel())[1:]))
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
