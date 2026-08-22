"""
Re-band fill_rank in already-built panel_layouts.geojson files to the
straggler-banding scheme (panel_fitting.assign_fill_ranks, 22 Aug):
ranks 1..80 = main arrays, 81..100 = straggler blocks that only exist
when the building has a big main array (>= MAIN_ARRAY_MIN_PANELS).

Exists so the banding change doesn't force a third multi-hour refit of
every region: ranks are pure post-processing over geometry the files
already carry. Idempotent -- built areas re-ranked twice band the same.

Panels in the geojson don't record their facet, so each panel is joined
to its building's facet by first-vertex point-in-ring (same join the
frontend curves use). Group = facet, matching the build-time rule.

Usage: python src/rerank_layouts.py [region ...]   (default: all areas)
"""

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.panel_fitting import (MAIN_ARRAY_MIN_PANELS, MINOR_ARRAY_MIN_FRACTION,
                               MINOR_ARRAY_MIN_PANELS, STRAGGLER_RANK_FLOOR)
from src.region_build import all_areas, area_paths


def point_in_ring(pt, ring):
    x, y = pt
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


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
        groups = {}  # facet index -> [panel feature]
        for pf in b["panels"]:
            c = pf["geometry"]["coordinates"][0][0]
            fi = next((i for i, ff in enumerate(b["facets"])
                       if point_in_ring(c, ff["geometry"]["coordinates"][0])), -1)
            groups.setdefault(fi, []).append(pf)

        largest = max(len(g) for g in groups.values())
        straggler_ids = set()
        if largest >= MAIN_ARRAY_MIN_PANELS:
            for g in groups.values():
                if 0 < len(g) < max(MINOR_ARRAY_MIN_PANELS, MINOR_ARRAY_MIN_FRACTION * largest):
                    straggler_ids.update(id(pf) for pf in g)

        # Order within each band: sunniest facet first (facet poa), then the
        # existing rank to preserve the original row-major fill order.
        facet_poa = {i: (ff["properties"].get("poa_kwh_m2_yr") or 0)
                     for i, ff in enumerate(b["facets"])}
        panel_facet = {id(pf): fi for fi, g in groups.items() for pf in g}
        key = lambda pf: (-facet_poa.get(panel_facet[id(pf)], 0), panel_facet[id(pf)],
                          pf["properties"].get("fill_rank", 100))
        main = sorted((pf for pf in b["panels"] if id(pf) not in straggler_ids), key=key)
        extras = sorted((pf for pf in b["panels"] if id(pf) in straggler_ids), key=key)
        for i, pf in enumerate(main):
            pf["properties"]["fill_rank"] = int(math.ceil((i + 1) / len(main) * STRAGGLER_RANK_FLOOR))
        for j, pf in enumerate(extras):
            pf["properties"]["fill_rank"] = STRAGGLER_RANK_FLOOR + int(
                math.ceil((j + 1) / len(extras) * (100 - STRAGGLER_RANK_FLOOR)))
        n_stragglers += len(extras)

    path.write_text(json.dumps(data))
    print(f"{name}: {len(buildings)} buildings re-ranked, {n_stragglers} straggler panels banded 81-100")


def main():
    for name in (sys.argv[1:] or all_areas()):
        rerank_area(name)


if __name__ == "__main__":
    main()
