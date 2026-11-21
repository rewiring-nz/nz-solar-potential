"""Roof faces from the reader's maps, the way a roof is drawn by hand.

A person marks a roof as points joined by straight lines: the outline's
corners, the ends of each ridge, valley and drop edge. The reader
(tools/train_roof_lines.py) predicts those points (the join map), the lines
by kind and the roof itself. This turns them back into the drawing:

1. joins: the peaks of the join map;
2. the outline: the edge of the roof map, simplified, its corners moved onto
   the joins beside them;
3. lines: a straight segment between two joins is kept when the line maps
   hold along it (mean LINE_MEAN or more, and no stretch of it empty), and it
   does not run through a third join;
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
KINDS = ("ridge", "valley", "cliff")


def join_points(jmap):
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
    return p.simplify(OUTLINE_SIMPLIFY_PX)


def _along(m, a, b):
    n = max(int(np.hypot(b[0] - a[0], b[1] - a[1])), 2)
    t = np.linspace(0.08, 0.92, n)            # the ends sit on joins, where lines of other kinds cross
    xs, ys = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
    return map_coordinates(m, [ys, xs], order=1, mode="nearest")


def faces_from_maps(p, names):
    """p: C x H x W probabilities; names: the map names in order.
    Returns (faces, lines, joins, outline): faces as polygons, lines as
    (a, b, kind) with kind ridge, valley or cliff."""
    ch = {n: p[i] for i, n in enumerate(names)}
    outline = roof_outline(ch["roof"])
    if outline is None or outline.is_empty:
        return [], [], [], None
    joins = join_points(ch["join"])
    near = outline.buffer(SNAP_PX)
    joins = [j for j in joins if near.contains(Point(j))]
    # outline corners move onto the join beside them; joins on the outline
    # that are not corners are added to it, so lines can end mid-edge
    ring = list(outline.exterior.coords)[:-1]
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
    lines = []
    for i in range(len(nodes)):
        for k in range(i + 1, len(nodes)):
            a, b = nodes[i], nodes[k]
            seg = LineString([a, b])
            if seg.length < 3:
                continue
            if any(m not in (i, k) and seg.distance(Point(nodes[m])) < THROUGH_PX for m in range(len(nodes))):
                continue                      # the two shorter pieces are tested on their own
            mid = seg.interpolate(0.5, normalized=True)
            if edge.distance(mid) < 2.0 and edge.distance(Point(a)) < 1.0 and edge.distance(Point(b)) < 1.0:
                continue                      # along the outline: already a line
            v = _along(anyline, a, b)
            if v.mean() >= LINE_MEAN and (v < LINE_LOW).mean() <= LINE_LOW_SHARE:
                kind = max(KINDS, key=lambda n: _along(ch[n], a, b).mean())
                lines.append((a, b, kind))
    net = unary_union([LineString(list(edge.coords))] + [LineString([a, b]) for a, b, _ in lines])
    faces = [f for f in polygonize(net)
             if f.area >= MIN_FACE_PX2 and outline.buffer(0.5).contains(f.representative_point())]
    return faces, lines, nodes, outline
