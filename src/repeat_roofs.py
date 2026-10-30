"""Identical roofs get one face reading.

THE PROBLEM. A terrace or a row of townhouses is one design built several
times, and its roofs are the same shape. Each is still read on its own: the
face selector (tools/predict_faces.py) picks among four noisy readings per
building, and on a roof none of them reads clearly the pick changes from one
copy to the next. 6/60 Hallenstein St is the case: six identical townhouses,
each a ~2 deg membrane with an 8 deg strip along its north-east edge (the
LiDAR shows the strip 0.8 m higher at that edge, falling south-west), came
out as five different face sets -- line, line, SAM, SAM, SAM and a one-face
form -- and 30 to 48 panels.

THE RULE. Roofs whose LiDAR surfaces are the same shape (within SAME_P80_M,
after any rotation by a right angle or a mirror, a shift, and a height
offset) form a set. Every member's reading is moved onto every other member
and scored there against that member's own returns; the reading that
explains the most returns across the whole set, less FACE_PENALTY per face,
is the set's reading, and every member gets it, moved onto its own roof.
The planes are still fitted to each member's own returns downstream.

WHAT IT IS NOT. Flat roofs are left alone: a flat surface matches every
other flat surface, and their readings already defer to the LiDAR path. A
pair is only compared when it is near (NEAR_M), of similar size and racked
to the same walls, so a match is a repeated design, not a coincidence.
Hand-drawn markup still wins over any reading (roof_partition reads the
drawn faces first). No reading is invented: the set's reading is one of its
members' own.

Writes data/repeat_faces/<id>.json in the selected-faces schema, with
`source: "repeat"`, `repeat_of` (whose reading it is) and `repeat_set`;
roof_partition reads it in place of data/selected_faces/<id>.json when
SOLAR_REPEAT_ROOFS=1. Region summary: data/regions/<r>/repeat_roofs.json.

Usage: python src/repeat_roofs.py <region> [--ids ...] [--dry-run]
"""

import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATA = Path(__file__).resolve().parent.parent / "data"
SELECTED_DIR = DATA / "selected_faces"
REPEAT_DIR = DATA / "repeat_faces"

CELL_M = 0.5            # surface grid; the survey is ~6 returns/m2
SHIFT_M = 2.5           # largest offset between two copies' return centroids
NEAR_M = 30.0           # copies of one design stand side by side
AREA_RATIO_MIN = 0.70   # outlines of one design, measured on Hallenstein St: 0.77-1.0
AXIS_TOL_DEG = 4.0      # walls of one design run the same way, mod 90
RELIEF_MIN_M = 0.5      # p95-p5 of the returns: below this a roof is flat
OVERLAP_MIN = 0.72      # share of the smaller roof's cells both surfaces cover; copies on
# Hallenstein St overlap 0.73-0.88 (their outlines differ by porches and eaves)
SAME_P80_M = 0.20       # 80th percentile of the height difference over the overlap
FACE_PENALTY = 0.01     # explained share a reading gives up per face
AGREE_SLACK = 0.02      # the set's reading may explain this much less than the members' own
MIN_POINTS = 150


def _frame(geom):
    from src.outline_axis import dominant_axis_deg
    th = math.radians(dominant_axis_deg(geom))
    return np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])


# the eight ways a copy can be laid: rotated by a right angle, mirrored or not
SYMS = [np.array(m, dtype=float) for m in (
    [[1, 0], [0, 1]], [[0, -1], [1, 0]], [[-1, 0], [0, -1]], [[0, 1], [-1, 0]],
    [[-1, 0], [0, 1]], [[0, 1], [1, 0]], [[1, 0], [0, -1]], [[0, -1], [-1, 0]])]


def roof_surface(pc, geom):
    """The building's top-surface returns in its own wall frame, or None.

    `local` is (along, across) metres from the returns' centroid, z the
    height less the median: a copy on a lower lot compares equal."""
    from src.roof_partition import top_surface, _points_in
    minx, miny, maxx, maxy = geom.bounds
    pts = pc.points_in_bbox(minx - 1, miny - 1, maxx + 1, maxy + 1, building_only=True)
    if pts is None or len(pts) < MIN_POINTS:
        return None
    pts = top_surface(_points_in(geom.buffer(0.5), pts))
    if len(pts) < MIN_POINTS:
        return None
    z = pts[:, 2]
    lo, hi = np.percentile(z, [5, 95])
    keep = (z > lo - 1.0) & (z < hi + 1.0)
    pts = pts[keep]
    U = _frame(geom)
    c = pts[:, :2].mean(axis=0)
    return {"pts": pts, "U": U, "c": c, "local": (pts[:, :2] - c) @ U,
            "z": pts[:, 2] - np.median(pts[:, 2]),
            "relief": float(hi - lo), "area": float(geom.area)}


def _grid(local, z, half):
    n = int(round(2 * half / CELL_M))
    ij = np.floor((local + half) / CELL_M).astype(int)
    ok = (ij[:, 0] >= 0) & (ij[:, 0] < n) & (ij[:, 1] >= 0) & (ij[:, 1] < n)
    ij, z = ij[ok], z[ok]
    s = np.zeros((n, n)); k = np.zeros((n, n))
    np.add.at(s, (ij[:, 1], ij[:, 0]), z)
    np.add.at(k, (ij[:, 1], ij[:, 0]), 1)
    with np.errstate(invalid="ignore"):
        g = s / k
    g[k == 0] = np.nan
    return g


def match(a, b):
    """How `b`'s roof lies on `a`'s: the best of the eight layings and the
    shifts within SHIFT_M, as {sym, t, p80, overlap, alts}, or None. `alts`
    holds every laying the heights cannot tell from the best (within
    LAYING_TIE_M): a strip that runs the width of a roof reads the same
    mirrored, and only the photo can choose (refine_with_photo).

    b's local point q_b maps to a's local point S q_b + t."""
    half = max(np.abs(a["local"]).max(), np.abs(b["local"]).max()) + SHIFT_M + CELL_M
    ga = _grid(a["local"], a["z"], half)
    na = np.isfinite(ga).sum()
    steps = int(SHIFT_M / CELL_M)
    per_sym = []
    for si, S in enumerate(SYMS):
        gb = _grid(b["local"] @ S.T, b["z"], half)
        nb = np.isfinite(gb).sum()
        need = OVERLAP_MIN * min(na, nb)
        best = None
        for dy in range(-steps, steps + 1):
            for dx in range(-steps, steps + 1):
                sb = np.roll(np.roll(gb, dy, axis=0), dx, axis=1)
                both = np.isfinite(ga) & np.isfinite(sb)
                n = both.sum()
                if n < need:
                    continue
                d = ga[both] - sb[both]
                d = np.abs(d - np.median(d))
                p80 = float(np.percentile(d, 80))
                if best is None or p80 < best["p80"]:
                    best = {"sym": si, "t": np.array([dx, dy]) * CELL_M,
                            "p80": p80, "overlap": n / min(na, nb)}
        if best is not None:
            per_sym.append(best)
    if not per_sym:
        return None
    per_sym.sort(key=lambda m: m["p80"])
    alts = [m for m in per_sym if m["p80"] <= per_sym[0]["p80"] + LAYING_TIE_M]
    for m in alts:
        m["t"] = _refine(a, b, SYMS[m["sym"]], m["t"])
    return {**alts[0], "alts": alts}


def _refine(a, b, S, t, step=0.1, reach=0.3):
    """Sub-cell shift: the 0.5 m grid search leaves up to 0.25 m, which moves
    a transferred seam by as much. Nearest-return height difference, robust."""
    from scipy.spatial import cKDTree
    tree = cKDTree(a["local"])
    best, bt = None, t
    for ox in np.arange(-reach, reach + 1e-9, step):
        for oy in np.arange(-reach, reach + 1e-9, step):
            q = b["local"] @ S.T + t + (ox, oy)
            d, i = tree.query(q, distance_upper_bound=0.35)
            ok = np.isfinite(d)
            if ok.sum() < 0.5 * len(q):
                continue
            dz = a["z"][i[ok]] - b["z"][ok]
            v = float(np.percentile(np.abs(dz - np.median(dz)), 80))
            if best is None or v < best:
                best, bt = v, t + np.array([ox, oy])
    return bt


PHOTO_RES_M = 0.1
PHOTO_REACH_M = 1.2     # how far the photo may move a laying the LiDAR found
PHOTO_NCC_MIN = 0.5     # a weaker photo match leaves the LiDAR laying as it is
LAYING_TIE_M = 0.03     # layings whose height match is this close are a tie for the photo


def _photo(img, geom, pad=PHOTO_REACH_M + 1.0):
    """Grey photo window round `geom`, with its world -> (row, col) map."""
    import rasterio.windows
    minx, miny, maxx, maxy = geom.bounds
    win = rasterio.windows.from_bounds(minx - pad, miny - pad, maxx + pad, maxy + pad,
                                       img.transform)
    arr = img.read([1, 2, 3], window=win, boundless=True, fill_value=0).astype(float).mean(axis=0)
    inv = ~img.window_transform(win)
    return arr, inv


def refine_with_photo(img, a, ga, b, gb, m):
    """The laying moved to where the two photos agree, or `m` unchanged.

    Heights pin a laying across a slope but hardly along it: on Hallenstein
    St the 8 deg strip runs the width of each roof, and the LiDAR laying left
    the transferred strip up to 1 m along the roof from the brown roofing it
    covers. The photo shows that roofing's ends. Copies side by side lean the
    same way in the photo, so photo against photo is a fair comparison (the
    reasoning src/register_imagery.py measures)."""
    from scipy.ndimage import map_coordinates
    if img is None:
        return m
    try:
        pa, inva = _photo(img, ga)
        pb, invb = _photo(img, gb)
    except Exception:
        return m
    inner = ga.buffer(-0.5)
    if inner.is_empty:
        return m
    minx, miny, maxx, maxy = inner.bounds
    xs = np.arange(minx, maxx, PHOTO_RES_M)
    ys = np.arange(miny, maxy, PHOTO_RES_M)
    X, Y = np.meshgrid(xs, ys)
    import shapely
    keep = shapely.contains_xy(inner, X.ravel(), Y.ravel())
    W = np.c_[X.ravel()[keep], Y.ravel()[keep]]
    if len(W) < 500:
        return m

    def sample(arr, inv, w):
        col, row = inv * (w[:, 0], w[:, 1])
        return map_coordinates(arr, [row, col], order=1, mode="nearest")

    va = sample(pa, inva, W)
    va = (va - va.mean()) / (va.std() + 1e-9)
    qa = (W - a["c"]) @ a["U"]
    best, bm = None, m
    steps = np.arange(-PHOTO_REACH_M, PHOTO_REACH_M + 1e-9, PHOTO_RES_M)
    for alt in m.get("alts") or [m]:
        S, t = SYMS[alt["sym"]], alt["t"]
        for ox in steps:
            for oy in steps:
                qb = (qa - (t + (ox, oy))) @ S          # b-local: S^T (qa - t)
                wb = qb @ b["U"].T + b["c"]
                vb = sample(pb, invb, wb)
                vb = (vb - vb.mean()) / (vb.std() + 1e-9)
                ncc = float((va * vb).mean())
                if best is None or ncc > best:
                    best = ncc
                    bm = {**alt, "t": t + np.array([ox, oy]), "photo_ncc": round(ncc, 3)}
    if best is None or best < PHOTO_NCC_MIN:
        return m
    return bm


def same_roof(a, b):
    """The match if `a` and `b` are copies of one roof, else None."""
    if a is None or b is None:
        return None
    if min(a["relief"], b["relief"]) < RELIEF_MIN_M:
        return None
    m = match(a, b)
    if m is None or m["p80"] > SAME_P80_M or m["overlap"] < OVERLAP_MIN:
        return None
    return m


def move_faces(faces, src, dst, m_dst_on_src):
    """Face rings from `src`'s roof onto `dst`'s. `m_dst_on_src` is
    match(src, dst): dst-local q maps to src-local S q + t."""
    S, t = SYMS[m_dst_on_src["sym"]], m_dst_on_src["t"]
    Sinv = S.T                      # every laying is orthogonal
    out = []
    for ring in faces:
        r = np.asarray(ring, dtype=float)
        ls = (r - src["c"]) @ src["U"]          # src local
        ld = (ls - t) @ Sinv.T                  # dst local
        out.append((ld @ dst["U"].T + dst["c"]).tolist())
    return out


def _fitted(rings, footprint, pts):
    """Rings clipped to the footprint, each with a plane fitted to the
    returns it covers -- the construction facets_from_selected_faces uses."""
    from shapely.geometry import Polygon
    from src.roof_partition import _fit_plane_robust, _points_in
    out = []
    for ring in rings:
        try:
            p = Polygon(ring)
            p = p if p.is_valid else p.buffer(0)
            p = p.intersection(footprint)
        except Exception:
            continue
        for g in getattr(p, "geoms", [p]):
            if g.geom_type != "Polygon" or g.area < 2.0:
                continue
            sub = _points_in(g, pts)
            if len(sub) < 10:
                continue
            out.append({"geometry": g, "plane": _fit_plane_robust(sub)})
    return out


ABSORB_MAX_M2 = 10.0    # a leftover piece up to this size joins its neighbour face
ABSORB_THIN_M = 0.75    # ...and so does any piece narrower than twice this


def _scrap(piece):
    return piece.area <= ABSORB_MAX_M2 or piece.buffer(-ABSORB_THIN_M).is_empty


def fill_footprint(rings, footprint):
    """The reading clipped to this copy's outline, its faces made disjoint,
    and the strips the outline reaches past it joined to the face beside them.

    Copies' outlines differ by porches and eaves (Hallenstein St: 143 to
    186 m2 for one design), so a moved reading leaves strips of the outline
    uncovered. The build fills those from the LiDAR, which reads an eave
    strip as a steep sliver (21 deg and 23 deg faces on two of the six). A
    small or thin leftover is part of the face it adjoins; a large one is a
    real extra part of the roof and is left for the build to read."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    polys = []
    for ring in rings:
        try:
            p = Polygon(ring)
            p = (p if p.is_valid else p.buffer(0)).intersection(footprint)
        except Exception:
            continue
        for g in getattr(p, "geoms", [p]):
            if g.geom_type == "Polygon" and g.area >= 1.0:
                polys.append(g)
    if not polys:
        return []
    # A reading may nest a face inside another (602's line reading: the whole
    # outline, and the strip on top of it). The inner face is the face; the
    # outer one is what is left round it. Overlapping faces reached the build
    # as one 143 m2 face spanning both planes on 4734604.
    polys.sort(key=lambda p: p.area)
    placed = []
    for p in polys:
        if placed:
            p = p.difference(unary_union(placed))
        parts = [g for g in getattr(p, "geoms", [p]) if g.geom_type == "Polygon"]
        for g in parts:
            # a scrap the cut left is a leftover, joined below
            if len(parts) > 1 and _scrap(g):
                continue
            if g.area >= 1.0:
                placed.append(g)
    polys = placed
    rest = footprint.difference(unary_union(polys))
    for piece in getattr(rest, "geoms", [rest]):
        if piece.geom_type != "Polygon" or piece.area < 0.05:
            continue
        if not _scrap(piece):
            continue
        shared = [piece.buffer(0.05).intersection(p.boundary).length for p in polys]
        k = int(np.argmax(shared))
        if shared[k] <= 0:
            continue
        merged = polys[k].union(piece).buffer(0.01).buffer(-0.01)
        if merged.geom_type == "Polygon":
            polys[k] = merged
    out = []
    for p in polys:
        out.extend(_without_holes(p))
    return [list(map(list, p.exterior.coords)) for p in out]


def _without_holes(p, depth=0):
    """`p` as polygons with no holes: a reading stores outer rings only, so a
    face round another face (the 2 deg membrane round the strip) would come
    back whole and overlap it. Cut across each hole along its long side."""
    from shapely.geometry import LineString
    from shapely.ops import split
    if not p.interiors or depth > 4:
        return [p]
    hole = p.interiors[0]
    from shapely.geometry import Polygon
    mrr = Polygon(hole).minimum_rotated_rectangle
    xs, ys = mrr.exterior.coords.xy
    e = [(xs[k + 1] - xs[k], ys[k + 1] - ys[k]) for k in range(2)]
    dx, dy = max(e, key=lambda v: v[0] ** 2 + v[1] ** 2)
    n = (dx * dx + dy * dy) ** 0.5 or 1.0
    c = Polygon(hole).centroid
    L = 4 * max(p.bounds[2] - p.bounds[0], p.bounds[3] - p.bounds[1])
    line = LineString([(c.x - dx / n * L, c.y - dy / n * L), (c.x + dx / n * L, c.y + dy / n * L)])
    out = []
    for g in split(p, line).geoms:
        if g.geom_type == "Polygon" and g.area >= 0.05:
            out.extend(_without_holes(g, depth + 1))
    return out or [p]


def explained(rings, footprint, pts):
    from src.roof_partition import explained_fraction
    fs = _fitted(rings, footprint, pts)
    return explained_fraction([{"geometry": f["geometry"], "plane_a": f["plane"][0],
                                "plane_b": f["plane"][1], "plane_c": f["plane"][2]}
                               for f in fs], pts) if fs else 0.0


def find_sets(gdf, pc, ids=None, log=print):
    """Sets of copies among `ids` (default all of gdf), as lists of ids, with
    each member's surface and pairwise matches. gdf is indexed by building_id."""
    from shapely.strtree import STRtree
    from src.outline_axis import dominant_axis_deg
    ids = list(gdf.index if ids is None else ids)
    geoms = [gdf.loc[i].geometry for i in ids]
    tree = STRtree(geoms)
    axis = {i: dominant_axis_deg(g) % 90.0 for i, g in zip(ids, geoms)}
    surf = {}

    def s(i):
        if i not in surf:
            try:
                surf[i] = roof_surface(pc, gdf.loc[i].geometry)
            except Exception:
                surf[i] = None
        return surf[i]

    parent = {i: i for i in ids}

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    matches = {}
    for k, (i, g) in enumerate(zip(ids, geoms)):
        for j_idx in tree.query(g.centroid.buffer(NEAR_M)):
            j = ids[int(j_idx)]
            if j <= i:
                continue
            gj = geoms[int(j_idx)]
            if g.centroid.distance(gj.centroid) > NEAR_M:
                continue
            r = min(g.area, gj.area) / max(g.area, gj.area)
            da = abs(axis[i] - axis[j]); da = min(da, 90.0 - da)
            if r < AREA_RATIO_MIN or da > AXIS_TOL_DEG:
                continue
            a, b = s(i), s(j)
            m = same_roof(a, b)
            if m is not None:
                matches[(i, j)] = m
                parent[root(j)] = root(i)
    matched = {i for pair in matches for i in pair}
    groups = {}
    for i in ids:
        if i in matched:
            groups.setdefault(root(i), []).append(i)
    return [sorted(v) for v in groups.values() if len(v) >= 2], surf, matches


def _reading(bid):
    fp = SELECTED_DIR / f"{bid}.json"
    try:
        return json.loads(fp.read_text())
    except (OSError, ValueError):
        return None


def agree(members, gdf, surf, img=None, log=print):
    """The set's reading: each member's own reading scored on every member it
    can be laid on. Returns (template id, {member: doc}) or (None, {})."""
    own = {i: _reading(i) for i in members}
    cands = [i for i in members if own[i] and own[i].get("faces")
             and own[i].get("source") not in ("repeat", "synthetic")]
    if not cands:
        return None, {}
    lay = {}
    for t in cands:
        for mbr in members:
            if mbr == t:
                lay[(t, mbr)] = {"sym": 0, "t": np.zeros(2)}
                continue
            m = match(surf[t], surf[mbr])
            if m is not None and m["p80"] <= SAME_P80_M:
                lay[(t, mbr)] = refine_with_photo(img, surf[t], gdf.loc[t].geometry,
                                                  surf[mbr], gdf.loc[mbr].geometry, m)
    score, per = {}, {}
    for t in cands:
        on = [mbr for mbr in members if (t, mbr) in lay]
        if len(on) < len(members):
            continue
        ex = []
        for mbr in members:
            rings = own[t]["faces"] if mbr == t else \
                move_faces(own[t]["faces"], surf[t], surf[mbr], lay[(t, mbr)])
            ex.append(explained(rings, gdf.loc[mbr].geometry, surf[mbr]["pts"]))
        per[t] = dict(zip(members, ex))
        score[t] = float(np.mean(ex)) - FACE_PENALTY * len(own[t]["faces"])
        log(f"    reading of {t} ({own[t]['source']}, {len(own[t]['faces'])} faces, "
            f"own score {own[t].get('score')}): explains {np.mean(ex):.3f} across the set "
            f"[{', '.join(f'{x:.2f}' for x in ex)}]")
    if not score:
        return None, {}
    t = max(score, key=lambda k: (score[k], own[k].get("score", 0)))
    # AGREEMENT MUST NOT COST EVIDENCE: the set's reading has to explain the
    # members' returns about as well as their own readings do (within
    # AGREE_SLACK on the set's mean), or the set keeps its own readings.
    # Compared on the members that have a reading of their own.
    read = [mbr for mbr in members if own[mbr] and own[mbr].get("faces")]
    mine = np.mean([explained(own[mbr]["faces"], gdf.loc[mbr].geometry, surf[mbr]["pts"])
                    for mbr in read])
    theirs = np.mean([per[t][mbr] for mbr in read])
    if theirs < mine - AGREE_SLACK:
        log(f"    set reading explains {theirs:.3f}, own readings {mine:.3f}: kept apart")
        return None, {}
    docs = {}
    for mbr in members:
        rings = own[t]["faces"] if mbr == t else \
            move_faces(own[t]["faces"], surf[t], surf[mbr], lay[(t, mbr)])
        rings = fill_footprint(rings, gdf.loc[mbr].geometry)
        docs[mbr] = {
            "source": "repeat", "repeat_of": int(t),
            "repeat_source": own[t]["source"],
            "repeat_set": [int(x) for x in members],
            "set_explained": round(score[t] + FACE_PENALTY * len(own[t]["faces"]), 3),
            "score": own[t].get("score", 0.0),
            "faces": [[[round(v, 2) for v in xy] for xy in r] for r in rings]}
    return t, docs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("region")
    ap.add_argument("--ids", nargs="*", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    import geopandas as gpd
    from src.region_build import area_paths, write_json_atomic
    from src.pointcloud_source import PointCloudSource
    p = area_paths(a.region)
    dd = p["dir"] / "building_outlines_dedup.geojson"
    from src.outline_fit import fitted_outlines
    # the outlines the layout clips to (src/outline_fit.py)
    gdf = fitted_outlines(gpd.read_file(dd if dd.exists() else p["outlines"]),
                          p["dir"]).set_index("building_id", drop=False)
    pc = PointCloudSource(max_cached_tiles=4)
    import rasterio
    img = rasterio.open(p["imagery"]) if Path(p["imagery"]).exists() else None
    ids = [i for i in (a.ids or list(gdf.index)) if i in gdf.index]
    sets, surf, _ = find_sets(gdf, pc, ids)
    print(f"{a.region}: {len(sets)} sets of identical roofs, "
          f"{sum(len(s) for s in sets)} buildings")
    summary = []
    REPEAT_DIR.mkdir(parents=True, exist_ok=True)
    region_ids = set(int(i) for i in ids)
    # a building no longer in a set must not keep an old set's reading
    for f in REPEAT_DIR.glob("*.json"):
        if int(f.stem) in region_ids and not a.dry_run:
            f.unlink()
    for members in sets:
        print(f"  set {members}")
        t, docs = agree(members, gdf, surf, img)
        summary.append({"members": members, "reading_of": t})
        if a.dry_run:
            continue
        for mbr, doc in docs.items():
            (REPEAT_DIR / f"{mbr}.json").write_text(json.dumps(doc))
    if not a.dry_run:
        write_json_atomic(p["dir"] / "repeat_roofs.json", {"sets": summary})
    return 0


if __name__ == "__main__":
    sys.exit(main())
