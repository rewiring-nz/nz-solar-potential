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


def test_hairline_gaps_and_overlaps_are_tidied_first():
    # built faces do not tile exactly: a 3 cm gap on one seam, a 2 cm overlap on another
    a, b, c = box(0, 0, 5.985, 8), box(6.015, 0, 9.01, 8), box(9.0, 0, 12, 8)
    cov = fs.as_coverage([a, b, c])
    assert cov is not None and shapely.coverage_is_valid(cov, gap_width=0.0)
    assert sum(g.area for g in cov) > 95.9


def test_faces_that_overlap_are_left_as_they_were():
    faces = [_face(box(0, 0, 7, 8), 20, 270), _face(box(6, 0, 12, 8), 20, 90)]
    assert fs.straighten_faces(faces) is faces


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("  pass ", name)
