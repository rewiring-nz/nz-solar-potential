"""
Run the full pipeline over every building and write per-facet and
per-panel geometries (not just building-level aggregates) to
data/panel_layouts.geojson, so the frontend can show the actual proposed
layout -- not just a kWp number -- when a building is clicked.

One FeatureCollection, features tagged with a "kind" property ("facet" or
"panel") and "building_id" so the frontend can filter to just the
clicked building's layout. Facets carry slope/aspect/irradiance;
panels carry which facet they belong to.

Usage: python src/build_layout_geojson.py
"""

import json
import sys
import time
from pathlib import Path

import pyproj
import rasterio
from shapely.ops import transform as shapely_transform

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import geopandas as gpd

from src.roof_segmentation import segment_building
from src.panel_fitting import fit_panels_on_facet
from src.obstruction_detection import detect_obstructions
from src.solar_model import SolarModel

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def main():
    gdf = gpd.read_file(DATA_DIR / "building_outlines.geojson")
    dsm_ds = rasterio.open(DATA_DIR / "dsm_mosaic.tif")
    imagery_ds = rasterio.open(DATA_DIR / "imagery_mosaic.tif")
    to_wgs84 = pyproj.Transformer.from_crs("EPSG:2193", "EPSG:4326", always_xy=True).transform

    print("Building solar yield lookup table (pvlib + NASA POWER)...")
    model = SolarModel()

    features = []
    t0 = time.time()
    for i, row in enumerate(gdf.itertuples()):
        facets = segment_building(dsm_ds, row.geometry, row.building_id)

        for f in facets:
            obstructions = detect_obstructions(imagery_ds, f["geometry"])
            panels = fit_panels_on_facet(f, obstructions=obstructions)
            poa = model.annual_poa_kwh_per_m2(f["slope_deg"], f["aspect_deg"])

            features.append({
                "type": "Feature",
                "geometry": shapely_transform(to_wgs84, f["geometry"]).__geo_interface__,
                "properties": {
                    "kind": "facet",
                    "building_id": int(row.building_id),
                    "slope_deg": round(f["slope_deg"], 1),
                    "aspect_deg": round(f["aspect_deg"], 1),
                    "poa_kwh_m2_yr": round(poa, 0),
                    "panel_count": len(panels),
                },
            })

            for o in obstructions:
                features.append({
                    "type": "Feature",
                    "geometry": shapely_transform(to_wgs84, o).__geo_interface__,
                    "properties": {"kind": "obstruction", "building_id": int(row.building_id)},
                })

            for p in panels:
                y = model.facet_yield(f, 1)
                features.append({
                    "type": "Feature",
                    "geometry": shapely_transform(to_wgs84, p["geometry"]).__geo_interface__,
                    "properties": {
                        "kind": "panel",
                        "building_id": int(row.building_id),
                        "ac_kwh_year": round(y["ac_kwh_year"], 0),
                    },
                })

        if i % 200 == 0:
            print(f"  {i}/{len(gdf)} elapsed={time.time() - t0:.1f}s")

    geojson = {"type": "FeatureCollection", "features": features}
    out_path = DATA_DIR / "panel_layouts.geojson"
    out_path.write_text(json.dumps(geojson))

    n_facets = sum(1 for f in features if f["properties"]["kind"] == "facet")
    n_panels = sum(1 for f in features if f["properties"]["kind"] == "panel")
    n_obs = sum(1 for f in features if f["properties"]["kind"] == "obstruction")
    print(f"\nSaved {out_path} ({out_path.stat().st_size / 1e6:.1f}MB)")
    print(f"{n_facets} facets, {n_panels} panels, {n_obs} obstructions across {len(gdf)} buildings")

    dsm_ds.close()
    imagery_ds.close()


if __name__ == "__main__":
    main()
