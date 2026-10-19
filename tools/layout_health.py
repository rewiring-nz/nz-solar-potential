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
# Racking (Josh, 20 Oct): one orientation per face, arrays of 4+ panels.
ARRAY_MIN = 3   # same as src/panel_fitting.py ARRAY_MIN_PANELS
ARRAY_TOUCH_M = 1.30            # same as src/panel_fitting.py: a rail runs on past a one-panel gap (vent)
ROOF_CHECKS = ["mixed_orientation", "small_array", "disorderly_rows", "ragged_face", "sliver_faces", "empty_good_face",
               "outline_over_ground", "poor_fit", "roof_beyond_outline"]
# reported for comparison with runs before 19 Oct, not scored
INFO_CHECKS = ["concave_face_any"]
WEIGHT = {"mixed_orientation": 4, "small_array": 3, "off_building": 3, "across_edge": 3, "on_obstruction": 2, "overlap": 2,
          "disorderly_rows": 4, "ragged_face": 2, "sliver_faces": 1, "empty_good_face": 4,
          "outline_over_ground": 6, "poor_fit": 4, "roof_beyond_outline": 6}
LABEL = {"mixed_orientation": "portrait and landscape mixed on a face", "small_array": "lone panel, or under 3 on a face",
         "off_building": "off the building", "across_edge": "across a face edge",
         "on_obstruction": "on an obstruction", "overlap": "panels overlap",
         "disorderly_rows": "disorderly rows", "ragged_face": "ragged face",
         "sliver_faces": "sliver faces", "empty_good_face": "empty good face",
         "outline_over_ground": "outline over ground", "poor_fit": "poor LiDAR fit",
         "roof_beyond_outline": "roof beyond outline"}


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
            # at least ARRAY_MIN on the face
            if len(ps) < ARRAY_MIN or any(n < 2 for n in sizes.values()):
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
        conf = d["facet"][0][1].get("roof_confidence")
        if conf is not None and conf < POOR_FIT:
            hits["poor_fit"] += 1
        for k, v in hits.items():
            totals[k] += v
        score = sum(WEIGHT.get(k, 0) * min(v, 10) for k, v in hits.items())
        if hits:
            roofs.append({"id": bid, "region": region, "score": score, "panels": len(panels),
                          "faces": len(faces), "hits": dict(hits)})
    roofs.sort(key=lambda r: -r["score"])
    n_flagged = sum(1 for r in roofs if r["score"])
    rates = {k: round(1000 * totals[k] / max(n_panels, 1), 2) for k in PANEL_CHECKS}
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
        regions = sorted(p.parent.name for p in (ROOT / "data" / "out").glob("*/summary.json"))
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
                  ", ".join(f"{LABEL[k]} {v}" for k, v in sorted(w["hits"].items(), key=lambda t: -WEIGHT[t[0]] * t[1])))
    if a.json:
        a.json.write_text(json.dumps(summary, separators=(",", ":")))
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
