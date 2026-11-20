"""One line between neighbouring roof faces.

Two faces of a roof meet along one line: a ridge, a hip, a valley or a step.
A reading gives each face its own polygon, and the two edges of a shared
boundary can come out as two lines a hand's width apart, or one face can lap
over the other. On the 19-roof test street (round 3, 21 Nov) there were 47
pairs of near-parallel edges of different faces 0.08 to 0.75 m apart over a
metre or more; every roof marked for "doubled" lines had them and the five
roofs with none were not marked for it.

Rule: where two faces overlap, the smaller gives way; a gap between two or
more faces narrower than WELD_GAP_M is given to a face beside it (the
larger face first, reaching half the limit), so the neighbour's edge becomes the one line. A notch in the roof's
outer edge touches one face only and is left alone, and so is any gap wider
than the limit: that is a real break between roof parts. Drawn faces are not
touched. SOLAR_WELD_FACES=0 turns it off.
"""
import os

from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

WELD = os.environ.get("SOLAR_WELD_FACES", "1") != "0"
WELD_GAP_M = float(os.environ.get("SOLAR_WELD_GAP_M", "0.9"))
OVERLAP_MAX_SHARE = 0.25      # more of the smaller face than this: not a lapped edge, leave it
TOUCH_M = 0.03


def _largest(g):
    if isinstance(g, MultiPolygon):
        g = max(g.geoms, key=lambda p: p.area)
    return g if isinstance(g, Polygon) and not g.is_empty else None


def _polys(g):
    return [p for p in getattr(g, "geoms", [g]) if isinstance(p, Polygon) and p.area > 0.01]


def weld_polygons(polys, gap_m=WELD_GAP_M):
    """The polygons with overlaps cut and narrow gaps between them closed."""
    polys = [p.buffer(0) for p in polys]
    order = sorted(range(len(polys)), key=lambda i: -polys[i].area)
    for n, i in enumerate(order):
        for j in order[n + 1:]:
            a, b = polys[i], polys[j]
            if not a.intersects(b):
                continue
            lap = a.intersection(b).area
            if 0 < lap <= OVERLAP_MAX_SHARE * b.area:
                cut = _largest(b.difference(a))
                if cut is not None:
                    polys[j] = cut
    r = gap_m / 2.0
    union = unary_union(polys)
    closed = union.buffer(r, join_style=2, mitre_limit=2.0).buffer(-r, join_style=2, mitre_limit=2.0)
    gaps = []
    for piece in _polys(closed.difference(union)):
        reach = piece.buffer(TOUCH_M)
        if sum(reach.intersects(p) for p in polys) >= 2:
            gaps.append(piece)            # one face only: a notch in the outer edge, left alone
    gaps = unary_union(gaps)
    # each stretch of gap goes to a face beside it, the larger face first
    for i in sorted(range(len(polys)), key=lambda k: -polys[k].area):
        if gaps.is_empty:
            break
        claim = gaps.intersection(polys[i].buffer(r + 0.02, join_style=2, mitre_limit=2.0))
        if claim.is_empty or claim.area < 1e-4:
            continue
        grown = unary_union([polys[i], claim.buffer(0.005, join_style=2)])
        for j, p in enumerate(polys):
            if j != i and grown.intersects(p):
                grown = grown.difference(p)
        grown = _largest(grown)
        if grown is not None:
            polys[i] = grown
            gaps = gaps.difference(claim)
    return polys


def weld_faces(facets):
    if not WELD or len(facets) < 2 or any(f.get("from_labels") for f in facets):
        return facets
    new = weld_polygons([f["geometry"] for f in facets])
    for f, g in zip(facets, new):
        if g is not None and not g.is_empty and g.is_valid:
            f["geometry"] = g
    return facets
