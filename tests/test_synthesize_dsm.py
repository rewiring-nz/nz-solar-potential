"""The no-LiDAR surface: hip roofs at the assumed pitch, flat big roofs."""
import math
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import synthesize_dsm as sd


def test_small_footprints_are_hipped_big_ones_flat():
    assert sd.roof_model(150)[0] == "hip" and sd.roof_model(150)[1] == sd.PITCH_DEG
    assert sd.roof_model(sd.FLAT_MIN_M2)[0] == "flat"


def test_hip_roof_rises_at_the_pitch_from_every_wall():
    house = box(0, 0, 12, 8)
    xs, ys = np.array([0.5, 4.0, 6.0]), np.array([4.0, 4.0, 4.0])
    z = sd.roof_heights(house, xs, ys, 100.0, "hip", 20.0, 3.0)
    t = math.tan(math.radians(20.0))
    assert np.allclose(z, [103 + 0.5 * t, 103 + 4 * t, 103 + 4 * t])   # ridge at 4 m in


def test_a_hip_roof_reads_as_four_planes_of_the_pitch():
    """Every point of a rectangle's roof lies on one of four planes whose
    slope is the assumed pitch -- what the face reader will find."""
    house = box(0, 0, 12, 8)
    gx, gy = np.meshgrid(np.arange(0.5, 12, 1.0), np.arange(0.5, 8, 1.0))
    z = sd.roof_heights(house, gx.ravel(), gy.ravel(), 0.0, "hip", 20.0, 3.0)
    x, y = gx.ravel(), gy.ravel()
    south = y < np.minimum.reduce([x, 12 - x, 8 - y])     # the south wall is nearest
    slope = np.polyfit(y[south], z[south], 1)[0]
    assert abs(math.degrees(math.atan(slope)) - 20.0) < 0.5


def test_flat_roofs_are_level():
    z = sd.roof_heights(box(0, 0, 30, 20), np.array([1.0, 15.0]), np.array([1.0, 10.0]),
                        200.0, "flat", 0.0, 5.0)
    assert np.allclose(z, 205.0)


def test_no_lidar_towns_resolve_to_a_synthetic_survey():
    # A no-LiDAR region that missed its survey box would warn and be skipped
    # at fetch time, so it would never reach the map.
    import config
    from src.surveys import survey_for
    for name in ("cromwell_west", "cromwell_east", "bannockburn", "glenorchy", "kinloch"):
        assert survey_for(config.REGIONS[name], name).get("elevation") == "synthetic", name


def test_small_buildings_get_a_gable_along_their_long_axis():
    assert sd.roof_model(40)[0] == "gable" and sd.roof_model(150)[0] == "hip"
    shed = box(0, 0, 8, 4)                         # long axis east-west
    faces = sd.gable_faces(sd.roof_outline(shed), 100.0, 3.0, 20.0)
    assert len(faces) == 2
    assert all(abs(q.area - 16) < 0.2 for q, _ in faces)
    from src.roof_partition import _slope_aspect
    aspects = sorted(round(_slope_aspect(pl)[1]) for _, pl in faces)
    assert aspects == [0, 180], aspects            # one face north, one south
    for _, pl in faces:
        assert abs(_slope_aspect(pl)[0] - 20.0) < 0.1
        # each plane is at eave height on its own long wall
        z_wall = pl[0] * 4 + pl[1] * (0 if _slope_aspect(pl)[1] > 90 else 4) + pl[2]
        assert abs(z_wall - 103.0) < 1e-6


def test_photo_roof_planes_rise_from_each_face_eave():
    import math
    # A 10 x 6 m gable read from the photo: north half faces north, south half south.
    north = {"ring": [[0, 3], [10, 3], [10, 6], [0, 6], [0, 3]], "slope": 30.0, "aspect": 0.0}
    south = {"ring": [[0, 0], [10, 0], [10, 3], [0, 3], [0, 0]], "slope": 30.0, "aspect": 180.0}
    flat = {"ring": [[20, 0], [24, 0], [24, 4], [20, 4], [20, 0]], "slope": 2.0, "aspect": None}
    faces, planes = sd.photo_roof([north, south, flat], 100.0, 3.0)
    z = lambda pl, x, y: pl[0] * x + pl[1] * y + pl[2]
    rise = 3 * math.tan(math.radians(30))
    assert abs(z(planes[0], 5, 6) - 103.0) < 1e-6 and abs(z(planes[0], 5, 3) - (103.0 + rise)) < 1e-6
    assert abs(z(planes[1], 5, 0) - 103.0) < 1e-6 and abs(z(planes[1], 5, 3) - (103.0 + rise)) < 1e-6
    assert planes[2] == [0.0, 0.0, 103.0]


def test_an_l_house_has_a_straight_valley():
    # equal arms: the two faces meeting at the inside corner mirror each other,
    # and the valley between them is a straight line, not the arc the
    # nearest-wall rule drew (4718459, 20 Oct)
    from shapely.geometry import Polygon
    from src.synthesize_dsm import hip_faces
    L = Polygon([(0, 0), (20, 0), (20, 8), (8, 8), (8, 20), (0, 20)])
    faces = sorted(f.area for f, _ in hip_faces(L))
    assert len(faces) == 6
    inner = [a for a in faces if 40 < a < 55]
    assert len(inner) == 2 and abs(inner[0] - inner[1]) < 0.02 * inner[0], faces


def test_a_shallow_wall_step_does_not_fan_the_hip():
    # a 1 m notch in the front wall: still a four-face hip, still roofing the whole house
    from shapely.geometry import Polygon
    from src.synthesize_dsm import hip_faces, without_jogs
    p = Polygon([(0, 0), (8, 0), (8, 1), (11, 1), (11, 0), (20, 0), (20, 10), (0, 10)])
    faces = hip_faces(without_jogs(p), mask=p)
    assert len(faces) == 4
    assert sum(f.area for f, _ in faces) > 0.97 * p.area


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("  pass ", name)
            except AssertionError as e:
                fails += 1
                print("  FAIL ", name, e)
    sys.exit(1 if fails else 0)
