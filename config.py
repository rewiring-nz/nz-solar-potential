"""Pilot run configuration."""

# WGS84 [min_lon, min_lat, max_lon, max_lat]. Central Queenstown town basin --
# confirmed against real LINZ data: 1270 buildings, full building-outline
# coverage (capture source "Queenstown 0.1m Urban Aerial Photos (2021)"),
# DSM available (layer 105855, "Otago - Queenstown LiDAR 1m DSM (2021)").
PILOT_BBOX = [168.655, -45.045, 168.675, -45.025]
PILOT_BBOX_NZTM2000 = [1257815.95, 5002860.10, 1259272.13, 5005166.35]  # EPSG:2193, same area

# --- Queenstown-wide expansion regions --------------------------------------
# WGS84 [min_lon, min_lat, max_lon, max_lat] per urban sub-region. Boxes are
# data-driven, not hand-drawn: candidate rectangles were validated against
# real LINZ building-outline counts (WFS), then tightened to the 2nd-98th
# percentile of actual building centroids +150m pad (with k-means splits for
# regions whose buildings cluster into separate settlements) -- fetching
# imagery for one giant rectangle spanning town-to-Arrowtown would be ~60GB
# of mostly lake and mountainside. ~9,650 buildings across these boxes
# (verified counts per box, Aug 2026) + 1,270 in the existing pilot bbox.
# The pilot itself stays on its original top-level paths; each region here
# gets its own data/regions/<name>/ tree.
REGIONS = {
    "town_west_fernhill":  [168.6211, -45.0463, 168.6578, -45.0295],  # 1245 buildings
    "town_gorge_north":    [168.6573, -45.0233, 168.6717, -45.0098],  # 189
    "frankton_flats":      [168.7001, -45.0309, 168.7499, -45.0147],  # 1625
    "frankton_quail_rise": [168.7337, -45.0160, 168.7600, -44.9939],  # 569
    "kelvin_heights":      [168.6791, -45.0485, 168.7196, -45.0271],  # 946
    "jacks_point":         [168.7255, -45.1017, 168.7589, -45.0724],  # 481
    "hanleys_farm":        [168.7428, -45.0742, 168.7599, -45.0609],  # 639
    "shotover_lakehayes":  [168.7335, -45.0008, 168.7814, -44.9606],  # 814
    "arthurs_point":       [168.6733, -44.9934, 168.7114, -44.9691],  # 413
    "arrowtown_millbrook": [168.7904, -44.9599, 168.8476, -44.9376],  # 2712
    # Gap-filler (Josh, 23 Aug): the strip between shotover_lakehayes and
    # arrowtown_millbrook plus the whole Lake Hayes east side fell between
    # the original bboxes -- Speargrass Flat properties reported missing.
    "speargrass_hayes":    [168.7780, -45.0100, 168.8480, -44.9550],
    # Old Arthurs Point (Josh, 23 Aug): lower Arthurs Point Rd / Shotover
    # bridge side fell east of the original arthurs_point bbox. 318 outlines,
    # full 2021 LiDAR coverage confirmed.
    "arthurs_point_east":  [168.7114, -45.0050, 168.7420, -44.9700],
    # Third gap region (Josh, 23 Aug): the whole Frankton Rd arm hillside --
    # Panorama Tce, Goldfield Hts, mid-arm -- sat between the (CBD-only)
    # pilot box and frankton_flats. Never fetched, never built.
    "frankton_arm":        [168.6740, -45.0330, 168.7360, -45.0080],
    # 11 audit-derived gap regions (23 Aug): a district-wide sweep of every
    # LINZ outline vs every bbox found 1,528 uncovered buildings clustered in
    # these slivers. Bboxes computed from the uncovered clusters themselves
    # (400m clustering, 250m pad) -- no more hand-drawn coverage.
    "lake_hayes_est_west": [168.7577, -45.0144, 168.7840, -44.9983],  # 350
    "arrowtown_east":      [168.8455, -45.0066, 168.8810, -44.9612],  # 199
    "gorge_road_corridor": [168.6599, -45.0018, 168.6822, -44.9757],  # 180
    "tucker_beach":        [168.7096, -44.9725, 168.7485, -44.9478],  # 134
    "sunshine_bay_west":   [168.5822, -45.0644, 168.6006, -45.0368],  # 126
    "dalefield":           [168.7491, -44.9630, 168.7924, -44.9377],  # 119
    "arrowtown_hills":     [168.8595, -44.9877, 168.8910, -44.9624],  # 79
    "frankton_arm_lake":   [168.6725, -45.0379, 168.6815, -45.0305],  # 79 (Josh's strip)
    "town_south_lake":     [168.6541, -45.0275, 168.6651, -45.0209],  # 56
    "frankton_east_lake":  [168.7478, -45.0307, 168.7564, -45.0215],  # 24
    "kelvin_south":        [168.7528, -45.0519, 168.7718, -45.0405],  # 15
}

# Buildings confirmed demolished/replaced since the 2021 capture (field
# reports) -- excluded from every build until LINZ data catches up.
DEMOLISHED_BUILDING_IDS = {
    4735131,  # 61 Ballarat St -- now under the new road corridor (Josh, 23 Aug)
}
LINZ_LIDAR_TILE_INDEX_LAYER = 105905  # "Otago - Queenstown LiDAR Tile Index (2021)" -- maps a
# bbox to the CL2_*.copc.laz point-cloud tile names, which are then fetched from OpenTopography's
# public bulk store (LINZ hosts the derived DSM/DEM rasters but not the raw point cloud)

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
