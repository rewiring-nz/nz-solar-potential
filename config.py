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
LINZ_IMAGERY_LAYER = 124754  # "Queenstown 0.1m Urban Aerial Photos (2026)" -- captured 12 Feb-3 Mar
# 2026, replacing the 2021 capture this pilot originally used. More current (new/changed rooftop
# equipment, growth) at the cost of no longer matching the DSM/building-outline capture year exactly
# (both still 2021) -- worth revisiting if that skew ever shows up as a real building-outline/roof
# misalignment, but the two are already independently-sourced datasets with their own tolerances.

PANEL_WIDTH_M = 1.0
PANEL_HEIGHT_M = 2.0
PANEL_EDGE_SETBACK_M = 0.3  # clearance from the roof's own outer edge (eave/verge) -- common
# fire-code convention. Lowered to 0.1 earlier per explicit request after it was found strangling
# narrow facets (a real ~1.4m-wide strip loses 0.6m total, under the panel's own 1m minimum
# dimension, so it fit zero panels despite real usable area, on #5371143) -- but that traded away
# realistic edge clearance on every *normal-width* facet just to rescue the rare narrow one, and
# was reported back as making edges "clearly wrong" on ordinary roofs. Restored to 0.3;
# PANEL_EDGE_SETBACK_FALLBACK_M below handles the narrow-facet case instead, per-facet.
PANEL_EDGE_SETBACK_FALLBACK_M = 0.1  # retried only for a facet that fits zero panels at the
# primary setback above -- keeps narrow facets panelable without loosening the default for
# everything else.
RIDGE_SETBACK_M = 0.25  # extra clearance specifically along a boundary shared with another real
# roof plane on the same building (a real ridge, hip, or valley) -- separate from, and on top of,
# PANEL_EDGE_SETBACK_M's outer-edge clearance. Two adjacent facets each erode this far back from
# their shared boundary, so the real join between two differently-angled roof sections reads as
# an actual visible gap (like real ridge cap flashing) instead of two panel grids butting flush
# against each other with no visual break between them.
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
