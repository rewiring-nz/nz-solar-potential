"""
Recompute every kWh in a region's layouts from the CURRENT solar model,
without touching geometry.

WHY THIS IS ITS OWN STAGE. The build used to compute each panel's yield at the
moment it laid the panel out and store only the answer, so any change to the
sun side of the model -- the cloud calibration, the derate, the lookup table
-- needed every roof in the country re-laid, LiDAR and all. The geometry and
the sun change for different reasons and at very different costs:

    geometry  faces, obstructions, panels, each panel's shading factor
              (near-field neighbours and far terrain)       -- hours, LiDAR
    sun       kWh per m2 at a slope and aspect, calibration,
              inverter and system losses                    -- minutes, no LiDAR

build_layout_geojson now writes each facet's and panel's plane slope, plane
aspect and shading factor unrounded (plane_slope_deg, plane_aspect_deg,
shading_factor). This stage reads them back and rewrites poa_kwh_m2_yr on
facets and ac_kwh_year on panels with the model as it stands now. Run on
fresh output it changes nothing (tests/synthetic checks that it is
bit-identical); after a model change, follow it with the rest of the yield
layer:

    run_district_build.sh --yield-only
    (= apply_yield, rerank_layouts, derive_solar_potential,
       patch_roof_confidence, bake_building_horizons, build_heatmap_raster,
       emit_region, then combine)

Layouts built before this stage existed carry no yield inputs; their
features are left untouched and counted, and the region needs one re-lay to
join the yield layer.

Usage: python src/apply_yield.py [region ...|all]   (no args = pilot, as every builder)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.preflight import preflight
from src.region_build import area_paths, area_centroid_wgs84, areas_from_argv, write_json_atomic
from src.solar_model import SolarModel

YIELD_KEYS = ("plane_slope_deg", "plane_aspect_deg", "shading_factor")


def apply_yield(layouts, model):
    """Rewrite facet POA and panel kWh in a layouts document, in place.
    Returns (updated, changed, missing): features recomputed, how many of
    those got a different value, and features that carry no yield inputs."""
    updated = changed = missing = 0
    for f in layouts["features"]:
        p = f["properties"]
        kind = p.get("kind")
        if kind not in ("facet", "panel"):
            continue
        if any(k not in p for k in YIELD_KEYS):
            missing += 1
            continue
        plane = {"slope_deg": p["plane_slope_deg"], "aspect_deg": p["plane_aspect_deg"]}
        if kind == "facet":
            # same expression as build_layout_geojson: facet POA x facet shading
            new = round(model.annual_poa_kwh_per_m2(plane["slope_deg"], plane["aspect_deg"])
                        * p["shading_factor"], 0)
            key = "poa_kwh_m2_yr"
        else:
            new = round(model.facet_yield(plane, 1, shading_factor=p["shading_factor"])
                        ["ac_kwh_year"], 0)
            key = "ac_kwh_year"
        if p.get(key) != new:
            changed += 1
        p[key] = new
        updated += 1
    return updated, changed, missing


def main(area):
    preflight("apply_yield", area)
    path = area_paths(area)["panel_layouts"]
    layouts = json.loads(path.read_text())
    centroid = area_centroid_wgs84(area)
    # Built exactly as build_layout_geojson builds it, so fresh output
    # round-trips bit for bit.
    model = SolarModel() if centroid is None else SolarModel(*centroid)
    updated, changed, missing = apply_yield(layouts, model)
    from src.build_keys import yield_code_hash
    layouts["yield_model"] = yield_code_hash()
    write_json_atomic(path, layouts)
    msg = f"[{area}] yield: {updated:,} features recomputed, {changed:,} changed"
    if missing:
        msg += (f"  [WARNING: {missing:,} features predate the yield layer and were "
                f"left as they are -- re-lay this region once to include them]")
    print(msg, flush=True)


if __name__ == "__main__":
    for _area in areas_from_argv(sys.argv):
        main(_area)
