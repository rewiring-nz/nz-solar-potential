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


def main():
    layouts = json.loads((DATA_DIR / "panel_layouts.geojson").read_text())
    per_building = {}
    for f in layouts["features"]:
        p = f["properties"]
        if p["kind"] != "panel":
            continue
        per_building.setdefault(p["building_id"], []).append(
            (p.get("fill_rank", 100), p.get("ac_kwh_year", 0)))

    sp_path = DATA_DIR / "solar_potential.geojson"
    sp = json.loads(sp_path.read_text())
    matched = 0
    for feat in sp["features"]:
        b = feat["properties"]["building_id"]
        panels = per_building.get(b, [])
        for d in DECILES:
            kept = [(r, k) for r, k in panels if r <= d]
            feat["properties"][f"fill_panels_{d}"] = len(kept)
            feat["properties"][f"fill_kwh_{d}"] = int(round(sum(k for _, k in kept)))
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
