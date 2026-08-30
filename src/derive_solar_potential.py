"""Derive a region's solar_potential.geojson FROM its built panel layouts.

build_heatmap re-runs the full pipeline (segment, obstructions, panels) to
produce the building-summary layer -- hours of recomputation of numbers the
layout stage already wrote, and a standing source of disagreement between the
two layers (5 Beach St: 135 vs 203 panels, documented in preview.html). This
derives the same schema by aggregation in seconds, and the layers cannot
disagree because one IS the sum of the other.

Usage: python src/derive_solar_potential.py <region> [...]
"""
import json, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import geopandas as gpd
import config
from src.region_build import area_paths


def derive(region):
    paths = area_paths(region)
    gdf = gpd.read_file(paths["outlines"]).set_index("building_id", drop=False)
    gdf_wgs = gdf.to_crs("EPSG:4326")
    d = json.loads(paths["panel_layouts"].read_text())
    agg = defaultdict(lambda: {"facet_count": 0, "obstruction_count": 0,
                               "panel_count": 0, "ac_kwh_year": 0.0,
                               "facet_area_m2": 0.0, "poa_w": 0.0})
    for f in d["features"]:
        p = f["properties"]
        a = agg[p["building_id"]]
        k = p["kind"]
        if k == "facet":
            a["facet_count"] += 1
            area = p.get("area_m2") or 0.0
            a["facet_area_m2"] += area
            a["poa_w"] += area * (p.get("poa_kwh_m2_yr") or 0.0)
        elif k == "obstruction":
            a["obstruction_count"] += 1
        elif k == "panel":
            a["panel_count"] += 1
            a["ac_kwh_year"] += p.get("ac_kwh_year") or 0.0

    panel_kw = config.PV_ASSUMPTIONS["panel_rated_power_w"] / 1000.0
    features = []
    for bid, row in gdf_wgs.iterrows():
        a = agg.get(bid)
        if a is None:
            continue
        kwp = a["panel_count"] * panel_kw
        features.append({
            "type": "Feature",
            "geometry": row.geometry.__geo_interface__,
            "properties": {
                "building_id": int(bid),
                "facet_count": a["facet_count"],
                "obstruction_count": a["obstruction_count"],
                "panel_count": a["panel_count"],
                "kwp": round(kwp, 2),
                "ac_kwh_day_avg": round(a["ac_kwh_year"] / 365.0, 1),
                "ac_kwh_year": round(a["ac_kwh_year"], 0),
                "facet_area_m2": round(a["facet_area_m2"], 1),
                "avg_poa_kwh_m2": round(a["poa_w"] / a["facet_area_m2"], 0)
                                  if a["facet_area_m2"] > 0 else 0,
            },
        })
    out = {"type": "FeatureCollection", "assumptions": config.PV_ASSUMPTIONS,
           "features": features}
    paths["solar_potential"].write_text(json.dumps(out))
    print(f"{region}: {len(features)} buildings, "
          f"{sum(1 for f in features if f['properties']['panel_count'])} with panels")


if __name__ == "__main__":
    for r in sys.argv[1:]:
        derive(r)
