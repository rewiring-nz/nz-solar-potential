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


def _grid(cells, w=1.0, h=1.7, gap=0.02):
    return [{"geometry": box(c * (w + gap), r * (h + gap), c * (w + gap) + w, r * (h + gap) + h)}
            for r, c in cells]


def test_installer_rows_drop_a_bump_keep_steps_and_an_l():
    pf.RECT_ARRAYS = True
    try:
        block = [(r, c) for r in range(2) for c in range(4)]
        bump = [(2, 1)]                                   # one panel stuck on top
        assert len(pf.shape_arrays(_grid(block + bump))) == 8
        ell = block + [(2, 0), (3, 0), (2, 1), (3, 1)]    # 2x4 plus a 2x2 on one end
        assert len(pf.shape_arrays(_grid(ell))) == 12
        hip = [(0, c) for c in range(6)] + [(1, c) for c in range(5)] + [(2, c) for c in range(4)]
        assert len(pf.shape_arrays(_grid(hip))) == 15     # stepped rows stay as steps
        column = [(r, 0) for r in range(4)]               # one portrait column up a narrow face
        assert len(pf.shape_arrays(_grid(column))) == 4
        narrow = [(0, 0), (0, 1), (1, 0), (2, 0)]         # tidying would leave 2: keep the face as it was
        assert len(pf.shape_arrays(_grid(narrow))) == 4
    finally:
        pf.RECT_ARRAYS = True


def test_a_partial_system_is_whole_rows_in_one_block():
    # 12 Brunswick St: half the system came out as scattered singles. With
    # shading noise only, the first 40 of a 6 x 20 array are two whole rows
    import numpy as np
    rng = np.random.default_rng(1)
    arr = []
    for r in range(6):
        for c in range(20):
            arr.append({"geometry": box(c * 1.15, r * 2.0, c * 1.15 + 1.13, r * 2.0 + 1.96),
                        "poa_kwh_m2_yr": 1500 + rng.normal(0, 15), "facet_key": 0, "order": r * 20 + c})
    rows = sorted({int(round(p["geometry"].bounds[1] / 2.0)) for p in pf._block_order(arr, "poa_kwh_m2_yr")[:40]})
    assert len(rows) == 2 and rows[1] - rows[0] == 1


def test_off_switch_keeps_everything():
    pf.RACKING = False
    try:
        assert len(pf.enforce_arrays(_row(0, 0, 1))) == 1
    finally:
        pf.RACKING = True


def test_panels_pack_at_their_true_pitch():
    # 1.134 m panels on a 0.1 m grid were rounded to 1.10: ten fitted in
    # 11.2 m where only nine real panels do
    import numpy as np
    occ = np.ones((20, 112), dtype=bool)          # 11.2 m by 2.0 m of clear roof
    placed, pw, ph = pf._pack_orientation(occ, 0.1, 1.134, 1.961)
    assert len(placed) == 9
    widths = {round((c1 - c0) * 0.1, 3) for _, c0, _, c1 in placed}
    heights = {round((r1 - r0) * 0.1, 3) for r0, _, r1, _ in placed}
    assert widths == {1.134} and heights == {1.961}
    lefts = sorted(c0 for _, c0, _, _ in placed)
    assert all(abs((b - a) * 0.1 - 1.134) < 1e-9 for a, b in zip(lefts, lefts[1:]))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("  pass ", name)
