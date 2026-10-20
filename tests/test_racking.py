"""Racking: clean arrays only -- no lone panel, at least three on a face; blocks
split by a skylight on one face stay (Josh, 20 Oct)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["SOLAR_RACKING"] = "1"
from shapely.geometry import box  # noqa: E402
from src import panel_fitting as pf  # noqa: E402


def _row(x0, y0, n, w=1.0, h=1.7, gap=0.02):
    return [{"geometry": box(x0 + k * (w + gap), y0, x0 + k * (w + gap) + w, y0 + h)} for k in range(n)]


def test_lone_panel_goes_blocks_on_the_face_stay():
    block = _row(0, 0, 4)
    lone = _row(10, 0, 1)
    pair = _row(0, 5, 2)
    kept = pf.enforce_arrays(block + lone + pair)
    assert len(kept) == 6 and all(p in block + pair for p in kept)


def test_face_with_under_three_goes_a_row_of_three_stays():
    assert pf.enforce_arrays(_row(0, 0, 2)) == []
    assert pf.enforce_arrays(_row(0, 0, 2) + _row(10, 0, 1)) == []
    assert len(pf.enforce_arrays(_row(0, 0, 3))) == 3


def test_pair_stays_only_on_a_small_face():
    pair = _row(0, 0, 2)
    assert pf.enforce_arrays(pair, face_area=8.7) == pair      # 4746517's gable halves
    assert pf.enforce_arrays(pair, face_area=30.0) == []
    assert pf.enforce_arrays(_row(0, 0, 1), face_area=8.7) == []


def test_two_blocks_of_three_split_by_a_skylight_stay():
    # 4747521: 3 + 3 on one face, 2 m apart, used to be dropped whole
    assert len(pf.enforce_arrays(_row(0, 0, 3) + _row(0, 3.7, 3))) == 6


def test_two_rows_touching_are_one_array():
    rows = _row(0, 0, 2) + _row(0, 1.72, 2)     # 2 x 2, rows 2 cm apart
    assert len(pf.enforce_arrays(rows)) == 4


def test_row_with_one_skipped_panel_is_one_array():
    # 2 + (vent) + 3 along one rail; 5370794 lost a whole face to this split
    w, gap = 1.13, 0.02
    row = _row(0, 0, 6, w=w, gap=gap)
    del row[2]
    assert len(pf.enforce_arrays(row)) == 5


def test_off_switch_keeps_everything():
    pf.RACKING = False
    try:
        assert len(pf.enforce_arrays(_row(0, 0, 1))) == 1
    finally:
        pf.RACKING = True


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("  pass ", name)
