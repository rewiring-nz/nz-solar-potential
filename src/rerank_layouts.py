"""
Re-band fill_rank in already-built panel_layouts.geojson files to the
straggler-banding scheme (panel_fitting.assign_fill_ranks, 22 Aug):
ranks 1..80 = main arrays, 81..100 = straggler blocks that only exist
when the building has a big main array (>= MAIN_ARRAY_MIN_PANELS).

Exists so the banding change doesn't force a third multi-hour refit of
every region: ranks are pure post-processing over geometry the files
already carry. Idempotent -- built areas re-ranked twice band the same.

Group = contiguous ARRAY, matching the build-time rule in
panel_fitting.assign_fill_ranks. It used to be the facet -- panels do not
record their facet, so each was joined to one by point-in-ring -- but a facet
is not an array: a curved roof split into three sections has every section
large enough to escape straggler banding while each holds a clean block plus a
scatter of lone panels.

Usage: python src/rerank_layouts.py [region ...]   (default: all areas)
"""

import json
import math
import sys
from pathlib import Path

import pyproj
from shapely.geometry import shape
from shapely.ops import transform as shapely_transform
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.preflight import preflight
from src.panel_fitting import (MAIN_ARRAY_MIN_PANELS, MINOR_ARRAY_MIN_FRACTION,
                               MINOR_ARRAY_MIN_PANELS, MINOR_ARRAY_ALWAYS_KEEP_PANELS,
                               STRAGGLER_RANK_FLOOR)
from src.region_build import all_areas, area_paths, write_json_atomic


def rerank_area(name):
    path = area_paths(name)["panel_layouts"]
    if not path.exists():
        print(f"{name}: no panel_layouts, skipping")
        return
    data = json.loads(path.read_text())

    buildings = {}
    for f in data["features"]:
        p = f["properties"]
        b = buildings.setdefault(p["building_id"], {"facets": [], "panels": []})
        if p["kind"] == "facet" and f["geometry"]["type"] == "Polygon":
            b["facets"].append(f)
        elif p["kind"] == "panel":
            b["panels"].append(f)

    n_stragglers = 0
    for b in buildings.values():
        if not b["panels"]:
            continue
        # Arrays FIRST -- the ordering below depends on them. This used to run
        # at the end of the loop, so ranking never knew what an array was, and
        # grouped by facet instead. See panel_fitting.assign_fill_ranks for the
        # same fix and the reasoning: on a curved roof split into three
        # sections, every section is big enough to escape straggler banding
        # while holding a clean block plus a scatter of lone panels.
        #
        # Pocket panels (gap fill, 100% only) are grouped on their own AFTER
        # the main panels, so a pocket bridging two arrays cannot merge them
        # and change what a partial layout shows.
        rest = [pf for pf in b["panels"] if not pf["properties"].get("gap_fill")]
        pocket = [pf for pf in b["panels"] if pf["properties"].get("gap_fill")]
        n_arrays = _assign_arrays(rest)
        _assign_arrays(pocket, start_id=n_arrays)

        groups = {}  # array id -> [panel feature]
        for pf in rest:
            groups.setdefault(pf["properties"].get("array_id", 0), []).append(pf)

        largest = max((len(g) for g in groups.values()), default=0)
        straggler_ids = set()
        if largest >= MAIN_ARRAY_MIN_PANELS:
            cutoff = min(MINOR_ARRAY_ALWAYS_KEEP_PANELS,
                         max(MINOR_ARRAY_MIN_PANELS, MINOR_ARRAY_MIN_FRACTION * largest))
            for g in groups.values():
                if 0 < len(g) < cutoff:
                    straggler_ids.update(id(pf) for pf in g)
        # Panels fitted on a low plane-fit big-roof facet ship demoted, not
        # deleted (build_layout_geojson tags them low_conf_fit). This pass
        # recomputes bands from array sizes alone, so without this line a
        # 90-panel low-confidence array would climb back to default density.
        straggler_ids.update(id(pf) for pf in b["panels"]
                             if pf["properties"].get("low_conf_fit"))

        # Whole arrays in order of total yield, and within an array the
        # existing rank, which preserves the compact reverse-erosion order the
        # original fit produced. Ordering by facet sunniness -- what this did
        # before -- is what made the density slider strip a whole dim SIDE
        # before it touched the lone panels on the sunny side.
        # MEAN yield per panel, not total -- same fix as
        # panel_fitting._order_by_array (#4740662: total let 40 shaded
        # panels outrank 18 sunny ones, so the slider showed the shady side
        # first). This file runs after the fitter and overwrites its ranks,
        # so the fix must live here too or it silently un-happens: #4751009
        # shipped with its 598 kWh/panel array ranked BELOW its 355 one.
        yield_of = {}
        for aid, g in groups.items():
            yield_of[aid] = (sum(pf["properties"].get("ac_kwh_year") or 0
                                 for pf in g) / len(g))
        for pf in pocket:
            aid = pf["properties"]["array_id"]
            if aid not in yield_of:
                g = [q for q in pocket if q["properties"]["array_id"] == aid]
                yield_of[aid] = sum(q["properties"].get("ac_kwh_year") or 0 for q in g) / len(g)
        # fill_order breaks fill_rank ties. fill_rank is a banded percentile,
        # so several panels share one, and a tie fell back to their order in
        # the FILE -- which meant re-ranking an already-ranked layout (every
        # yield-only rebuild, src/apply_yield.py) could swap neighbours that a
        # fresh build would not: 18 of 508 synthetic panels on first test.
        # fill_order is the exact sequence the fit (or the last rerank)
        # produced, consistent with fill_rank, so ties now follow the fit.
        key = lambda pf: (-yield_of.get(pf["properties"].get("array_id", 0), 0),
                          pf["properties"].get("array_id", 0),
                          pf["properties"].get("fill_rank", 100),
                          pf["properties"].get("fill_order", 0))
        # Pocket panels exist for 100% only: ranked last, at 100.
        pocket = sorted(pocket, key=key)
        main = sorted((pf for pf in rest if id(pf) not in straggler_ids), key=key)
        extras = sorted((pf for pf in rest if id(pf) in straggler_ids), key=key)
        for i, pf in enumerate(main):
            pf["properties"]["fill_rank"] = int(math.ceil((i + 1) / len(main) * STRAGGLER_RANK_FLOOR))
        for j, pf in enumerate(extras):
            pf["properties"]["fill_rank"] = STRAGGLER_RANK_FLOOR + int(
                math.ceil((j + 1) / len(extras) * (100 - STRAGGLER_RANK_FLOOR)))
        for pf in pocket:
            pf["properties"]["fill_rank"] = 100
        n_stragglers += len(extras)

        # fill_order: the same sequence as an exact count, so the frontend can
        # ask for "the best 14 panels" (a 6kW system) instead of a percentage.
        for i, pf in enumerate(main + extras + pocket):
            pf["properties"]["fill_order"] = i + 1

    write_json_atomic(path, data)
    print(f"{name}: {len(buildings)} buildings re-ranked, {n_stragglers} straggler panels banded 81-100")


ARRAY_TOUCH_TOL_M = 0.35

# The layout geojson is EPSG:4326. Buffering by 0.35 in degrees is a ~39 km
# probe, so every panel touched every other one and every building came out as
# a single array: measured on the shipped pilot file, 0 of 1,033 buildings had
# more than one. array_id and array_size have therefore been meaningless in the
# tiles since they were added, and any frontend filter built on them could
# never have worked. Cluster in metres.
_TO_NZTM = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2193", always_xy=True).transform


def _assign_arrays(panel_features, start_id=0):
    """Contiguous-block id and size over the SURVIVING panels of one building.
    Ids continue from start_id; returns the last id used."""
    if not panel_features:
        return start_id
    geoms = [shapely_transform(_TO_NZTM, shape(pf["geometry"])) for pf in panel_features]
    tree = STRtree(geoms)
    seen, gid = {}, start_id
    for i in range(len(geoms)):
        if i in seen:
            continue
        gid += 1
        stack, members = [i], []
        seen[i] = gid
        while stack:
            k = stack.pop()
            members.append(k)
            probe = geoms[k].buffer(ARRAY_TOUCH_TOL_M)
            for j in tree.query(probe):
                j = int(j)
                if j not in seen and probe.intersects(geoms[j]):
                    seen[j] = gid
                    stack.append(j)
        for k in members:
            panel_features[k]["properties"]["array_id"] = gid
            panel_features[k]["properties"]["array_size"] = len(members)
    return gid


def main():
    for name in (sys.argv[1:] or all_areas()):
        preflight("rerank_layouts", name)
        rerank_area(name)


if __name__ == "__main__":
    main()
