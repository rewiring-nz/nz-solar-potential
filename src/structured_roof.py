"""Replace a ragged face partition with the clean roof it is, when the LiDAR agrees.

    SOLAR_STRUCTURED_ROOFS=0   turns it off

WHY. A hip roof is BUILT as the straight skeleton of its walls: every ridge
and hip sits where the slopes rising from two walls meet. On sparse LiDAR
(Queenstown 2021 is ~1.7 returns/m2 on a roof) the face partition guesses
those lines from scattered points, and draws them ragged: little triangles,
lines crossing at odd angles (9 Sandford Terrace, #4731128, 15 Oct). The
skeleton needs only the outline, and its lines are exact.

MEASURED 15 Oct on 254 suburban houses (Lake Hayes Estate): a plain form
explained the returns as well as the partition on 40 (16%), and on the worst
partitions far better (#5371931: 26% of returns explained as two faces, 87%
as the hip it visibly is; #4726765: 20% -> 84%). Most houses (wings, gable
ends, mixed pitches) keep their partition. The CBD-heavy benchmark is
unchanged (no plain form fits its roofs).

WHAT THIS DOES. For a pitched building with a LiDAR partition, the plain
forms of its outline (src/photo_shape._form_candidates: a hip, a gable either
way, one slope, flat) are each given planes fitted to the returns in their
faces. A form replaces the partition only when it explains the returns
about as well (inlier share within STRUCTURED_TOL of the partition's) --
fewer, straighter faces win a near tie, because that is what real roofs are.
Anything the forms cannot explain -- dormers, wings at other pitches, a flat
section -- keeps the partition. Drawn and photo-read roofs are never touched.
"""

import os

import numpy as np

STRUCTURED_TOL = 0.03      # a form may explain this much less of the roof and still win
INLIER_M = 0.20            # a return within this of its face's plane is explained
MIN_FACE_POINTS = 12
MIN_PITCH_DEG = 10.0       # mostly-flat roofs keep their partition
MAX_AREA_M2 = 600.0        # big commercial roofs keep theirs
MIN_SLOPE_DEG, MAX_SLOPE_DEG = 8.0, 55.0
STATS = {"considered": 0, "replaced": 0}


STRAIGHTEN_M = 0.4         # hip faces are labelled on a 0.25 m grid; their edges are straightened


def enabled():
    return os.environ.get("SOLAR_STRUCTURED_ROOFS", "1") == "1"


def _straight(faces):
    """Straighten a form's face edges as a coverage (shared edges kept exact)."""
    import shapely
    polys = [p for p, _ in faces]
    try:
        simple = shapely.coverage_simplify(polys, STRAIGHTEN_M)
    except Exception:
        return faces
    out = []
    for (p, a), q in zip(faces, simple):
        if not q.is_valid:
            q = q.buffer(0)
        out.append((q if (q.geom_type == "Polygon" and q.area > 0.5 * p.area) else p, a))
    return out


def _fit(pts):
    A = np.c_[pts[:, 0], pts[:, 1], np.ones(len(pts))]
    return np.linalg.lstsq(A, pts[:, 2], rcond=None)[0]


def _inlier_share(faces_planes, pts):
    """Share of returns within INLIER_M of the plane of the face they fall in."""
    import shapely
    hit = np.zeros(len(pts), bool)
    seen = np.zeros(len(pts), bool)
    for poly, (a, b, c) in faces_planes:
        on = shapely.contains_xy(poly, pts[:, 0], pts[:, 1]) & ~seen
        seen |= on
        hit[on] = np.abs(pts[on, 2] - (a * pts[on, 0] + b * pts[on, 1] + c)) <= INLIER_M
    return float(hit.sum() / max(seen.sum(), 1)), int(seen.sum())


def maybe_structured(facets, footprint, pc_source, building_id=None):
    """facets, or a cleaner set of the same shape when a plain form fits as well."""
    if not enabled() or not facets or footprint is None:
        return facets
    if any(f.get("from_labels") or f.get("synthetic") for f in facets):
        return facets
    if footprint.area > MAX_AREA_M2 or len(facets) < 2:
        return facets
    area = sum(f["geometry"].area for f in facets)
    pitch = sum(f["geometry"].area * (f.get("slope_deg") or 0) for f in facets) / max(area, 1e-9)
    if pitch < MIN_PITCH_DEG:
        return facets
    import shapely
    try:
        pts = pc_source.points_in_bbox(*footprint.bounds, building_only=True)
    except Exception:
        return facets
    if pts is None or len(pts) < 50:
        return facets
    pts = pts[shapely.contains_xy(footprint, pts[:, 0], pts[:, 1])]
    if len(pts) < 50:
        return facets
    STATS["considered"] += 1
    current, _ = _inlier_share([(f["geometry"], (f["plane_a"], f["plane_b"], f["plane_c"])) for f in facets], pts)

    from src.photo_shape import _form_candidates
    from src.roof_partition import _slope_aspect
    best = None
    for name, faces in _form_candidates(footprint):
        if name == "flat":
            continue
        faces = _straight(faces)
        fitted = []
        ok = True
        for poly, _asp in faces:
            on = shapely.contains_xy(poly, pts[:, 0], pts[:, 1])
            if on.sum() < MIN_FACE_POINTS:
                ok = False
                break
            plane = _fit(pts[on])
            s, _ = _slope_aspect(plane)
            if not (MIN_SLOPE_DEG <= s <= MAX_SLOPE_DEG):
                ok = False
                break
            fitted.append((poly, tuple(float(v) for v in plane), int(on.sum())))
        if not ok:
            continue
        share, _ = _inlier_share([(p, pl) for p, pl, _ in fitted], pts)
        if best is None or share > best[0] or (abs(share - best[0]) < 1e-9 and len(fitted) < len(best[2])):
            best = (share, name, fitted)
    # ...and the house as its wings, each a plain roof (src/composite_roof.py)
    try:
        from src import composite_roof as CR
        comp = CR.composite_faces(footprint, pts) if CR.enabled() else None
    except Exception:
        comp = None
    if comp:
        cfaces, kinds = comp
        cf = []
        for poly, plane in cfaces:
            on = shapely.contains_xy(poly, pts[:, 0], pts[:, 1])
            cf.append((poly, plane, int(on.sum())))
        share, _ = _inlier_share([(p, pl) for p, pl, _ in cf], pts)
        if best is None or share > best[0] + 1e-9:
            best = (share, "wings:" + "+".join(kinds), cf)
    if best is None or best[0] < current - STRUCTURED_TOL:
        return facets
    share, name, fitted = best
    STATS["replaced"] += 1
    template = facets[0]
    out = []
    for poly, (a, b, c), n in fitted:
        s, asp = _slope_aspect((a, b, c))
        f = {k: template[k] for k in ("building_id", "building_geometry") if k in template}
        f.update({"geometry": poly, "plane_a": a, "plane_b": b, "plane_c": c,
                  "slope_deg": s, "aspect_deg": asp, "area_m2": float(poly.area),
                  "point_count": n, "structured": name})
        out.append(f)
    return out
