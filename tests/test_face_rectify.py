"""face_rectify: a traced zig-zag seam becomes one straight line on the
walls' axis; a seam that turns a corner stays two lines; overlaps and
hairline gaps do not stop it; Josh's markup and plain forms are left alone;
a roof it cannot rebuild keeps its faces."""
import os
os.environ["SOLAR_RECTIFY_FACES"] = "1"
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shapely.geometry import Polygon, box  # noqa: E402
from src import face_rectify as fr  # noqa: E402


def _face(poly, slope=25, aspect=0, c=100.0, **kw):
    t = math.tan(math.radians(slope))
    return dict(geometry=poly, area_m2=poly.area, slope_deg=slope, aspect_deg=aspect,
                plane_a=-t * math.sin(math.radians(aspect)), plane_b=-t * math.cos(math.radians(aspect)),
                plane_c=c, **kw)


def _zigzag_gable(depth=0.5, step=1.0, w=12.0, h=8.0, overlap=0.0):
    """Two faces split at y = h/2 by a seam that zig-zags +-depth every step."""
    seam = [(x, h / 2 + (depth if (i % 2) else -depth)) for i, x in enumerate(
        [k * step for k in range(int(w / step) + 1)])]
    seam[0] = (0, h / 2); seam[-1] = (w, h / 2)
    south = Polygon([(0, 0), (w, 0)] + [(x, y + overlap) for x, y in reversed(seam)])
    north = Polygon([(0, h)] + [(x, y - overlap) for x, y in seam] + [(w, h)])
    return south, north


def _straight_seam(faces):
    s, n = (f["geometry"] for f in faces)
    seam = s.boundary.intersection(n.buffer(0.005))
    ys = [y for g in getattr(seam, "geoms", [seam]) for _, y in g.coords]
    return max(ys) - min(ys)


def test_zigzag_seam_becomes_one_straight_line():
    s, n = _zigzag_gable()
    out = fr.rectify_faces([_face(s, aspect=180), _face(n, aspect=0)])
    assert len(out) == 2
    assert _straight_seam(out) < 0.05
    for f in out:
        assert len(f["geometry"].exterior.coords) - 1 == 4
        assert abs(f["geometry"].area - 48) < 2.5


def test_overlapping_traced_faces_still_rebuild():
    s, n = _zigzag_gable(overlap=0.15)
    out = fr.rectify_faces([_face(s, aspect=180), _face(n, aspect=0)])
    assert _straight_seam(out) < 0.05
    assert out[0]["geometry"].intersection(out[1]["geometry"]).area < 1e-6


def test_seam_off_the_wall_axis_by_a_few_degrees_snaps_to_it():
    # the seam runs from y = 3.6 to y = 4.4 across 12 m (3.8 deg): a ridge is parallel to the walls
    s = Polygon([(0, 0), (12, 0), (12, 4.4), (0, 3.6)])
    n = Polygon([(0, 3.6), (12, 4.4), (12, 8), (0, 8)])
    out = fr.rectify_faces([_face(s, aspect=180), _face(n, aspect=0)])
    assert _straight_seam(out) < 0.05


def test_l_shaped_valley_stays_two_lines():
    # an L-plan roof: a corner face meets the main face along a seam that turns 90 degrees
    main = Polygon([(0, 0), (12, 0), (12, 4), (4, 4), (4, 12), (0, 12)])
    wing = Polygon([(4, 4), (12, 4), (12, 12), (4, 12)])
    out = fr.rectify_faces([_face(main, aspect=225), _face(wing, aspect=45)])
    g = {round(f["geometry"].area) for f in out}
    assert g == {80, 64}


def test_markup_and_forms_are_left_alone():
    s, n = _zigzag_gable()
    drawn = [_face(s, from_labels=True), _face(n)]
    assert fr.rectify_faces(drawn) is drawn
    form = [_face(s, structured=True), _face(n, structured=True)]
    assert fr.rectify_faces(form) is form


def test_off_unless_asked():
    os.environ["SOLAR_RECTIFY_FACES"] = "0"
    try:
        s, n = _zigzag_gable()
        faces = [_face(s), _face(n)]
        assert fr.rectify_faces(faces) is faces
    finally:
        os.environ["SOLAR_RECTIFY_FACES"] = "1"


def test_sliver_no_seam_encloses_goes_to_its_neighbours():
    s, n = _zigzag_gable()
    sliver = box(5, 3.5, 5.6, 4.1).difference(s).difference(n)
    faces = [_face(s, aspect=180), _face(n, aspect=0)]
    if not sliver.is_empty and sliver.geom_type == "Polygon":
        faces.append(_face(sliver))
    out = fr.rectify_faces(faces)
    assert len(out) == 2 and _straight_seam(out[:2]) < 0.05


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("  pass ", name)
