"""Identical roofs are found and share one reading; an outline offset from
its roof is slid onto it, and only when the slide is a clear win."""
import json
import math
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon, box
from shapely.affinity import rotate, translate

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.repeat_roofs as rr
import src.outline_fit as of


class _Cloud:
    """points_in_bbox / ground_points_in_bbox over fixed arrays."""
    def __init__(self, roof, ground):
        self.roof, self.ground = roof, ground

    def points_in_bbox(self, x0, y0, x1, y1, building_only=True):
        p = self.roof
        return p[(p[:, 0] >= x0) & (p[:, 0] <= x1) & (p[:, 1] >= y0) & (p[:, 1] <= y1)]

    def ground_points_in_bbox(self, x0, y0, x1, y1):
        p = self.ground
        return p[(p[:, 0] >= x0) & (p[:, 0] <= x1) & (p[:, 1] >= y0) & (p[:, 1] <= y1)]


RNG = np.random.default_rng(7)


def _townhouse(ox, oy, mirror=False, base=10.0, w=12.0, d=13.0):
    """The Hallenstein St roof: a 2 deg membrane falling south, with an
    8 deg strip along the north edge and a 4 m-long box on its east half
    that only a strip-length laying puts in the right place. Returns
    (outline, returns)."""
    x = RNG.uniform(0, w, 3000)
    y = RNG.uniform(0, d, 3000)
    z = base + np.tan(math.radians(2)) * y
    strip = y > d - 4.5
    z = np.where(strip, base + np.tan(math.radians(2)) * (d - 4.5)
                 + np.tan(math.radians(8)) * (y - (d - 4.5)), z)
    lump = (x > 7) & (x < 11) & (y > 3) & (y < 5)
    z = z + np.where(lump, 0.8, 0.0)
    z = z + RNG.normal(0, 0.03, len(z))
    if mirror:
        x = w - x
    return box(ox, oy, ox + w, oy + d), np.c_[x + ox, y + oy, z]


def _flat(ox, oy, w=12.0, d=13.0, base=10.0):
    x = RNG.uniform(0, w, 3000)
    y = RNG.uniform(0, d, 3000)
    return box(ox, oy, ox + w, oy + d), np.c_[x + ox, y + oy, base + RNG.normal(0, 0.03, 3000)]


def test_copies_match_even_mirrored_and_a_flat_roof_does_not():
    a_geom, a_pts = _townhouse(1000.0, 5000.0)
    b_geom, b_pts = _townhouse(1014.0, 5000.0, mirror=True, base=11.0)
    f_geom, f_pts = _flat(1028.0, 5000.0)
    pc = _Cloud(np.vstack([a_pts, b_pts, f_pts]), np.zeros((0, 3)))
    a, b, f = (rr.roof_surface(pc, g) for g in (a_geom, b_geom, f_geom))
    m = rr.same_roof(a, b)
    assert m is not None, "a mirrored copy on a higher lot is the same roof"
    assert rr.same_roof(a, f) is None, "a flat roof matches everything and is left alone"


def test_a_face_moves_onto_the_same_place_on_the_copy():
    a_geom, a_pts = _townhouse(1000.0, 5000.0)
    b_geom, b_pts = _townhouse(1015.0, 5002.0, mirror=True)
    pc = _Cloud(np.vstack([a_pts, b_pts]), np.zeros((0, 3)))
    a, b = rr.roof_surface(pc, a_geom), rr.roof_surface(pc, b_geom)
    m = rr.match(a, b)
    # the box sits on a's east half; on the mirrored copy it is on the west
    lump_a = [[1007.0, 5003.0], [1011.0, 5003.0], [1011.0, 5005.0], [1007.0, 5005.0]]
    lump_b = Polygon([[1016.0, 5005.0], [1020.0, 5005.0], [1020.0, 5007.0], [1016.0, 5007.0]])
    moved = Polygon(rr.move_faces([lump_a], a, b, m)[0])
    assert moved.intersection(lump_b).area / lump_b.area > 0.8, moved.wkt


def test_set_reading_is_the_one_that_explains_every_copy():
    tmp = Path(tempfile.mkdtemp())
    rr.SELECTED_DIR = tmp
    import geopandas as gpd
    geoms, pts = zip(*[_townhouse(1000.0 + 14 * k, 5000.0) for k in range(3)])
    gdf = gpd.GeoDataFrame({"building_id": [1, 2, 3]}, geometry=list(geoms),
                           crs="EPSG:2193").set_index("building_id", drop=False)
    pc = _Cloud(np.vstack(pts), np.zeros((0, 3)))
    # copy 1 is read right (membrane + strip); copy 2 as one plane; copy 3 has none
    right = [[[1000, 5000], [1012, 5000], [1012, 5008.5], [1000, 5008.5], [1000, 5000]],
             [[1000, 5008.5], [1012, 5008.5], [1012, 5013], [1000, 5013], [1000, 5008.5]]]
    one = [[[1014, 5000], [1026, 5000], [1026, 5013], [1014, 5013], [1014, 5000]]]
    (tmp / "1.json").write_text(json.dumps({"source": "line", "score": 0.61, "faces": right}))
    (tmp / "2.json").write_text(json.dumps({"source": "hypothesis", "score": 0.37, "faces": one}))
    sets, surf, _ = rr.find_sets(gdf, pc)
    assert sets == [[1, 2, 3]], sets
    t, docs = rr.agree(sets[0], gdf, surf, log=lambda *a: None)
    assert t == 1 and set(docs) == {1, 2, 3}, (t, list(docs))
    assert all(len(d["faces"]) == 2 and d["repeat_of"] == 1 for d in docs.values())


def test_a_strip_the_copy_has_beyond_the_reading_joins_its_face():
    from shapely.ops import unary_union
    fp = box(0, 0, 12, 13.6)
    rings = [list(box(0, 0, 12, 8.5).exterior.coords), list(box(0, 8.5, 12, 13).exterior.coords)]
    out = [Polygon(r) for r in rr.fill_footprint(rings, fp)]
    assert len(out) == 2
    assert abs(unary_union(out).area - fp.area) < 0.1
    # a face nested inside another is cut out of it
    nested = [list(fp.exterior.coords), rings[1]]
    out3 = [Polygon(r) for r in rr.fill_footprint(nested, fp)]
    assert len(out3) == 2 and abs(sum(p.area for p in out3) - fp.area) < 0.1, [p.area for p in out3]
    # ...and a face round another comes back as pieces with no holes
    ring_in = list(box(3, 4, 9, 9).exterior.coords)
    out4 = [Polygon(r) for r in rr.fill_footprint([list(fp.exterior.coords), ring_in], fp)]
    assert abs(sum(p.area for p in out4) - fp.area) < 0.1, [p.area for p in out4]
    assert unary_union(out4[1:]).intersection(out4[0]).area < 0.01 or len(out4) >= 3
    # a real extra wing stays for the build to read
    fp2 = fp.union(box(12, 0, 17, 5))
    out2 = [Polygon(r) for r in rr.fill_footprint(rings, fp2)]
    assert abs(unary_union(out2).area - fp.area) < 0.1


def test_roof_partition_reads_the_set_reading_only_when_switched_on():
    import src.roof_partition as rp
    sel, rep = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    (sel / "9.json").write_text(json.dumps({"source": "sam", "score": 0.4, "faces": []}))
    (rep / "9.json").write_text(json.dumps({"source": "repeat", "score": 0.61, "faces": []}))
    old = rp.SELECTED_FACES_DIR, rp.REPEAT_FACES_DIR, rp.USE_REPEAT_ROOFS
    try:
        rp.SELECTED_FACES_DIR, rp.REPEAT_FACES_DIR = sel, rep
        rp.USE_REPEAT_ROOFS = False
        assert rp.selected_score(9) == 0.4 and not rp.selected_agreed(9)
        rp.USE_REPEAT_ROOFS = True
        assert rp.selected_score(9) == 0.61 and rp.selected_agreed(9)
    finally:
        rp.SELECTED_FACES_DIR, rp.REPEAT_FACES_DIR, rp.USE_REPEAT_ROOFS = old


def _offset_roof(dx, dy):
    """A 16 x 12 m roof 6 m up, and its outline traced dx, dy off it."""
    roof = box(2000.0, 6000.0, 2016.0, 6012.0)
    n = 16 * 12 * 6
    x = RNG.uniform(2000.0, 2016.0, n)
    y = RNG.uniform(6000.0, 6012.0, n)
    gx, gy = np.meshgrid(np.arange(1985.0, 2031.0, 1.0), np.arange(5985.0, 6027.0, 1.0))
    ground = np.c_[gx.ravel(), gy.ravel(), np.zeros(gx.size)]
    pc = _Cloud(np.c_[x, y, np.full(n, 6.0)], ground)
    return translate(roof, dx, dy), pc


def test_offset_outline_slides_onto_the_roof():
    outline, pc = _offset_roof(1.0, -0.75)
    m = of.measure(pc, outline, None)
    s = of.accepted(m)
    assert s is not None and abs(s[0] + 1.0) <= 0.13 and abs(s[1] - 0.75) <= 0.13, m


def test_an_outline_on_its_roof_stays_put():
    outline, pc = _offset_roof(0.0, 0.0)
    assert of.accepted(of.measure(pc, outline, None)) is None


def test_a_slide_to_the_edge_of_the_search_is_refused():
    assert of.accepted([of.SHIFT_MAX_M, 0.0, 0.5, 0.9]) is None
    assert of.accepted([0.3, 0.0, 0.5, 0.9]) is None          # too small to be an offset
    assert of.accepted([1.0, 0.0, 0.85, 0.88]) is None        # not a clear win


def test_fitted_outlines_moves_only_when_switched_on():
    import geopandas as gpd
    d = Path(tempfile.mkdtemp())
    (d / "outline_fit.json").write_text(json.dumps({"5": [1.0, 0.0, 0.7, 0.95]}))
    g = gpd.GeoDataFrame({"building_id": [5]}, geometry=[box(0, 0, 10, 10)], crs="EPSG:2193")
    assert of.fitted_outlines(g.copy(), d, use=False).geometry[0].bounds[0] == 0.0
    of._marked = lambda: set()
    assert of.fitted_outlines(g.copy(), d, use=True).geometry[0].bounds[0] == 1.0


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
