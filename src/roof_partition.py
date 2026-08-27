"""
Build a roof as a PLANAR PARTITION of the surveyed footprint, cut only by
straight lines, refined until the planes explain the LiDAR.

The design constraint is Josh's, stated directly:

    "Most roof shapes are relatively simple. They aren't fuzzy, they are
    straight lines, generally a few different angles."

    "Those unique shapes are still generally made up of the same principles as
    household roofs, just more of them on the same building footprint. For
    example a hotel of apartments with many household like roofs in the same
    building footprint. Or a big warehouse roof with multiple angle roof
    sections... clear flat angled sections at differing pitches, with differing
    cut offs, but in general the roof shape principles remain the same."

    "The roof will almost never be some type of organic shape."

Two consequences, and they are the whole module.

STRAIGHT BY CONSTRUCTION. Every facet boundary is either a footprint edge
(surveyed by LINZ, already straight) or a cut line. No boundary is ever traced
from a raster. That alone removes the defect Josh has now reported four times:
the shipped facets on 29 Edinburgh Dr carry 1335-1835 vertices each on a roof
that is four rectangles, and on 1/5 Sydney St 874-1035 each. A partition cannot
produce those shapes -- the vertex count is bounded by the number of cuts.

COMPLEXITY EARNED, NOT ASSUMED. Fitting one template per building would do
exactly what Josh warned against -- "you should not fit a simple roof when the
underlying roof is actually more complex". So nothing is assumed about how many
faces a roof has. A region is kept whole when one plane already explains its
points; it is cut when a plane does not, and each half is then asked the same
question. A simple gable stops after one cut. A hotel keeps going. The stopping
rule is fit quality, so complexity is spent only where the roof actually has it.

Cut directions come from the footprint itself. Roofs are built on walls, so
ridges, hips and valleys run parallel or perpendicular to the walls below far
more often than not, and the surveyed outline is a better source for those
angles than anything recoverable from a 5.7 pts/m2 cloud.
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import shapely.vectorized
from shapely.geometry import LineString, Polygon
from shapely.ops import split as shapely_split, unary_union

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

# A facet is accepted when this share of its points lie within the band below.
# 0.15 m is a little over twice the DSM's own noise, so a genuine plane clears
# it easily and a plane spanning two faces cannot.
ACCEPT_INLIER = 0.85
INLIER_BAND_M = 0.15

MIN_FACET_M2 = 6.0          # below ~3 panels a face is not worth racking
MIN_PIECE_M2 = 4.0          # a cut may not leave a sliver smaller than this
MAX_DEPTH = 7               # 2^7 pieces; a hotel of many small roofs needs the depth
MIN_POINTS = 25

ANGLE_TOL_DEG = 4.0         # footprint edge directions this close are one direction
OFFSET_STEP_M = 0.25        # how finely each candidate direction is swept. A ridge cut
# landing half a metre off leaves a strip of the WRONG plane on both sides of it,
# which drags the fit down on exactly the roofs whose structure was found correctly.
# A cut only has to help at all, not pay for itself immediately. Requiring a
# real gain from each single cut was tried and it makes the recursion blind:
# on 1/5 Sydney St, a twelve-unit roof, the whole footprint fits one plane at
# 13% and the BEST available single cut reaches only 16%, because both halves
# are still multi-plane messes. That roof needs about ten cuts before the fit
# improves sharply, and a one-step-lookahead test can never see past the first.
# So the stopping rule is "this region is now explained" -- or too small, or too
# deep -- and over-splitting is handled afterwards by _merge_bridgeable, which
# undoes any cut a panel could lie across anyway.
MIN_SPLIT_GAIN = 0.005


def _fit_plane(pts):
    """Least-squares plane through points, as (a, b, c) with z = ax + by + c."""
    A = np.column_stack([pts[:, 0], pts[:, 1], np.ones(len(pts))])
    coef, *_ = np.linalg.lstsq(A, pts[:, 2], rcond=None)
    return float(coef[0]), float(coef[1]), float(coef[2])


def _fit_plane_robust(pts, iterations=6):
    """Trimmed fit, so a chimney or a parapet does not tilt the whole face."""
    keep = np.ones(len(pts), bool)
    plane = _fit_plane(pts)
    for _ in range(iterations):
        r = pts[:, 2] - (plane[0] * pts[:, 0] + plane[1] * pts[:, 1] + plane[2])
        nxt = np.abs(r - np.median(r[keep])) < INLIER_BAND_M * 2
        if nxt.sum() < MIN_POINTS or (nxt == keep).all():
            break
        keep = nxt
        plane = _fit_plane(pts[keep])
    return plane


def _inlier_fraction(pts, plane):
    r = pts[:, 2] - (plane[0] * pts[:, 0] + plane[1] * pts[:, 1] + plane[2])
    return float((np.abs(r - np.median(r)) < INLIER_BAND_M).mean())


def _points_in(poly, pts):
    if len(pts) == 0:
        return pts
    return pts[shapely.vectorized.contains(poly, pts[:, 0], pts[:, 1])]


def _edge_directions(poly):
    """Distinct directions of the footprint's own edges, longest first.

    Roofs are built on walls: ridges and hips run parallel or perpendicular to
    the outline far more often than not, and the LINZ outline is surveyed, so
    these angles are exact in a way nothing recovered from the point cloud is."""
    coords = np.asarray(poly.exterior.coords)
    segs = coords[1:] - coords[:-1]
    lens = np.hypot(segs[:, 0], segs[:, 1])
    angs = np.degrees(np.arctan2(segs[:, 1], segs[:, 0])) % 180.0
    order = np.argsort(-lens)
    out = []
    for i in order:
        if lens[i] < 1.0:
            continue
        a = angs[i]
        if all(min(abs(a - b), 180 - abs(a - b)) > ANGLE_TOL_DEG for b in out):
            out.append(float(a))
    # Every wall direction, its perpendicular, and its two diagonals. The
    # diagonals are not decoration: a hip BISECTS the corner between two walls,
    # so a hip line runs at 45 degrees to both, and without them the partition
    # cannot cut a hip roof at all -- it is forced to approximate one with
    # rectangular cuts, which is why 5 Isle St stalled at two faces and 45%
    # on-plane. Josh named this exact geometry: "a 45 degree mitre type roof
    # joint... they are very common on roof geometry".
    perp = [(a + 90.0) % 180.0 for a in out]
    diag = [(a + 45.0) % 180.0 for a in out] + [(a + 135.0) % 180.0 for a in out]
    seen, uniq = [], []
    for a in out + perp + diag:
        if all(min(abs(a - b), 180 - abs(a - b)) > ANGLE_TOL_DEG for b in seen):
            seen.append(a)
            uniq.append(a)
    return uniq


def _cut(poly, angle_deg, offset):
    """Split a polygon with an infinite line at this angle and offset."""
    theta = np.radians(angle_deg)
    d = np.array([np.cos(theta), np.sin(theta)])
    n = np.array([-d[1], d[0]])
    c = np.array(poly.centroid.coords[0])
    span = max(poly.bounds[2] - poly.bounds[0], poly.bounds[3] - poly.bounds[1]) * 2 + 10
    mid = c + n * offset
    line = LineString([mid - d * span, mid + d * span])
    try:
        parts = list(shapely_split(poly, line).geoms)
    except Exception:
        return []
    return [p for p in parts if isinstance(p, Polygon) and p.area >= MIN_PIECE_M2]


def _score(poly, pts):
    """Area-weighted inlier fraction if this region were one facet."""
    sub = _points_in(poly, pts)
    if len(sub) < MIN_POINTS:
        return None, 0.0
    plane = _fit_plane_robust(sub)
    return plane, _inlier_fraction(sub, plane)


def _best_cut(poly, pts, base_score):
    """The straight line that best explains this region as two planes."""
    best = None
    for angle in _edge_directions(poly):
        theta = np.radians(angle)
        n = np.array([-np.sin(theta), np.cos(theta)])
        coords = np.asarray(poly.exterior.coords)
        c = np.array(poly.centroid.coords[0])
        proj = (coords - c) @ n
        lo, hi = proj.min(), proj.max()
        if hi - lo < 2 * OFFSET_STEP_M:
            continue
        for off in np.arange(lo + OFFSET_STEP_M, hi - OFFSET_STEP_M + 1e-9, OFFSET_STEP_M):
            parts = _cut(poly, angle, float(off))
            if len(parts) < 2:
                continue
            tot = num = 0.0
            ok = True
            for part in parts:
                pl, sc = _score(part, pts)
                if pl is None:
                    ok = False
                    break
                num += sc * part.area
                tot += part.area
            if not ok or tot <= 0:
                continue
            combined = num / tot
            if combined > base_score + MIN_SPLIT_GAIN and (best is None or combined > best[0]):
                best = (combined, parts)
    return best


def _cut_on_line(poly, A, B, C, cx, cy):
    """Split a polygon along A(x-cx) + B(y-cy) + C = 0.

    Everything is in coordinates local to (cx, cy), and both reasons are
    NZTM's fault. Anchoring the line at its closest point to the ORIGIN puts
    that anchor about 5,000 km away, so a line segment a few tens of metres
    long never reaches the building and the split silently does nothing. And
    a plane's intercept is its height at x=0, y=0, which for NZTM is an
    astronomical number, so differencing two of them loses all the precision
    that matters. Both vanish once the origin is the polygon itself."""
    n = np.hypot(A, B)
    if n < 1e-9:
        return []
    d = np.array([-B, A]) / n              # direction along the line
    origin = np.array([cx, cy])
    pt0 = origin - np.array([A, B]) * (C / (n ** 2))   # nearest point ON the line
    span = max(poly.bounds[2] - poly.bounds[0], poly.bounds[3] - poly.bounds[1]) * 2 + 10
    line = LineString([pt0 - d * span, pt0 + d * span])
    try:
        parts = list(shapely_split(poly, line).geoms)
    except Exception:
        return []
    return [q for q in parts if isinstance(q, Polygon) and q.area >= MIN_PIECE_M2]


def _refine_cut(poly, pts, parts):
    """Move a swept cut onto the two planes' own intersection line.

    The sweep can only place a cut to within OFFSET_STEP_M, and a ridge that
    lands even 25 cm off leaves a strip of the WRONG plane on both sides of it,
    which is what kept fit lagging while structure was already correct. But two
    planes that meet do so along an exact line -- that line IS the ridge or hip,
    and it is available in closed form. Solve for it and re-cut there.

    Only for planes that actually intersect: near-parallel faces at different
    heights are a step, not a fold, and their intersection is meaningless or
    infinitely far away, so the swept cut stands."""
    if len(parts) != 2:
        return parts
    pa, _ = _score(parts[0], pts)
    pb, _ = _score(parts[1], pts)
    if pa is None or pb is None:
        return parts
    cx, cy = poly.centroid.x, poly.centroid.y
    A, B = pa[0] - pb[0], pa[1] - pb[1]
    # height gap between the two planes AT the centroid, not at x=0,y=0
    C = ((pa[0] * cx + pa[1] * cy + pa[2]) - (pb[0] * cx + pb[1] * cy + pb[2]))
    if np.hypot(A, B) < 1e-3:
        return parts        # parallel: a step in height, keep the swept cut
    refined = _cut_on_line(poly, A, B, C, cx, cy)
    if len(refined) < 2:
        return parts

    def weighted(ps):
        tot = num = 0.0
        for q in ps:
            _, sc = _score(q, pts)
            num += sc * q.area
            tot += q.area
        return num / tot if tot else 0.0

    return refined if weighted(refined) > weighted(parts) else parts


def _partition(poly, pts, depth=0):
    plane, score = _score(poly, pts)
    if plane is None:
        return []
    if score >= ACCEPT_INLIER or depth >= MAX_DEPTH or poly.area < 2 * MIN_FACET_M2:
        return [(poly, plane)]
    best = _best_cut(poly, pts, score)
    if best is None:
        return [(poly, plane)]      # no straight cut explains it better -- keep it whole
    parts = _refine_cut(poly, pts, best[1])
    out = []
    for part in parts:
        out.extend(_partition(part, _points_in(part, pts), depth + 1))
    return out or [(poly, plane)]


def _slope_aspect(plane):
    a, b, _ = plane
    slope = float(np.degrees(np.arctan(np.hypot(a, b))))
    aspect = float((np.degrees(np.arctan2(-a, -b)) + 360.0) % 360.0)
    return slope, aspect


def _plane_angle(p, q):
    na = np.array([-p[0], -p[1], 1.0]); na /= np.linalg.norm(na)
    nb = np.array([-q[0], -q[1], 1.0]); nb /= np.linalg.norm(nb)
    return float(np.degrees(np.arccos(np.clip(abs(na @ nb), -1.0, 1.0))))


BRIDGE_MAX_STEP_M = 0.10


def _step_at_join(pa, pb, poly_a, poly_b):
    """Height gap between two planes WHERE THEY ADJOIN.

    Without this the merge is wrong in a way an angle test cannot see: two
    parallel faces at different levels have identical normals, so they read as
    0 degrees apart and get merged straight across a step. That is exactly what
    happened here -- the recursion built 5 correct faces on 5 Isle St at 87-99%
    on-plane and the merge collapsed them to 2 at 45%. Third time this same bug
    has appeared in this codebase; comparing planes at their shared boundary
    rather than comparing normals is the only thing that catches it."""
    shared = poly_a.buffer(0.3).intersection(poly_b.buffer(0.3))
    if shared.is_empty:
        return float("inf")
    c = shared.centroid
    za = pa[0] * c.x + pa[1] * c.y + pa[2]
    zb = pb[0] * c.x + pb[1] * c.y + pb[2]
    return abs(float(za - zb))


def _merge_bridgeable(faces, pts):
    """Undo cuts a panel could lie across.

    A cut that buys fit but not enough to stop a panel spanning it costs the
    ridge setback on both sides for nothing. 5 degrees over a 1.7 m panel is a
    15 cm rise, past what a rigid frame bridges."""
    faces = list(faces)
    changed = True
    while changed and len(faces) > 1:
        changed = False
        for i in range(len(faces)):
            for j in range(i + 1, len(faces)):
                (pi, li), (pj, lj) = faces[i], faces[j]
                if not pi.buffer(0.25).intersects(pj):
                    continue
                if _plane_angle(li, lj) > 5.0:
                    continue
                if _step_at_join(li, lj, pi, pj) > BRIDGE_MAX_STEP_M:
                    continue
                u = unary_union([pi, pj])
                if u.geom_type != "Polygon":
                    continue
                gain = (u.buffer(-config.RIDGE_SETBACK_M).area
                        - pi.buffer(-config.RIDGE_SETBACK_M).area
                        - pj.buffer(-config.RIDGE_SETBACK_M).area)
                if gain <= 0.5:
                    continue
                sub = _points_in(u, pts)
                pl = _fit_plane_robust(sub) if len(sub) >= MIN_POINTS else li
                faces = [f for k, f in enumerate(faces) if k not in (i, j)] + [(u, pl)]
                changed = True
                break
            if changed:
                break
    return faces


def partition_roof(building_id, footprint, pts):
    """Surveyed footprint + point cloud -> straight-edged, plane-backed facets."""
    inside = _points_in(footprint, pts)
    if len(inside) < MIN_POINTS:
        return []
    faces = _partition(footprint, inside)
    faces = _merge_bridgeable(faces, inside)

    out = []
    for poly, plane in faces:
        if poly.area < MIN_FACET_M2:
            continue
        sub = _points_in(poly, inside)
        slope, aspect = _slope_aspect(plane)
        if slope > config.MAX_ROOF_SLOPE_DEG:
            continue
        out.append({
            "building_id": building_id,
            "geometry": Polygon(poly.exterior, [r for r in poly.interiors]),
            "plane_a": plane[0], "plane_b": plane[1], "plane_c": plane[2],
            "slope_deg": slope, "aspect_deg": aspect,
            "area_m2": float(poly.area), "point_count": int(len(sub)),
        })
    return out
