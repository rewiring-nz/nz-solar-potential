"""
Placement quality gate, applied as a post-filter over built panel layouts.

Drops any placed panel whose underlying LiDAR contradicts "flat roof
surface here" -- the failure classes from the 23-building field-report set
(docs/bugdoc-2026-08-22.md):

- too few building-class returns beneath it  -> carpark / air / demolished
  (19 Industrial Pl, 10/16 Kent St, 61 Ballarat St)
- surface not meaningfully above bare earth  -> ground-level slab/yard
- points disagree with the local panel plane -> covers vents/plant/level
  changes the obstruction pass missed (17 Marine Pde; audit's 3,459 lumpy)

Runs per region on panel_layouts.geojson IN PLACE (before rerank/deciles/
shrink/tile). The same checks move into fit time with the Wave-1 rebuild;
this post-filter exists so the worst placements leave the live map a
rebuild-cycle earlier.

Usage: python src/gate_panels.py [region ...]   (default: all areas)
"""

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pyproj
import shapely
from shapely.geometry import shape
from shapely.ops import transform as shp_transform

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.preflight import preflight
from src.pointcloud_source import PointCloudSource
from src.region_build import all_areas, area_paths, write_json_atomic

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TO_NZTM = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2193", always_xy=True).transform

MIN_HEIGHT_ABOVE_GROUND_M = 2.0  # a panel's surface must stand this far above the LOCAL ground,
# measured from LiDAR ground-class returns nearby (not the 8m DEM, and not the
# building-class flag). Ground classification is the most reliable product in a
# LAS file; building classification is not -- 6 Shotover St is a real commercial
# roof where only 24% of returns under each panel carried the building flag, and
# a classification-fraction rule deleted 155 of its 163 panels. Geometry decides.
GROUND_SEARCH_RADIUS_M = 20.0
MIN_EVIDENCE_PTS = 8        # fewer total returns than this under a panel = survey too thin
# to judge here at all -> keep. (7 Cedar Dr: a real roof at 2.0 pts/m2 total
# had 63 of 69 fitted panels executed by absolute-count thresholds.)
MAX_LOCAL_RMS = 0.28        # points under one 2m panel should fit their own plane this well
BELOW_PLANE_TOLERANCE_M = 0.35  # returns further below the panel's own roof level than this are
# not the roof -- see the lumpy test for the measurement that motivated it


def panel_ok(poly, pc):
    minx, miny, maxx, maxy = poly.bounds
    # A veto requires EVIDENCE AGAINST a roof, never mere absence of data:
    # a LiDAR coverage gap (zero returns of ANY class) means "unknown" and
    # the panel stays. Cost of this lesson: a cropped-tile coverage hole
    # made the first gate run execute 96 healthy houses.
    pts_all = pc.points_in_bbox(minx - 0.3, miny - 0.3, maxx + 0.3, maxy + 0.3, building_only=False)
    if len(pts_all) == 0:
        return True, "no_coverage_kept"
    inside_all = shapely.contains_xy(poly, pts_all[:, 0], pts_all[:, 1])
    n_all = int(inside_all.sum())
    if n_all < MIN_EVIDENCE_PTS:
        return True, "thin_coverage_kept"  # not enough returns of ANY class to judge
    all_in = pts_all[inside_all]
    # Surface height under the panel: the upper cluster of returns, so a few
    # ground returns seen through a gap can't drag it down.
    roof_z = float(np.percentile(all_in[:, 2], 75))

    # Local ground from ground-class returns in a neighbourhood; if the tile
    # has none nearby, fall back to the lowest returns around the panel.
    c = poly.centroid
    r = GROUND_SEARCH_RADIUS_M
    # Each query below scans whole decoded tiles (millions of returns), so
    # each is made only when its answer is needed: `around` only without
    # enough ground returns, `pts` only once the cheap early exits are past.
    # Same tiles as the queries before them, so the results are unchanged.
    local_ground = _ground_beside(pc, c.x, c.y)
    if local_ground is None:
        around = pc.points_in_bbox(c.x - r, c.y - r, c.x + r, c.y + r, building_only=False)
        if len(around) >= 20:
            local_ground = float(np.percentile(around[:, 2], 5))
        else:
            return True, "no_ground_reference_kept"
    if (roof_z - local_ground) < MIN_HEIGHT_ABOVE_GROUND_M:
        # sits at ground level: carpark, yard, slab, or air over a gap where
        # the only returns are the ground below
        return False, "sparse"
    pts = pc.points_in_bbox(minx - 0.3, miny - 0.3, maxx + 0.3, maxy + 0.3, building_only=True)
    inside = shapely.contains_xy(poly, pts[:, 0], pts[:, 1]) if len(pts) else np.zeros(0, bool)
    pp = pts[inside] if len(pts) and inside.any() else all_in
    # NO height-above-DEM test. The wide DEM is 8m-resolution smoothed bare
    # earth: on sloping ground its cell averages uphill terrain, so a real
    # single-storey roof can sit <1m above it (4 Abbottswood Ln: roof 392.9,
    # DEM 391.4 -> every panel wrongly read as ground-level, including the
    # north face that carries REAL installed panels in the photo). Height is
    # already implied by LAS building classification, which is per-return and
    # far more reliable here; a rooftop parking deck is an exclusion-list case,
    # not a height-rule case.
    # Local planarity: the points under one panel must fit their own plane --
    # but measured only against the ROOF SURFACE, not against everything the
    # scanner saw through the gap at a roof edge.
    #
    # This test exists to catch a panel sitting on unmodelled structure: a
    # chimney, a vent, plant. Structure is ABOVE the roof. Points BELOW it are
    # the wall, the eave soffit, or the ground seen past the edge, and a panel
    # near a roof edge picks them up routinely. Measured over three commercial
    # roofs, of the panels this test rejected:
    #     82% had their outliers mostly BELOW the roof plane  (edge artefact)
    #     10% had them mostly ABOVE                            (real structure)
    # and separately, 87% of all gate drops were edge panels. It was cutting
    # 45 Camp St from 63 fitted panels to 31 -- a roof left sparsely
    # populated with plenty of space free.
    #
    # Same physics guard obstruction_detection already applies to its own
    # candidates: deviation on both sides is roof form, deviation above is an
    # object. Drop the below-plane returns before fitting the local plane.
    if len(pp) >= 6:
        roof_ref = float(np.percentile(pp[:, 2], 75))
        on_roof = pp[pp[:, 2] >= roof_ref - BELOW_PLANE_TOLERANCE_M]
        if len(on_roof) >= 6:
            pp = on_roof
    if len(pp) >= 6:
        x0, y0 = pp[:, 0].mean(), pp[:, 1].mean()
        A = np.column_stack([pp[:, 0] - x0, pp[:, 1] - y0, np.ones(len(pp))])
        try:
            coeffs, *_ = np.linalg.lstsq(A, pp[:, 2], rcond=None)
            rms = float(np.sqrt(np.mean((A @ coeffs - pp[:, 2]) ** 2)))
            if rms > MAX_LOCAL_RMS:
                return False, "lumpy"
        except np.linalg.LinAlgError:
            pass
    return True, "ok"


GROUND_NEAR_M = 8.0      # ground returns this close to the panel first...
GROUND_NEAR_Q = 20       # ...their low end: the ground beside the building
GROUND_FAR_Q = 25        # failing that, the low quarter of the 20 m box


def _ground_beside(pc, x, y):
    """Ground height beside the panel: the low end of the ground returns
    within GROUND_NEAR_M, else of the 20 m box.

    The old test took the MEDIAN of the 20 m box. On a hillside that is the
    neighbourhood's middle height, so the uphill half of a real roof read as
    standing under 2 m above it and was deleted as ground: 29 York Street lost
    10 of the 11 panels on its sunny face (Josh, 24 Oct). A plane through the
    20 m of terrain fixed York but failed worse on houses cut into a slope --
    the plane runs above the building's platform (4721951, Frankton Arm, a
    200-panel roof zeroed in the v57 build). The ground right beside the
    building, at its low end, is what the roof stands above in both cases; a
    carpark or slab is still level with the ground around it. On 200-roof
    samples, sparse drops: central Queenstown 336 (median) -> 73, Wanaka 177
    -> 33, Arrowtown 446 -> 421 (there almost all on buildings newer than the
    survey, which the LiDAR sees as bare ground)."""
    for r, q in ((GROUND_NEAR_M, GROUND_NEAR_Q), (GROUND_SEARCH_RADIUS_M, GROUND_FAR_Q)):
        g = pc.ground_points_in_bbox(x - r, y - r, x + r, y + r)
        if len(g) >= 20:
            return float(np.percentile(g[:, 2], q))
    return None


def rerack(features):
    """After the gate, each face's panels are racked again: a panel the gate
    took out of an array can leave a lone panel or a stray pair behind (54,505
    panels in central Queenstown shipped with 105 lone and 904 in pairs), and
    the fitter's array rules ran before the gate. Only faces that lost panels
    are touched. Returns (features, panels removed)."""
    from collections import defaultdict
    from src.panel_fitting import enforce_arrays, shape_arrays
    key = lambda q: (q.get("building_id"), round(float(q.get("plane_aspect_deg") or 0), 4),
                     round(float(q.get("plane_slope_deg") or 0), 4))
    face_area, face_n, groups = {}, {}, defaultdict(list)
    for i, f in enumerate(features):
        q = f["properties"]
        if q.get("kind") == "facet" and q.get("plane_aspect_deg") is not None:
            face_n[key(q)] = q.get("panel_count")
            try:
                face_area[key(q)] = shp_transform(TO_NZTM, shape(f["geometry"])).area
            except Exception:
                pass
        elif q.get("kind") == "panel" and q.get("plane_aspect_deg") is not None:
            groups[key(q)].append(i)
    drop = set()
    for k, idx in groups.items():
        fa = face_area.get(k)
        if face_n.get(k) is not None and face_n[k] <= len(idx):
            continue   # the gate took nothing from this face
        pan = [{"geometry": shp_transform(TO_NZTM, shape(features[i]["geometry"])),
                "facet_aspect_deg": k[1], "facet_slope_deg": k[2], "_i": i} for i in idx]
        kept = shape_arrays(enforce_arrays(pan, face_area=fa), face_area=fa)
        alive = {p["_i"] for p in kept}
        drop.update(i for i in idx if i not in alive)
    return [f for i, f in enumerate(features) if i not in drop], len(drop)


def _photo_read(name):
    """Buildings whose roof was read from the photo because the LiDAR has
    nothing there -- houses built after the 2021 survey, or in its gaps
    (tools/predict_photo_shape.py, photo_roofs.json). The surface tests ask
    the LiDAR whether a panel sits on roof; there it sees the bare ground the
    house was built on, so they deleted ~90% of these buildings' panels
    (Arrowtown Millbrook, v55: 12,206 fitted, 1,456 shipped). Like a drawn
    roof, they skip the surface gates."""
    try:
        from src.synthesize_dsm import load_photo_roofs
        return {int(b) for b in load_photo_roofs(name)}
    except Exception:
        return set()


def _has_usable_markup(building_id):
    """A drawn roof is not second-guessed by the surface gates, for the
    same reason a drawn face skips the plane-fit judgment: lumpy/sparse ask
    whether the SURFACE is real roof, and he has answered by hand. #4725488
    (119-face sawtooth, tiny 26-degree faces, 1.7 pts/m2): the sparse gate
    took his 100-panel roof to 32."""
    try:
        from src.roof_line_source import _labels, VOID_FLAGS
        lab = _labels().get(str(building_id))
        return bool(lab and lab.get("problem") not in VOID_FLAGS
                    and (lab.get("faces") or []))
    except Exception:
        return False


def gate_area(name, pc, only_ids=None):
    import config
    non_roof = getattr(config, "NON_ROOF_BUILDING_IDS", set())
    path = area_paths(name)["panel_layouts"]
    if not path.exists():
        print(f"{name}: no layouts, skipping")
        return
    d = json.loads(path.read_text())
    photo_read = _photo_read(name)
    # Counter, not a fixed dict: adding a new veto reason to panel_ok() should
    # show up in the summary, never KeyError halfway through an area.
    kept, dropped = [], Counter()
    errors = 0  # gate crashes keep the panel, but MUST be visible: a systematic
    # failure here would otherwise look like a clean run that changed nothing.
    for f in d["features"]:
        if f["properties"].get("kind") != "panel" or f["geometry"]["type"] != "Polygon":
            kept.append(f)
            continue
        if only_ids is not None and f["properties"].get("building_id") not in only_ids:
            kept.append(f)          # patch mode: gate only the rebuilt buildings
            continue
        if f["properties"].get("building_id") in non_roof:
            dropped["sparse"] += 1
            continue
        if _has_usable_markup(f["properties"].get("building_id")) \
                or f["properties"].get("building_id") in photo_read:
            kept.append(f)   # drawn and photo-read roofs skip the surface gates
            continue
        ok, why = _gate_feature(f, pc)
        if why == "error-kept":
            errors += 1   # never drop a panel on a gate crash
        if ok:
            kept.append(f)
        else:
            dropped[why] += 1
    kept, n_rerack = rerack(kept)
    if n_rerack:
        dropped["rerack"] += n_rerack
    n_dropped = sum(dropped.values())
    d["features"] = kept
    write_json_atomic(path, d)
    reasons = ", ".join(f"{k} {v}" for k, v in sorted(dropped.items())) or "none"
    msg = f"{name}: dropped {n_dropped} panels ({reasons})"
    if errors:
        msg += f"  [WARNING: {errors} panels kept because the gate errored]"
    print(msg)


# Workers this machine can actually feed, rather than a flat 4.
#
# The old flat cap dated from workers that cached EIGHT decoded LiDAR tiles
# each; eight of those crashed a 64 GB machine on Wellington's dense survey.
# The cache is three tiles now, and a running worker measured 0.70 GB RES on
# the VM -- of which the wide DEM was 0.07 GB, and that load is gone entirely
# (see _init_gate_worker). So the flat 4 was leaving a 16-core box at 25%
# while the gate is the single longest
# stage in the build (1240s on town_west_fernhill, against 362s for the layout
# builder that was already running ten workers).
#
# PER_WORKER_GB is set well above the 0.70 GB measured here on purpose: a
# denser survey decodes fatter tiles, and this stage is the one with a crash in
# its history. Bounding by RAM rather than hardcoding a number is the actual
# fix -- a smaller machine now scales itself down instead of trusting a
# constant that was tuned on someone else's hardware.
GATE_PER_WORKER_GB = 1.25
GATE_USABLE_RAM_FRACTION = 0.55   # headroom for the parent, which holds every feature
GATE_MAX_JOBS = 12                # past this the parent-side merge is the bottleneck


GATE_CACHED_TILES = 3   # _init_gate_worker's PointCloudSource cache


def _gate_jobs(name=None):
    import os
    per = GATE_PER_WORKER_GB
    if name is not None:
        # the survey's own tile size, from LAZ headers (pointcloud_source);
        # GATE_PER_WORKER_GB stays the floor, so Queenstown is unchanged
        try:
            from src.pointcloud_source import per_worker_gb
            from src.region_build import area_bbox_nztm
            per, tile_gb, _ = per_worker_gb(GATE_PER_WORKER_GB, 0.5, GATE_CACHED_TILES,
                                            area_bbox_nztm(name))
        except Exception:
            per = GATE_PER_WORKER_GB
    try:
        total_gb = (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
                    / 1024 ** 3)
    except (ValueError, OSError, AttributeError):
        return 4                  # unknown machine: the old conservative value
    by_ram = int(total_gb * GATE_USABLE_RAM_FRACTION // per)
    return max(1, min(by_ram, (os.cpu_count() or 2) - 1, GATE_MAX_JOBS))


_W = {}


def _init_gate_worker():
    import os
    os.environ["SOLAR_LAZ_SINGLE"] = "1"   # see pointcloud_source: one decode thread each
    """Per-process context: the point-cloud reader and the wide DEM. Loaded once
    per worker, not per panel."""
    # Workers x cached tiles x decoded-tile size is the real memory bill. Eight
    # workers each caching eight tiles crashed a 64 GB machine on Wellington's
    # dense survey; two tiles per worker is plenty here because a panel query
    # touches exactly the tile(s) under one building.
    _W["pc"] = PointCloudSource(max_cached_tiles=GATE_CACHED_TILES)
    # NO wide DEM here. Every worker used to read the whole mosaic and pass it
    # to panel_ok, which has not looked at it since the height-above-DEM test
    # was removed (see the comment in panel_ok) -- the load and the two
    # parameters outlived the only code that read them. That was 0.07 GB a
    # worker while the mosaic covered Queenstown; widening it to Kingston and
    # Wanaka on 22 September made it 1.12 GB, so twelve workers would have held
    # 13 GB of an array nothing reads.


def _gate_feature(f, pc):
    """(ok, why) for one panel feature -- the ONE per-panel rule, used by the
    serial gate (patching) and the parallel one (district builds) alike. The
    serial path used to lack the corrupt-geometry veto, so a patched building
    and the same building in a full build could be gated differently."""
    try:
        poly = shp_transform(TO_NZTM, shape(f["geometry"]))
        minx, miny, maxx, maxy = poly.bounds
        if (maxx - minx) > 50 or (maxy - miny) > 50:
            # A panel is ~2 m. A bounds span past 50 m is corrupt geometry, and
            # its bbox query scans every tile -- one such panel wedged the
            # island_bay gate at 5,000/121,273 with ordered reporting hiding
            # everything queued behind it. Corrupt input never earns a panel.
            return False, "corrupt-geometry"
        return panel_ok(poly, pc)
    except Exception:
        return True, "error-kept"


def _gate_one(feature_json):
    ok, why = _gate_feature(json.loads(feature_json), _W["pc"])
    return feature_json, ok, why



def _gate_batch(feature_jsons):
    return [_gate_one(fj) for fj in feature_jsons]


def gate_area_parallel(name, jobs=None):
    """gate_area, fanned across processes. This stage was the wall-clock floor
    of every build: single-threaded at 9-11 minutes per area while everything
    around it scaled with cores. Panels are independent, so it fans trivially;
    measured behaviour is identical because panel_ok is pure per panel."""
    from concurrent.futures import ProcessPoolExecutor
    import config
    non_roof = getattr(config, "NON_ROOF_BUILDING_IDS", set())
    path = area_paths(name)["panel_layouts"]
    if not path.exists():
        print(f"{name}: no layouts, skipping")
        return
    d = json.loads(path.read_text())
    # Results land in their INPUT slot, not in completion order. Appending as
    # futures finished put every gated panel after every facet and in a
    # different order each run, so rerank_layouts (which numbers arrays and
    # breaks fill_rank ties by input order) gave the same roof different
    # array ids and fill orders from one run to the next. Slotting them back
    # makes this exactly gate_area's output, as the docstring always claimed.
    out = [None] * len(d["features"])
    dropped, errors = Counter(), 0
    photo_read = _photo_read(name)
    todo, todo_slot = [], []
    for i, f in enumerate(d["features"]):
        if f["properties"].get("kind") != "panel" or f["geometry"]["type"] != "Polygon":
            out[i] = f
        elif f["properties"].get("building_id") in non_roof:
            dropped["sparse"] += 1
        elif _has_usable_markup(f["properties"].get("building_id")) \
                or f["properties"].get("building_id") in photo_read:
            out[i] = f   # drawn and photo-read roofs skip the surface gates -- see gate_area
        else:
            todo.append(json.dumps(f))
            todo_slot.append(i)
    jobs = jobs or _gate_jobs(name)
    # Spawn, never fork. On Linux the default is fork, and forked children
    # inherit the parent's initialised GDAL/rasterio state -- workers segfault
    # and the pool dies with BrokenProcessPool (seen on the VM's first run).
    # macOS spawns by default, which is why local testing never showed it.
    import multiprocessing
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=jobs, initializer=_init_gate_worker,
                             mp_context=ctx) as ex:
        # Explicit futures, completion order, hard timeout. ex.map wedged
        # deterministically on island_bay at result 5,000 with every worker
        # asleep and the data proven clean single-process -- whatever the
        # stdlib queue pathology was, ordered iteration hid all progress
        # behind it. as_completed reports truth in real time, and a future
        # that never finishes gets NAMED and counted, never waited on forever.
        from concurrent.futures import as_completed
        BATCH = 64
        done_n = 0
        batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
        futs = {ex.submit(_gate_batch, b): i for i, b in enumerate(batches)}
        for fut in as_completed(futs, timeout=None):
            bi = futs[fut]
            try:
                results = fut.result(timeout=600)
            except Exception as exc:
                print(f"  {name}: batch {bi} failed ({exc!r}); "
                      f"{BATCH} panels kept ungated", flush=True)
                results = [(fj, True, "error-kept") for fj in batches[bi]]
            for j, (fj, ok, why) in enumerate(results):
                done_n += 1
                if done_n % 5000 == 0:
                    print(f"  {name}: {done_n}/{len(todo)} panels gated", flush=True)
                if why == "error-kept":
                    errors += 1
                if ok:
                    out[todo_slot[bi * BATCH + j]] = json.loads(fj)
                else:
                    dropped[why] += 1
    feats, n_rerack = rerack([f for f in out if f is not None])
    if n_rerack:
        dropped["rerack"] += n_rerack
    n_dropped = sum(dropped.values())
    d["features"] = feats
    write_json_atomic(path, d)
    reasons = ", ".join(f"{k} {v}" for k, v in sorted(dropped.items())) or "none"
    msg = f"{name}: dropped {n_dropped} panels ({reasons}) [{jobs} workers]"
    if errors:
        msg += f"  [WARNING: {errors} panels kept because the gate errored]"
    print(msg, flush=True)


def main():
    for name in (sys.argv[1:] or all_areas()):
        preflight("gate_panels", name)
        gate_area_parallel(name)


if __name__ == "__main__":
    main()
