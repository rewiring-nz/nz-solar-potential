"""Every roof in a build, checked against rules a correct layout cannot break.

    python tools/layout_health.py pilot                 # one region
    python tools/layout_health.py                       # every built region
    python tools/layout_health.py pilot --worst 40      # and list the worst roofs
    python tools/layout_health.py --json data/layout_health.json

WHY. Layout mistakes were found by eye -- a screenshot, a bug form, a case
roof -- one roof at a time, which cannot keep up with a district, let alone
the country (Josh, 18 Oct: "very clear panel layout issues on many
rooftops"). These checks need no markup and no judgement: each is
something a sound layout never does, so every hit is a defect, and the
rate of each across a build is a number a change can be held to.

    OFF THE BUILDING     a panel more than 10% outside the building outline
    ACROSS A FACE EDGE   a panel split between two roof faces (over a ridge,
                         hip or valley): no face holds 90% of it
    ON AN OBSTRUCTION    a panel overlapping a marked obstruction
    PANELS OVERLAP       two panels sharing more than 5% of their area
    DISORDERLY ROWS      panels on one face not parallel (spread of their
                         angles over 3 degrees): rows are laid square
    RAGGED FACE          a face whose outline is jagged (area / hull < 0.75)
                         or has a vertex every metre or less along its edge
    SLIVER FACES         faces under 4 m2 on a roof (real roofs rarely have them)
    EMPTY GOOD FACE      a sunny face of 25 m2 or more, under 40 degrees,
                         with no panels at all
    OUTLINE OVER GROUND  more than 12% of the building outline sits within
                         1 m of the ground around it -- the outline is off
                         the real roof (13 Douglas Ave, 42 Suburb St), so
                         every face and panel inside it is shifted too
    POOR LIDAR FIT       under 60% of the roof's laser returns lie on the
                         faces drawn: the faces are not the roof
    FACES STEP APART     two faces sharing an edge, each drawn on its own
                         plane (slope/aspect, height from the DSM), step
                         apart by over 0.4 m along it where the DSM shows
                         one continuous roof: the edge is off the line where
                         the planes cross, or a tilt is wrong (3B Inverness
                         Crescent: ridge 1.1 m off, 0.46 m step). A step the
                         DSM also shows (a lower wing) is not counted.
    ROOF BEYOND OUTLINE  smooth roof-height LiDAR, grown out from inside the
                         outline (stopping at ground, trees and other
                         buildings), covers more than max(10 m2, 8%) outside
                         it: the outline is smaller than the roof (13 Douglas
                         Avenue: 21 m2, 13%). A wing built after the LiDAR is
                         not in it either; the photo building finder covers
                         those (42 Suburb Street).
                         OFF BY DEFAULT (--experimental): checked by eye on 18
                         Oct, most hits were tree canopy and rising hillside --
                         it needs the bare-earth DEM, not a ground guessed from
                         the lowest returns nearby.

The first group can only fire if the layout code breaks its own rules (it
places panels inside its faces and outline by construction); the outline
and fit checks compare against the LiDAR, which is where the visible
mistakes start.

Rates are per 1,000 panels (panel checks) or per 100 roofs (roof checks),
so regions of any size compare. The worst roofs are ranked by a weighted
count of their defects, for review and for the case list.
"""

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OFF_ROOF_SHARE = 0.10
FACE_HOLD_SHARE = 0.90
OBSTRUCTION_SHARE = 0.03
OVERLAP_SHARE = 0.05
ROW_SPREAD_DEG = 3.0
RAGGED_SOLIDITY = 0.75
RAGGED_M_PER_VERTEX = 1.0
# A face is bent where its convex hull leaves a notch. A notch OUTSIDE the
# building is the building's shape -- a flat roof over an L-shaped house is
# an L -- and is fine. A notch INSIDE the building is another face biting
# into this one: a seam that wanders across the roof. Only those count
# (19 Oct: most faces the solidity rule flagged in Arrowtown were flat and
# single-slope roofs on L- and U-shaped buildings, drawn correctly).
# "Lines not following the ridges", measured: two pitched faces meet where
# their planes cross, so a seam's distance from that crossing is how far the
# drawn line is off the real ridge, hip or valley. Seams whose planes do not
# cross near them (a step between storeys) are not seams of that kind and
# are skipped. Needs the "plane" the layout stage writes (from v52).
SEAM_MIN_SLOPE = 10.0
SEAM_MIN_LEN_M = 2.0
SEAM_OFF_M = 0.5
# "Faces that don't meet in 3D", measured. The 3D view (buildings3d.js) draws
# each face on a plane with the face's own slope/aspect and a height set by
# the median DSM inside it; where two faces share an edge, the difference of
# their two planes along it is the step or wedge it shows (the plane gap).
# Whether the roof really steps there is read from the DSM across the same
# line, STEP_REACH_M either side, against two surfaces built from the same two
# planes: DRAWN, each plane on its own side of the drawn edge (what the 3D view
# shows), and JOINED, the two planes meeting where they cross (the lower
# envelope at a ridge or hip, the upper at a valley). At a real step -- a
# parapet, a lower wing -- the drawn surface fits the DSM and the joined one
# cannot. Where the DSM is one continuous roof, the joined surface fits and
# the drawn one misses it on a strip beside the edge: the edge is off the line
# where the planes cross, or a tilt is wrong. Comparing the planes with each
# side's DSM alone cannot tell these apart: a ridge drawn 1.4 m off the crest
# leaves each plane fitting its own side and the DSM, seen through those
# tilts, appears to step by exactly the plane gap (3B Inverness Crescent,
# #4719777, 0.49 m).
# The two surfaces differ only between the edge and the planes' crossing, so
# they are compared over that strip and STEP_PAD_M beyond it (at most
# STEP_REACH_M). A station is a crease when the joined surface's mean
# |DSM - surface| there is within STEP_JOINED_FIT_M (it is the roof) and either
# under STEP_FIT_RATIO of the drawn one's or the planes cross within
# STEP_NEAR_M of the edge -- closer than a 1 m DSM can put a wall, so no step
# there can be told from the crease. Where neither surface fits (a face that
# spans two levels of a big roof: 16 m apart at its edge on #5371126) the
# edge is not judged here; POOR LIDAR FIT covers those faces. An edge counts
# when most stations are creases and the plane gap at the median station is
# over STEP_OFF_M. The 3D view closes gaps under 0.6 m at shared vertices, so
# a 0.4-0.6 m gap shows as a twisted wedge rather than a ledge.
# Faces are measured where the layout puts them, in the LiDAR's frame. The
# map's tiles move each building by its photo lean (src/emit_region.py), so
# a view that samples the DSM at the tiles' faces sees more steps: on the
# Queenstown regions, 14.5% of edges at the shifted faces, 6.8% unshifted.
STEP_OFF_M = 0.40
STEP_MIN_LEN_M = 1.5
STEP_STATION_M = 0.5
STEP_REACH_M = 2.5
STEP_PAD_M = 0.75
STEP_NEAR_M = 0.5
STEP_FIT_RATIO = 0.7
STEP_JOINED_FIT_M = 0.15
NOTCH_MIN_M2 = 1.0
NOTCH_MIN_DEPTH_M = 0.5
NOTCH_INSIDE_SHARE = 0.5
SLIVER_M2 = 4.0
EMPTY_FACE_M2 = 25.0
EMPTY_MAX_SLOPE = 40.0
EMPTY_MIN_POA = 1100.0      # kWh/m2/yr after shading: a face worth panels
GROUND_RING = (3.0, 8.0)    # ground taken from the lowest returns this far outside
GROUND_PCT = 10
OVER_GROUND_M = 1.0
OVER_GROUND_SHARE = 0.12
POOR_FIT = 0.60
BEYOND_M2, BEYOND_SHARE = 8.0, 0.05
EAVE_BAND_M = 0.8           # roof within this of the outline is eave overhang, not a missing wing
GROW_REACH_M = 12.0
EXPERIMENTAL = False

PANEL_CHECKS = ["off_building", "across_edge", "on_obstruction", "overlap"]
# Racking (Josh, 20 Oct): one orientation per face, no lone panel, 3+ on a face (a pair on a small face).
ARRAY_MIN = 3   # same as src/panel_fitting.py ARRAY_MIN_PANELS
ARRAY_PAIR_FACE_M2 = 12.0       # same as src/panel_fitting.py
ARRAY_TOUCH_M = 1.30            # same as src/panel_fitting.py: a rail runs on past a one-panel gap (vent)
ROOF_CHECKS = ["mixed_orientation", "small_array", "disorderly_rows", "ragged_face", "sliver_faces", "empty_good_face",
               "outline_over_ground", "poor_fit", "roof_beyond_outline", "seam_off_ridge", "seam_step"]
# reported for comparison with runs before 19 Oct, not scored
INFO_CHECKS = ["concave_face_any"]
WEIGHT = {"mixed_orientation": 4, "small_array": 3, "off_building": 3, "across_edge": 3, "on_obstruction": 2, "overlap": 2,
          "disorderly_rows": 4, "ragged_face": 2, "sliver_faces": 1, "empty_good_face": 4,
          "outline_over_ground": 6, "poor_fit": 4, "roof_beyond_outline": 6,
          "seam_off_ridge": 4, "seam_step": 4}
LABEL = {"mixed_orientation": "portrait and landscape mixed on a face", "small_array": "lone panel, or under 3 on a face (2 on a small face)",
         "off_building": "off the building", "across_edge": "across a face edge",
         "on_obstruction": "on an obstruction", "overlap": "panels overlap",
         "disorderly_rows": "disorderly rows", "ragged_face": "ragged face",
         "sliver_faces": "sliver faces", "empty_good_face": "empty good face",
         "outline_over_ground": "outline over ground", "poor_fit": "poor LiDAR fit",
         "roof_beyond_outline": "roof beyond outline", "seam_off_ridge": "seam off the ridge",
         "seam_step": "faces step apart where the LiDAR does not"}


def _tilt(q):
    """(a, b) of z = a x + b y + c from a face's slope and aspect, as buildings3d.js does."""
    s, asp = q.get("slope_deg"), q.get("aspect_deg")
    if s is None or asp is None:
        return None
    t = math.tan(math.radians(s))
    A = math.radians(asp)
    return -t * math.sin(A), -t * math.cos(A)


def _face_offset(g, ab, H, step=0.6, inset=0.45):
    """The 3D view's height for a face: the median DSM residual off its tilt,
    sampled on a `step` grid `inset` in from the edges, outliers rejected."""
    import numpy as np
    import shapely
    core = g.buffer(-inset)
    if core.is_empty or core.area < 0.5:
        core, step = g.buffer(-0.15), 0.35
    if core.is_empty:
        return None
    x0, y0, x1, y1 = core.bounds
    xs, ys = np.meshgrid(np.arange(math.ceil(x0 / step) * step, x1 + 1e-9, step),
                         np.arange(math.ceil(y0 / step) * step, y1 + 1e-9, step))
    xs, ys = xs.ravel(), ys.ravel()
    m = shapely.contains_xy(core, xs, ys)
    xs, ys = xs[m], ys[m]
    if xs.size < 2:
        return None
    z = H(xs, ys)
    ok = np.isfinite(z)
    if ok.sum() < 2:
        return None
    r = z[ok] - (ab[0] * xs[ok] + ab[1] * ys[ok])
    c = float(np.median(r))
    for _ in range(3):
        lim = max(0.25, 2.5 * 1.4826 * float(np.median(np.abs(r - c))))
        k = r[np.abs(r - c) <= lim]
        if not k.size or k.size == r.size:
            break
        r, c = k, float(np.median(k))
    return c


def _seam_steps(faces, H):
    """One record per shared edge of at least STEP_MIN_LEN_M:
    {i, j, length, gap, drawn, joined, crease, miss, shift, mid}.
    gap: median height of face i's plane minus face j's along the edge (the
    3D view's step); drawn / joined: median mean |DSM - surface| across the
    edge, over the strip out to the planes' crossing, for the two surfaces;
    crease: share of stations judged one continuous roof (see STEP_*); miss:
    the median |gap| when most stations are creases, else 0; shift: median
    distance from the edge to where the planes cross.
    faces: [(polygon, properties)] in NZTM; H(xs, ys) -> DSM heights (NaN off-survey)."""
    import numpy as np
    import shapely
    from shapely.ops import linemerge
    planes = []
    for g, q in faces:
        ab = _tilt(q)
        c = _face_offset(g, ab, H) if ab is not None and not g.is_empty else None
        planes.append(None if c is None else np.array([ab[0], ab[1], c]))
    out = []
    ts = np.concatenate([-np.arange(STEP_REACH_M, 0, -0.25), np.arange(0.25, STEP_REACH_M + 1e-9, 0.25)])
    for i in range(len(faces)):
        for j in range(i + 1, len(faces)):
            pi, pj = planes[i], planes[j]
            if pi is None or pj is None:
                continue
            gi, gj = faces[i][0], faces[j][0]
            if gi.distance(gj) > 0.1:
                continue
            seam = gi.boundary.intersection(gj.buffer(0.1))
            lines = [s for s in getattr(seam, "geoms", [seam]) if s.geom_type in ("LineString", "MultiLineString")]
            if not lines:
                continue
            seam = linemerge(lines) if len(lines) > 1 else lines[0]
            pieces = [s for s in getattr(seam, "geoms", [seam]) if s.length >= STEP_STATION_M]
            if sum(s.length for s in pieces) < STEP_MIN_LEN_M:
                continue
            dp = pi - pj
            grad = math.hypot(dp[0], dp[1])
            gaps, rd, rj, shift, crease = [], [], [], [], []
            for s in pieces:
                for d in np.arange(STEP_STATION_M / 2, s.length, STEP_STATION_M):
                    p = s.interpolate(d)
                    q2 = s.interpolate(min(d + 0.2, s.length)); q1 = s.interpolate(max(d - 0.2, 0))
                    tx, ty = q2.x - q1.x, q2.y - q1.y
                    L = math.hypot(tx, ty)
                    if L < 1e-6:
                        continue
                    xs, ys = p.x - ty / L * ts, p.y + tx / L * ts
                    in_i = shapely.contains_xy(gi, xs, ys)
                    in_j = shapely.contains_xy(gj, xs, ys) & ~in_i
                    gap = float(dp[0] * p.x + dp[1] * p.y + dp[2])
                    off = abs(gap) / grad if grad > 1e-3 else np.inf
                    z = H(xs, ys)
                    ok = (in_i | in_j) & np.isfinite(z) & (np.abs(ts) <= min(off + STEP_PAD_M, STEP_REACH_M))
                    if (ok & in_i).sum() < 2 or (ok & in_j).sum() < 2:
                        continue
                    zi = pi[0] * xs + pi[1] * ys + pi[2]
                    zj = pj[0] * xs + pj[1] * ys + pj[2]
                    drawn = np.where(in_i, zi, zj)
                    fit = lambda surf: float(np.mean(np.abs(z[ok] - surf[ok])))
                    gaps.append(gap)
                    shift.append(off)
                    rd.append(fit(drawn))
                    rj.append(min(fit(np.minimum(zi, zj)), fit(np.maximum(zi, zj))))
                    crease.append(rj[-1] <= STEP_JOINED_FIT_M
                                  and (off <= STEP_NEAR_M or rj[-1] < STEP_FIT_RATIO * rd[-1]))
            if len(gaps) < max(2, int(STEP_MIN_LEN_M / STEP_STATION_M)):
                continue
            gaps, rd, rj = np.array(gaps), np.array(rd), np.array(rj)
            crease = float(np.mean(crease))
            gap = float(np.median(gaps))
            mid = seam.interpolate(0.5, normalized=True)
            out.append({"i": i, "j": j, "length": round(sum(s.length for s in pieces), 2),
                        "gap": round(gap, 2), "drawn": round(float(np.median(rd)), 2),
                        "joined": round(float(np.median(rj)), 2), "crease": round(crease, 2),
                        "miss": round(float(np.median(np.abs(gaps))), 2) if crease > 0.5 else 0.0,
                        "shift": round(float(np.median(shift)), 2), "mid": (mid.x, mid.y)})
    return out


def _dsm_sampler(dsm, geom, pad=3.0):
    """Bilinear DSM heights around one roof: H(xs, ys) -> z (NaN off-survey)."""
    import numpy as np
    from rasterio.windows import from_bounds
    from scipy.ndimage import map_coordinates
    # whole pixels: read() snaps a fractional window but window_transform()
    # keeps the fraction, which would slide the heights up to a pixel
    win = from_bounds(*geom.buffer(pad).bounds, transform=dsm.transform)
    win = win.round_offsets(op="floor").round_lengths(op="ceil")
    z = dsm.read(1, window=win, boundless=True, fill_value=np.nan).astype("float64")
    if dsm.nodata is not None:
        z[z == dsm.nodata] = np.nan
    inv = ~dsm.window_transform(win)

    def H(xs, ys):
        c, r = inv * (np.asarray(xs, float), np.asarray(ys, float))
        return map_coordinates(z, [r - 0.5, c - 0.5], order=1, mode="constant", cval=np.nan)
    return H


def _seam_offsets(faces):
    """[(length m, median offset m)] for each pitched seam whose planes cross near it.
    faces: [(polygon, properties)] in NZTM, properties carrying "plane"."""
    import math
    out = []
    for i in range(len(faces)):
        for j in range(i + 1, len(faces)):
            (gi, qi), (gj, qj) = faces[i], faces[j]
            pi, pj = qi.get("plane"), qj.get("plane")
            if not pi or not pj:
                continue
            if min(qi.get("slope_deg") or 0, qj.get("slope_deg") or 0) < SEAM_MIN_SLOPE:
                continue
            seam = gi.boundary.intersection(gj.buffer(0.05))
            if seam.is_empty or seam.length < SEAM_MIN_LEN_M:
                continue
            A, B = pi[0] - pj[0], pi[1] - pj[1]
            grad = math.hypot(A, B)
            if grad < 0.05:
                continue
            offs = []
            for k in range(11):
                p = seam.interpolate(k / 10, normalized=True)
                dz = (pi[0] * p.x + pi[1] * p.y + pi[2]) - (pj[0] * p.x + pj[1] * p.y + pj[2])
                offs.append(abs(dz) / grad)
            offs.sort()
            med = offs[5]
            # a crossing further off than the faces are wide is a step, not a ridge
            if med > 0.5 * math.sqrt(min(gi.area, gj.area)):
                continue
            out.append((seam.length, med))
    return out


def _inner_notch(face, foot):
    """True when the face has a real notch that lies inside the building."""
    from shapely.geometry import Point
    hull = face.convex_hull
    gap = hull.difference(face)
    for n in getattr(gap, "geoms", [gap]):
        if n.geom_type != "Polygon" or n.area < NOTCH_MIN_M2:
            continue
        if n.exterior.distance(hull.exterior) > 0:   # not a notch, a hole
            continue
        depth = max(hull.exterior.distance(Point(c)) for c in n.exterior.coords)
        if depth < NOTCH_MIN_DEPTH_M:
            continue
        inside = n.intersection(foot).area / n.area if foot is not None else 1.0
        if inside >= NOTCH_INSIDE_SHARE:
            return True
    return False


def _angle(poly):
    """Orientation of a panel rectangle's long side, folded to [0, 90)."""
    r = poly.minimum_rotated_rectangle
    xs, ys = r.exterior.coords.xy
    best = None
    for i in range(4):
        dx, dy = xs[i + 1] - xs[i], ys[i + 1] - ys[i]
        L = math.hypot(dx, dy)
        if best is None or L > best[0]:
            best = (L, math.degrees(math.atan2(dy, dx)) % 90.0)
    return best[1]


def _spread(angles):
    """Spread of folded angles (degrees), robust to the 0/90 wrap."""
    if len(angles) < 3:
        return 0.0
    import numpy as np
    a = np.radians(np.array(angles) * 4)       # 90-degree period -> full circle
    m = math.atan2(np.sin(a).mean(), np.cos(a).mean())
    d = np.degrees(np.angle(np.exp(1j * (a - m)))) / 4
    return float(np.percentile(np.abs(d), 90))


def check_region(region, features=None):
    """Every roof in a region's built layout -- or in `features`, layout
    features built in memory (tools/layout_ab.py) for some of its roofs."""
    import geopandas as gpd
    from shapely.geometry import shape
    from shapely.ops import unary_union
    from shapely.ops import transform as shp_transform
    from shapely.strtree import STRtree
    from pyproj import Transformer
    from src.region_build import area_paths
    p = area_paths(region)
    lay = Path(p["panel_layouts"])
    if features is None:
        if not lay.exists():
            return None
        features = json.loads(lay.read_text())["features"]
    to_m = Transformer.from_crs(4326, 2193, always_xy=True).transform
    by = defaultdict(lambda: {"facet": [], "panel": [], "obstruction": []})
    for f in features:
        q = f["properties"]
        k = q.get("kind")
        if k not in ("facet", "panel", "obstruction") or q.get("building_id") is None:
            continue
        try:
            g = shp_transform(to_m, shape(f["geometry"]))
        except Exception:
            continue
        if not g.is_valid:
            g = g.buffer(0)
        by[int(q["building_id"])][k].append((g, q))
    outl = Path(p["outlines"])
    fp = {}
    if outl.exists():
        g = gpd.read_file(outl).to_crs(2193)
        fp = {int(b): geom for b, geom in zip(g["building_id"], g.geometry)}

    dsm = None
    try:
        import rasterio
        dsm = rasterio.open(p["dsm"]) if Path(p["dsm"]).exists() else None
    except Exception:
        dsm = None

    def over_ground(foot):
        """Share of the outline within OVER_GROUND_M of the ground around it."""
        import numpy as np
        from rasterio.features import geometry_mask
        from rasterio.windows import from_bounds
        ring = foot.buffer(GROUND_RING[1]).difference(foot.buffer(GROUND_RING[0]))
        win = from_bounds(*ring.bounds, transform=dsm.transform)
        z = dsm.read(1, window=win, boundless=True, fill_value=np.nan).astype("float32")
        if dsm.nodata is not None:
            z[z == dsm.nodata] = np.nan
        tr = dsm.window_transform(win)
        inside = geometry_mask([foot], out_shape=z.shape, transform=tr, invert=True)
        around = geometry_mask([ring], out_shape=z.shape, transform=tr, invert=True)
        g, r = z[around], z[inside]
        g, r = g[np.isfinite(g)], r[np.isfinite(r)]
        if g.size < 10 or r.size < 10:
            return None
        ground = np.percentile(g, GROUND_PCT)
        return float((r < ground + OVER_GROUND_M).mean())

    all_fp = list(fp.items())
    fp_tree = None
    if all_fp:
        from shapely.strtree import STRtree as _T
        fp_tree = _T([g for _, g in all_fp])

    def roof_beyond(bid, foot):
        """m2 of smooth, roof-height LiDAR connected to the roof but outside its outline."""
        import numpy as np
        from rasterio.features import geometry_mask
        from rasterio.windows import from_bounds
        from scipy.ndimage import uniform_filter, label
        reach = foot.buffer(GROW_REACH_M)
        win = from_bounds(*reach.buffer(8).bounds, transform=dsm.transform)
        z = dsm.read(1, window=win, boundless=True, fill_value=np.nan).astype("float32")
        if dsm.nodata is not None:
            z[z == dsm.nodata] = np.nan
        tr = dsm.window_transform(win)
        px = abs(tr.a * tr.e)
        msk = lambda geom: geometry_mask([geom], out_shape=z.shape, transform=tr, invert=True)
        inside = msk(foot)
        gz = z[msk(foot.buffer(8).difference(foot.buffer(3)))]
        gz = gz[np.isfinite(gz)]
        if gz.size < 10:
            return 0.0
        ground = np.percentile(gz, GROUND_PCT)
        zz = np.nan_to_num(z, nan=ground)
        mean = uniform_filter(zz, 3); sq = uniform_filter(zz * zz, 3)
        rough = np.sqrt(np.maximum(sq - mean * mean, 0))
        ok = (zz > ground + 2.0) & (rough < 0.35) & msk(reach)
        if fp_tree is not None:
            others = [all_fp[i][1] for i in fp_tree.query(reach) if all_fp[i][0] != bid]
            if others:
                from shapely.ops import unary_union
                ok &= ~msk(unary_union(others).buffer(0.5))
        lab, _ = label(ok | inside, structure=np.ones((3, 3)))
        seeds = np.unique(lab[inside & ok])
        keep = np.isin(lab, seeds[seeds > 0])
        # eaves overhang a wall-traced outline all round; only roof past
        # that band is a part of the building the outline left out
        band = msk(foot.buffer(EAVE_BAND_M))
        return float((keep & ~band).sum() * px)

    totals = defaultdict(int)
    roofs = []
    n_panels = n_roofs = 0
    for bid, d in by.items():
        if not d["facet"]:
            continue
        n_roofs += 1
        hits = defaultdict(int)
        panels = [g for g, _ in d["panel"]]
        n_panels += len(panels)
        faces = [g for g, _ in d["facet"]]
        obst = [g for g, _ in d["obstruction"]]
        foot = fp.get(bid)
        ftree = STRtree(faces)
        otree = STRtree(obst) if obst else None
        ptree = STRtree(panels) if panels else None
        on_face = defaultdict(list)
        for i, pg in enumerate(panels):
            a = pg.area or 1e-9
            if foot is not None and pg.difference(foot).area > OFF_ROOF_SHARE * a:
                hits["off_building"] += 1
            shares = [(j, faces[j].intersection(pg).area / a) for j in ftree.query(pg)]
            best = max(shares, key=lambda t: t[1]) if shares else (None, 0.0)
            if best[1] < FACE_HOLD_SHARE:
                hits["across_edge"] += 1
            if best[0] is not None:
                on_face[best[0]].append(pg)
            if otree is not None and any(obst[j].intersection(pg).area > OBSTRUCTION_SHARE * a for j in otree.query(pg)):
                hits["on_obstruction"] += 1
            if any(j > i and panels[j].intersection(pg).area > OVERLAP_SHARE * a for j in ptree.query(pg)):
                hits["overlap"] += 1
        for j, ps in on_face.items():
            if _spread([_angle(pg) for pg in ps]) > ROW_SPREAD_DEG:
                hits["disorderly_rows"] += 1
            # orientation: a panel's long side relative to the others on its face
            longs = []
            for pg in ps:
                cc = list(pg.exterior.coords)
                e1 = math.hypot(cc[1][0] - cc[0][0], cc[1][1] - cc[0][1])
                e2 = math.hypot(cc[2][0] - cc[1][0], cc[2][1] - cc[1][1])
                a, b = (cc[0], cc[1]) if e1 >= e2 else (cc[1], cc[2])
                longs.append(math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0)
            if longs:
                ref = longs[0]
                if any(min(abs(x - ref) % 180, 180 - abs(x - ref) % 180) > 45 for x in longs):
                    hits["mixed_orientation"] += 1
            # arrays: groups of touching panels on this face
            par = list(range(len(ps)))
            def fd(i):
                while par[i] != i:
                    par[i] = par[par[i]]; i = par[i]
                return i
            for a in range(len(ps)):
                for b in range(a + 1, len(ps)):
                    if ps[a].distance(ps[b]) <= ARRAY_TOUCH_M:
                        par[fd(b)] = fd(a)
            sizes = {}
            for a in range(len(ps)):
                sizes[fd(a)] = sizes.get(fd(a), 0) + 1
            # same rule as src/panel_fitting.py enforce_arrays: no lone panel,
            # at least ARRAY_MIN on the face (a clean pair on a face under
            # ARRAY_PAIR_FACE_M2)
            need = 2 if faces[j].area < ARRAY_PAIR_FACE_M2 else ARRAY_MIN
            if len(ps) < need or any(n < 2 for n in sizes.values()):
                hits["small_array"] += 1
        for (g, q) in d["facet"]:
            if g.is_empty or g.area <= 0:
                continue
            hull = g.convex_hull.area or 1e-9
            ext = g.exterior if g.geom_type == "Polygon" else max(g.geoms, key=lambda x: x.area).exterior
            per_vertex = ext.length / max(len(ext.coords) - 1, 1)
            if g.area / hull < RAGGED_SOLIDITY:
                hits["concave_face_any"] += 1
            if per_vertex < RAGGED_M_PER_VERTEX or (g.area / hull < RAGGED_SOLIDITY and _inner_notch(g, foot)):
                hits["ragged_face"] += 1
            if g.area < SLIVER_M2:
                hits["sliver_faces"] += 1
            if (g.area >= EMPTY_FACE_M2 and (q.get("panel_count") or 0) == 0
                    and (q.get("slope_deg") or 0) <= EMPTY_MAX_SLOPE
                    and (q.get("poa_kwh_m2_yr") or 0) >= EMPTY_MIN_POA):
                hits["empty_good_face"] += 1
        photo_only = d["facet"][0][1].get("from_labels") is None and bid >= 900_000_000
        if dsm is not None and foot is not None and not photo_only:
            try:
                og = over_ground(foot)
            except Exception:
                og = None
            if og is not None and og > OVER_GROUND_SHARE:
                hits["outline_over_ground"] += 1
            if EXPERIMENTAL:
                try:
                    extra = roof_beyond(bid, foot)
                except Exception:
                    extra = 0.0
                if extra > max(BEYOND_M2, BEYOND_SHARE * foot.area):
                    hits["roof_beyond_outline"] += 1
        seams = _seam_offsets(d["facet"])
        totals["seams_measured"] += len(seams)
        off = sum(1 for _, m in seams if m > SEAM_OFF_M)
        if off:
            hits["seam_off_ridge"] += off
        steps = []
        if dsm is not None and len(d["facet"]) > 1:
            try:
                steps = _seam_steps(d["facet"], _dsm_sampler(dsm, unary_union(faces)))
            except Exception:
                steps = []
        totals["step_seams_measured"] += len(steps)
        bad_steps = [s for s in steps if s["miss"] > STEP_OFF_M]
        if bad_steps:
            hits["seam_step"] += len(bad_steps)
        conf = d["facet"][0][1].get("roof_confidence")
        if conf is not None and conf < POOR_FIT:
            hits["poor_fit"] += 1
        for k, v in hits.items():
            totals[k] += v
        score = sum(WEIGHT.get(k, 0) * min(v, 10) for k, v in hits.items())
        if hits:
            roofs.append({"id": bid, "region": region, "score": score, "panels": len(panels),
                          "faces": len(faces), "hits": dict(hits),
                          **({"step_miss_m": round(sum(s["miss"] * s["length"] for s in bad_steps), 1),
                              "steps": [{k: s[k] for k in ("i", "j", "length", "gap", "drawn", "joined", "crease", "shift")}
                                        for s in bad_steps]} if bad_steps else {})})
    roofs.sort(key=lambda r: -r["score"])
    n_flagged = sum(1 for r in roofs if r["score"])
    rates = {k: round(1000 * totals[k] / max(n_panels, 1), 2) for k in PANEL_CHECKS}
    # of the pitched seams that could be measured (layouts with planes), the share off the ridge
    rates["seams_off_share"] = (round(100 * totals["seam_off_ridge"] / totals["seams_measured"], 1)
                                if totals["seams_measured"] else None)
    # of the shared edges measured against the DSM, the share whose faces step apart where the roof does not
    rates["step_seams_share"] = (round(100 * totals["seam_step"] / totals["step_seams_measured"], 1)
                                 if totals["step_seams_measured"] else None)
    rates.update({k: round(100 * sum(1 for r in roofs if r["hits"].get(k)) / max(n_roofs, 1), 2) for k in ROOF_CHECKS + INFO_CHECKS})
    clean = n_roofs - n_flagged
    return {"region": region, "roofs": n_roofs, "panels": n_panels, "clean_roofs": clean,
            "clean_share": round(clean / max(n_roofs, 1), 4), "counts": dict(totals),
            "rates": rates, "worst": [r for r in roofs if r["score"]][:300]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("regions", nargs="*")
    ap.add_argument("--worst", type=int, default=15)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--experimental", action="store_true", help="also run roof-beyond-outline (unreliable)")
    a = ap.parse_args()
    global EXPERIMENTAL
    EXPERIMENTAL = a.experimental
    if a.regions:
        regions = a.regions
    else:
        from src.region_ids import list_regions
        regions = list_regions(ROOT / "data" / "out", sentinel="summary.json")
    out = []
    for r in regions:
        res = check_region(r)
        if res is None:
            continue
        out.append(res)
        print(f"{r:24s} {res['roofs']:6,d} roofs  {100 * res['clean_share']:5.1f}% clean   " +
              "  ".join(f"{LABEL[k]} {res['rates'][k]}" for k in PANEL_CHECKS + ROOF_CHECKS), flush=True)
    roofs = sum(r["roofs"] for r in out)
    panels = sum(r["panels"] for r in out)
    counts = defaultdict(int)
    for r in out:
        for k, v in r["counts"].items():
            counts[k] += v
    worst = sorted((w for r in out for w in r["worst"]), key=lambda w: -w["score"])
    summary = {
        "roofs": roofs, "panels": panels,
        "clean_share": round(sum(r["clean_roofs"] for r in out) / max(roofs, 1), 4),
        "per_1000_panels": {k: round(1000 * counts[k] / max(panels, 1), 2) for k in PANEL_CHECKS},
        "roofs_with_pct": {k: round(sum(r["rates"].get(k, 0) * r["roofs"] for r in out) / max(roofs, 1), 2)
                           for k in ROOF_CHECKS},
        "regions": {r["region"]: {k: r[k] for k in ("roofs", "panels", "clean_share", "rates")} for r in out},
        "worst": worst[:500],
    }
    print(f"\nALL {roofs:,} roofs, {panels:,} panels: {100 * summary['clean_share']:.1f}% of roofs pass every check")
    print("  per 1,000 panels: " + ", ".join(f"{LABEL[k]} {v}" for k, v in summary["per_1000_panels"].items()))
    print("  % of roofs with:  " + ", ".join(f"{LABEL[k]} {v}" for k, v in summary["roofs_with_pct"].items()))
    if a.worst:
        print(f"\nworst {a.worst}:")
        for w in worst[:a.worst]:
            print(f"  #{w['id']:<9} {w['region']:20s} score {w['score']:3d}  {w['panels']:4d} panels {w['faces']:3d} faces  " +
                  ", ".join(f"{LABEL.get(k, k)} {v}" for k, v in sorted(w["hits"].items(), key=lambda t: -WEIGHT.get(t[0], 0) * t[1])))
    if a.json:
        a.json.write_text(json.dumps(summary, separators=(",", ":")))
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
