"""combine_regions refuses regions emitted under different numeric PV
assumptions (the served assumptions.json comes from one region)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.combine_regions import mixed_assumptions


def test_same_assumptions_pass():
    a = {"system_derate_pct": 16.4, "panel_rated_power_w": 500, "notes": "x"}
    assert mixed_assumptions([{"region": "a", "assumptions": a},
                              {"region": "b", "assumptions": dict(a, notes="y")}]) == {}


def test_different_derate_is_named():
    out = mixed_assumptions([
        {"region": "a", "assumptions": {"system_derate_pct": 16.4}},
        {"region": "b", "assumptions": {"system_derate_pct": 14.0}},
        {"region": "c", "assumptions": {"system_derate_pct": 16.4}}])
    assert out == {"system_derate_pct": {16.4: ["a", "c"], 14.0: ["b"]}}


def test_region_without_assumptions_is_ignored():
    assert mixed_assumptions([{"region": "a", "assumptions": {"system_derate_pct": 16.4}},
                              {"region": "b"}]) == {}


if __name__ == "__main__":
    test_same_assumptions_pass()
    test_different_derate_is_named()
    test_region_without_assumptions_is_ignored()
    print("combine assumptions: 3 checks passed")
