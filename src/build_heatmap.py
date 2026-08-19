"""
Run the full pipeline (segment -> fit panels -> solar yield) over every
building in the pilot bbox and write a single GeoJSON: one feature per
building (footprint geometry), properties = kWp, average daily kWh,
annual kWh, panel/facet counts, plus the PV assumptions used -- so the
frontend can render both the heatmap and the click-to-inspect popup
straight from this one file, and the displayed assumptions can never
drift from what was actually calculated.

Usage: python src/build_heatmap.py
"""

import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from src.roof_segmentation import segment_building
from src.panel_fitting import fit_panels_on_facet
from src.obstruction_detection import detect_obstructions
from src.solar_model import SolarModel

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def main():
    gdf = gpd.read_file(DATA_DIR / "building_outlines.geojson")
    gdf_wgs84 = gdf.to_crs("EPSG:4326")  # for the map frontend
    dsm_ds = rasterio.open(DATA_DIR / "dsm_mosaic.tif")
    imagery_ds = rasterio.open(DATA_DIR / "imagery_mosaic.tif")

    print("Building solar yield lookup table (pvlib + NASA POWER)...")
    model = SolarModel()

    features = []
    t0 = time.time()
    for i, (row, row_wgs84) in enumerate(zip(gdf.itertuples(), gdf_wgs84.itertuples())):
        facets = segment_building(dsm_ds, row.geometry, row.building_id)

        kwp = dc_kwh_year = ac_kwh_year = ac_kwh_day = panel_count = obstruction_count = 0
        facet_area_m2 = poa_weighted_sum = 0
        for f in facets:
            poa = model.annual_poa_kwh_per_m2(f["slope_deg"], f["aspect_deg"])
            facet_area_m2 += f["area_m2"]
            poa_weighted_sum += f["area_m2"] * poa

            obstructions = detect_obstructions(imagery_ds, f["geometry"])
            obstruction_count += len(obstructions)
            panels = fit_panels_on_facet(f, obstructions=obstructions)
            if not panels:
                continue
            y = model.facet_yield(f, len(panels))
            panel_count += len(panels)
            kwp += y["kwp"]
            dc_kwh_year += y["dc_kwh_year"]
            ac_kwh_year += y["ac_kwh_year"]
            ac_kwh_day += y["ac_kwh_day_avg"]

        # For the Heat Map mode's "if X% of the roof were covered" estimate --
        # computed client-side from these two (area x coverage x panel power
        # density gives kWp; multiplying further by avg_poa_kwh_m2/1000
        # gives kWh/yr), not from the actual fitted panel layout, so the
        # slider can answer "what if" without a server round-trip.
        avg_poa_kwh_m2 = round(poa_weighted_sum / facet_area_m2, 0) if facet_area_m2 > 0 else 0

        features.append({
            "type": "Feature",
            "geometry": row_wgs84.geometry.__geo_interface__,
            "properties": {
                "building_id": int(row.building_id),
                "facet_count": len(facets),
                "obstruction_count": obstruction_count,
                "panel_count": panel_count,
                "kwp": round(kwp, 2),
                "ac_kwh_day_avg": round(ac_kwh_day, 1),
                "ac_kwh_year": round(ac_kwh_year, 0),
                "facet_area_m2": round(facet_area_m2, 1),
                "avg_poa_kwh_m2": avg_poa_kwh_m2,
            },
        })
        if i % 200 == 0:
            print(f"  {i}/{len(gdf)} elapsed={time.time() - t0:.1f}s")

    geojson = {
        "type": "FeatureCollection",
        "assumptions": config.PV_ASSUMPTIONS,
        "features": features,
    }

    out_path = DATA_DIR / "solar_potential.geojson"
    out_path.write_text(json.dumps(geojson))

    n_with_panels = sum(1 for f in features if f["properties"]["panel_count"] > 0)
    total_kwp = sum(f["properties"]["kwp"] for f in features)
    total_kwh_year = sum(f["properties"]["ac_kwh_year"] for f in features)
    print(f"\nSaved {out_path}")
    print(f"{len(features)} buildings, {n_with_panels} with viable panels, "
          f"{total_kwp:.0f} kWp total, {total_kwh_year:,.0f} kWh/year total for the pilot bbox")

    dsm_ds.close()
    imagery_ds.close()


if __name__ == "__main__":
    main()
