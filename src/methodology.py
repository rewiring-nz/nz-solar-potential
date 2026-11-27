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
  * NATIONAL SOURCES ARE LINZ'S WORDS. Accuracy, resolution and capture dates
    come from data/source_metadata.json (tools/fetch_source_metadata.py,
    LINZ's own layer metadata). Area-specific survey layers are discovered
    from LINZ metadata at runtime and cached under ignored data/.
  * THE FILE IS CURRENT. The test rebuilds the file and fails if the
    committed methodology.json differs.
  * EACH PART SAYS HOW TO CHANGE IT. Every item gives why it is done this
    way, what else could work, where it goes wrong (open roofs from
    data/roof_cases.json picked by words in their defect, GitHub labels,
    known limits) and how to change it (SOLAR_* switches, the tests that
    prove a change, and a JUDGES entry: how better or worse is decided).
    The test fails when a switch, test file, label or judge is not real.
    tools/cases.py rewrites this file whenever it saves the register.

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
        "cpi_pct": (_fmt(_js_const("economics.js", r"cpi_pct:\s*([\d.]+)"), 1), "economics.js ECON_DEFAULTS.cpi_pct"),
        "inv_year": (_fmt(_js_const("economics.js", r"inverter_replace_year:\s*([\d.]+)") + 1),
                     "economics.js ECON_DEFAULTS.inverter_replace_year + 1"),
        "batt_year": (_fmt(_js_const("economics.js", r"battery_replace_year:\s*([\d.]+)") + 1),
                      "economics.js ECON_DEFAULTS.battery_replace_year + 1"),
        "batt_repl_pct": (_fmt(_js_const("economics.js", r"battery_replace_cost_pct:\s*([\d.]+)")),
                          "economics.js ECON_DEFAULTS.battery_replace_cost_pct"),
        "batt_per_kwh": (_fmt(_js_const("economics.js", r"cost_per_kwh:\s*([\d.]+)")),
                         "economics.js BATTERY_DEFAULTS.cost_per_kwh"),
        "life_years": (_fmt(_js_const("economics.js", r"life_years:\s*([\d.]+)")),
                       "economics.js ECON_DEFAULTS.life_years"),
        "discount_pct": (_fmt(_js_const("economics.js", r"discount_rate_pct:\s*([\d.]+)"), 1),
                         "economics.js ECON_DEFAULTS.discount_rate_pct"),
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
        "edge_gap_wide_m": (_fmt(_env_default("src/panel_fitting.py", "EDGE_GAP_WIDE_M"), 2),
                            "src/panel_fitting.py EDGE_GAP_WIDE_M (default)"),
        "ridge_gap_wide_m": (_fmt(_env_default("src/panel_fitting.py", "RIDGE_GAP_WIDE_M"), 2),
                             "src/panel_fitting.py RIDGE_GAP_WIDE_M (default)"),
        "edge_gap_small": (_fmt(int(_env_default("src/panel_fitting.py", "EDGE_GAP_SMALL_PANELS"))),
                           "src/panel_fitting.py EDGE_GAP_SMALL_PANELS (default)"),
        "edge_gap_keep_pct": (_fmt(_env_default("src/panel_fitting.py", "EDGE_GAP_KEEP_FRACTION") * 100, 0),
                              "src/panel_fitting.py EDGE_GAP_KEEP_FRACTION (default), as %"),
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
    v["inverter_loss_pct"] = (_fmt(100 - pv["inverter_efficiency_pct"], 0),
                              "100 − config.PV_ASSUMPTIONS.inverter_efficiency_pct")
    v["kept_pct"] = (_fmt(inv * (1 - derate) * 100, 1),
                     "inverter_efficiency × (1 − system_derate), config.PV_ASSUMPTIONS")
    return v


# ---- the diagram ------------------------------------------------------------
# Each step: title, icon, one-line summary, items. Each item:
#   title, text   plain words, {placeholders} only for figures
#   facts         value names shown beside the text
#   code          "path::symbol" -- the files to edit
#   stages        src/build_plan.py names, "fetch:<what>" for input
#                 preparation, "page:<file>" for the map page
#   why           the trade-off: why it is done this way and not another
#   try           methods a contributor could try instead
#   fails         where it goes wrong: "words" pick the open roofs in
#                 data/roof_cases.json whose defect names them, "labels" the
#                 GitHub issue labels for it, "known" limits stated in words
#   change        how to change it: "switches" (SOLAR_* environment
#                 switches that turn the method off or tune it), "tests"
#                 (the checks that prove a change), "judge" (a JUDGES key:
#                 how a change is judged better or worse)
#   show          optional: a small working picture (THRESHOLD or LOSSES)
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
         "code": ["src/linz_exports.py", "src/localities.py"], "stages": ["fetch:src/fetch_regions.py"],
         "why": "LINZ's outlines are the one open, national list of buildings, and each keeps its ID from one "
                "build to the next, so a roof's fixes and reports stay attached to it.",
         "try": ["Outlines traced from the laser scan, for houses LINZ has not drawn yet",
                 "Outlines read from the newest aerial photo by an image model"],
         "fails": {"labels": ["geometry"],
                   "known": ["A house built since LINZ last drew the area has no outline, so no estimate."]},
         "change": {"switches": ["SOLAR_OUTLINE_SOURCE"],
                    "tests": ["tests/test_linz_exports.py", "tests/test_region_ids.py"], "judge": "inputs"}},
        {"title": "A laser scan of the roofs",
         "text": "LINZ's LiDAR survey for the area: the raw point cloud (from OpenTopography's copy) where it "
                 "has been published, and LINZ's surface model otherwise. Only points classed as building are "
                 "used for roofs. The ground model and surface model give trees, neighbours and hills.",
         "code": ["src/fetch_pointcloud_regions.py::tilename_to_filename", "src/pointcloud_source.py::PointCloudSource",
                  "src/fetch_data.py::fetch_raster"],
         "stages": ["fetch:src/fetch_regions.py", "fetch:src/fetch_pointcloud_regions.py"],
         "why": "A laser scan measures height to a few centimetres, so a roof's slope and direction are measured, "
                "not guessed from a photo. The raw points keep detail the gridded surface smooths away.",
         "try": ["Heights from overlapping aerial photos where the scan is old",
                 "Using two surveys of different years together"],
         "fails": {"labels": ["scale"],
                   "known": ["A roof built or changed since the survey is read as it was then.",
                             "Where the raw points are not published, small roof detail is lost in the surface model."]},
         "change": {"switches": ["SOLAR_DISABLE_POINTCLOUD", "SOLAR_TIFF_READER"],
                    "tests": ["tests/test_surveys.py", "tests/test_s3_rasters.py"], "judge": "inputs"}},
        {"title": "Distant hills",
         "text": "LINZ's national elevation model, out past the area, for hills on the horizon.",
         "code": ["src/fetch_dem_wide.py"], "stages": ["fetch:src/fetch_dem_wide.py"],
         "why": "The laser scan stops at the survey's edge, but mountains far beyond it block the low sun. The "
                "national model covers every hill at a size a build can read quickly.",
         "try": ["A finer elevation model where one covers the hills",
                 "Checking horizons against photos taken from real roofs"],
         "fails": {"labels": ["model"],
                   "known": ["LINZ calls this model cartographic, not precise: a ridge can be metres off."]},
         "change": {"switches": ["SOLAR_DEM_WIDE_SOURCE"],
                    "tests": ["tests/test_shading_split.py"], "judge": "energy"}},
        {"title": "Aerial photos",
         "text": "LINZ aerial photos, the newest that cover the area, for roof lines and objects on roofs. "
                 "The map itself shows LINZ Basemaps' photo.",
         "code": ["src/fetch_regions.py"], "stages": ["fetch:src/fetch_regions.py"],
         "why": "The photo shows roof lines, skylights and vents that the scan blurs, and it is usually newer "
                "than the scan.",
         "try": ["Sharper photos where a council has them",
                 "Older photos, to see roofs before trees grew over them"],
         "fails": {"labels": ["geometry"],
                   "known": ["Without a photo, a build skips finding objects from the photo."]},
         "change": {"switches": ["SOLAR_INCLUDE_IMAGERY"],
                    "tests": ["tests/test_surveys.py"], "judge": "inputs"}},
        {"title": "Places with no laser scan",
         "text": "Where no LiDAR survey exists, roofs are modelled from the outline: a gable or hipped roof at "
                 "{guess_pitch_deg}° for houses and flat for large buildings. Every such building says so on the map.",
         "facts": [("Assumed pitch", "guess_pitch_deg", "°")],
         "code": ["src/synthesize_dsm.py"], "stages": ["fetch:src/synthesize_dsm.py"],
         "why": "A plain, modest roof gives a fair first estimate where there is nothing to measure, and the map "
                "says it is a guess rather than hiding the town.",
         "try": ["Reading gable or hip from the photo's roof lines",
                 "Reading the pitch from shadows in the photo"],
         "fails": {"labels": ["no-lidar"],
                   "known": ["Every roof in such a town gets the same pitch, so steep and shallow roofs are both off."]},
         "change": {"switches": ["SOLAR_PHOTO_ROOFS"],
                    "tests": ["tests/test_synthesize_dsm.py"], "judge": "layout"}},
        {"title": "Lining the data up",
         "text": "The photo and the scan were taken in different years and lean differently. Each roof's lean "
                 "is measured by matching the photos (up to {lean_search_m} m), and an outline that sits off the "
                 "scanned roof is slid onto it (up to {outline_slide_m} m) without changing its shape. A roof whose faces "
                 "come from the scan's planes is laid over the map's photo directly: its lines are moved in "
                 "proportion to their height until they sit on the edges the photo shows.",
         "facts": [("Largest lean looked for", "lean_search_m", " m"), ("Largest outline slide", "outline_slide_m", " m")],
         "code": ["src/register_imagery.py", "src/outline_fit.py", "src/photo_lean.py"],
         "stages": ["register_imagery", "outline_fit"],
         "why": "People judge a roof by the photo under it. Panels drawn a metre off the roof look wrong even when "
                "the sums are right, so the drawing follows the photo and the sums follow the scan.",
         "try": ["Fitting the outline's shape to the scan's roof edges, not only sliding it",
                 "Correcting each photo's lean from where its camera was"],
         "fails": {"words": ["outline", "align", "off the roof", "off the image", "floating", "off the building",
                             "way off"],
                   "labels": ["geometry"]},
         "change": {"switches": ["SOLAR_OUTLINE_FIT", "SOLAR_PHOTO_LEAN"],
                    "tests": ["tests/test_register_imagery.py", "tests/test_hand_outline.py"], "judge": "markup"}},
     ]},
    {"title": "Read each roof", "icon": "🏠",
     "summary": "Find the roof's faces, their slope and direction, and anything on them.",
     "items": [
        {"title": "Four readings of the roof",
         "text": "Each roof is read four ways: an image model that outlines shapes in the photo, a model "
                 "trained on hand-drawn roof lines, the laser scan's flat regions, and the simplest roof form "
                 "that fits. Each reading is scored on how well its edges match the lines in the photo and how "
                 "well its faces fit the scan, and the best is kept.",
         "code": ["tools/predict_faces.py", "src/face_candidates.py::score_candidate", "src/vision_roof.py"],
         "stages": ["fetch:tools/predict_faces.py"],
         "why": "Each reading fails on different roofs. Scoring several against the scan and the photo and keeping "
                "the best is more reliable than tuning any one of them.",
         "try": ["A model trained on the hand markup that draws the roof lines directly",
                 "Letting the readings vote line by line instead of picking one whole roof"],
         "fails": {"words": ["roof lines", "lines wrong", "shape", "slivers", "kite", "bow-tie", "jagged",
                             "pyramid", "faces missed", "obvious faces", "sawtooth"],
                   "labels": ["geometry", "roof-case"]},
         "change": {"switches": ["SOLAR_SELECTED_FACES", "SOLAR_VISION_ROOF"],
                    "tests": ["tests/test_reading_referee.py", "tests/test_face_steps.py"], "judge": "markup"}},
        {"title": "When the reading is doubtful",
         "text": "A reading scoring under {pick_min} is not used. One under {pick_trust} competes with faces "
                 "found from the laser scan alone, and the scan's version wins when it explains the roof better "
                 "or more simply.",
         "facts": [("Lowest score used", "pick_min", ""), ("Score that needs no contest", "pick_trust", "")],
         "code": ["src/roof_partition.py::SELECTED_TRUST", "src/roof_segmentation.py"],
         "stages": ["build_layout_geojson"],
         "why": "A low score means the reading and the scan disagree. Faces from the scan alone are plainer, but "
                "rarely wrong in kind.",
         "try": ["Learning the two limits from the hand markup instead of setting them by hand"],
         "fails": {"words": ["read wrongly", "invented", "unnecessary"], "labels": ["geometry"]},
         "change": {"switches": ["SOLAR_SELECTED_MIN_SCORE", "SOLAR_SELECTED_TRUST"],
                    "tests": ["tests/test_reading_referee.py"], "judge": "markup"},
         "show": {"kind": "threshold", "ask": "A reading that scores", "range": [0, 1, 0.01], "unit": "",
                  "cuts": ["pick_min", "pick_trust"],
                  "outcomes": ["is not used", "competes with faces from the scan", "is used as it is"]}},
        {"title": "Roof faces from the laser scan's own planes",
         "text": "Flat planes are grown on the laser points themselves, and each roof line is put where two of "
                 "those planes meet or where the height steps. A roof read this way is used ahead of the image "
                 "models' reading; a paid reading of the photo and a hand-drawn roof still come first.",
         "code": ["src/point_roof.py", "src/roof_partition.py::selected_doc"], "stages": ["point_roof"],
         "why": "A line where two measured planes meet follows the real ridge. An edge in the photo may be a "
                "shadow, a gutter or a change of paint.",
         "try": ["Stricter merging of planes on low-pitch roofs",
                 "Fitting neighbouring planes together so they share their ridge exactly"],
         "fails": {"words": ["valley", "ridges", "wonky", "misaligned"], "labels": ["geometry"]},
         "change": {"switches": ["SOLAR_POINT_ROOF"],
                    "tests": ["tests/test_plane_refresh.py", "tests/test_seam_steps.py"], "judge": "markup"}},
        {"title": "Hand-drawn roofs come first",
         "text": "Where a person has drawn a roof's faces and lines, the drawing is used as it is. Identical "
                 "roofs in a row of houses share the reading that explains the whole set best.",
         "code": ["src/roof_partition.py::partition_roof", "src/repeat_roofs.py"], "stages": ["repeat_roofs"],
         "why": "A person looking at the photo is right more often than any model, and identical houses should "
                "get identical answers.",
         "try": ["More people drawing roofs: every drawing fixes a roof and becomes a test",
                 "Training the reading models on each new drawing"],
         "fails": {"words": ["identical", "markup"], "labels": ["roof-case"]},
         "change": {"switches": ["SOLAR_REPEAT_ROOFS", "SOLAR_HIDE_LABELS"],
                    "tests": ["tests/test_repeat_roofs.py", "tests/test_drawn_solar.py"], "judge": "markup"}},
        {"title": "Slope and direction",
         "text": "Each face gets a flat plane fitted to its laser points, ignoring points more than "
                 "{plane_band_cm} cm off it. Faces steeper than {max_slope_deg}° are walls, not roof. A roof whose "
                 "points fit its planes less than {roof_conf_pct}% of the time gets no panels.",
         "facts": [("Steepest roof face", "max_slope_deg", "°"), ("Roof must fit its planes", "roof_conf_pct", "%")],
         "code": ["src/roof_partition.py::_fit_plane_robust", "src/build_layout_geojson.py::MIN_ROOF_CONFIDENCE"],
         "stages": ["patch_roof_confidence"],
         "why": "A fit that ignores stray points is not tilted by chimneys, gutters or overhanging trees, as a "
                "plain average would be.",
         "try": ["Telling a balcony or deck from roof by its height below the eaves",
                 "Fitting all of a roof's faces at once"],
         "fails": {"words": ["balcon", "deck", "flat"], "labels": ["geometry"]},
         "change": {"switches": ["SOLAR_EMPTY_FACE_REFIT", "SOLAR_BALCONY_LEVELS"],
                    "tests": ["tests/test_roof_levels.py", "tests/test_sunken.py"], "judge": "markup"},
         "show": {"kind": "threshold", "ask": "A roof whose points fit its planes", "range": [0, 100, 1],
                  "unit": "% of the time", "cuts": ["roof_conf_pct"],
                  "outcomes": ["gets no panels", "can get panels"]}},
        {"title": "Objects on the roof",
         "text": "Vents, skylights, chimneys and plant are found two ways: laser points standing more than "
                 "{obs_height_m} m off the roof's plane, and patches of the photo that stand out from the roof "
                 "around them (within {obs_photo_radius_m} m). Panels keep clear of both.",
         "facts": [("Height off the plane", "obs_height_m", " m")],
         "code": ["src/obstruction_detection.py::detect_obstructions_combined", "src/obstruction_planarity.py"],
         "stages": ["build_layout_geojson"],
         "why": "The scan finds things that stand up; the photo finds flat things such as skylights. Each alone "
                "misses one kind.",
         "try": ["An image model trained on the drawn obstructions",
                 "The scan's return strength, to spot glass"],
         "fails": {"words": ["obstruction", "plant"], "labels": ["geometry"]},
         "change": {"switches": ["SOLAR_SMALL_OBJECTS", "SOLAR_OBS_PLANE_CHECK"],
                    "tests": ["tests/test_small_obstructions.py", "tests/test_obstruction_planarity.py"],
                    "judge": "markup"}},
     ]},
    {"title": "Sunlight", "icon": "☀️",
     "summary": "How much sun reaches a roof of each slope and direction over a year.",
     "items": [
        {"title": "Clear-sky sun, hour by hour",
         "text": "The sun's path and clear-sky light for every hour of a year, from the pvlib library's "
                 "Ineichen model.",
         "code": ["src/solar_model.py::build_poa_lookup_table", "src/solar_model.py~~model=\"ineichen\""],
         "why": "Ineichen is a standard, well-tested clear-sky model, and pvlib is the open library solar "
                "engineers use, so anyone can check it.",
         "try": ["Hourly sunlight from satellite records instead of a clear sky cut by cloud"],
         "fails": {"labels": ["model"]},
         "change": {"tests": ["tests/test_reference_cases.py"], "judge": "energy"}},
        {"title": "Cloud, from measured records",
         "text": "Clear-sky sun is cut each month to what ground instruments have measured (pyranometers, which "
                 "record all the sunlight reaching the ground). Near Queenstown (within about {solarview_km} km) "
                 "that is NIWA's SolarView record for Queenstown, built on decades of measurements at Queenstown "
                 "Airport. Elsewhere it is the nearest of {n_stations} NIWA radiation stations, up to about "
                 "{station_max_km} km away. Each roof's workings name its station and how far away it is.",
         "facts": [("NIWA stations", "n_stations", ""), ("Furthest station used", "station_max_km", " km")],
         "code": ["src/solar_model.py::solarview_calibrated_monthly_factors",
                  "src/solar_model.py::niwa_station_measured_monthly_factors"],
         "why": "Ground instruments are the most accurate measure of sunlight there is. Models are checked "
                "against them, not the other way round.",
         "try": ["Satellite cloud records, which are finer in space than stations"],
         "fails": {"labels": ["model"],
                   "known": ["A station far from a town may have a different climate from it."]},
         "change": {"tests": ["tests/test_shading_split.py", "tests/test_reference_cases.py"], "judge": "energy"}},
        {"title": "Differences between towns",
         "text": "A station measures one spot. How much sunnier or duller each town is than its station comes "
                 "from ERA5, a global weather reanalysis (a weather model corrected by satellite and weather "
                 "observations), in cells about {era5_ns_km} km north to south by {era5_ew_km} km east to west, "
                 "averaged over {era5_years}. Only its differences between places are used, held between "
                 "{era5_lo} and {era5_hi} of the station's figure; cells averaging over {era5_max_elev} m high "
                 "are mountains, not towns, and are left out.",
         "facts": [("ERA5 cell", "era5_ns_km", " km north–south"), ("Years averaged", "era5_years", "")],
         "code": ["src/solar_model.py::era5_spatial_ratio", "src/solar_model.py::_era5_monthly"],
         "why": "A weather model gets the difference between nearby places better than the amount itself, so "
                "only its ratio is used and the measured level is kept.",
         "try": ["A finer reanalysis or a satellite sunlight product"],
         "fails": {"labels": ["model"]},
         "change": {"switches": ["SOLAR_ERA5_SPATIAL"], "tests": ["tests/test_reference_cases.py"],
                    "judge": "energy"}},
        {"title": "What the sunlight data cannot see",
         "text": "Cloud is a long-term monthly average for a place, the same every day of that month: a typical "
                 "year, not this year. It is no finer than the station and the ERA5 cell, so it cannot see fog "
                 "or inversion cloud that sits in one valley or town, such as winter morning fog at Wanaka or "
                 "Cromwell. Where a town gets more of that than its station, its winter sun is overstated. "
                 "Hills, trees and buildings are different: they are measured for every roof, from {dem_m} m "
                 "terrain and the laser scan (Shade, below).",
         "code": ["src/solar_model.py::solarview_calibrated_monthly_factors", "src/solar_model.py::era5_spatial_ratio"],
         "why": "Payback depends on a typical year. A particular year's weather would add noise, not accuracy.",
         "try": ["Satellite fog records for valley towns",
                 "Measuring winter sun on a few roofs in towns that get fog"],
         "fails": {"labels": ["model"],
                   "known": ["Valley fog is not seen, so winter sun in foggy towns is overstated."]},
         "change": {"tests": ["tests/test_reference_cases.py"], "judge": "energy"}},
        {"title": "Onto a sloped roof",
         "text": "Sunlight is split into direct and diffuse light (the Erbs method) and turned onto each roof "
                 "slope and direction with the Perez sky model, in {slope_bin}° slope by {aspect_bin}° direction steps.",
         "facts": [("Slope step", "slope_bin", "°"), ("Direction step", "aspect_bin", "°")],
         "code": ["src/solar_model.py~~irradiance.erbs", "src/solar_model.py~~model=\"perez\""],
         "why": "Erbs and Perez are the standard pair in solar design tools. Working each slope and direction "
                "out once, as a table, makes every roof fast.",
         "try": ["Finer slope and direction steps", "A measured split of direct and diffuse light"],
         "fails": {"labels": ["model"]},
         "change": {"tests": ["tests/test_reference_cases.py", "tests/test_shading_split.py"], "judge": "energy"}},
     ]},
    {"title": "Shade", "icon": "🌳",
     "summary": "Hills, trees and buildings that block the sun, worked out for every panel.",
     "items": [
        {"title": "Nearby trees and buildings",
         "text": "From each panel, the laser scan's surface is searched in every direction (every "
                 "{near_step_deg}°, out to {near_m} m) for anything that blocks the sun.",
         "facts": [("Directions", "near_step_deg", "° apart"), ("Distance searched", "near_m", " m")],
         "code": ["src/building_shading.py::building_shading_factor"], "stages": ["build_layout_geojson"],
         "why": "The scan already holds every tree and neighbour, so shade is measured for each panel instead of "
                "guessed for the roof.",
         "try": ["Trees that lose their leaves, letting winter sun through",
                 "Tracing the sun through the scan's points rather than its surface"],
         "fails": {"labels": ["model"],
                   "known": ["A tree is solid all year: a bare winter tree shades as much as a summer one."]},
         "change": {"tests": ["tests/test_shading_split.py"], "judge": "energy"}},
        {"title": "Hills",
         "text": "Each building's horizon of hills, in {far_bins} directions out to {far_km} km, seen from its "
                 "eaves, and the area's own horizon out to {area_hz_km} km.",
         "facts": [("Directions", "far_bins", ""), ("Distance", "far_km", " km")],
         "code": ["src/building_horizon.py::far_profile", "src/terrain_horizon.py"],
         "stages": ["bake_building_horizons"],
         "why": "In a mountain valley the hills take the morning or evening sun. One horizon per building, from "
                "its eaves, catches that without working it out for every panel.",
         "try": ["A horizon from each panel instead of each building"],
         "fails": {"labels": ["model"]},
         "change": {"tests": ["tests/test_shading_split.py"], "judge": "energy"}},
        {"title": "Deep shade",
         "text": "Direct sun that is blocked is taken off; light from the open sky is reduced by how much sky "
                 "the panel sees. A panel keeping less than {deep_shade_pct}% of its direct sun is not placed.",
         "facts": [("Deep-shade limit", "deep_shade_pct", "%")],
         "code": ["src/solar_model.py::SolarModel", "src/build_layout_geojson.py::DEEP_SHADE_FACTOR"],
         "why": "A panel that sees little direct sun pays back too slowly to be worth placing.",
         "try": ["Setting the limit by money (what the panel saves) instead of sunlight"],
         "fails": {"labels": ["model"]},
         "change": {"tests": ["tests/test_shading_split.py"], "judge": "layout"},
         "show": {"kind": "threshold", "ask": "A panel that keeps", "range": [0, 100, 1],
                  "unit": "% of its direct sun", "cuts": ["deep_shade_pct"],
                  "outcomes": ["is not placed", "can be placed"]}},
     ]},
    {"title": "Panels and power", "icon": "🔆",
     "summary": "Lay panels sunniest first and work out what each one makes.",
     "items": [
        {"title": "Laying out panels",
         "text": "{panel_w} W panels ({panel_w_m} × {panel_h_m} m) are packed in rows on each face, "
                 "{edge_setback_m} m from edges and ridges on a small face; a face that fits more than {edge_gap_small} "
                 "keeps {edge_gap_wide_m} m from the roof edge and {ridge_gap_wide_m} m from ridges, unless that keeps "
                 "under {edge_gap_keep_pct}% of its panels. Portrait unless landscape fits {landscape_pct}% more. "
                 "A face pointing away from the sun with less than {sun_side_pct}% of the sunniest face's sun is left "
                 "bare. Stray panels go: an array needs at least {min_array}.",
         "facts": [("Panel", "panel_w", " W"), ("Edge setback", "edge_setback_m", " m")],
         "code": ["src/panel_fitting.py::fit_panels_on_facet", "src/panel_fitting.py::enforce_arrays"],
         "stages": ["build_layout_geojson", "gate_panels"],
         "why": "Fixed gaps and clean rows put panels where an installer would. Landscape is used only when it "
                "clearly fits more, because mixed rows cost more to rack.",
         "try": ["Packing whole arrays at once instead of row by row",
                 "Rules taken from installers' real layouts"],
         "fails": {"words": ["layout", "orientation", "portrait", "vertical", "fit more", "unfilled",
                             "standalone", "lone", "tiny sets", "over an edge", "ridge", "missing"],
                   "labels": ["geometry", "roof-case"]},
         "change": {"switches": ["SOLAR_EDGE_GAP", "SOLAR_LANDSCAPE_MARGIN", "SOLAR_SUN_SIDE_REL"],
                    "tests": ["tests/test_racking.py", "tests/test_installer_rules.py"], "judge": "layout"},
         "show": {"kind": "threshold", "ask": "A face away from the sun, with", "range": [0, 100, 1],
                  "unit": "% of the sunniest face's sun", "cuts": ["sun_side_pct"],
                  "outcomes": ["is left bare", "gets panels"]}},
        {"title": "Sunniest first",
         "text": "Panels are ranked by how much they make, so a smaller system takes the best spots. The "
                 "density slider fills in that order.",
         "code": ["src/panel_fitting.py::assign_fill_ranks", "src/rerank_layouts.py::rerank_area"],
         "stages": ["rerank_layouts"],
         "why": "Most homes want fewer panels than fit. Ranking lets every system size take its own best spots.",
         "try": ["Ranking by money (use at home against export) instead of energy"],
         "fails": {"words": ["slider", "cut"], "labels": ["geometry"]},
         "change": {"switches": ["SOLAR_RERANK_ROWS"], "tests": ["tests/test_racking.py"], "judge": "layout"}},
        {"title": "Power from each panel",
         "text": "Each panel's sunlight after shade, times its rating, less {total_loss_pct}% for the inverter, "
                 "heat, dirt, wiring and glass reflection. The building's total is the sum of its panels.",
         "facts": [("Inverter efficiency", "inverter_pct", "%"), ("Other losses", "derate_pct", "%"),
                   ("Total losses", "total_loss_pct", "%")],
         "code": ["src/solar_model.py::SolarModel", "src/apply_yield.py", "src/derive_solar_potential.py::derive"],
         "stages": ["derive_solar_potential", "apply_yield"],
         "why": "Each panel is worked out from its own sunlight, so a part-shaded roof is not averaged away.",
         "try": ["A panel temperature model from local air temperature and wind"],
         "fails": {"labels": ["model"],
                   "known": ["One loss figure for every system: hot, dusty or old systems lose more."]},
         "change": {"tests": ["tests/test_generation.mjs", "tests/test_workings.mjs", "tests/test_reference_cases.py"],
                    "judge": "energy"},
         "show": {"kind": "losses", "parts": [["Inverter", "inverter_loss_pct"], ["Heat, dirt, wiring, glass", "derate_pct"]],
                  "kept": "kept_pct"}},
        {"title": "Buildings that are gone",
         "text": "An outline whose building no longer shows in the newest photo is withheld.",
         "code": ["src/demolished.py"], "stages": ["demolished"],
         "why": "A cheap pixel check screens every outline, so the paid second look is needed only for the few "
                "it flags.",
         "try": ["A free local model for the second look"],
         "fails": {"words": ["phantom", "knocked down"], "labels": ["geometry"]},
         "change": {"switches": ["SOLAR_DEMOLISHED", "SOLAR_DEMOLISHED_REFEREE"],
                    "tests": ["tests/test_demolished.py"], "judge": "release"}},
        {"title": "Heat map mode",
         "text": "The heat map colours every {heat_res_m} m of roof by its yearly sunlight, from the same "
                 "sun and shade model.",
         "code": ["src/build_heatmap_raster.py::render_building"], "stages": ["build_heatmap_raster"],
         "why": "The same model as the panels, so the colours and the figures never disagree.",
         "try": ["Hours of direct sun in winter, as a second colour scale"],
         "fails": {"labels": ["model"]},
         "change": {"tests": ["tests/test_output_contract.py"], "judge": "energy"}},
     ]},
    {"title": "Money", "icon": "💲",
     "summary": "Bills before and after, the cost, and the saving once a loan is repaid.",
     "items": [
        {"title": "A household's day",
         "text": "Each building's use of power hour by hour, for an average day in each season: a home from "
                 "the Electricity Authority's figures for its area, scaled for people and house size; a business "
                 "by its floor area and its type: offices and shops from New Zealand's commercial building survey, sheds and workshops at about a third of that. Solar powers the building first; the rest is exported.",
         "code": ["bill_tool.js::btEvaluate", "economics.js::simulateDay"], "stages": ["page:bill_tool.js"],
         "why": "Average days by season are public and local. Real meter data for each home is neither.",
         "try": ["Real smart-meter profiles, shared with consent",
                 "Profiles for homes with a heat pump or an electric car"],
         "fails": {"known": ["Use does not follow the weather, so a cold, dull week costs the same as an average one."]},
         "change": {"tests": ["tests/test_bill_tool.mjs", "tests/test_economics.mjs"], "judge": "money"}},
        {"title": "Batteries",
         "text": "A battery charges only from spare solar. On a time-of-use plan it saves its charge for "
                 "peak hours, giving off-peak only what the coming peak will not need before the sun refills "
                 "it; on a flat plan it covers any shortfall as it comes. Each season's typical day is repeated "
                 "until the battery's charge settles, and the charts show it charging and powering the home.",
         "code": ["economics.js::simulateDay", "bill_tool.js::btRun", "bill_tool.js::btSeasonChart"],
         "stages": ["page:economics.js"],
         "why": "Charging only from spare solar is how most home batteries are set up, and it keeps the saving "
                "from solar apart from tariff tricks.",
         "try": ["Charging from the grid off-peak on a time-of-use plan"],
         "fails": {"known": ["A typical day repeated cannot show a run of dull days."]},
         "change": {"tests": ["tests/test_low_solar_battery.mjs", "tests/test_economics.mjs"], "judge": "money"}},
        {"title": "The whole year, day by day",
         "text": "The year chart has a different day for every day of the year. Each hour's use and solar follow a smooth curve "
                 "through the year that keeps each season's average day exactly, and each day's sunlight "
                 "follows a real year's weather at the roof (the last full year of the ERA-five record), rescaled so each "
                 "season makes what the bills say. The battery carries its charge from one day to the next. "
                 "Use does not follow the weather. The bills are still worked out on the four average days; "
                 "the chart shows how far the day-by-day year's grid use differs from them.",
         "code": ["bill_tool.js::btYearHours", "bill_tool.js::btBlendYear", "low_solar.js::weatherYear"],
         "stages": ["page:bill_tool.js"],
         "why": "A real year's weather shows the swings an average day hides, while the bills stay on the "
                "averages they were checked against.",
         "try": ["Working the bills out on the day-by-day year too"],
         "fails": {"known": ["One year's weather: a duller or sunnier year would differ."]},
         "change": {"tests": ["tests/test_low_solar_battery.mjs", "tests/test_bill_tool.mjs"], "judge": "money"}},
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
         "stages": ["page:regional_prices.js"],
         "why": "The Electricity Authority publishes the same figures for every network each month, so every area "
                "is priced the same way and the prices update themselves.",
         "try": ["Real retail plans, retailer by retailer"],
         "fails": {"known": ["Splitting one price into peak and off-peak only approximates real plans."]},
         "change": {"tests": ["tests/test_economics.mjs"], "judge": "money"}},
        {"title": "Cost and savings",
         "text": "The cost per kW falls with system size (Assumptions & pricing). Over a {life_years}-year life "
                 "power prices rise {elec_infl_pct}% a year, export rates fall {export_decline_pct}% a year and panels "
                 "lose {degradation_pct}% a year, shown in today's dollars.",
         "facts": [("System life", "life_years", " years"), ("Panel ageing", "degradation_pct", "% a year")],
         "code": ["economics.js::costPerKw", "bill_tool.js::btReturns"], "stages": ["page:economics.js"],
         "why": "Today's dollars make a saving late in the system's life comparable with one now.",
         "try": ["Installer quotes by region in place of one cost curve"],
         "fails": {"known": ["One cost curve for every roof: a hard roof to work on costs more."]},
         "change": {"tests": ["tests/test_economics.mjs", "tests/test_reference_cases.mjs"], "judge": "money"}},
        {"title": "Net saving with a loan",
         "text": "The green box: an average year over the loan, in today's dollars. Bill savings, less the "
                 "repayments on a loan for the system (and battery) at {loan_rate_pct}% over {loan_years} years. "
                 "Replacements are added to the loan when they fall due and repaid with interest by its end: "
                 "the inverter in year {inv_year}, and a battery in year {batt_year} at {batt_repl_pct}% of "
                 "today's price. Paid upfront, they are paid in cash in their year. The repayment is fixed, so "
                 "{cpi_pct}% inflation makes it smaller in today's dollars each year.",
         "facts": [("Loan rate", "loan_rate_pct", "%"), ("Loan term", "loan_years", " years"),
                   ("Battery", "batt_per_kwh", " $/kWh")],
         "code": ["bill_tool.js::btAnnuity", "bill_tool.js::btEvaluate", "bill_tool.js::btReturns"],
         "stages": ["page:bill_tool.js"],
         "why": "Most households would borrow. What is left each year after repayments is the figure they can "
                "act on.",
         "try": ["Green home-loan top-ups at mortgage rates"],
         "fails": {"known": ["One loan rate for everyone."]},
         "change": {"tests": ["tests/test_bill_tool.mjs", "tests/test_reference_cases.mjs"], "judge": "money"}},
        {"title": "Return on investment",
         "text": "Paid upfront: the yearly rate the cost earns over the system's life, after inflation, "
                 "replacements included. With a loan nothing is paid up front, so the return is the net saving a year.",
         "code": ["bill_tool.js::btReturns"], "stages": ["page:bill_tool.js"],
         "why": "A rate of return compares solar with other uses of the same money.",
         "try": ["Years to pay back, shown beside the rate"],
         "fails": {"known": ["The rate assumes the price rises above hold for the whole life."]},
         "change": {"tests": ["tests/test_bill_tool.mjs"], "judge": "money"}},
     ]},
    {"title": "Checks and publishing", "icon": "✅",
     "summary": "Every figure is re-worked and checked before it reaches the map.",
     "items": [
        {"title": "Every figure shows its workings",
         "text": "Tap a number on a building's card to see the sum that made it. The map re-works every "
                 "panel's power from the published inputs and refuses to publish on any mismatch.",
         "code": ["src/emit_region.py", "workings.js", "tools/predeploy_check.py"],
         "stages": ["emit_region", "add_addresses"],
         "why": "A figure anyone can open and add up again is one they can trust, or show to be wrong.",
         "try": ["A public page of what changed in each release, roof by roof"],
         "fails": {"labels": ["bug", "infra"]},
         "change": {"tests": ["tests/test_workings.mjs", "tests/test_output_contract.py", "tests/test_methodology.py"],
                    "judge": "release"}},
     ]},
]

# How a change is judged better or worse, by kind of change. Each item's
# change.judge names one. "run" lines are shell commands; every repo path in
# them must exist (tests/test_methodology.py).
JUDGES: dict = {
    "markup": {
        "title": "Scored blind against the hand-drawn roofs",
        "steps": [
            "Switch the change on and off with its switch, so both runs see the same roofs.",
            "Build the development roofs as if nobody had drawn them, and score what comes out against the drawings: "
            "face overlap, roof lines found, objects found.",
            "Fix rules against the development set only. Score the held-back set once, at the end.",
            "Where no one has drawn a roof, the owner judges before and after side by side, shuffled, without labels.",
        ],
        "run": ["SOLAR_HIDE_LABELS=1 python tools/markup_benchmark.py run",
                "python tools/markup_benchmark.py score",
                "python tools/render_built.py --cases --before <old layouts> --out cases.jpg"],
        "files": ["data/roof_set.json", "data/roof_labels.json", "data/roof_cases.json"],
    },
    "layout": {
        "title": "The same roofs, change off and on",
        "steps": [
            "Run the build's own layout code on a sample of roofs with the change off, then on.",
            "Count what a sound layout never does: panels off the building, across a ridge, on an object.",
            "After a build, no roof should lose roof-face area. Render every roof that moved.",
        ],
        "run": ["SOLAR_X=0 python tools/layout_ab.py pilot --n 200 --out off.json",
                "python tools/layout_ab.py pilot --n 200 --out on.json",
                "python tools/layout_health.py pilot --worst 40",
                "python tools/face_area_check.py --before <old layouts> pilot"],
        "files": ["data/roof_cases.json"],
    },
    "energy": {
        "title": "Worked examples, and the browser against Python",
        "steps": [
            "The worked examples can be checked by hand: a panel's kWh, an hour of sun.",
            "The browser must give the same kWh as the build for real panels.",
            "A change to the sun model or panel figures regenerates that fixture and needs a full rebuild.",
            "Compare with an independent model such as PVGIS on a few roofs.",
        ],
        "run": ["python tests/test_reference_cases.py",
                "python tools/export_workings_fixture.py kinloch > tests/fixtures/workings_kinloch.json",
                "node tests/test_generation.mjs"],
        "files": ["tests/fixtures/workings_kinloch.json"],
    },
    "money": {
        "title": "Worked examples and checks on the page",
        "steps": [
            "The tests price fixed homes and compare with sums worked by hand.",
            "On the page, every roof's money pill must equal its card's figure.",
        ],
        "run": ["node tests/test_economics.mjs", "node tests/test_bill_tool.mjs",
                "node tests/test_reference_cases.mjs"],
        "files": ["tools/econ_check.js", "tools/money_check.js"],
    },
    "inputs": {
        "title": "Same buildings, same areas",
        "steps": [
            "The fetch tests check which buildings and layers an area gets.",
            "A new source must give the same buildings where the old one covers them.",
            "The deploy gate compares the whole build with the live map before it is published.",
        ],
        "run": ["python tests/test_linz_exports.py", "python tests/test_surveys.py",
                "bash tools/deploy_from_vm.sh"],
        "files": ["tools/predeploy_check.py"],
    },
    "release": {
        "title": "The deploy gate",
        "steps": [
            "Every release is compared with the live map: roofs that lose panels, figures that disagree.",
            "Each flagged roof is rendered and judged before the release goes out.",
        ],
        "run": ["bash tools/deploy_from_vm.sh", "python tools/predeploy_check.py"],
        "files": ["tools/predeploy_check.py", "data/roof_cases.json"],
    },
}

# GitHub issue labels the diagram may point to: the repository's own labels.
LABELS = {"bug", "geometry", "model", "scale", "infra", "roof-case", "no-lidar"}
REPO = "rewiring-nz/nz-solar-potential"
CASES = ROOT / "data" / "roof_cases.json"
CASES_SHOWN = 6   # open roofs listed per item; the rest are counted


INTRO = ("How each roof's estimate is made, from the data sources to the money. Pick a step, then a "
         "part of it: what it does, why, what else could work, where it goes wrong and how to change it.")


def _fill(text: str, vals: dict, where: str) -> str:
    # names with digits in them are not figures: 3D, 2D, the ERA5 record
    bare = re.sub(r"\b(?:[23]D|ERA5)\b", "", re.sub(r"\{[a-z_0-9]+\}", "", text))
    if re.search(r"\d", bare):
        raise ValueError(f"{where}: a number is typed in the prose; use a {{placeholder}}: {text!r}")
    return re.sub(r"\{([a-z_0-9]+)\}", lambda m: vals[m.group(1)][0], text)


def sources() -> list[dict]:
    """National LINZ layers with stable roles, and LINZ's own statements.

    Regional elevation and imagery layers vary by area; they are selected and
    recorded at runtime rather than enumerated in version-controlled config.
    """
    meta = json.loads(META.read_text())
    c = _config()
    out = []
    used = {}
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


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def load_cases() -> list[dict]:
    """The flagged roofs (data/roof_cases.json, tools/cases.py)."""
    return json.loads(CASES.read_text())["cases"] if CASES.exists() else []


def _cases_for(words: list[str], cases: list[dict]) -> list[dict]:
    """Open roofs whose defect names any of the words, most often flagged first."""
    words = [w.lower() for w in words]
    hit = [c for c in cases if c.get("status") != "fixed" and c.get("defect")
           and any(w in c["defect"].lower() for w in words)]
    hit.sort(key=lambda c: (-c.get("flagged_times", 1), c["id"]))
    return [{"id": c["id"], "region": c["region"], "defect": c["defect"], "status": c["status"]} for c in hit]


def switch_home(name: str, code: list[str]) -> str:
    """The file that reads a SOLAR_* switch: one the item cites if it does,
    else the first under src/ or tools/."""
    pat = re.compile(re.escape(name) + r"\b")
    cited = [ROOT / r.split("::")[0].split("~~")[0] for r in code]
    rest = sorted((ROOT / "src").glob("*.py")) + sorted((ROOT / "tools").glob("*.py")) \
        + sorted((ROOT / "src").glob("*.sh"))
    for f in cited + rest:
        if f.name != "methodology.py" and f.is_file() and pat.search(f.read_text()):
            return str(f.relative_to(ROOT))
    raise KeyError(f"no file reads {name}")


def _show(show: dict, vals: dict, where: str) -> dict:
    if show["kind"] == "threshold":
        lo, hi, step = show["range"]
        cuts = [{"at": _num(vals[k][0]), "source": vals[k][1]} for k in show["cuts"]]
        if len(show["outcomes"]) != len(cuts) + 1:
            raise ValueError(f"{where}: a threshold needs one more outcome than cuts")
        return {"kind": "threshold", "ask": _fill(show["ask"], vals, where), "unit": show["unit"],
                "min": lo, "max": hi, "step": step, "cuts": cuts,
                "outcomes": [_fill(o, vals, where) for o in show["outcomes"]]}
    if show["kind"] == "losses":
        return {"kind": "losses",
                "parts": [{"label": lab, "pct": _num(vals[k][0]), "source": vals[k][1]} for lab, k in show["parts"]],
                "kept": {"pct": _num(vals[show["kept"]][0]), "source": vals[show["kept"]][1]}}
    raise ValueError(f"{where}: unknown picture {show['kind']!r}")


def build() -> dict:
    vals = values()
    cases = load_cases()
    steps = []
    for si, s in enumerate(STEPS):
        items = []
        for it in s["items"]:
            where = f"{s['title']} / {it['title']}"
            fails, change = it.get("fails", {}), it.get("change", {})
            hit = _cases_for(fails.get("words", []), cases)
            code = it.get("code", [])
            out = {
                "title": it["title"],
                "text": _fill(it["text"], vals, where),
                "facts": [{"label": lab, "value": vals[k][0] + (unit or ""), "source": vals[k][1]}
                          for lab, k, unit in it.get("facts", [])],
                "code": code,
                "stages": it.get("stages", []),
                "why": _fill(it.get("why", ""), vals, where),
                "try": [_fill(t, vals, where) for t in it.get("try", [])],
                "fails": {"cases": hit[:CASES_SHOWN], "cases_total": len(hit),
                          "labels": fails.get("labels", []),
                          "known": [_fill(k, vals, where) for k in fails.get("known", [])]},
                "change": {"switches": [{"name": n, "file": switch_home(n, code)} for n in change.get("switches", [])],
                           "tests": change.get("tests", []), "judge": change.get("judge")},
            }
            if it.get("show"):
                out["show"] = _show(it["show"], vals, where)
            items.append(out)
        steps.append({"title": s["title"], "icon": s.get("icon", ""),
                      "summary": _fill(s["summary"], vals, s["title"]), "items": items})
    judges = {k: {"title": _fill(j["title"], vals, k), "steps": [_fill(x, vals, k) for x in j["steps"]],
                  "run": j["run"], "files": j["files"]} for k, j in JUDGES.items()}
    return {
        "intro": _fill(INTRO, vals, "intro"),
        "repo": REPO,
        "steps": steps,
        "judges": judges,
        "cases_total": len([c for c in cases if c.get("status") != "fixed"]),
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
