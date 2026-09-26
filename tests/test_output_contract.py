"""The lists emit_region ships must be the contract's lists, exactly.

src/output_contract.py freezes the property names the map reads. emit_region
has its own lists (it is what cuts the tiles). If they drift -- a property
added to KEEP in build_building_tiles, a LAYOUT_PROPS edit -- the served
schema changes without anyone deciding it should, which is exactly what the
contract exists to stop. Change both together, bump CONTRACT_VERSION, update
preview.html. tests/synthetic/run.py checks a real region's output too.

Run: .venv/bin/python tests/test_output_contract.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from src import output_contract as oc
    from src.emit_region import SLIM_KEEP, LAYOUT_PROPS
    from src.split_building_detail import DETAIL_KEYS
    bad = []
    if list(SLIM_KEEP) != oc.BUILDING_ALLOWED:
        bad.append(f"building tile properties drifted: +{sorted(set(SLIM_KEEP) - set(oc.BUILDING_ALLOWED))} "
                   f"-{sorted(set(oc.BUILDING_ALLOWED) - set(SLIM_KEEP))} (or reordered)")
    if list(LAYOUT_PROPS) != oc.LAYOUT_ALLOWED:
        bad.append(f"layout tile properties drifted: {LAYOUT_PROPS} vs {oc.LAYOUT_ALLOWED}")
    if set(DETAIL_KEYS) | {"mv"} != set(oc.DETAIL_ALLOWED):
        bad.append(f"detail keys drifted: {DETAIL_KEYS} + mv vs {oc.DETAIL_ALLOWED}")
    for b in bad:
        print("  FAIL  " + b)
    print("  pass  emit_region ships exactly the contract" if not bad else "")
    return 1 if bad else 0


def test_no_estimate_layout_feature_is_valid():
    from src.output_contract import validate_layout_feature

    valid = {"kind": "no_estimate", "building_id": 123, "btype": "home"}
    assert validate_layout_feature("layout", valid) is None
    assert "missing ['building_id']" in validate_layout_feature(
        "layout", {"kind": "no_estimate", "btype": "home"})
    assert "extra ['unexpected']" in validate_layout_feature(
        "layout", {**valid, "unexpected": 1})
    assert "not in the layout contract" in validate_layout_feature(
        "layout", {"kind": "mystery", "building_id": 123})
    assert "layer 'wrong'" in validate_layout_feature("wrong", valid)


if __name__ == "__main__":
    test_no_estimate_layout_feature_is_valid()
    sys.exit(main())
