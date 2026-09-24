"""The direction a building's walls run: its dominant axis, from its edges.

WHY NOT THE MINIMUM ROTATED RECTANGLE. Everything that racks panels or
straightens roof lines "to the building" used to take the building's axis
from the long side of its minimum-area bounding rectangle. For a rectangle,
or anything close to one, that is the wall direction. For a STEPPED outline
it is not: the smallest box around a staircase lies along the diagonal of the
steps, whichever way the walls run. 8 Sydney Street (#5371137) has every wall
at 76 degrees and a bounding rectangle at 9; its flat-roof panels were racked
21 degrees across the building, and every traced face on such a roof was
straightened to the same diagonal.

The walls are what an installer racks to and what a roof line follows, so
the axis is taken from them directly: the length-weighted mode of the edge
directions (mod 90, so a wall and the one at right angles to it vote
together), refined by the length-weighted circular mean of the edges near
it. For a rectangle this is the rectangle's axis; for a stepped or L-shaped
outline it is the walls', not the diagonal's.
"""

import math

MODE_TOL_DEG = 5.0   # edges within this of the mode vote for it and refine it


def _edges(geom):
    polys = getattr(geom, "geoms", [geom])
    for p in polys:
        ext = getattr(p, "exterior", None)
        if ext is None:
            continue
        cc = list(ext.coords)
        for (x0, y0), (x1, y1) in zip(cc[:-1], cc[1:]):
            L = math.hypot(x1 - x0, y1 - y0)
            if L > 0:
                yield L, math.degrees(math.atan2(y1 - y0, x1 - x0)) % 90.0


def _dev(a, b):
    d = abs(a - b) % 90.0
    return min(d, 90.0 - d)


def _mrr_long_side_deg(geom):
    try:
        cc = list(geom.minimum_rotated_rectangle.exterior.coords)
    except Exception:
        return None
    if len(cc) < 3:
        return None
    e = [(math.hypot(cc[i + 1][0] - cc[i][0], cc[i + 1][1] - cc[i][1]),
          math.degrees(math.atan2(cc[i + 1][1] - cc[i][1], cc[i + 1][0] - cc[i][0])))
         for i in range(2)]
    return max(e)[1]


def dominant_axis_deg(geom):
    """Wall direction of `geom`, degrees counter-clockwise from +x, in
    [0, 180). Of the two perpendicular wall directions it returns the one
    nearer the bounding rectangle's long side, so callers that want "the long
    axis" still get it. None for an empty or degenerate geometry."""
    edges = list(_edges(geom)) if geom is not None and not geom.is_empty else []
    long_side = _mrr_long_side_deg(geom) if edges else None
    if not edges:
        return None
    best = max(edges, key=lambda e: sum(L for L, a in edges if _dev(a, e[1]) <= MODE_TOL_DEG))[1]
    sx = sy = 0.0
    for L, a in edges:
        if _dev(a, best) <= MODE_TOL_DEG:
            sx += L * math.cos(math.radians(4 * a))
            sy += L * math.sin(math.radians(4 * a))
    axis = (math.degrees(math.atan2(sy, sx)) / 4.0) % 90.0
    if long_side is None:
        return axis

    def off(a):   # undirected angle between a and the long side, 0..90
        d = abs(a - long_side) % 180.0
        return min(d, 180.0 - d)
    return min((axis, axis + 90.0), key=off)


# SEVERAL WALL DIRECTIONS. A building with a wing at another angle has two
# sets of walls: 12 Stanley Street (#4725580) runs 55% of its wall length at
# 45 degrees and 41% at 75. One axis for the whole building straightens the
# second wing's roof lines 30 degrees across themselves -- the misaligned
# lines on its pyramid roof. So a face takes the wall family that fits it.
FAMILY_MIN_SHARE = 0.2   # of total wall length, for a direction to be a family
FAMILY_SEP_DEG = 10.0    # families closer than this are one family


def wall_families_deg(geom):
    """Wall directions (mod 90) carrying at least FAMILY_MIN_SHARE of the
    outline's length, dominant first. [] for a degenerate geometry."""
    edges = list(_edges(geom)) if geom is not None and not geom.is_empty else []
    total = sum(L for L, _ in edges)
    fams = []
    rest = edges
    while rest and total > 0:
        best = max(rest, key=lambda e: sum(L for L, a in rest if _dev(a, e[1]) <= MODE_TOL_DEG))[1]
        near = [(L, a) for L, a in rest if _dev(a, best) <= MODE_TOL_DEG]
        if sum(L for L, _ in near) < FAMILY_MIN_SHARE * total:
            break
        sx = sum(L * math.cos(math.radians(4 * a)) for L, a in near)
        sy = sum(L * math.sin(math.radians(4 * a)) for L, a in near)
        ax = (math.degrees(math.atan2(sy, sx)) / 4.0) % 90.0
        if all(_dev(ax, f) > FAMILY_SEP_DEG for f in fams):
            fams.append(ax)
        rest = [(L, a) for L, a in rest if _dev(a, best) > FAMILY_SEP_DEG]
    return fams


def face_axis_deg(footprint, face):
    """The wall direction a roof face should be straightened to: the
    building's dominant axis, or -- on a building with more than one wall
    family -- the family whose frame boxes the face most tightly. Judged by
    fit, not by the face's own edges, because a traced face's edges are
    pixel staircases that vote for the raster grid."""
    dom = dominant_axis_deg(footprint)
    fams = wall_families_deg(footprint)
    if dom is None or len(fams) < 2 or face is None or face.is_empty:
        return dom
    from shapely import affinity

    def slack(ax):
        env = affinity.rotate(face, -ax, origin=(0, 0)).envelope
        return env.area
    best = min(fams, key=slack)
    if _dev(best, dom) <= FAMILY_SEP_DEG:
        return dom
    return best
