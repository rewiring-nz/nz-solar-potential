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
# Depth alone is the WRONG stopping rule and it was quietly ruining the hardest
# roofs. Cuts do not halve a region evenly -- a ridge shaves a strip off one
# side -- so a large remainder can descend seven levels while barely shrinking,
# exhaust its budget and be returned whole. On 1/5 Sydney St that surrendered a
# 138 m2 face at 15% on-plane, 40% of the footprint, even though _best_cut had
# a 25% cut available for it. Depth is now generous and the real brakes are
# size and a per-building face cap, both of which track what is actually being
# spent rather than how many times the function has recursed.
MAX_DEPTH = 14
MAX_FACES = 60              # a hotel needs many; nothing real needs more
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
# How much usable area a cut may cost, PER POINT of fit it buys. An absolute cap
# was tried first and is wrong: one legitimate ridge cut across a 15 m roof
# already costs about 7 m2 of setback, so any flat cap tight enough to stop
# over-fragmentation also stops the first honest cut, and every roof collapsed
# to a single facet at 38% on-plane.
#
# Scaling it by the fit gain is the honest trade. A cut that takes a roof from
# 30% to 90% on-plane has fixed the building and has earned a lot of setback; a
# cut that buys two points has not earned any.
# Swept, not guessed. 0.5 was shipped first and was badly wrong: it cost 20
# points of fit on complex roofs (64% on-plane against 86% with the rule off)
# to buy a facet count that was not needed -- 47 Stanley St fell from 22 facets
# at 96% to 4 at 53%, which is under-modelling a roof, not making it blocky.
#
#   cost/fit    hard roofs              random roofs
#   0.5         64% on-plane,  4 facets   83%,  4
#   2.0         84%,          11          93%,  6
#   unlimited   86%,          12          93%,  6
#
# 2.0 takes essentially all the available fit while houses still come back at
# about six faces, which is the blockiness Josh asked for. Past 2.0 nothing
# changes, so the rule is only ever binding on the roofs where it should be.
SETBACK_COST_PER_FIT = 2.0

# A "fold" test was tried here -- treat a face carrying points far off its own
# plane as containing a physical drop, and cut it regardless of setback cost.
# It does not work, and the reason is worth keeping: the signal does not
# separate the cases. 5 Isle St, which Josh confirms is correctly three faces,
# has a face with 10.6% of its points more than 0.5 m off plane; 7 Anderson
# Heights, which he says is wrong, has 10.0% and 10.2%. Any threshold that cuts
# one cuts the other. Nor does WHERE those points sit help -- on both roofs they
# cluster within half a metre of a facet edge, which is just bleed from the
# neighbouring plane.


def _fit_plane(pts):
    """Least-squares plane through points, as (a, b, c) with z = ax + by + c.

    Solved about the points' own centroid, then shifted back. Solving on raw
    NZTM coordinates -- x near 1.2 million, y near 5 million, against a column
    of ones -- is a condition number around 1e6, and it does not merely lose a
    little precision: it silently returns planes that do not fit their own
    points. Two faces of 5 Isle St measured 0.1 degrees apart with a 0.00 m step
    at their join, and the plane fitted to their union scored 16% on-plane
    against 99% for each of them separately, which blocked a merge that should
    obviously have happened and left that roof at 5 faces where Josh counted 3."""
    x0, y0 = pts[:, 0].mean(), pts[:, 1].mean()
    A = np.column_stack([pts[:, 0] - x0, pts[:, 1] - y0, np.ones(len(pts))])
    coef, *_ = np.linalg.lstsq(A, pts[:, 2], rcond=None)
    a, b = float(coef[0]), float(coef[1])
    return a, b, float(coef[2]) - a * x0 - b * y0


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


def _usable(poly, setback=None):
    """Area left after the ridge setback -- what panel packing actually gets."""
    setback = config.RIDGE_SETBACK_M if setback is None else setback
    if poly.is_empty:
        return 0.0
    try:
        return float(poly.buffer(-setback).area)
    except Exception:
        return 0.0


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


# Big planes first; their intersections ARE the roof lines.
#
# Josh, after seeing an edge detector draw a maze over a simple roof: "You need
# a way to clearly detect and define big flat planes that make up roof shapes,
# generally all these planes are large in size, and connect smoothly at angled
# edges most of the time."
#
# That inverts the problem. Hunting edges in imagery and inferring planes from
# them is backwards and fails in both directions -- texture produces lines that
# are not roof features, and a real ridge that is a soft intensity step
# produces no line at all. But two planes that meet do so along their exact
# analytic intersection: no detection, no threshold, no false positives. Find
# the planes and the ridges, hips and valleys come out for free, straight and
# in the right place.
#
# The size prior is the whole point and is what previous attempts at this got
# wrong -- roof_reconstruct fitted planes to whatever the points supported and
# shattered roofs into strips. A plane has to be BIG to exist at all here.
PLANE_MIN_AREA_M2 = 12.0        # a face smaller than ~6 panels is not a roof plane
PLANE_MIN_FOOTPRINT_SHARE = 0.04
PLANE_TOL_M = 0.20              # a point this close to a plane is on it
PLANE_MAX = 10                  # real roofs are simple; past this it is not planes any more
PLANE_RANSAC_ITERS = 250
PLANE_SAMPLE_RADIUS_M = 6.0     # 3-point samples drawn locally, or a plane gets fitted
# through three points on three different faces and describes nothing


def _detect_large_planes(pts, footprint, rng, max_planes=None):
    """Greedy RANSAC for the few LARGE planes a roof is actually made of.

    Take the best-supported plane, remove its points, repeat -- stopping as soon
    as the best remaining plane is too small to be a roof face rather than
    grinding on until every leftover point has one."""
    if len(pts) < MIN_POINTS:
        return []
    min_area = max(PLANE_MIN_AREA_M2, PLANE_MIN_FOOTPRINT_SHARE * footprint.area)
    pt_area = footprint.area / max(len(pts), 1)      # plan area each point stands for
    min_pts = max(MIN_POINTS, int(min_area / max(pt_area, 1e-9)))

    remaining = pts
    planes = []
    tree = None
    cap = PLANE_MAX if max_planes is None else max_planes
    while len(planes) < cap and len(remaining) >= min_pts:
        from scipy.spatial import cKDTree
        tree = cKDTree(remaining[:, :2])
        best, best_n = None, 0
        for _ in range(PLANE_RANSAC_ITERS):
            i = rng.integers(len(remaining))
            near = tree.query_ball_point(remaining[i, :2], PLANE_SAMPLE_RADIUS_M)
            if len(near) < 3:
                continue
            pick = rng.choice(near, size=3, replace=False)
            trio = remaining[pick]
            v1, v2 = trio[1] - trio[0], trio[2] - trio[0]
            nrm = np.cross(v1, v2)
            if abs(nrm[2]) < 1e-6:
                continue
            a, b = -nrm[0] / nrm[2], -nrm[1] / nrm[2]
            c = trio[0, 2] - a * trio[0, 0] - b * trio[0, 1]
            n = int((np.abs(remaining[:, 2] - (a * remaining[:, 0] + b * remaining[:, 1] + c))
                     < PLANE_TOL_M).sum())
            if n > best_n:
                best, best_n = (a, b, c), n
        if best is None or best_n < min_pts:
            break
        inl = np.abs(remaining[:, 2] - (best[0] * remaining[:, 0]
                                        + best[1] * remaining[:, 1] + best[2])) < PLANE_TOL_M
        planes.append(_fit_plane_robust(remaining[inl]))
        remaining = remaining[~inl]
    return planes


def _plane_intersection_cuts(planes, poly):
    """(angle, offset) for every pair of planes that meet, in _cut's convention.

    This is the replacement for detecting roof lines. Two planes intersect along
    one exact line; near-parallel pairs are skipped because their intersection is
    meaningless or far away -- those are steps in height, not folds."""
    cx, cy = poly.centroid.x, poly.centroid.y
    out = []
    for i in range(len(planes)):
        for j in range(i + 1, len(planes)):
            pa, pb = planes[i], planes[j]
            A, B = pa[0] - pb[0], pa[1] - pb[1]
            if np.hypot(A, B) < 1e-3:
                continue
            C = ((pa[0] * cx + pa[1] * cy + pa[2]) - (pb[0] * cx + pb[1] * cy + pb[2]))
            n = np.hypot(A, B)
            ang = float((np.degrees(np.arctan2(A, -B))) % 180.0)   # line dir _|_ to (A,B)
            off = float(-C / n)
            out.append((ang, off))
    return out


def _partition(poly, pts, depth=0, budget=None):
    if budget is None:
        budget = [MAX_FACES]
    plane, score = _score(poly, pts)
    if plane is None:
        return []

    # Acceptance needs BOTH: enough points near the plane, and no fold.
    #
    # The inlier fraction alone is blind to how far the outliers are, and that
    # is not a small blind spot. 7 Anderson Heights had a 73 m2 face scoring
    # exactly 85% -- the acceptance bar -- while 10% of its points sat more than
    # half a metre off it and nearly 5% over a metre. That is a roof dropping
    # through the middle of a face, and it was accepted as one plane, so the
    # recursion never even asked whether a cut would help. Josh: "two panel
    # planes placed and both overlapping a roof ridge where it drops in the
    # middle."
    #
    # A tail that far out is structure, not noise. Roughness raises the count of
    # points just outside the band; a fold puts them metres away.
    if (score >= ACCEPT_INLIER or depth >= MAX_DEPTH
            or poly.area < 2 * MIN_FACET_M2 or budget[0] <= 1):
        return [(poly, plane)]
    best = _best_cut(poly, pts, score)
    if best is None:
        return [(poly, plane)]      # no straight cut explains it better -- keep it whole
    parts = _refine_cut(poly, pts, best[1])

    # A cut has to earn the panel area it costs. Every facet is eroded by the
    # ridge setback before packing, so cutting one face into two loses a strip
    # down the middle permanently -- and a fit improvement that cannot be used,
    # because the pieces are too narrow to rack panels on, is worth nothing.
    #
    # Without this the recursion buys fit indefinitely: measured on random pilot
    # roofs it produced 20 facets on a 255 m2 house and 25 on 333 m2, about
    # 13 m2 each. Real houses have two to eight planes. Josh, twice: "they need
    # to be large and blocky most of the time like real rooftops", and "it's
    # highly unlikely there would ever be very many vertices on a house".
    gain = max(0.0, best[0] - score)
    cost = _usable(poly) - sum(_usable(q) for q in parts)
    if cost > SETBACK_COST_PER_FIT * gain * max(_usable(poly), 1e-9):
        return [(poly, plane)]
    out = []
    budget[0] -= 1          # this cut spends one face from the building's budget
    for part in parts:
        out.extend(_partition(part, _points_in(part, pts), depth + 1, budget))
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
                if len(sub) < MIN_POINTS:
                    continue
                pl = _fit_plane_robust(sub)
                # The merged face has to still be a plane. Angle, step and area
                # gain all pass on faces that individually fit well but whose
                # UNION does not -- a gentle curve is exactly that, every
                # adjacent pair within the bridge angle while the whole sweep is
                # not one plane. Unchecked, this built a 138 m2 face on 1/5
                # Sydney St sitting at 15% on-plane out of pieces that were
                # each fine, and nothing downstream could recover from it
                # because the recursion had already finished.
                merged_fit = _inlier_fraction(sub, pl)
                worst_before = min(_inlier_fraction(_points_in(pi, pts), li),
                                   _inlier_fraction(_points_in(pj, pts), lj))
                if merged_fit < min(ACCEPT_INLIER, worst_before - 0.02):
                    continue
                faces = [f for k, f in enumerate(faces) if k not in (i, j)] + [(u, pl)]
                changed = True
                break
            if changed:
                break
    return faces


# How many planes a roof actually has, from Josh: "there are generally not going
# to be many planes on a roof, most probably only have between 1 and 10 or so.
# Unless a big hotel or business roof, but then still... Likely between 1 and 30
# or so." That is the prior this whole module was missing, and it is why a
# 93%-on-plane score could sit on a roof he called clearly wrong: fit says
# nothing about whether a shape looks like a roof.
PLANES_TYPICAL_MAX = 10          # a house
PLANES_LARGE_MAX = 30            # a hotel or a large commercial roof
PLANES_LARGE_ROOF_M2 = 600.0     # above this footprint, allow the higher count
MAX_INTERSECTION_LINES = 24      # cutting by every pair explodes; keep the best


def partition_by_planes(building_id, footprint, pts, seed=0):
    """Big planes from the LiDAR, trimmed by each other and by the building edge.

    Josh's description exactly: "make big planes based on detectable roof angles
    with the lidar, and then trim those planes by either the edge of the building
    or another plane."

    Nothing is detected in imagery and no boundary is traced. A plane's extent is
    decided by where it stops being the best explanation of the points -- which
    is either where another plane takes over, along the exact line the two
    intersect, or the surveyed footprint edge. Both are straight by construction,
    so the result cannot be fuzzy and the faces meet cleanly at real angles."""
    rng = np.random.default_rng(seed)
    inside = _points_in(footprint, pts)
    if len(inside) < MIN_POINTS:
        return []
    cap = PLANES_LARGE_MAX if footprint.area > PLANES_LARGE_ROOF_M2 else PLANES_TYPICAL_MAX
    planes = _detect_large_planes(inside, footprint, rng, max_planes=cap)
    if not planes:
        return []

    # Cut by where the planes meet. Ordered by how much roof each pair actually
    # separates, so if the cap bites it is the least important joins that go.
    cuts = _plane_intersection_cuts(planes, footprint)[:MAX_INTERSECTION_LINES]
    cells = [footprint]
    for ang, off in cuts:
        nxt = []
        for c in cells:
            parts = _cut(c, ang, off)
            nxt.extend(parts if len(parts) >= 2 else [c])
        cells = nxt
        if len(cells) > 200:      # runaway guard on a pathological roof
            break

    # Each cell goes to the plane its own points support best; cells too sparse
    # to vote take the plane of the nearest cell that could.
    labelled = []
    for cell in cells:
        if cell.area < MIN_PIECE_M2:
            continue
        sub = _points_in(cell, inside)
        if len(sub) < 6:
            labelled.append((cell, None))
            continue
        res = [np.median(np.abs(sub[:, 2] - (a * sub[:, 0] + b * sub[:, 1] + c)))
               for a, b, c in planes]
        labelled.append((cell, int(np.argmin(res))))
    known = [(g, i) for g, i in labelled if i is not None]
    if not known:
        return []
    for k, (g, i) in enumerate(labelled):
        if i is None:
            labelled[k] = (g, min(known, key=lambda t: t[0].distance(g))[1])

    # Adjacent cells on the same plane are one face.
    out = []
    for pi in range(len(planes)):
        mine = [g for g, i in labelled if i == pi]
        if not mine:
            continue
        merged = unary_union(mine)
        for poly in (merged.geoms if merged.geom_type == "MultiPolygon" else [merged]):
            if poly.area < MIN_FACET_M2:
                continue
            sub = _points_in(poly, inside)
            plane = _fit_plane_robust(sub) if len(sub) >= MIN_POINTS else planes[pi]
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


# The LINZ outline is the BUILDING, not the roof.
#
# Josh drew the true roof outline on 7 Anderson Heights and it does not follow
# the footprint: the roof overhangs it on one side and sits inside it on
# another. Measured across five buildings, 6.6% to 18.8% of roof-height points
# fall outside the footprint, by up to 2 m. Those are eaves.
#
# This module cuts the footprint into faces, so every perimeter face was wrong
# at its edge before any ridge logic ran, and roof area was understated
# everywhere. The footprint is still the right SKELETON -- it is surveyed and
# straight -- so the roof outline is built by pushing it out to where the roof
# actually stops rather than by tracing points, which would reintroduce the
# fuzz the whole approach exists to avoid.
# Deliberately timid, because a uniform buffer grows toward the NEIGHBOURS too
# and their roofs sit at similar heights, so a loose test walks straight onto
# them: at 2.0 m and a 50% share this grew 7 Anderson Heights by 66% and 2/8
# Wakatipu by 81%, when Josh's drawn roof outline is nearer 10% larger than the
# footprint. Held to a typical eave, and needing the ring to be almost entirely
# roof-height before it is accepted.
# OFF pending better work -- see roof_outline. The FINDING is solid and matters:
# 6.6% to 18.8% of roof-height points fall outside the LINZ footprint, by up to
# 2 m, so roof area is understated everywhere and every perimeter face is wrong
# at its edge. But a uniform buffer is the wrong instrument. Josh's drawn outline
# on 7 Anderson Heights is not the footprint grown evenly -- the roof overhangs
# on one side and sits INSIDE it on another -- and growing uniformly took that
# roof to 16 faces against the 8 he counted. This needs per-edge treatment:
# decide independently for each footprint edge how far the roof runs past it.
EAVE_MAX_M = 0.0
EAVE_STEP_M = 0.25
EAVE_MIN_POINT_SHARE = 0.85


def roof_outline(footprint, pts):
    """Footprint grown out to the real roof edge, staying straight-sided."""
    if len(pts) < MIN_POINTS:
        return footprint
    inside = _points_in(footprint, pts)
    if len(inside) < MIN_POINTS:
        return footprint
    lo, hi = np.percentile(inside[:, 2], [5, 95])
    lo -= 0.5
    hi += 0.5

    best = footprint
    for grow in np.arange(EAVE_STEP_M, EAVE_MAX_M + 1e-9, EAVE_STEP_M):
        ring = footprint.buffer(grow).difference(footprint.buffer(grow - EAVE_STEP_M))
        got = _points_in(ring, pts)
        if len(got) < 4:
            break
        at_roof = float(((got[:, 2] >= lo) & (got[:, 2] <= hi)).mean())
        if at_roof < EAVE_MIN_POINT_SHARE:
            break
        best = footprint.buffer(grow, join_style=2)   # mitred: keeps corners sharp
    return best if best.geom_type == "Polygon" else footprint


def partition_roof(building_id, footprint, pts, imagery_ds=None):
    """Surveyed footprint + point cloud -> straight-edged, plane-backed facets.

    Strong imagery lines, if imagery is supplied, are cut FIRST and without the
    point cloud getting a vote. Everywhere else in this module a cut has to earn
    itself against the LiDAR, which is right when both sensors can see the
    feature and wrong when only one can. 7 Anderson Heights is the case that
    forced it: two hipped sections whose creases are unmistakable in 0.1 m
    imagery and almost absent from a point cloud that is near-flat across the
    whole roof. Every LiDAR-scored candidate there was rejected -- correctly, by
    its own logic, since cutting did not improve a fit that was never wrong
    about height -- so the faces ran straight over both hips and the panels
    followed. Josh: "two panel planes placed and both overlapping a roof ridge
    where it drops in the middle."

    Only lines carrying enough evidence to be a primary crease qualify; see
    roof_lines.strong_roof_lines."""
    footprint = roof_outline(footprint, pts)
    inside = _points_in(footprint, pts)
    if len(inside) < MIN_POINTS:
        return []

    cells = [footprint]
    if imagery_ds is not None:
        try:
            from src.roof_lines import strong_roof_lines
            for ang, off in strong_roof_lines(imagery_ds, footprint):
                nxt = []
                for c in cells:
                    parts = _cut(c, ang, off)
                    nxt.extend(parts if len(parts) >= 2 else [c])
                cells = nxt
        except Exception:
            cells = [footprint]

    faces = []
    for cell in cells:
        faces.extend(_partition(cell, _points_in(cell, inside)))
    if not faces:
        return []
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
