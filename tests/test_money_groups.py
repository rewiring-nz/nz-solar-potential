"""Money groups: the per-cell sums the map prices zoomed-out savings from.

emit_region sums each cell's roofs by (sun region, size band, height band for
non-homes) as flat "mg:<key>:<field>" entries, so combine_regions adds them
across regions like every other cell sum; combine_regions packs them into one
compact "mg" list per cell.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.emit_region import money_group
from src.combine_regions import _money_groups


def _roof(panels, btype="home", height=4.0, area=120.0):
    p = {"btype": btype, "height_m": height, "facet_area_m2": area}
    for d in range(10, 101, 10):
        p[f"fill_panels_{d}"] = panels * d / 100
        p[f"fill_kwh_{d}"] = panels * d * 4
    return p


def test_homes_band_by_size_only():
    k1, _ = money_group(_roof(20, height=3), "wanaka_town")
    k2, _ = money_group(_roof(21, height=9), "wanaka_town")
    k3, _ = money_group(_roof(40), "wanaka_town")
    assert k1 == k2 == "wanaka_town|8|", (k1, k2)
    assert k3 != k1


def test_non_homes_band_by_height_too():
    k1, _ = money_group(_roof(20, "business", 3.5), "qt")
    k2, _ = money_group(_roof(20, "business", 7.2), "qt")
    assert k1 == "qt|8|3" and k2 == "qt|8|7", (k1, k2)


def test_region_is_part_of_the_key():
    assert money_group(_roof(20), "a")[0] != money_group(_roof(20), "b")[0]


def test_no_layout_no_group():
    assert money_group({"btype": "home"}, "a") is None
    assert money_group({"btype": "home", "fill_panels_100": 0}, "a") is None


def test_sums_pack_into_rows():
    v = {"n": 3}
    for r in (_roof(20), _roof(21), _roof(40)):
        key, sums = money_group(r, "qt")
        for f, x in sums.items():
            v[f"mg:{key}:{f}"] = v.get(f"mg:{key}:{f}", 0.0) + x
    rows = _money_groups(v)
    assert [r[0] for r in rows] == ["qt|10|", "qt|8|"], rows
    small = rows[1]
    assert small[1] == 2 and small[2] == 240 and small[3] == 8.0, small
    assert len(small) == 4 + 10 + 10
    assert small[4 + 9] == 41.0 and small[14 + 9] == 41 * 400, small
    assert _money_groups({"n": 1}) == []


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
