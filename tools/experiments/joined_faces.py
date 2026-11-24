"""Scoring faces rebuilt from the drawn lines, loose ends carried to what they point at.

The label's stored `faces` are the closed cells of the outline and the drawn
lines (the markup page's facesFor). A line that stops short of the edge or of
another line closes no cell, so the stored faces run across it: a ridge drawn
0.5 m short of the eave leaves one face over both slopes, and a reading that
follows the ridge scores as wrong. Here every loose end (on nothing) is
carried along its own direction to the first line or edge within REACH_M, as
the build does for drawn roofs (SOLAR_DANGLE_JOIN_M), and the faces are the
cells of the result. An end within NODE_M of something is joined to it, as
the page joins it.

    python tools/experiments/joined_faces.py <reach m> [out.json]
"""
import sys, json
import numpy as np
from shapely.geometry import Polygon, LineString, Point
from shapely.ops import unary_union, polygonize, nearest_points

NODE_M, MIN_FACE_M2 = 0.3, 1.0


def roof_edge(b):
    faces = [Polygon(f['ring']).buffer(0) for f in b.get('faces') or [] if f.get('ring') and len(f['ring']) >= 3]
    faces = [f for f in faces if not f.is_empty]
    if not faces:
        return None, []
    edge = unary_union([f.buffer(0.3) for f in faces]).buffer(-0.3)
    edge = max(edge.geoms, key=lambda g: g.area) if edge.geom_type == 'MultiPolygon' else edge
    return edge, [f for f in faces if f.area >= MIN_FACE_M2]


def joined(b, reach):
    """(faces rebuilt, faces stored) as polygons in NZTM."""
    edge, stored = roof_edge(b)
    if edge is None:
        return [], []
    segs = [LineString(l.get('points') or [l['a'], l['b']]) for l in b.get('lines') or []]
    segs = [s for s in segs if s.length > 0.05]
    ring = LineString(edge.exterior.coords)
    extra = []
    for i, s in enumerate(segs):
        c = list(s.coords)
        others = unary_union([ring] + [o for j, o in enumerate(segs) if j != i])
        for end, prev in ((0, 1), (-1, -2)):
            p, q = np.array(c[end]), np.array(c[prev]); d = p - q; n = float(np.hypot(*d))
            P = Point(p); gap = others.distance(P)
            if n < 1e-6 or gap < 1e-9:
                continue
            if gap <= NODE_M:                                     # the page joins an end this close
                t = np.array(nearest_points(others, P)[0].coords[0]); v = t - p; m = float(np.hypot(*v))
                extra.append(LineString([tuple(p), tuple(t + (v / m * 0.02 if m > 1e-9 else 0))]))
                continue
            if reach <= 0:
                continue
            hit = LineString([tuple(p), tuple(p + d / n * reach)]).intersection(others)
            if hit.is_empty:
                # nothing ahead: a line never ends in the middle of a face, so an end with a line
                # or the edge beside it (within the same reach) meets that at the nearest point
                if gap <= reach:
                    t = np.array(nearest_points(others, P)[0].coords[0]); v = t - p; m = float(np.hypot(*v))
                    extra.append(LineString([tuple(p), tuple(t + v / m * 0.02)]))
                continue
            far = min(getattr(hit, 'geoms', [hit]), key=lambda g: g.distance(P))
            fp = np.array(far.coords[0])
            extra.append(LineString([tuple(p), tuple(fp + d / n * 0.02)]))
    # the stored faces' own edges go in too: they lie on drawn lines, so a cell the page closed stays closed
    net = unary_union([ring] + segs + extra + [LineString(f.exterior.coords) for f in stored])
    inside = edge.buffer(0.05)
    new = [f for f in polygonize(net) if f.area >= MIN_FACE_M2 and inside.contains(f.representative_point())]
    return new, stored


def unused_m(b, faces):
    """Metres of drawn line, inside the roof edge, that lie on no face edge: lines that divide nothing."""
    edge, _ = roof_edge(b)
    segs = [LineString(l.get('points') or [l['a'], l['b']]) for l in b.get('lines') or []]
    if edge is None or not segs or not faces:
        return 0.0
    ln = unary_union(segs).intersection(edge.buffer(-0.3))
    return float(ln.difference(unary_union([f.exterior for f in faces]).buffer(0.15)).length)


if __name__ == '__main__':
    L = json.load(open('data/roof_labels.json'))['buildings']
    reach = float(sys.argv[1]); out = {}; loose = {}; more, same, less = [], 0, []
    for bid, b in L.items():
        if not b.get('lines'):
            continue
        new, stored = joined(b, reach)
        if not stored:
            continue
        if len(new) > len(stored): more.append((bid, len(stored), len(new)))
        elif len(new) < len(stored): less.append((bid, len(stored), len(new)))
        else: same += 1
        out[bid] = [[[round(x, 3), round(y, 3)] for x, y in f.exterior.coords] for f in new]
        loose[bid] = round(unused_m(b, new), 1)
    print('reach %.1f m: %d roofs; more faces than stored on %d, the same on %d, fewer on %d' % (reach, len(out), len(more), same, len(less)))
    print('more:', more[:40]); print('fewer:', less[:15])
    if len(sys.argv) > 2:
        json.dump(out, open(sys.argv[2], 'w'))
        json.dump(loose, open(sys.argv[2].replace('.json', '_unused_m.json'), 'w'))
    print('roofs with over 0.5 m of drawn line on no face edge:', sum(v > 0.5 for v in loose.values()))
