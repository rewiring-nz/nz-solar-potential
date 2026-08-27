"""
Run the full pipeline over every building and write per-facet and
per-panel geometries (not just building-level aggregates) to
data/panel_layouts.geojson, so the frontend can show the actual proposed
layout -- not just a kWp number -- when a building is clicked.

One FeatureCollection, features tagged with a "kind" property ("facet" or
"panel") and "building_id" so the frontend can filter to just the
clicked building's layout. Facets carry slope/aspect/irradiance;
panels carry which facet they belong to.

Runs the per-building work across processes. Buildings are independent --
segmentation, obstruction detection and packing all read shared rasters and
write nothing shared -- and the parallel wrapper around this only fans out
across AREAS, so rebuilding one area (the loop Josh actually iterates on) used
one core out of twelve and took hours. The same per-building work parallelises
to ~25 minutes for the pilot in scan_defects.py.

Usage: python src/build_layout_geojson.py [area ...] [--jobs N]
"""

import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pyproj
import rasterio
from shapely.ops import transform as shapely_transform

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import geopandas as gpd

from src.roof_segmentation import segment_building_best, _area_weighted_inlier
from src.pointcloud_source import PointCloudSource
from src.panel_fitting import fit_panels_on_facet, drop_minor_arrays, assign_fill_ranks
from src.obstruction_detection import detect_obstructions_combined
from src.solar_model import SolarModel
from src.building_shading import building_shading_factor
from src.region_build import area_paths, area_centroid_wgs84, areas_from_argv

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEEP_SHADE_FACTOR = 0.45  # a panel keeping less than this share of the year's direct beam is
# under a tree/neighbour and should not be proposed at all

# Below this share of a roof's points lying within 30 cm of the planes we are
# about to place panels on, do not propose panels at all. Josh said it twice
# about 10 Stanley St, once unprompted and once on the comparison sheet: "very
# complicated roof, this one should probably just have no panels on it at all".
# Showing a confident-looking layout on a roof we have not understood is worse
# than showing nothing -- the number carries authority it has not earned.
# 10 Stanley measures 35% on-plane; the next worst building in the sampled
# pilot set is 52%, so this separates the genuinely unmodelled roofs rather
# than trimming a continuum.
MIN_ROOF_CONFIDENCE = 0.45

DEFAULT_MAX_JOBS = 6

_CTX = {}


def _init_worker(area, model):
    """One heavy context per process. The SolarModel is built ONCE in the parent
    and shipped in (0.5 MB, picklable): constructing it per worker would fire a
    NASA POWER request per process."""
    paths = area_paths(area)
    dsm_ds = rasterio.open(paths["dsm"])
    _CTX.update({
        "gdf": gpd.read_file(paths["outlines"]).set_index("building_id", drop=False),
        "dsm_ds": dsm_ds,
        "dsm_band": dsm_ds.read(1),
        "imagery_ds": rasterio.open(paths["imagery"]) if paths["imagery"].exists() else None,
        "pc_source": PointCloudSource(),
        "model": model,
        "to_wgs84": pyproj.Transformer.from_crs("EPSG:2193", "EPSG:4326",
                                                always_xy=True).transform,
    })


def _build_one(building_id):
    """Everything for one building. Returns its GeoJSON features."""
    try:
        return _build_one_inner(building_id)
    except Exception as exc:
        print(f"  building {building_id} FAILED: {exc!r}", flush=True)
        return []


def _build_one_inner(building_id):
    c = _CTX
    model, dsm_ds, dsm_band = c["model"], c["dsm_ds"], c["dsm_band"]
    to_wgs84, pc_source, imagery_ds = c["to_wgs84"], c["pc_source"], c["imagery_ds"]
    row_geom = c["gdf"].loc[building_id].geometry

    features = []
    # Imagery is passed in so the partition can cut on roof creases the LiDAR
    # cannot resolve -- see roof_partition.partition_roof. Rural areas have no
    # imagery and fall back to LiDAR-only cuts.
    facets = segment_building_best(dsm_ds, pc_source, row_geom, building_id,
                                   imagery_ds=imagery_ds)

    # Do not propose panels on a roof we have not understood -- see
    # MIN_ROOF_CONFIDENCE. Facets are still emitted so the roof draws on the
    # map; only the layout is withheld.
    confidence = _area_weighted_inlier(facets, pc_source) if facets else 0.0
    modelled = confidence >= MIN_ROOF_CONFIDENCE

    per_facet = []
    for f in facets:
        facet_centroid = f["geometry"].centroid
        shading_factor = building_shading_factor(
            dsm_band, dsm_ds.transform, dsm_ds.nodata,
            facet_centroid.x, facet_centroid.y, model.hourly,
            own_geom=f["geometry"], terrain_horizon_profile=model.horizon_profile)
        facet_poa = model.annual_poa_kwh_per_m2(f["slope_deg"], f["aspect_deg"])
        if not modelled:
            per_facet.append({"facet": f, "panels": [], "obstructions": [],
                              "poa": facet_poa * shading_factor,
                              "shading_factor": shading_factor})
            continue
        plane = (f["plane_a"], f["plane_b"], f["plane_c"])
        obstructions = detect_obstructions_combined(imagery_ds, pc_source, f["geometry"], plane)
        siblings = [other for other in facets if other is not f]
        panels = fit_panels_on_facet(f, obstructions=obstructions, sibling_facets=siblings)
        kept_panels = []
        for pnl in panels:
            cpt = pnl["geometry"].centroid
            psf = building_shading_factor(dsm_band, dsm_ds.transform, dsm_ds.nodata,
                                          cpt.x, cpt.y, model.hourly, own_geom=f["geometry"],
                                          terrain_horizon_profile=model.horizon_profile)
            if psf < DEEP_SHADE_FACTOR:
                continue
            pnl["poa_kwh_m2_yr"] = facet_poa * psf
            pnl["shading_factor"] = psf
            kept_panels.append(pnl)
        per_facet.append({"facet": f, "panels": kept_panels, "obstructions": obstructions,
                          "poa": facet_poa * shading_factor, "shading_factor": shading_factor})

    kept_panel_lists = drop_minor_arrays([pf["panels"] for pf in per_facet])
    all_kept = [pnl for panels in kept_panel_lists for pnl in panels]
    if all_kept:
        assign_fill_ranks(all_kept)

    for pf, panels in zip(per_facet, kept_panel_lists):
        f = pf["facet"]
        features.append({
            "type": "Feature",
            "geometry": shapely_transform(to_wgs84, f["geometry"]).__geo_interface__,
            "properties": {
                "kind": "facet",
                "building_id": int(building_id),
                "slope_deg": round(f["slope_deg"], 1),
                "aspect_deg": round(f["aspect_deg"], 1),
                "poa_kwh_m2_yr": round(pf["poa"], 0),
                "panel_count": len(panels),
                "roof_confidence": round(confidence, 2),
            },
        })
        for o in pf["obstructions"]:
            features.append({
                "type": "Feature",
                "geometry": shapely_transform(to_wgs84, o).__geo_interface__,
                "properties": {"kind": "obstruction", "building_id": int(building_id)},
            })
        for pnl in panels:
            y = model.facet_yield(f, 1, shading_factor=pnl.get("shading_factor", pf["shading_factor"]))
            features.append({
                "type": "Feature",
                "geometry": shapely_transform(to_wgs84, pnl["geometry"]).__geo_interface__,
                "properties": {
                    "kind": "panel",
                    "building_id": int(building_id),
                    "ac_kwh_year": round(y["ac_kwh_year"], 0),
                    "fill_rank": pnl["fill_rank"],
                    "fill_order": pnl["fill_order"],
                    "array_id": pnl["array_id"],
                    "array_size": pnl["array_size"],
                },
            })
    return features


def main(area="pilot", jobs=None, limit=0, dry_run=False):
    paths = area_paths(area)
    gdf = gpd.read_file(paths["outlines"])
    ids = [int(b) for b in gdf["building_id"].tolist()]
    if limit:
        ids = ids[:limit]

    print(f"[{area}] Building solar yield lookup table (pvlib + NASA POWER)...")
    centroid = area_centroid_wgs84(area)
    model = SolarModel() if centroid is None else SolarModel(*centroid)

    # Bounded deliberately, and not by core count. PointCloudSource caches every
    # decoded LiDAR tile for the life of its process -- the module docstring for
    # run_full_build.sh warns the full set is ~10GB decoded -- so each worker
    # carries its own copy of whatever tiles its buildings touch. Defaulting to
    # cpu_count-1 gave 11 workers on this machine and the run was killed before
    # it produced a single feature. Six is what has actually been measured
    # working, and it already gives most of the speedup (280s -> 114s on 100
    # buildings).
    jobs = jobs or min(DEFAULT_MAX_JOBS, max(1, (os.cpu_count() or 2) - 1))
    print(f"[{area}] {len(ids)} buildings on {jobs} workers", flush=True)

    features, done, t0 = [], 0, time.time()
    if jobs == 1:
        _init_worker(area, model)
        for bid in ids:
            features.extend(_build_one(bid))
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(ids)} elapsed={time.time() - t0:.1f}s", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=jobs, initializer=_init_worker,
                                 initargs=(area, model)) as ex:
            # chunked so the per-building task dispatch does not dominate
            for chunk in ex.map(_build_one, ids, chunksize=8):
                features.extend(chunk)
                done += 1
                if done % 200 == 0:
                    print(f"  {done}/{len(ids)} elapsed={time.time() - t0:.1f}s", flush=True)

    if dry_run:
        print(f"[{area}] dry run: {len(features)} features in {time.time() - t0:.0f}s "
              f"on {jobs} workers -- nothing written")
        return
    geojson = {"type": "FeatureCollection", "features": features}
    out_path = paths["panel_layouts"]
    out_path.write_text(json.dumps(geojson))

    n_facets = sum(1 for f in features if f["properties"]["kind"] == "facet")
    n_panels = sum(1 for f in features if f["properties"]["kind"] == "panel")
    n_obs = sum(1 for f in features if f["properties"]["kind"] == "obstruction")
    gated = len({f["properties"]["building_id"] for f in features
                 if f["properties"]["kind"] == "facet"
                 and f["properties"].get("roof_confidence", 1.0) < MIN_ROOF_CONFIDENCE})
    print(f"\nSaved {out_path} ({out_path.stat().st_size / 1e6:.1f}MB) in {time.time() - t0:.0f}s")
    print(f"{n_facets} facets, {n_panels} panels, {n_obs} obstructions across {len(ids)} buildings")
    print(f"{gated} buildings withheld as not confidently modelled (<{MIN_ROOF_CONFIDENCE:.0%} on-plane)")


if __name__ == "__main__":
    _jobs, _limit = None, 0
    _argv = sys.argv[:]
    for _flag in ("--jobs", "--limit"):
        if _flag in _argv:
            _i = _argv.index(_flag)
            _val = int(_argv[_i + 1])
            _argv = _argv[:_i] + _argv[_i + 2:]
            if _flag == "--jobs":
                _jobs = _val
            else:
                _limit = _val
    _dry = "--dry-run" in _argv
    _argv = [a for a in _argv if a != "--dry-run"]
    for _area in areas_from_argv(_argv):
        main(_area, jobs=_jobs, limit=_limit, dry_run=_dry)
