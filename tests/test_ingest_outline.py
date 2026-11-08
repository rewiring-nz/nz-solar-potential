"""ingest_labels keeps a drawn outline (`outline`, the ring the build uses in
place of the surveyed one) and the single outline lines it was cut from
(`olines`, which let a re-opened roof add or remove a part) when it writes
data/roof_labels.json, and rejects an outline that is not a ring on its roof."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import ingest_labels as ing  # noqa: E402

X, Y = 1258000.0, 5004000.0
OUTLINE = [[X + 1, Y + 1], [X + 13, Y + 1], [X + 13, Y + 9], [X + 1, Y + 9], [X + 1, Y + 1]]
OLINES = [{"a": [X + 7, Y + 1], "b": [X + 7, Y + 9]}]
KNOWN = {"7": {"area": "pilot", "bounds": [X - 5, Y - 5, X + 20, Y + 15]}}


def _ingest(tmp_path, monkeypatch, rec):
    monkeypatch.setattr(ing, "LABELS", tmp_path / "roof_labels.json")
    monkeypatch.setattr(ing, "PROGRESS", tmp_path / "label_progress.json")
    monkeypatch.setattr(ing, "bundle_roofs", lambda areas=(): KNOWN)
    monkeypatch.setattr(ing, "_queue_n", lambda: 1)
    f = tmp_path / "returned.json"
    f.write_text(json.dumps({"saved_utc": "2026-11-08T00:00:00Z",
                             "buildings": {"7": rec}}))
    monkeypatch.setattr(sys, "argv", ["ingest_labels.py", str(f)])
    assert ing.main() == 0
    return json.loads((tmp_path / "roof_labels.json").read_text())["buildings"]


def test_outline_and_olines_are_kept(tmp_path, monkeypatch):
    held = _ingest(tmp_path, monkeypatch, {
        "area": "pilot", "lines": [], "obstructions": [], "nopanel": [],
        "outline": OUTLINE, "olines": OLINES})
    assert held["7"]["outline"] == OUTLINE
    assert held["7"]["olines"] == OLINES


def test_outline_alone_is_a_marked_roof():
    rec = {"area": "pilot", "lines": [], "outline": OUTLINE}
    assert ing.check_building("7", rec, KNOWN) == []


def test_outline_off_the_roof_is_rejected():
    far = [[x + 500, y] for x, y in OUTLINE]
    probs = ing.check_building("7", {"area": "pilot", "outline": far}, KNOWN)
    assert any("outside" in p for p in probs)


if __name__ == "__main__":
    from tests.fixture_runner import run
    run(globals())
