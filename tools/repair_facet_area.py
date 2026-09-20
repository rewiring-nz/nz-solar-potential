"""Recompute facet_area_m2 / avg_poa_kwh_m2 from the merged layouts.

Repairs the damage from patch_buildings reading p["area_m2"] off a facet
feature, which the layout emitter does not write. Every building that driver
touched came out with facet_area_m2 = 0, and Heat Map mode -- whose whole
estimate is kWp = area x coverage x density -- showed it as 0.0 kW, while
Panel Layout mode two clicks away showed the same roof's real output.
Measured before the fix: 2,496 of the district's 14,507 roofs with panels.

The driver is fixed; this repairs the standing file so the district does not
need a four-hour rebuild to stop lying about a sixth of its roofs.

Computed the same way src/derive_solar_potential.py does it, through the same
helper, so this cannot drift from the stage whose output it is patching.

FROM THE MERGED LAYOUTS, DELIBERATELY, and that needs saying because the
per-region layouts are the stage's own input and they DISAGREE: on the pilot
region alone, 503 buildings have more facets in the merged file and 59 have
fewer. The merged file is the newer of the two -- this month's work added
facets, through residual fill and the plateau splits -- and, more to the
point, it is the one tippecanoe builds panel_layouts.pmtiles from, so it is
what the map actually draws. Aligning the dashboard to anything else would
make the summary disagree with the panels beside it.

That the two ever diverged is its own bug and is logged in BACKLOG.md.

Usage: python tools/repair_facet_area.py [--write]
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.derive_solar_potential import _facet_area_m2
from src.region_build import write_json_atomic

DATA = Path(__file__).resolve().parents[1] / "data"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    layouts = DATA / "panel_layouts.geojson"
    sp_path = DATA / "solar_potential.geojson"
    if not layouts.exists() or not sp_path.exists():
        print("need both data/panel_layouts.geojson and data/solar_potential.geojson")
        return 1

    agg = defaultdict(lambda: {"area": 0.0, "poa_w": 0.0, "n": 0})
    for f in json.loads(layouts.read_text())["features"]:
        p = f["properties"]
        if p.get("kind") != "facet":
            continue
        b = agg[p["building_id"]]
        area = _facet_area_m2(f)
        b["area"] += area
        b["poa_w"] += area * (p.get("poa_kwh_m2_yr") or 0.0)
        b["n"] += 1

    sp = json.loads(sp_path.read_text())
    fixed = moved = 0
    for f in sp["features"]:
        p = f["properties"]
        b = agg.get(p.get("building_id"))
        if not b or b["n"] == 0:
            continue
        area = round(b["area"], 1)
        poa = round(b["poa_w"] / b["area"], 0) if b["area"] > 0 else 0
        was = p.get("facet_area_m2") or 0
        if abs(was - area) > 0.05:
            moved += 1
            if not was:
                fixed += 1
        p["facet_area_m2"] = area
        p["avg_poa_kwh_m2"] = poa

    print(f"{moved} buildings with a different roof area, "
          f"{fixed} of them previously zero")
    if not a.write:
        print("dry run -- pass --write to apply")
        return 0
    write_json_atomic(sp_path, sp)
    print("written. Now re-run:")
    print("  python src/bake_density_deciles.py")
    print("  python src/split_building_detail.py")
    print("  python src/build_building_tiles.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
