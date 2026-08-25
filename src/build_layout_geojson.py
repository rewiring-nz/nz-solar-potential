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

from src.roof_segmentation import segment_building_best
from src.pointcloud_source import PointCloudSource
from src.panel_fitting import fit_panels_on_facet, drop_minor_arrays, assign_fill_ranks
from src.obstruction_detection import detect_obstructions_combined
from src.solar_model import SolarModel
from src.building_shading import building_shading_factor
from src.region_build import area_paths, area_centroid_wgs84, areas_from_argv

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEEP_SHADE_FACTOR = 0.45  # a panel keeping less than this share of the year's direct beam is
# under a tree/neighbour and should not be proposed at all


def main(area="pilot"):
    paths = area_paths(area)
    gdf = gpd.read_file(paths["outlines"])
    dsm_ds = rasterio.open(paths["dsm"])
    imagery_ds = rasterio.open(paths["imagery"]) if paths["imagery"].exists() else None
    pc_source = PointCloudSource()
    to_wgs84 = pyproj.Transformer.from_crs("EPSG:2193", "EPSG:4326", always_xy=True).transform

    print(f"[{area}] Building solar yield lookup table (pvlib + NASA POWER)...")
    centroid = area_centroid_wgs84(area)
    model = SolarModel() if centroid is None else SolarModel(*centroid)
    dsm_band = dsm_ds.read(1)  # loaded once, reused for every building's own near-field shading scan

    features = []
    t0 = time.time()
    for i, row in enumerate(gdf.itertuples()):
        facets = segment_building_best(dsm_ds, pc_source, row.geometry, row.building_id)

        # Two passes per building: fit everything first, then building-level
        # post-processing (straggler-array removal + fill ranks) needs every
        # facet's panels in hand before anything is emitted.
        per_facet = []
        for f in facets:
            facet_centroid = f["geometry"].centroid
            shading_factor = building_shading_factor(dsm_band, dsm_ds.transform, dsm_ds.nodata,
                                                       facet_centroid.x, facet_centroid.y, model.hourly,
                                                       own_geom=f["geometry"], terrain_horizon_profile=model.horizon_profile)
            plane = (f["plane_a"], f["plane_b"], f["plane_c"])
            obstructions = detect_obstructions_combined(imagery_ds, pc_source, f["geometry"], plane)
            siblings = [other for other in facets if other is not f]
            panels = fit_panels_on_facet(f, obstructions=obstructions, sibling_facets=siblings)
            facet_poa = model.annual_poa_kwh_per_m2(f["slope_deg"], f["aspect_deg"])
            # PER-PANEL shading. A facet-wide scalar averages a tree that
            # shades one end of a roof across the whole face, so panels get
            # placed in deep shade and merely dim the facet's yield a little.
            # Measured spread across a single treed roof: 0.63-0.90. Panels
            # below DEEP_SHADE_FACTOR are dropped outright -- no installer
            # puts a panel under a canopy. ~1.2ms per panel, so affordable.
            kept_panels = []
            for p in panels:
                c = p["geometry"].centroid
                psf = building_shading_factor(dsm_band, dsm_ds.transform, dsm_ds.nodata,
                                               c.x, c.y, model.hourly, own_geom=f["geometry"],
                                               terrain_horizon_profile=model.horizon_profile)
                if psf < DEEP_SHADE_FACTOR:
                    continue
                p["poa_kwh_m2_yr"] = facet_poa * psf
                p["shading_factor"] = psf
                kept_panels.append(p)
            panels = kept_panels
            poa = facet_poa * shading_factor
            per_facet.append({"facet": f, "panels": panels, "obstructions": obstructions,
                               "poa": poa, "shading_factor": shading_factor})

        kept_panel_lists = drop_minor_arrays([pf["panels"] for pf in per_facet])
        all_kept = [p for panels in kept_panel_lists for p in panels]
        assign_fill_ranks(all_kept)

        for pf, panels in zip(per_facet, kept_panel_lists):
            f = pf["facet"]
            features.append({
                "type": "Feature",
                "geometry": shapely_transform(to_wgs84, f["geometry"]).__geo_interface__,
                "properties": {
                    "kind": "facet",
                    "building_id": int(row.building_id),
                    "slope_deg": round(f["slope_deg"], 1),
                    "aspect_deg": round(f["aspect_deg"], 1),
                    "poa_kwh_m2_yr": round(pf["poa"], 0),
                    "panel_count": len(panels),
                },
            })

            for o in pf["obstructions"]:
                features.append({
                    "type": "Feature",
                    "geometry": shapely_transform(to_wgs84, o).__geo_interface__,
                    "properties": {"kind": "obstruction", "building_id": int(row.building_id)},
                })

            for p in panels:
                # each panel's kWh uses ITS OWN shading factor (set above), so a
                # partly-shaded roof reports a realistic mix rather than one average
                y = model.facet_yield(f, 1, shading_factor=p.get("shading_factor", pf["shading_factor"]))
                features.append({
                    "type": "Feature",
                    "geometry": shapely_transform(to_wgs84, p["geometry"]).__geo_interface__,
                    "properties": {
                        "kind": "panel",
                        "building_id": int(row.building_id),
                        "ac_kwh_year": round(y["ac_kwh_year"], 0),
                        "fill_rank": p["fill_rank"],
                        # fill_order is the same sequence as an exact count, so
                        # the frontend can ask for "the best 14 panels" (a 6kW
                        # system) rather than a percentage, which is the way a
                        # real quote is actually sized. array_size is how many
                        # panels are in this panel's contiguous block, so
                        # "clean arrays only, nothing under N" is a client-side
                        # filter and can be retuned without a rebuild.
                        "fill_order": p["fill_order"],
                        "array_id": p["array_id"],
                        "array_size": p["array_size"],
                    },
                })

        if i % 200 == 0:
            print(f"  {i}/{len(gdf)} elapsed={time.time() - t0:.1f}s")

    geojson = {"type": "FeatureCollection", "features": features}
    out_path = paths["panel_layouts"]
    out_path.write_text(json.dumps(geojson))

    n_facets = sum(1 for f in features if f["properties"]["kind"] == "facet")
    n_panels = sum(1 for f in features if f["properties"]["kind"] == "panel")
    n_obs = sum(1 for f in features if f["properties"]["kind"] == "obstruction")
    print(f"\nSaved {out_path} ({out_path.stat().st_size / 1e6:.1f}MB)")
    print(f"{n_facets} facets, {n_panels} panels, {n_obs} obstructions across {len(gdf)} buildings")

    dsm_ds.close()
    if imagery_ds is not None:
        imagery_ds.close()


if __name__ == "__main__":
    for _area in areas_from_argv(sys.argv):
        main(_area)
