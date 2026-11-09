"""The vision referee's pick is read before the other readings only with
SOLAR_READING_REFEREE=1 (src/reading_referee.py)."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shapely.geometry import box
import src.reading_referee as RR
import src.roof_partition as RP


def _setup(on):
    tmp = Path(tempfile.mkdtemp())
    RR.REFEREE_FACES_DIR = tmp / "referee_faces"
    RR.USE_READING_REFEREE = on
    sel = tmp / "selected_faces"
    sel.mkdir()
    (sel / "7.json").write_text(json.dumps({"source": "sam", "score": 0.4, "faces": []}))
    RP.SELECTED_FACES_DIR = sel
    RP.USE_REPEAT_ROOFS = False


def test_referee_pick_wins_when_on():
    _setup(on=True)
    doc = RR.write(7, [box(0, 0, 5, 4), box(5, 0, 10, 4)], "lidar_partition", "claude-opus-5-5")
    assert doc["score"] == 1.0 and len(doc["faces"]) == 2
    got = RP.selected_doc(7)
    assert got["source"] == "referee" and got["picked"] == "lidar_partition", got
    assert RP.selected_score(7) == 1.0          # trusted: does not compete


def test_referee_pick_ignored_when_off():
    _setup(on=False)
    RR.write(7, [box(0, 0, 5, 4)], "lidar_partition", "claude-opus-5-5")
    assert RP.selected_doc(7)["source"] == "sam"


def test_tiny_pick_writes_nothing():
    _setup(on=True)
    assert RR.write(8, [box(0, 0, 0.5, 0.5)], "form:hip", "m") is None
    assert not RR.path(8).exists()


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
