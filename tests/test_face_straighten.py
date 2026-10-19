"""face_straighten: one plane is one face; the edges between faces are
straight lines shared by both neighbours; a valley, a step or Josh's
markup is left alone."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shapely  # noqa: E402
from shapely.geometry import Polygon, box  # noqa: E402
from src import face_straighten as fs  # noqa: E402


def _face(poly, slope, aspect, c=100.0, **kw):
    # plane z = a x + b y + c rising away from `aspect` (downslope direction)
    t = math.tan(math.radians(slope))
    a = -t * math.sin(math.radians(aspect))
    b = -t * math.cos(math.radians(aspect))
    return dict(geometry=poly, area_m2=poly.area, slope_deg=slope, aspect_deg=aspect,
                plane_a=a, plane_b=b, plane_c=c, **kw)


def test_flat_roof_cut_in_two_becomes_one_face():
    left, right = box(0, 0, 6, 8), box(6, 0, 12, 8)
    out = fs.merge_coplanar_faces([_face(left, 2, 0), _face(right, 2.5, 0)])
    assert len(out) == 1 and abs(out[0]["geometry"].area - 96) < 1e-6


def test_gable_halves_stay_apart():
    # same slope, opposite directions: a ridge, not one plane
    n = _face(box(0, 4, 10, 8), 25, 0, c=100 + 4 * math.tan(math.radians(25)) * 2)
    s = _face(box(0, 0, 10, 4), 25, 180)
    assert len(fs.merge_coplanar_faces([n, s])) == 2


def test_low_pitch_gable_halves_stay_apart():
    # 3 degrees each way, meeting at a ridge at y = 10: they agree ALONG the
    # seam (they cross there) but they are two planes
    t = math.tan(math.radians(3))
    s_ = _face(box(0, 0, 30, 10), 3, 180, c=100.0)                 # rises north to the ridge
    n_ = _face(box(0, 10, 30, 20), 3, 0, c=100.0 + 20 * t)         # falls north from it
    assert len(fs.merge_coplanar_faces([s_, n_])) == 2


def test_a_step_between_storeys_stays_apart():
    low, high = _face(box(0, 0, 6, 8), 2, 0, c=100.0), _face(box(6, 0, 12, 8), 2, 0, c=100.6)
    assert len(fs.merge_coplanar_faces([low, high])) == 2


def test_markup_faces_are_not_touched():
    faces = [_face(box(0, 0, 6, 8), 2, 0, from_labels=True), _face(box(6, 0, 12, 8), 2, 0)]
    assert fs.merge_coplanar_faces(faces) is faces
    assert fs.straighten_faces(faces) is faces


def test_zigzag_seam_straightens_and_neighbours_still_meet():
    # two faces split by a seam that zig-zags 0.2 m either side of x = 6
    seam = [(6 + (0.2 if k % 2 else -0.2), k * 0.8) for k in range(11)]
    left = Polygon([(0, 0)] + seam + [(0, 8)])
    right = Polygon([(12, 0)] + seam + [(12, 8)])
    out = fs.straighten_faces([_face(left, 20, 270), _face(right, 20, 90)])
    l, r = out[0]["geometry"], out[1]["geometry"]
    assert len(l.exterior.coords) < len(left.exterior.coords)
    assert l.intersection(r).area < 1e-6                       # no overlap
    assert abs(l.union(r).area - 96) < 1e-6                     # no gap
    assert l.union(r).equals_exact(box(0, 0, 12, 8), 1e-6) or \
        abs(l.union(r).symmetric_difference(box(0, 0, 12, 8)).area) < 1e-6   # outline kept


def test_seam_drawn_off_the_ridge_moves_onto_it():
    # gable 10 x 8, ridge truly at y = 4; the partition drew the seam at y = 4.6
    t = math.tan(math.radians(30))
    south = _face(box(0, 0, 10, 4.6), 30, 180, c=100.0)          # z = 100 + t y
    north = _face(box(0, 4.6, 10, 8), 30, 0, c=100.0 + 8 * t)    # z = 100 + t (8 - y)
    out = fs.seams_to_plane_lines([south, north])
    s_new = out[0]["geometry"]
    assert abs(s_new.bounds[3] - 4.0) < 0.05, s_new.bounds
    assert abs(s_new.area + out[1]["geometry"].area - 80) < 1e-6


class _Points:
    def __init__(self, pts):
        self.pts = pts

    def points_in_bbox(self, x0, y0, x1, y1, building_only=True):
        import numpy as np
        p = self.pts
        return p[(p[:, 0] >= x0) & (p[:, 0] <= x1) & (p[:, 1] >= y0) & (p[:, 1] <= y1)]


def _gable_returns(ridge_y, t, step=0.3):
    import numpy as np
    xs, ys = np.meshgrid(np.arange(0.15, 10, step), np.arange(0.15, 8, step))
    z = np.where(ys <= ridge_y, 100.0 + t * ys, 100.0 + t * ridge_y + t * (ridge_y - ys))
    return _Points(np.c_[xs.ravel(), ys.ravel(), z.ravel()])


def test_seam_moves_when_the_returns_agree():
    t = math.tan(math.radians(30))
    south = _face(box(0, 0, 10, 4.6), 30, 180, c=100.0)
    north = _face(box(0, 4.6, 10, 8), 30, 0, c=100.0 + 8 * t)
    out = fs.seams_to_plane_lines([south, north], _gable_returns(4.0, t))
    assert abs(out[0]["geometry"].bounds[3] - 4.0) < 0.05


def test_seam_stays_when_the_returns_say_the_planes_are_wrong():
    # the fitted planes cross at y = 4, but the real ridge (the returns) is at y = 4.6
    t = math.tan(math.radians(30))
    south = _face(box(0, 0, 10, 4.6), 30, 180, c=100.0)
    north = _face(box(0, 4.6, 10, 8), 30, 0, c=100.0 + 8 * t)
    faces = [south, north]
    assert fs.seams_to_plane_lines(faces, _gable_returns(4.6, t)) is faces


def test_valley_seam_moves_the_other_way():
    # the same two planes upside down (a valley at y = 4), seam drawn at y = 4.6
    t = math.tan(math.radians(30))
    south = _face(box(0, 0, 10, 4.6), 30, 0, c=100.0 + 4 * t)    # z = 100 + 4t - t y : falls to the valley
    north = _face(box(0, 4.6, 10, 8), 30, 180, c=100.0 - 4 * t)  # z = 100 - 4t + t y
    out = fs.seams_to_plane_lines([south, north])
    assert abs(out[0]["geometry"].bounds[3] - 4.0) < 0.05, out[0]["geometry"].bounds


def test_seam_already_on_the_ridge_is_left():
    t = math.tan(math.radians(30))
    faces = [_face(box(0, 0, 10, 4), 30, 180, c=100.0), _face(box(0, 4, 10, 8), 30, 0, c=100.0 + 8 * t)]
    assert fs.seams_to_plane_lines(faces) is faces


def test_hairline_gaps_and_overlaps_are_tidied_first():
    # built faces do not tile exactly: a 3 cm gap on one seam, a 2 cm overlap on another
    a, b, c = box(0, 0, 5.985, 8), box(6.015, 0, 9.01, 8), box(9.0, 0, 12, 8)
    cov = fs.as_coverage([a, b, c])
    assert cov is not None and shapely.coverage_is_valid(cov, gap_width=0.0)
    assert sum(g.area for g in cov) > 95.9


def test_a_face_inside_another_is_cut_out_of_it():
    big, inner = box(0, 0, 12, 8), box(4, 3, 7, 5)        # a lower level drawn inside the main face
    cov = fs.as_coverage([big, inner])
    assert cov is not None
    assert abs(cov[1].area - 6) < 1e-6 and abs(cov[0].area - 90) < 1e-6
    assert cov[0].intersection(cov[1]).area < 1e-9


def test_a_face_is_never_split_and_half_dropped():
    # an L-ish roof: main block and a wing joined by a 1 m neck; a small lower
    # face lies across the neck. Tidying must not keep the main block and lose the wing.
    from shapely.ops import unary_union
    main = unary_union([box(0, 0, 10, 10), box(10, 4, 11, 5), box(11, 0, 20, 10)])
    lower = box(9.5, 3, 11.5, 6)
    cov = fs.as_coverage([main, lower])
    assert cov is None or cov[0].area > 0.95 * main.difference(lower).area


def test_faces_that_overlap_are_left_as_they_were():
    faces = [_face(box(0, 0, 7, 8), 20, 270), _face(box(6, 0, 12, 8), 20, 90)]
    assert fs.straighten_faces(faces) is faces


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("  pass ", name)
