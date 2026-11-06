"""
The methodology diagram on the map ("How this map is made"), written from
the code.

methodology.js draws methodology.json; this module writes that file. The
diagram must describe what the code does, so nothing in it is typed twice:

  * NO NUMBER IS TYPED IN PROSE. A figure in a sentence is a {placeholder}
    filled from the code (a config value, a module constant) when the file is
    written. build() refuses prose containing a digit, so a value cannot be
    written in by hand and left behind when the code changes.
  * EVERY BUILD STEP IS PLACED. Each item lists the build stages it describes
    (src/build_plan.py). tests/test_methodology.py fails when a stage in the
    plan is in no item, so a new stage cannot ship undescribed.
  * EVERY CODE REFERENCE RESOLVES. Items cite "path::symbol"; the test fails
    when the file or the top-level name has gone.
  * SOURCES ARE LINZ'S WORDS. Accuracy, resolution and capture dates come from
    data/source_metadata.json (tools/fetch_source_metadata.py, LINZ's own
    layer metadata). The test fails when config names a layer that file does
    not describe.
  * THE FILE IS CURRENT. The test rebuilds the file and fails if the
    committed methodology.json differs.

Run:  python src/methodology.py          # rewrite methodology.json
      python src/methodology.py --check  # exit 1 if it is out of date
"""
from __future__ import annotations

import argparse
import ast
import math
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "methodology.json"
META = ROOT / "data" / "source_metadata.json"


# ---- values from the code ---------------------------------------------------
def _config():
    import config
    return config


def _module_const(path: str, name: str):
    """A top-level constant from a module, read without importing it (so the
    diagram needs no geospatial stack): the literal assigned to `name`."""
    tree = ast.parse((ROOT / path).read_text())
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                return ast.literal_eval(node.value)
    raise KeyError(f"{path}: no top-level constant {name}")


def _env_default(path: str, name: str) -> float:
    """The default of a constant set as float(os.environ.get("SOLAR_X", "0.6")),
    read from the module's source: the value the build uses when the switch is
    unset."""
    tree = ast.parse((ROOT / path).read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            for sub in ast.walk(node.value):
                if (isinstance(sub, ast.Call) and getattr(sub.func, "attr", "") == "get"
                        and len(sub.args) == 2 and isinstance(sub.args[1], ast.Constant)):
                    return float(sub.args[1].value)
            return float(ast.literal_eval(node.value))
    raise KeyError(f"{path}: no top-level {name}")


def _js_const(path: str, pattern: str):
    """A number from a JavaScript file, by a regex with one group."""
    m = re.search(pattern, (ROOT / path).read_text())
    if not m:
        raise KeyError(f"{path}: {pattern!r} not found")
    return float(m.group(1))


def _module_const_list(path: str, name: str):
    """A top-level list whose items may hold expressions: its length from the
    AST, without evaluating it."""
    tree = ast.parse((ROOT / path).read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value.elts
    raise KeyError(f"{path}: no top-level list {name}")


def _era5_cell_deg() -> float:
    """The ERA5 grid spacing, from the cell files' names (lat_lon.json)."""
    lats = sorted({float(f.stem.split("_")[0]) for f in (ROOT / "climate" / "era5").glob("*_*.json")})
    return min(b - a for a, b in zip(lats, lats[1:]))


def _era5_years() -> str:
    """The years ERA5's monthly means are taken over, from solar_model's filter."""
    m = re.search(r"\(\s*(\d{4})\s*<=\s*day\.year\s*<=\s*(\d{4})\s*\)", (ROOT / "src/solar_model.py").read_text())
    return f"{m.group(1)}–{m.group(2)}"


def _fmt(v, dp=None):
    if isinstance(v, float) and dp is None:
        dp = 0 if v.is_integer() else (2 if abs(v) < 10 else 1)
    if isinstance(v, (int, float)):
        return f"{v:,.{dp or 0}f}"
    return str(v)


def values() -> dict:
    """Every figure the prose may quote, as {name: (text, source)}."""
    c = _config()
    pv = c.PV_ASSUMPTIONS
    inv = pv["inverter_efficiency_pct"] / 100
    derate = pv["system_derate_pct"] / 100
    v = {
        "panel_w": (_fmt(pv["panel_rated_power_w"]), "config.PV_ASSUMPTIONS.panel_rated_power_w"),
        "panel_eff": (_fmt(pv["panel_efficiency_pct"], 1), "config.PV_ASSUMPTIONS.panel_efficiency_pct"),
        "panel_w_m": (_fmt(c.PANEL_WIDTH_M, 3), "config.PANEL_WIDTH_M"),
        "panel_h_m": (_fmt(c.PANEL_HEIGHT_M, 3), "config.PANEL_HEIGHT_M"),
        "inverter_pct": (_fmt(pv["inverter_efficiency_pct"], 0), "config.PV_ASSUMPTIONS.inverter_efficiency_pct"),
        "derate_pct": (_fmt(pv["system_derate_pct"], 2), "config.PV_ASSUMPTIONS.system_derate_pct"),
        "total_loss_pct": (_fmt((1 - inv * (1 - derate)) * 100, 1),
                           "1 − inverter_efficiency × (1 − system_derate), config.PV_ASSUMPTIONS"),
        "edge_setback_m": (_fmt(c.PANEL_EDGE_SETBACK_M, 2), "config.PANEL_EDGE_SETBACK_M"),
        "ridge_setback_m": (_fmt(c.RIDGE_SETBACK_M, 2), "config.RIDGE_SETBACK_M"),
        "max_slope_deg": (_fmt(c.MAX_ROOF_SLOPE_DEG), "config.MAX_ROOF_SLOPE_DEG"),
        "guess_pitch_deg": (_fmt(c.PHOTO_ONLY_PITCH_DEG), "config.PHOTO_ONLY_PITCH_DEG"),
        "loan_rate_pct": (_fmt(_js_const("economics.js", r"loan_rate_pct:\s*([\d.]+)"), 1),
                          "economics.js ECON_DEFAULTS.loan_rate_pct"),
        "loan_years": (_fmt(_js_const("economics.js", r"loan_years:\s*([\d.]+)")),
                       "economics.js ECON_DEFAULTS.loan_years"),
        "life_years": (_fmt(_js_const("economics.js", r"life_years:\s*([\d.]+)")),
                       "economics.js ECON_DEFAULTS.life_years"),
        "discount_pct": (_fmt(_js_const("economics.js", r"discount_rate_pct:\s*([\d.]+)"), 1),
                         "economics.js ECON_DEFAULTS.discount_rate_pct"),
        "survey_count": (_fmt(len(c.SURVEYS)), "len(config.SURVEYS)"),
    }
    # the home prices: the EA's figures for the site's network region
    rp_text = (ROOT / "regional_prices.js").read_text()
    rp = json.loads(rp_text.split("const REGIONAL_PRICES = ", 1)[1].split(";\nif ", 1)[0])
    code = re.search(r"priceRegion:\s*(\d+)", (ROOT / "site-config.js").read_text()).group(1)
    r = rp["regions"][code]
    v.update({
        "price_region": (r["name"], "regional_prices.js, region SITE.priceRegion"),
        "price_month": (r["month"][:7], "regional_prices.js month (EA)"),
        "price_c": (_fmt(r["variable_c"], 2), "regional_prices.js variable_c (EA, incl. GST)"),
        "peak_c": (_fmt(r["peak_c"], 0), "regional_prices.js peak_c"),
        "offpeak_c": (_fmt(r["offpeak_c"], 0), "regional_prices.js offpeak_c"),
        "daily_d": (_fmt(r["daily"], 2), "regional_prices.js daily (EA)"),
        "export_c": (_fmt(_js_const("economics.js", r"home_offpeak_sell_c:\s*([\d.]+)")),
                     "economics.js ECON_DEFAULTS.home_offpeak_sell_c"),
    })
    v["peak_export_c"] = (_fmt(_js_const("economics.js", r"home_peak_sell_c:\s*([\d.]+)")),
                          "economics.js ECON_DEFAULTS.home_peak_sell_c")
    v["biz_peak_c"] = (_fmt(_js_const("economics.js", r"biz_peak_c:\s*([\d.]+)")), "economics.js ECON_DEFAULTS.biz_peak_c")
    v["biz_offpeak_c"] = (_fmt(_js_const("economics.js", r"biz_offpeak_c:\s*([\d.]+)")), "economics.js ECON_DEFAULTS.biz_offpeak_c")
    # the other network regions the map's areas are priced for
    other = sorted({str(k) for k in re.findall(r":\s*(\d+)", re.search(r"priceRegionByArea:\s*\{([^}]*)\}",
                    (ROOT / "site-config.js").read_text()).group(1))} - {code})
    v["other_regions"] = ("; ".join(f"{rp['regions'][k]['name']} {_fmt(rp['regions'][k]['variable_c'], 2)}c "
                                    f"and ${_fmt(rp['regions'][k]['daily'], 2)} a day" for k in other),
                          "regional_prices.js, the regions in SITE.priceRegionByArea")
    M = lambda path, name, dp=None: (_fmt(_module_const(path, name), dp), f"{path} {name}")
    E = lambda path, name, dp=None: (_fmt(_env_default(path, name), dp), f"{path} {name} (default)")
    v.update({
        "lean_search_m": M("src/register_imagery.py", "SEARCH_M"),
        "outline_slide_m": M("src/outline_fit.py", "SHIFT_MAX_M"),
        "pick_min": E("src/roof_partition.py", "SELECTED_MIN_SCORE", 2),
        "pick_trust": E("src/roof_partition.py", "SELECTED_TRUST", 2),
        "plane_band_cm": (_fmt(_module_const("src/roof_partition.py", "INLIER_BAND_M") * 200, 0),
                          "2 × src/roof_partition.py INLIER_BAND_M, in cm"),
        "roof_conf_pct": (_fmt(_module_const("src/build_layout_geojson.py", "MIN_ROOF_CONFIDENCE") * 100, 0),
                          "src/build_layout_geojson.py MIN_ROOF_CONFIDENCE, as %"),
        "obs_height_m": M("src/obstruction_detection.py", "HEIGHT_RESIDUAL_THRESHOLD_M", 1),
        "obs_photo_radius_m": M("src/obstruction_detection.py", "LOCAL_BACKGROUND_RADIUS_M", 1),
        "slope_bin": M("src/solar_model.py", "SLOPE_BIN_DEG"),
        "aspect_bin": M("src/solar_model.py", "ASPECT_BIN_DEG"),
        "station_max_deg": M("src/solar_model.py", "NIWA_STATION_MAX_DIST_DEG", 1),
        "station_max_km": (_fmt(_module_const("src/solar_model.py", "NIWA_STATION_MAX_DIST_DEG") * 111, 0),
                           "src/solar_model.py NIWA_STATION_MAX_DIST_DEG x 111 km"),
        "solarview_km": (_fmt(_module_const("src/solar_model.py", "SOLARVIEW_CAL_MAX_DIST_DEG") * 111, 0),
                         "src/solar_model.py SOLARVIEW_CAL_MAX_DIST_DEG x 111 km"),
        "n_stations": (_fmt(len(_module_const_list("src/solar_model.py", "NIWA_MEASURED_GHI_STATIONS"))),
                       "src/solar_model.py NIWA_MEASURED_GHI_STATIONS, count"),
        "era5_ns_km": (_fmt(_era5_cell_deg() * 111, 0), "climate/era5 cell spacing x 111 km"),
        "era5_ew_km": (_fmt(_era5_cell_deg() * 111 * math.cos(math.radians(45)), 0),
                       "climate/era5 cell spacing x 111 km x cos 45 degrees"),
        "era5_years": (_era5_years(), "src/solar_model.py _era5_cell year filter"),
        "era5_max_elev": M("src/solar_model.py", "ERA5_MAX_CELL_ELEV_M"),
        "dem_m": ("8", "LINZ layer 51768 (data/dem_wide_mosaic.tif), 8 m cells"),
        "era5_lo": (_fmt(_module_const("src/solar_model.py", "ERA5_RATIO_RANGE")[0], 2), "src/solar_model.py ERA5_RATIO_RANGE[0]"),
        "era5_hi": (_fmt(_module_const("src/solar_model.py", "ERA5_RATIO_RANGE")[1], 2), "src/solar_model.py ERA5_RATIO_RANGE[1]"),
        "area_hz_step": M("src/terrain_horizon.py", "AZIMUTH_STEP_DEG"),
        "area_hz_km": M("src/terrain_horizon.py", "MAX_DISTANCE_KM"),
        "near_step_deg": M("src/building_shading.py", "BUILDING_AZIMUTH_STEP_DEG"),
        "near_m": (_fmt(_module_const("src/building_shading.py", "BUILDING_MAX_DISTANCE_KM") * 1000, 0),
                   "src/building_shading.py BUILDING_MAX_DISTANCE_KM, in m"),
        "far_bins": M("src/building_horizon.py", "N_BINS"),
        "far_km": M("src/building_horizon.py", "FAR_MAX_KM"),
        "eave_pctl": M("src/building_horizon.py", "EAVE_PCTL"),
        "deep_shade_pct": (_fmt(_module_const("src/build_layout_geojson.py", "DEEP_SHADE_FACTOR") * 100, 0),
                           "src/build_layout_geojson.py DEEP_SHADE_FACTOR, as %"),
        "sun_side_pct": (_fmt(_env_default("src/build_layout_geojson.py", "SUN_SIDE_REL") * 100, 0),
                         "src/build_layout_geojson.py SUN_SIDE_REL (default), as %"),
        "landscape_pct": (_fmt(_env_default("src/panel_fitting.py", "LANDSCAPE_WIN_MARGIN") * 100, 0),
                          "src/panel_fitting.py LANDSCAPE_WIN_MARGIN (default), as %"),
        "grid_cm": (_fmt(_module_const("src/panel_fitting.py", "RASTER_RESOLUTION_M") * 100, 0),
                    "src/panel_fitting.py RASTER_RESOLUTION_M, in cm"),
        "min_array": M("src/panel_fitting.py", "ARRAY_MIN_PANELS"),
        "heat_res_m": M("src/build_heatmap_raster.py", "HR_RES_M", 1),
        "home_cap_kw": (_fmt(_js_const("rooftop-solar.html", r"HOME_SYSTEM_CAP_KW\s*=\s*([\d.]+)")),
                        "rooftop-solar.html HOME_SYSTEM_CAP_KW"),
        "elec_infl_pct": (_fmt(_js_const("economics.js", r"elec_inflation_pct:\s*([\d.]+)"), 1),
                          "economics.js ECON_DEFAULTS.elec_inflation_pct"),
        "export_decline_pct": (_fmt(_js_const("economics.js", r"export_decline_pct:\s*([\d.]+)"), 1),
                               "economics.js ECON_DEFAULTS.export_decline_pct"),
        "degradation_pct": (_fmt(_js_const("economics.js", r"degradation_pct:\s*([\d.]+)"), 1),
                            "economics.js ECON_DEFAULTS.degradation_pct"),
        "cpi_pct": (_fmt(_js_const("economics.js", r"cpi_pct:\s*([\d.]+)"), 1), "economics.js ECON_DEFAULTS.cpi_pct"),
    })
    return v


# ---- the diagram ------------------------------------------------------------
# Each step: title, icon, one-line summary, items. Each item: title, text
# (plain words, {placeholders} only for figures), facts (value names shown in
# the Details table), code ("path::symbol"), stages (src/build_plan.py names
# or "fetch:<what>" for input preparation, "page:<file>" for the map page).
# "path::name" must be a top-level name in that Python file (or text in any
# other file); "path~~text" must appear in the file word for word -- used to
# pin the names of methods (Perez, Erbs) to the calls that use them.
STEPS: list[dict] = [
    {"title": "Build the 3D landscape", "icon": "🛰️",
     "summary": "Building outlines, a laser scan of every roof and tree, and aerial photos, all from LINZ.",
     "items": [
        {"title": "Which buildings exist",
         "text": "Every building outline in an area comes from LINZ's NZ Building Outlines, picked by the "
                 "suburb, town and council LINZ records on each outline.",
         "code": ["src/linz_exports.py", "src/localities.py"], "stages": ["fetch:src/fetch_regions.py"]},
        {"title": "A laser scan of the roofs",
         "text": "LINZ's LiDAR survey for the area: the raw point cloud (from OpenTopography's copy) where it "
                 "has been published, and LINZ's surface model otherwise. Only points classed as building are "
                 "used for roofs. The ground model and surface model give trees, neighbours and hills.",
         "code": ["src/fetch_pointcloud_regions.py::tilename_to_filename", "src/pointcloud_source.py::PointCloudSource",
                  "src/fetch_data.py::fetch_raster"],
         "stages": ["fetch:src/fetch_regions.py", "fetch:src/fetch_pointcloud_regions.py"]},
        {"title": "Distant hills",
         "text": "LINZ's national elevation model, out past the area, for hills on the horizon.",
         "code": ["src/fetch_dem_wide.py"], "stages": ["fetch:src/fetch_dem_wide.py"]},
        {"title": "Aerial photos",
         "text": "LINZ aerial photos, the newest that cover the area, for roof lines and objects on roofs. "
                 "The map itself shows LINZ Basemaps' photo.",
         "code": ["src/fetch_regions.py"], "stages": ["fetch:src/fetch_regions.py"]},
        {"title": "Places with no laser scan",
         "text": "Where no LiDAR survey exists, roofs are modelled from the outline: a gable or hipped roof at "
                 "{guess_pitch_deg}° for houses and flat for large buildings. Every such building says so on the map.",
         "facts": [("Assumed pitch", "guess_pitch_deg", "°")],
         "code": ["src/synthesize_dsm.py"], "stages": ["fetch:src/synthesize_dsm.py"]},
        {"title": "Lining the data up",
         "text": "The photo and the scan were taken in different years and lean differently. Each roof's lean "
                 "is measured by matching the photos (up to {lean_search_m} m), and an outline that sits off the "
                 "scanned roof is slid onto it (up to {outline_slide_m} m) without changing its shape.",
         "facts": [("Largest lean looked for", "lean_search_m", " m"), ("Largest outline slide", "outline_slide_m", " m")],
         "code": ["src/register_imagery.py", "src/outline_fit.py"], "stages": ["register_imagery", "outline_fit"]},
     ]},
    {"title": "Read each roof", "icon": "🏠",
     "summary": "Find the roof's faces, their slope and direction, and anything on them.",
     "items": [
        {"title": "Four readings of the roof",
         "text": "Each roof is read four ways: an image model that outlines shapes in the photo, a model "
                 "trained on hand-drawn roof lines, the laser scan's flat regions, and the simplest roof form "
                 "that fits. Each reading is scored on how well its edges match the lines in the photo and how "
                 "well its faces fit the scan, and the best is kept.",
         "code": ["tools/predict_faces.py", "src/face_candidates.py::score_candidate"],
         "stages": ["fetch:tools/predict_faces.py"]},
        {"title": "When the reading is doubtful",
         "text": "A reading scoring under {pick_min} is not used. One under {pick_trust} competes with faces "
                 "found from the laser scan alone, and the scan's version wins when it explains the roof better "
                 "or more simply.",
         "facts": [("Lowest score used", "pick_min", ""), ("Score that needs no contest", "pick_trust", "")],
         "code": ["src/roof_partition.py::SELECTED_TRUST", "src/roof_segmentation.py"],
         "stages": ["build_layout_geojson"]},
        {"title": "Hand-drawn roofs come first",
         "text": "Where a person has drawn a roof's faces and lines, the drawing is used as it is. Identical "
                 "roofs in a row of houses share the reading that explains the whole set best.",
         "code": ["src/roof_partition.py::partition_roof", "src/repeat_roofs.py"], "stages": ["repeat_roofs"]},
        {"title": "Slope and direction",
         "text": "Each face gets a flat plane fitted to its laser points, ignoring points more than "
                 "{plane_band_cm} cm off it. Faces steeper than {max_slope_deg}° are walls, not roof. A roof whose "
                 "points fit its planes less than {roof_conf_pct}% of the time gets no panels.",
         "facts": [("Steepest roof face", "max_slope_deg", "°"), ("Roof must fit its planes", "roof_conf_pct", "%")],
         "code": ["src/roof_partition.py::_fit_plane_robust", "src/build_layout_geojson.py::MIN_ROOF_CONFIDENCE"],
         "stages": ["patch_roof_confidence"]},
        {"title": "Objects on the roof",
         "text": "Vents, skylights, chimneys and plant are found two ways: laser points standing more than "
                 "{obs_height_m} m off the roof's plane, and patches of the photo that stand out from the roof "
                 "around them (within {obs_photo_radius_m} m). Panels keep clear of both.",
         "facts": [("Height off the plane", "obs_height_m", " m")],
         "code": ["src/obstruction_detection.py::detect_obstructions_combined", "src/obstruction_planarity.py"],
         "stages": ["build_layout_geojson"]},
     ]},
    {"title": "Sunlight", "icon": "☀️",
     "summary": "How much sun reaches a roof of each slope and direction over a year.",
     "items": [
        {"title": "Clear-sky sun, hour by hour",
         "text": "The sun's path and clear-sky light for every hour of a year, from the pvlib library's "
                 "Ineichen model.",
         "code": ["src/solar_model.py::build_poa_lookup_table", "src/solar_model.py~~model=\"ineichen\""]},
        {"title": "Cloud, from measured records",
         "text": "Clear-sky sun is cut each month to what ground instruments have measured (pyranometers, which "
                 "record all the sunlight reaching the ground). Near Queenstown (within about {solarview_km} km) "
                 "that is NIWA's SolarView record for Queenstown, built on decades of measurements at Queenstown "
                 "Airport. Elsewhere it is the nearest of {n_stations} NIWA radiation stations, up to about "
                 "{station_max_km} km away. Each roof's workings name its station and how far away it is.",
         "facts": [("NIWA stations", "n_stations", ""), ("Furthest station used", "station_max_km", " km")],
         "code": ["src/solar_model.py::solarview_calibrated_monthly_factors",
                  "src/solar_model.py::niwa_station_measured_monthly_factors"]},
        {"title": "Differences between towns",
         "text": "A station measures one spot. How much sunnier or duller each town is than its station comes "
                 "from ERA5, a global weather reanalysis (a weather model corrected by satellite and weather "
                 "observations), in cells about {era5_ns_km} km north to south by {era5_ew_km} km east to west, "
                 "averaged over {era5_years}. Only its differences between places are used, held between "
                 "{era5_lo} and {era5_hi} of the station's figure; cells averaging over {era5_max_elev} m high "
                 "are mountains, not towns, and are left out.",
         "facts": [("ERA5 cell", "era5_ns_km", " km north–south"), ("Years averaged", "era5_years", "")],
         "code": ["src/solar_model.py::era5_spatial_ratio", "src/solar_model.py::_era5_monthly"]},
        {"title": "What the sunlight data cannot see",
         "text": "Cloud is a long-term monthly average for a place, the same every day of that month: a typical "
                 "year, not this year. It is no finer than the station and the ERA5 cell, so it cannot see fog "
                 "or inversion cloud that sits in one valley or town, such as winter morning fog at Wanaka or "
                 "Cromwell. Where a town gets more of that than its station, its winter sun is overstated. "
                 "Hills, trees and buildings are different: they are measured for every roof, from {dem_m} m "
                 "terrain and the laser scan (Shade, below).",
         "code": ["src/solar_model.py::solarview_calibrated_monthly_factors", "src/solar_model.py::era5_spatial_ratio"]},
        {"title": "Onto a sloped roof",
         "text": "Sunlight is split into direct and diffuse light (the Erbs method) and turned onto each roof "
                 "slope and direction with the Perez sky model, in {slope_bin}° slope by {aspect_bin}° direction steps.",
         "facts": [("Slope step", "slope_bin", "°"), ("Direction step", "aspect_bin", "°")],
         "code": ["src/solar_model.py~~irradiance.erbs", "src/solar_model.py~~model=\"perez\""]},
     ]},
    {"title": "Shade", "icon": "🌳",
     "summary": "Hills, trees and buildings that block the sun, worked out for every panel.",
     "items": [
        {"title": "Nearby trees and buildings",
         "text": "From each panel, the laser scan's surface is searched in every direction (every "
                 "{near_step_deg}°, out to {near_m} m) for anything that blocks the sun.",
         "facts": [("Directions", "near_step_deg", "° apart"), ("Distance searched", "near_m", " m")],
         "code": ["src/building_shading.py::building_shading_factor"], "stages": ["build_layout_geojson"]},
        {"title": "Hills",
         "text": "Each building's horizon of hills, in {far_bins} directions out to {far_km} km, seen from its "
                 "eaves, and the area's own horizon out to {area_hz_km} km.",
         "facts": [("Directions", "far_bins", ""), ("Distance", "far_km", " km")],
         "code": ["src/building_horizon.py::far_profile", "src/terrain_horizon.py"],
         "stages": ["bake_building_horizons"]},
        {"title": "Deep shade",
         "text": "Direct sun that is blocked is taken off; light from the open sky is reduced by how much sky "
                 "the panel sees. A panel keeping less than {deep_shade_pct}% of its direct sun is not placed.",
         "facts": [("Deep-shade limit", "deep_shade_pct", "%")],
         "code": ["src/solar_model.py::SolarModel", "src/build_layout_geojson.py::DEEP_SHADE_FACTOR"]},
     ]},
    {"title": "Panels and power", "icon": "🔆",
     "summary": "Lay panels sunniest first and work out what each one makes.",
     "items": [
        {"title": "Laying out panels",
         "text": "{panel_w} W panels ({panel_w_m} × {panel_h_m} m) are packed in rows on each face, "
                 "{edge_setback_m} m from edges and ridges, portrait unless landscape fits {landscape_pct}% more. "
                 "A face pointing away from the sun with less than {sun_side_pct}% of the sunniest face's sun is left "
                 "bare. Stray panels go: an array needs at least {min_array}.",
         "facts": [("Panel", "panel_w", " W"), ("Edge setback", "edge_setback_m", " m")],
         "code": ["src/panel_fitting.py::fit_panels_on_facet", "src/panel_fitting.py::enforce_arrays"],
         "stages": ["build_layout_geojson", "gate_panels"]},
        {"title": "Sunniest first",
         "text": "Panels are ranked by how much they make, so a smaller system takes the best spots. The "
                 "density slider fills in that order.",
         "code": ["src/panel_fitting.py::assign_fill_ranks", "src/rerank_layouts.py::rerank_area"],
         "stages": ["rerank_layouts"]},
        {"title": "Power from each panel",
         "text": "Each panel's sunlight after shade, times its rating, less {total_loss_pct}% for the inverter, "
                 "heat, dirt, wiring and glass reflection. The building's total is the sum of its panels.",
         "facts": [("Inverter efficiency", "inverter_pct", "%"), ("Other losses", "derate_pct", "%"),
                   ("Total losses", "total_loss_pct", "%")],
         "code": ["src/solar_model.py::SolarModel", "src/apply_yield.py", "src/derive_solar_potential.py::derive"],
         "stages": ["derive_solar_potential", "apply_yield"]},
        {"title": "Buildings that are gone",
         "text": "An outline whose building no longer shows in the newest photo is withheld.",
         "code": ["src/demolished.py"], "stages": ["demolished"]},
        {"title": "Heat map mode",
         "text": "The heat map colours every {heat_res_m} m of roof by its yearly sunlight, from the same "
                 "sun and shade model.",
         "code": ["src/build_heatmap_raster.py::render_building"], "stages": ["build_heatmap_raster"]},
     ]},
    {"title": "Money", "icon": "💲",
     "summary": "Bills before and after, the cost, and the saving once a loan is repaid.",
     "items": [
        {"title": "A household's day",
         "text": "Each building's use of power hour by hour, for an average day in each season: a home from "
                 "the Electricity Authority's figures for its area, scaled for people and house size; a business "
                 "by its floor area. Solar powers the building first; the rest is exported.",
         "code": ["bill_tool.js::btEvaluate", "economics.js::simulateDay"], "stages": ["page:bill_tool.js"]},
        {"title": "Batteries",
         "text": "A battery charges only from spare solar. On a time-of-use plan it saves its charge for "
                 "peak hours, giving off-peak only what the coming peak will not need before the sun refills "
                 "it; on a flat plan it covers any shortfall as it comes. Each season's typical day is repeated "
                 "until the battery's charge settles, and the charts show it charging and powering the home.",
         "code": ["economics.js::simulateDay", "bill_tool.js::btRun", "bill_tool.js::btSeasonChart"],
         "stages": ["page:economics.js"]},
        {"title": "Power prices",
         "text": "Home prices are the Electricity Authority's latest for {price_region} ({price_month}): "
                 "{price_c}c a kWh and ${daily_d} a day for a standard-use home, incl. GST. The one price is split "
                 "into {peak_c}c in peak hours and {offpeak_c}c off-peak, so a typical home pays about the same average. "
                 "Areas on another network use its prices: {other_regions}. "
                 "Businesses pay energy prices, {biz_peak_c}c peak and {biz_offpeak_c}c off-peak; their network "
                 "and connection charges are a separate bill that solar does not change. "
                 "Exported solar earns {export_c}c a kWh in the day and {peak_export_c}c at peak in the first year "
                 "(retailer buy-back rates). "
                 "The prices are refreshed each month.",
         "facts": [("EA price", "price_c", "c/kWh"), ("Peak", "peak_c", "c/kWh"), ("Off-peak", "offpeak_c", "c/kWh"),
                   ("Daily charge", "daily_d", " $/day"), ("Export", "export_c", "c/kWh")],
         "code": ["tools/fetch_regional_prices.mjs", "economics.js::applyRegionalPrices", "regional_prices.js"],
         "stages": ["page:regional_prices.js"]},
        {"title": "Cost and savings",
         "text": "The cost per kW falls with system size (Assumptions & pricing). Over a {life_years}-year life "
                 "power prices rise {elec_infl_pct}% a year, export rates fall {export_decline_pct}% a year and panels "
                 "lose {degradation_pct}% a year, shown in today's dollars.",
         "facts": [("System life", "life_years", " years"), ("Panel ageing", "degradation_pct", "% a year")],
         "code": ["economics.js::costPerKw", "bill_tool.js::btReturns"], "stages": ["page:economics.js"]},
        {"title": "Net saving with a loan",
         "text": "The green box: bill savings less the repayments on a loan for the system and its one "
                 "replacement inverter, at {loan_rate_pct}% over {loan_years} years. Both can be changed.",
         "facts": [("Loan rate", "loan_rate_pct", "%"), ("Loan term", "loan_years", " years")],
         "code": ["bill_tool.js::btAnnuity"], "stages": ["page:bill_tool.js"]},
     ]},
    {"title": "Checks and publishing", "icon": "✅",
     "summary": "Every figure is re-worked and checked before it reaches the map.",
     "items": [
        {"title": "Every figure shows its workings",
         "text": "Tap a number on a building's card to see the sum that made it. The map re-works every "
                 "panel's power from the published inputs and refuses to publish on any mismatch.",
         "code": ["src/emit_region.py", "workings.js", "tools/predeploy_check.py"],
         "stages": ["emit_region", "add_addresses"]},
     ]},
]

INTRO = ("How each roof's estimate is made, from the data sources to the money. Tap a step for more, and "
         "Details for the figures and the code behind them.")


def _fill(text: str, vals: dict, where: str) -> str:
    # names with digits in them are not figures: 3D, 2D, the ERA5 record
    bare = re.sub(r"\b(?:[23]D|ERA5)\b", "", re.sub(r"\{[a-z_0-9]+\}", "", text))
    if re.search(r"\d", bare):
        raise ValueError(f"{where}: a number is typed in the prose; use a {{placeholder}}: {text!r}")
    return re.sub(r"\{([a-z_0-9]+)\}", lambda m: vals[m.group(1)][0], text)


def sources() -> list[dict]:
    """Every LINZ layer the build reads, with LINZ's own statements."""
    meta = json.loads(META.read_text())
    c = _config()
    out = []
    used = {}
    for s in c.SURVEYS:
        for k, what in (("dsm_layer", "LiDAR surface model (roofs, trees, buildings)"),
                        ("dem_layer", "LiDAR ground model (bare earth)"),
                        ("lidar_tile_index_layer", "finding the LiDAR point-cloud tiles"),
                        ("imagery_layer", "aerial photo: roof lines, obstructions"),
                        ("reference_imagery_layer", "photo from the LiDAR's year, to line the two up")):
            if s.get(k):
                used.setdefault(str(s[k]), set()).add(what)
    used.setdefault(str(c.LINZ_BUILDING_OUTLINES_LAYER), set()).add("which buildings exist, and their outlines")
    used.setdefault(str(c.LINZ_SUBURBS_LOCALITIES_LAYER), set()).add("naming and bounding each area")
    used.setdefault(str(c.LINZ_WIDE_DEM_LAYER), set()).add("distant hills on the horizon")
    for lid in sorted(used, key=lambda x: (meta.get(x, {}).get("title") or "", x)):
        m = meta.get(lid)
        if m is None:
            raise KeyError(f"LINZ layer {lid} is not in {META.name}: run tools/fetch_source_metadata.py")
        acc = None
        if m.get("vertical_accuracy_m") or m.get("horizontal_accuracy_m"):
            parts = []
            if m.get("vertical_accuracy_m"):
                parts.append(f"±{m['vertical_accuracy_m']:g} m vertical")
            if m.get("horizontal_accuracy_m"):
                parts.append(f"±{m['horizontal_accuracy_m']:g} m horizontal")
            acc = ", ".join(parts) + (f" ({m['confidence_pct']:g}% confidence)" if m.get("confidence_pct") else "")
        out.append({"id": int(lid), "title": m["title"], "url": m["url"],
                    "used_for": "; ".join(sorted(used[lid])),
                    "resolution": f"{m['ground_resolution_m']:g} m pixels" if m.get("ground_resolution_m") else None,
                    "accuracy": acc, "captured": m.get("captured")})
    return out


def build() -> dict:
    vals = values()
    steps = []
    for si, s in enumerate(STEPS):
        items = []
        for it in s["items"]:
            where = f"{s['title']} / {it['title']}"
            items.append({
                "title": it["title"],
                "text": _fill(it["text"], vals, where),
                "facts": [{"label": lab, "value": vals[k][0] + (unit or ""), "source": vals[k][1]}
                          for lab, k, unit in it.get("facts", [])],
                "code": it.get("code", []),
                "stages": it.get("stages", []),
            })
        steps.append({"title": s["title"], "icon": s.get("icon", ""),
                      "summary": _fill(s["summary"], vals, s["title"]), "items": items})
    return {
        "intro": _fill(INTRO, vals, "intro"),
        "steps": steps,
        "sources": sources(),
        "footer": FOOTER,
    }


FOOTER = ("Written from the code (src/methodology.py). Every figure here is read from the "
          "code or from LINZ's own layer descriptions, and a test fails if the diagram and "
          "the code ever disagree.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    text = json.dumps(build(), indent=1, ensure_ascii=False) + "\n"
    if a.check:
        ok = OUT.exists() and OUT.read_text() == text
        print("methodology.json is current" if ok else "methodology.json is out of date: run python src/methodology.py")
        return 0 if ok else 1
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
