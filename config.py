"""Pilot run configuration."""

# WGS84 [min_lon, min_lat, max_lon, max_lat]. Central Queenstown town basin --
# confirmed against real LINZ data: 1270 buildings, full building-outline
# coverage (capture source "Queenstown 0.1m Urban Aerial Photos (2021)"),
# DSM available (layer 105855, "Otago - Queenstown LiDAR 1m DSM (2021)").
PILOT_BBOX = [168.655, -45.045, 168.675, -45.025]
PILOT_BBOX_NZTM2000 = [1257815.95, 5002860.10, 1259272.13, 5005166.35]  # EPSG:2193, same area

# LINZ layer IDs (confirmed to exist and cover the pilot bbox)
LINZ_BUILDING_OUTLINES_LAYER = 101290
LINZ_DSM_LAYER = 105855  # "Otago - Queenstown LiDAR 1m DSM (2021)"
LINZ_DEM_LAYER = 105898  # "Otago - Queenstown LiDAR 1m DEM (2021)" -- bare earth, for shading horizon
LINZ_IMAGERY_LAYER = 114745  # "Queenstown 0.1m Urban Aerial Photos (2021)" -- same year as the DSM and
# the source the building outlines themselves were extracted from, so all three line up temporally

PANEL_WIDTH_M = 1.0
PANEL_HEIGHT_M = 2.0
PANEL_EDGE_SETBACK_M = 0.3  # clearance from roof edges/ridges, fire code convention
MAX_ROOF_SLOPE_DEG = 45

# --- PV system assumptions ---
# These are shown to the end user in the UI, not just baked into the model
# silently -- the brief calls for transparency here. Keep this block as the
# single source of truth so the frontend can render exactly these numbers
# next to every estimate.
PV_ASSUMPTIONS = {
    "panel_rated_power_w": 440,  # W per panel at STC, typical current residential panel
    "panel_area_m2": PANEL_WIDTH_M * PANEL_HEIGHT_M,
    "panel_efficiency_pct": 22.0,  # STC efficiency implied by 440W / 2m2 / 1000W/m2
    "inverter_efficiency_pct": 97.0,  # typical string/micro-inverter conversion efficiency
    "system_derate_pct": 14.0,  # soiling, wiring loss, temperature derate, mismatch -- combined
    "notes": (
        "kWp = panel count x rated power at Standard Test Conditions "
        "(1000 W/m2, 25C cell temp) -- a nameplate figure, not a real-world "
        "output. Daily/annual kWh applies pvlib clear-sky irradiance per "
        "roof facet's slope/aspect, bias-corrected against NASA POWER's "
        "actual cloud-adjusted averages for the area, then derates for "
        "inverter efficiency and system losses above. Real output varies "
        "with weather, panel brand/age, and shading not captured by the "
        "2021 LiDAR survey (e.g. tree growth since capture)."
    ),
}
