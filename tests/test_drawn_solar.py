"""Existing solar marked in the labelling tool is panel area: it never reaches
the fitter as an obstruction (drawn_obstruction_polys) and is returned
separately for the build to cut out of detected obstructions."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import roof_line_source as rls  # noqa: E402

X, Y = 1258000.0, 5004000.0
SOLAR = {"shape": "polygon", "solar": True,
         "ring": [[X, Y], [X + 4, Y], [X + 4, Y + 2], [X, Y + 2]]}
VENT = {"shape": "rect", "ring": [[X + 6, Y], [X + 7, Y], [X + 7, Y + 1], [X + 6, Y + 1]]}


def _labels(tmp_path, monkeypatch, rec):
    p = tmp_path / "roof_labels.json"
    p.write_text(json.dumps({"buildings": {"1": rec}}))
    monkeypatch.setattr(rls, "LABELS_PATH", p)
    monkeypatch.setattr(rls, "_LABELS_CACHE", [None])
    monkeypatch.setenv("SOLAR_MARKUP_FRAME", "photo")
    monkeypatch.delenv("SOLAR_HIDE_LABELS", raising=False)


def test_solar_is_not_an_obstruction(tmp_path, monkeypatch):
    _labels(tmp_path, monkeypatch, {"lines": [], "obstructions": [SOLAR, VENT]})
    obs = rls.drawn_obstruction_polys(1)
    assert len(obs) == 1 and abs(obs[0].area - 1.0) < 1e-6
    sol = rls.drawn_solar_polys(1)
    assert len(sol) == 1 and abs(sol[0].area - 8.0) < 1e-6


def test_void_flag_ignores_solar(tmp_path, monkeypatch):
    _labels(tmp_path, monkeypatch, {"lines": [], "obstructions": [SOLAR], "problem": "absent"})
    assert rls.drawn_solar_polys(1) == []


if __name__ == "__main__":
    from tests.fixture_runner import run
    run(globals())
