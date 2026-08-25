"""
Bake per-building density-decile stats into data/solar_potential.geojson:
for each building, cumulative placed panel count and annual kWh at fill
densities 10..100 in steps of 10, as properties fill_panels_10..100 and
fill_kwh_10..100 (kWh rounded to int).

Why: with panel geometry served as vector tiles, the frontend can no longer
sum every panel in the viewport for the left dashboard's "buildings in map
view" estimate (tiles outside the view / below the zoom simply aren't
loaded). These ten numbers per building make that estimate a cheap sum over
the (small, always fully loaded) buildings source at ANY zoom, and they
also power the per-building panel-placement box without needing the
building's tiles. Runs on the MERGED files after merge_regions.

Usage: python src/bake_density_deciles.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.region_build import write_json_atomic

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DECILES = list(range(10, 101, 10))
MIN_CLEAN_ARRAY = 4       # matches panel_fitting.MINOR_ARRAY_MIN_PANELS
# Panel counts a real quote lands on, at 440W: roughly 3, 4.5, 6, 7.5, 9, 12,
# 15, 20 and 30 kW. Households sit in the first half of that ladder.
SYSTEM_PANEL_STEPS = [7, 10, 14, 17, 20, 27, 34, 45, 68]


def main():
    layouts = json.loads((DATA_DIR / "panel_layouts.geojson").read_text())
    per_building = {}
    for f in layouts["features"]:
        p = f["properties"]
        if p["kind"] != "panel":
            continue
        per_building.setdefault(p["building_id"], []).append(
            (p.get("fill_rank", 100), p.get("ac_kwh_year", 0),
             p.get("fill_order", 0), p.get("array_size", 1)))

    sp_path = DATA_DIR / "solar_potential.geojson"
    sp = json.loads(sp_path.read_text())
    matched = 0
    for feat in sp["features"]:
        b = feat["properties"]["building_id"]
        panels = per_building.get(b, [])
        for d in DECILES:
            kept = [t for t in panels if t[0] <= d]
            feat["properties"][f"fill_panels_{d}"] = len(kept)
            feat["properties"][f"fill_kwh_{d}"] = int(round(sum(t[1] for t in kept)))

        # "Clean arrays only": panels sitting in a contiguous block of at least
        # MIN_CLEAN_ARRAY. Baked per building so the map-view total for that
        # mode is a real sum and not an approximation -- 92% of panels are in
        # blocks of 30+, but the buildings where that is NOT true are exactly
        # the complex roofs this mode is meant to treat differently.
        clean = [t for t in panels if t[3] >= MIN_CLEAN_ARRAY]
        feat["properties"]["fill_panels_arrays"] = len(clean)
        feat["properties"]["fill_kwh_arrays"] = int(round(sum(t[1] for t in clean)))

        # System-size targeting: cumulative kWh by fill_order, so the frontend
        # can ask "the best N panels" (a 6kW system) and get the right energy
        # without loading the panel tiles. Stored at the sizes a real quote
        # uses; households are 3-12kW (Josh), so the ladder is dense there.
        by_order = sorted(t for t in panels if t[2])
        for n in SYSTEM_PANEL_STEPS:
            sel = by_order[:n]
            feat["properties"][f"sys_kwh_{n}"] = int(round(sum(t[1] for t in sel)))
        if panels:
            matched += 1
    write_json_atomic(sp_path, sp)
    print(f"Baked deciles for {matched}/{len(sp['features'])} buildings "
          f"({sp_path.stat().st_size / 1e6:.1f}MB)")
    # The join is by building_id across two independently written files. If it
    # ever breaks (a type change, a stale merge), every decile bakes as 0 and
    # the whole in-view estimate silently reads zero on a map that still looks
    # correct. Say so here instead of shipping it.
    if matched < 0.5 * len(sp["features"]):
        print(f"  WARNING: only {matched} of {len(sp['features'])} buildings matched a "
              f"layout by building_id -- expected most of them. Check that "
              f"panel_layouts.geojson is the merged file for this build.")


if __name__ == "__main__":
    main()
