# How a roof estimate is made

Every step from a laser scan to the numbers on the map, in plain words. Each step names the file that does it. Open a section to read it.

| Coverage | Value |
| --- | --- |
| Buildings | 32,838 |
| Panels | 1,424,084 |
| A year | 832.8 GWh |

## Survey inputs

What the estimate can see: the LiDAR laser survey, aerial photos and the LINZ register of building outlines. The district is cut into regions and each building belongs to exactly one.

### List the regions

The configured regions plus whatever is on disk. Auckland (Mt Eden) is built but kept off the map.

**Source:** `src/region_build.py · config.py`

### Fetch building outlines

LINZ NZ Building Outlines for each region. An outline is the surveyed wall line, so it sets the roof's edge and its straight sides.

Where a labeller has moved or redrawn the outline in the markup tool, the drawn outline replaces the surveyed one (and any automatic slide onto the LiDAR) everywhere the build reads outlines.

**Source:** `src/fetch_data.py · src/outline_fit.py`

### Fetch the surface model and photos

The 1 m LiDAR surface model and 0.1 m aerial photos. Areas over 8 km² are fetched in chunks and joined. A region with no photos still builds, without photo-based obstruction detection, and the build says so.

**Source:** `src/fetch_regions.py`

### Fetch the point cloud

The raw LiDAR: about 5.8 building points per m², around six times the detail of the 1 m grid. Only returns classed as building count as roof, unless there are fewer than 10, when all returns are used. Only the top storey is modelled.

**Source:** `src/fetch_pointcloud_regions.py · src/pointcloud_source.py`

### Give each building one region

Region boxes overlap. Each building goes to the region whose box holds its centre deepest inside, where its data is least likely to be clipped.

**Source:** `src/region_build.py`

### Check inputs before building

Missing photos, or a surface model that does not line up with the buildings, are caught before hours of compute are spent.

**Source:** `src/run_district_build.sh · src/preflight.py`

### Model a surface where there is no LiDAR

Cromwell, Bannockburn, Glenorchy and Kinloch have no LiDAR. Ground comes from the LINZ 8 m DEM; roofs are hips at 20° under 400 m² and flat above, and buildings under 60 m² are gables. Every such building is labelled "pitch guessed".

**Source:** `src/synthesize_dsm.py`

## Reading the roof

Each footprint is cut into flat faces a panel could lie on. Hand-drawn roofs win over every automatic reading. A roof the pipeline cannot read gets no panels and a plain reason, not a guess.

### Faces

#### Pick the reading

In order: hand-drawn markup; then the selected faces, chosen from four precomputed readings (photo segmentation, a roof-line detector trained on the markup, LiDAR regions grown by surface direction, and the simplest roof form that fits). The simplest form is taken when it is confident (0.55 or more) and the other three all score under 0.50; otherwise the pick goes by score among the first three (`tools/predict_faces.py`). Then a fit to the LiDAR alone. A selected reading scored under 0.30 (`SELECTED_MIN_SCORE`) is not used; one under 0.60 (`SELECTED_TRUST`) must compete with the LiDAR fit and loses if the LiDAR fit explains the returns clearly better.

**Source:** `src/roof_segmentation.py · src/roof_partition.py · tools/predict_faces.py`

#### Hand-drawn roofs

Roof lines are drawn on the plain photo with a metre grid and no model output, so the drawing is not steered by what the pipeline believes. Drawn lines are sealed into faces: near endpoints are joined and loose ends extended until they meet a line. Each face takes the plane its own points support; a face too small for that borrows a neighbour's.

**Source:** `mark_roofs.html · src/label_geometry.py`

#### Drawn roofs skip the automatic gates

The confidence and plane-fit gates test planes the pipeline fitted, which is the wrong test for a drawn face. Where obstructions are drawn, only the drawn ones count.

**Source:** `src/build_layout_geojson.py`

#### LiDAR fit: cut along the walls

Ridges, hips and valleys usually run parallel or square to the walls, so cut directions come from the outline. A region stays whole while one plane holds 85% of its points within 15 cm; otherwise it is cut and each half tested again. A simple gable stops after one cut; a hotel keeps going (up to 60 faces).

**Source:** `src/roof_partition.py`

#### Keep plain forms exact

Where a plain hip or gable explains the roof, its straight lines are kept as they are.

**Source:** `src/structured_roof.py`

#### Tidy the faces

Faces are rebuilt as a clean tiling with no gaps or overlaps. Neighbours on the same plane merge. Edges are straightened. A strip narrower than a row of panels joins the neighbouring slope it lies on. Each face's slope is refitted from its own points. Faces over 55° are not modelled.

**Source:** `src/face_straighten.py · src/roof_segmentation.py`

#### Lower roof levels and courtyards

A wide, flat lower level gets its own face. Courtyards and open decks are removed so no panel sits over them.

**Source:** `src/roof_levels.py · src/roof_partition.py`

#### Match the map photo

The map photo leans 0.5–1.5 m off the LiDAR. Each building's lean is measured photo to photo and the drawing is moved to match, so panels sit on the roof you see. Where the match is weak, the roof alone is matched (the street does not lean); only then is a neighbour's lean borrowed.

**Source:** `src/register_imagery.py`

#### Roofs that changed since the survey

A roof that looks different in the current photo is flagged on its card. Buildings newer than the LiDAR are read from the photo.

**Source:** `src/build_layout_geojson.py`

#### Buildings gone since the survey

An outline whose current photo shows no wall edge along much of it over uneven ground, or a big change in brightness, is shown with the survey photo to a vision model. If it says the building is gone or replaced by one of a different shape, the roof gets no panels and its card says why. A drawn roof is never withheld.

**Source:** `src/demolished.py`

### Obstructions

#### Find objects by colour

Each pixel is compared with its own surroundings (1.2 m), not the whole face, so one big plant yard does not hide a small vent. A colour blob counts only if the LiDAR backs it up, by height or by returns off the roof plane; paint and stains are flat. Small crisp objects (vents, flues) count on contrast alone. Skylights are found separately. Each object is padded 0.25 m outward.

**Source:** `src/obstruction_detection.py`

#### Find objects by height

Returns standing well off the face's plane are obstructions: equipment with height but no colour contrast. A flat-topped box 1.5 m or more above the deck and up to 60 m² (plant room, lift overrun) is an obstruction, not a roof level.

**Source:** `src/obstruction_detection.py`

#### Hand back what is really roof

A patch whose returns lie on this face's plane, or a neighbour's, is roof. So is a big patch whose returns lie on up to three planes parallel to the face (80% of them): a raised roof level, not plant, unless it is such a box. A patch hugging an edge goes unless it stands up.

**Source:** `src/obstruction_planarity.py`

### Say nothing rather than guess

#### Confidence gate

Roof confidence is the area-weighted share of roof points within 0.30 m of the faces. Under 45%, the faces are drawn but no panels are proposed. A building that cannot be modelled stays on the map with a reason: too steep, too little survey, no usable surface, covered by obstructions, no room for a panel, demolished, too complex, or modelling failed. A building that runs past its time budget is dropped and reported by id: 10 minutes, rising by 0.6 s per m² of footprint over 1,500 m² to a 30-minute cap (`src/build_layout_geojson.py`).

**Source:** `src/build_layout_geojson.py · src/roof_segmentation.py · src/derive_solar_potential.py`

## Placing the panels

Real panels are racked on each face the way an installer would: clean arrays, square to the building, on the sunny side, clear of edges and obstructions. Then each panel is checked against the survey.

### The panel

1.134 × 1.961 m, 500 W (Trina TSM-500NEG18R.25), 22.5% efficient.

**Source:** `config.py`

### Lay the face flat

A tilted face is unrolled before packing: the up-slope direction is stretched by 1/cos(slope), because a panel on a slope covers more roof than its plan shadow.

**Source:** `src/panel_fitting.py`

### Clearances

Obstructions and drawn fold lines are cut out first. Then panels keep 0.1 m from the roof edge and 0.1 m from ridges, hips and valleys. A face that fits more than 6 panels keeps 0.4 m from the roof edge and 0.3 m from ridges instead, because a traced edge is often a few tenths of a metre out and a large array can spare its outer column or row; it falls back to 0.1 m when the wider gap would keep under 75% of the face's panels or change its orientation, so a narrow section still gets its row. A fit is kept only when it holds at least 2 more panels than the one before it, starting from none (`SETBACK_LADDER_MIN_GAIN_PANELS`), so a face whose best fit is a single panel gets none. A fold gentler than 9° keeps only 0.05 m.

**Source:** `config.py (PANEL_EDGE_SETBACK_M, RIDGE_SETBACK_M) · src/panel_fitting.py`

### Square to the walls

Each building racks on one grid. On a square-cornered building (walls cover at least 80% of the outline) every face racks square to its nearest wall. On other outlines, a face within 20° of a main wall racks to that wall. Columns line up across rows.

**Source:** `src/panel_fitting.py (building_frame, WALL_CLEAN_COVER)`

### Portrait first

Panels run portrait up the slope. Landscape wins only if it fits 40% more panels, counting a face too narrow for portrait as turned in both choices. A face under 5° is treated as flat.

**Source:** `src/panel_fitting.py (LANDSCAPE_WIN_MARGIN, FLAT_SLOPE_DEG)`

### Clean arrays

One orientation per face. No lone panel: every block has at least 2 touching panels. At least 3 panels on a face, except a clean pair on a face under 12 m². Panels within 1.3 m are one array, because rails span a vent. On a roof with an array of 10 or more, a group under 4 panels that is part of no array is dropped. Each row is a continuous run and the array is centred on its face.

**Source:** `src/panel_fitting.py (ARRAY_MIN_PANELS, ARRAY_MIN_GROUP, ARRAY_TOUCH_M, drop_scraps)`

### Sunny side only

A face more than 112.5° off north stays bare when the roof has a sunnier side, that is, when it gets under 78% of the sunniest face's sun. A face under 65% of the best sun possible at the site never takes panels. Sun share is judged under an open sky and the Queenstown basin's weather in every town, so the same roof gets the same answer anywhere; shade has its own checks. If the sunny side ends up empty, the next-best side is used.

**Source:** `src/build_layout_geojson.py (away_from_sun)`

### Big roofs

On a roof over 1,000 m², a face must fit its own plane well and take at least 8 panels. If not, its panels move to the straggler band (ranks 81–100) rather than being deleted.

**Source:** `src/build_layout_geojson.py`

### Check each panel against the survey

A panel goes if the survey says there is no roof under it: less than 2 m above the ground beside the building, or points that do not fit a plane (0.28 m RMS), or returns more than 0.35 m below its roof level. Missing data never removes a panel: under 8 returns, it stays. Drawn and photo-read roofs skip these tests. Faces the check touched are re-racked.

**Source:** `src/gate_panels.py`

### Deep shade

A panel that keeps less than 45% of the year's direct sun, under a tree or a neighbour, is not proposed.

**Source:** `src/build_layout_geojson.py (DEEP_SHADE_FACTOR)`

### Fill order and default size

Panels are ranked 1–100: coarse sun bands first, then whole rows outward from the best row, so any smaller system is a clean block. Ranks 81–100 are stragglers. On the map, a home (one household, under 350 m² of roof) shows its best panels up to 14 kW. Businesses and larger buildings show every panel that fits. The size slider sets one share for every roof.

**Source:** `src/panel_fitting.py · rooftop-solar.html (HOME_SYSTEM_CAP_KW)`

## Sunlight and yield

How much electricity each panel makes. Clear-sky sun for the place, scaled to measured sunlight, cut for hills, trees and neighbours, then converted to AC kWh with one stated loss.

### Clear-sky year

Hourly direct and diffuse sun for a representative year from pvlib's Ineichen clear-sky model.

**Source:** `src/solar_model.py`

### Scale to measured sunlight

Within about 28 km of Queenstown: NIWA SolarView measured GHI. Beyond about 44 km: the nearest NIWA measured-radiation station (Queenstown Aero for Wānaka and Hāwea). In between, a mix of the two weighted by distance. Each is then scaled by ERA5's difference in sunlight between the region and that point (about 25 km cells). NASA POWER is a fallback no built region uses.

**Source:** `src/solar_model.py`

### Mountains

The skyline in each direction comes from the LINZ 8 m DEM. Direct sun is zeroed whenever the sun is behind it. Diffuse skylight is kept, the same simplification PVGIS makes. The building's own skyline below already includes these hills, so they are counted once.

**Source:** `src/terrain_horizon.py`

### Onto the roof

Sun is projected onto each slope and direction with the Perez sky model and added up over the year. Results are cached in 5° slope by 10° direction bins.

**Source:** `src/solar_model.py`

### Each building's own skyline

72 directions, seen from eave height (the 15th percentile of surface height in the footprint). Far: the 8 m DEM. Near: the 1 m surface model within 300 m, excluding the building itself. The higher of the two counts in each direction.

**Source:** `src/building_horizon.py`

### Shade per face and per panel

Trees and buildings within 100 m shade each face and panel separately, so one wing's shade is not averaged away across the roof. Shade blocks direct sun, weighted by how square the sun hits the face. Skylight drops only by the share of sky the nearby skyline hides.

**Source:** `src/building_shading.py`

### Convert to kWh

kWh/m² of sun on the panel equals full-power hours, because panels are rated at 1,000 W/m². Total system loss is 16.4% including the inverter (0.97 × 0.8621 = 0.836), covering heat, dirt, mismatch, wiring, tolerance, downtime, and light reflected off the glass at low angles (2.76%, PVGIS for Queenstown). Checked against PVGIS (Queenstown, north-facing 25°): the in-plane sunlight agrees within 1% and the map's yield sits about 5% above PVGIS, whose temperature and system losses are generic.

**Source:** `config.py (PV_ASSUMPTIONS)`

### Add it up and check it

A building's kWh is the sum of its panels. The page recomputes every panel's kWh from the same inputs and shows its workings. The build refuses to publish a region whose figures disagree.

**Source:** `src/derive_solar_potential.py · generation.js · src/emit_region.py`

### Daily curves and an outside check

For each slope and direction: an average day per season and a clear day, drawn on the building card and used by the bill tool. The curve is scaled so its year equals the annual figure. Hills are applied in the browser from a mask per 150 m cell. Separately, sun on the panel is compared with PVGIS across sites, tilts and directions.

**Source:** `src/build_seasonal_curves.py · src/build_terrain_masks.py · src/validate_against_pvgis.py`

## Cost, savings and payback

Every roof is priced by the bill tool: a power bill with and without solar, then the savings over 30 years. Every input is editable and every figure opens to its workings. The full panel draws an average day in each season (use by what uses it, solar, grid and exports), lays those days end to end as the year hour by hour (8,760 hours, downloadable, adding up to the same bills), and writes out this method with the numbers in use. Indicative, not a quote.

### Home or business

The council's district plan zone decides (Queenstown Lakes District Council): town centres, shopping, commercial and civic zones are businesses, industrial zones industrial. Where a zone allows both (resort zones such as Jacks Point and Millbrook, which are mostly houses; Business Mixed Use; Settlements), or there is no zoning, a roof over 400 m² or a system over 100 kW counts as a business.

**Source:** `tools/fetch_zoning.py · src/building_types.py · economics.js`

### What it costs

$3,000/kW under 3 kW; $2,300 for 3–8 kW; $1,800 for 8–25 kW; $1,500 for 25–50 kW; $1,200 over 50 kW, including GST. Each rate sits at the middle of its band and the per-kW rate runs in a straight line between them (`costPerKw`), so the total rises with every kW. A battery is $900/kWh.

**Source:** `economics.js (cost_tiers)`

### Household use

A home's year is the Electricity Authority's average for its regional council (EMI Residential consumption league table, 2025; Otago 7,829 kWh), scaled for its people against an average home of 2.6 (40% of use held fixed, an estimate) and for the size of the house by roof area (under 100 m² 30% less, 100–140 m² 15% less, 140–220 m² typical, 220–300 m² 15% more, 300–400 m² 30% more, over 400 m² 50% more; `BT_HOUSE_SIZE_BANDS`). Each season's day, and its spread over the hours, is the metered half-hourly load of the town's grid exit points over the last year (EMI Grid_export; Queenstown: Frankton), which sets how much more a winter day uses than a summer one. That load is homes plus shops and hotels, whose working day fills the midday dip and dulls the peaks (Frankton's winter evening averages 1.17 times its day; homes' 1.71), and EMI publishes no half-hourly split between them. So each hour's share of a home's day keeps the town's hour-to-hour timing inside four periods (night 21–07, morning 07–09, day 09–17, evening 17–21) and takes metered homes' level for each period, per season (GREEN Grid study, Part B Table 3.1: about 40 homes in Taranaki and Hawke's Bay, 2015), with the joins smoothed. A winter home day then runs 0.54 times its average overnight, 1.4 at 8 am, 0.71 at midday and 1.83 at 7 pm. The peak and off-peak home prices are split so this home pays the EA's average price (`tools/fetch_regional_prices.mjs --resplit`). `tools/build_load_profiles.py` writes `load_profiles.js`. Electric cars are added on top. Default: 3 people, no car. A non-home building uses electricity per m² of floor before heating, by type: business 100 kWh (BRANZ BEES SR297/1: NZ office premises a median 115 kWh/m² of electricity and retail 146, heating and cooling included, median HVAC 23), industrial 35 (CIBSE TM46 workshop and storage warehouse; BEES office/warehouse sites about 25), school 35 (UK 39–43, Australian Green Star 54), hospital or medical 150 and community 40 (both estimates), at least 6,000 kWh a year. Its floor is its roof area × storeys, the storeys counted from its height above the ground in the LiDAR surface (`height_m`): 3.6 m a storey, 6 m for industrial. Heating by degree-days scales with the heated floor: 60% of it, 15% for industrial (an estimate: sheds are mostly unheated). Use is spread across the working day. The card's two bills are the highest winter month (the dearest of June, July and August) and the lowest summer month (the cheapest of December, January and February), today and after the install, each worked out over that month's days from the year's days blended smoothly through the seasons (`bill_tool.js` `btBillMonths`). A bill you enter is taken as that month's and scales the modelled use to match it exactly. An estimated bill is shown rounded like every modelled dollar figure ($10 under $1,000, $100 under $10,000, $1,000 above): it rests on averages for the building's type and size, not its own meter. A bill you enter is shown as entered.

**Source:** `bill_tool.js`

### Prices

Time-of-use (`economics.js` `ECON_DEFAULTS`). Homes: 50c peak, 25c off-peak, $2.50 a day, replaced by the Electricity Authority's prices where the site names a network region; flat plan 30c. Businesses pay energy prices only, their network and connection charges being a separate bill solar does not change: 22c peak, 12c off-peak, no daily charge; flat plan 18c, set above the time-of-use average because a single-rate commercial plan carries the retailer's peak risk. Export: 12c in the day; at peak 23c for a home and 15c for a business. Peak is 7–10am and 6–9pm.

**Source:** `economics.js`

### Hour by hour

For an average day in each season, the roof's own generation curve serves the load first, then charges a battery (90% round trip, 10% reserve, 5 kW, solar only), then exports. The rest comes from the grid. The bill is imports × price + daily charge − exports × buyback.

**Source:** `bill_tool.js · economics.js (simulateDay)`

### Over 30 years

Power prices rise 4% a year; export falls 2% a year; panels lose 0.5% a year. The inverter is replaced in year 16 (after 15 years) at 18% of the solar cost, and a battery in year 15 (after 14 years) at 70% of today's battery price. Figures are in today's dollars (2% general inflation taken out). Results: payback year, net savings and return on investment.

**Source:** `bill_tool.js (btReturns)`

### Panel colours

Every roof's panel colours come from the same bill model. Panels that cover power used on site, sunniest first, are valued at the retail price; the rest at the export price. A roof that is not open uses the daily shape of a sunny-side roof in its area, scaled to its own yearly kWh. The building card also offers the economic size: the panels that pay for themselves.

**Source:** `bill_tool.js (btPanelEcon)`

## Resilience

How many days in a row a roof makes little power, and the battery a home would need to ride them out.

### Low-solar runs

ERA5 daily sunlight, 1940–2025 (Copernicus, via Open-Meteo, CC BY 4.0), sets the day-to-day pattern; the roof's own seasonal output sets the level. Direct and diffuse light are scaled separately. A low day is under 20% (editable) of the roof's average day. The card shows the longest run on record, the once-a-decade run and a typical year's run. ERA5 smooths the darkest days, so in the Wakatipu each day is mapped onto the Queenstown Aero station record.

**Source:** `low_solar.js · tools/pack_era5_daily.py · tools/station_qmap.py`

### Battery for a home

Sized to run on roof and battery alone through the hardest low-solar run (a normal year, once a decade, or the record), using the bill tool's hour-by-hour household use, winter heating included. If the roof makes less than the home uses on an average winter day, that shortfall is shown separately.

**Source:** `low_solar.js (battery)`

## Publishing the map

Each region is built and emitted on its own, then joined. A gate compares the new build with the live one before anything is published.

### Build only what changed

Each building has a build key covering its reading, its markup and the geometry code. Only stale buildings rebuild. Each stage records when it finished and skips if its inputs are older.

**Source:** `src/run_district_build.sh · src/build_keys.py · src/run_stage.py`

### Finish each building's record

The building summary is the sum of its panels. Roof confidence, the street address (the address point inside the footprint, else the nearest) and panel counts and kWh at every 10% of coverage are added, so the dashboard can total what is in view at any zoom.

**Source:** `src/derive_solar_potential.py · src/patch_roof_confidence.py · src/add_addresses.py · src/bake_density_deciles.py`

### Emit each region

Building tiles and panel and face tiles (zoom 13–16), sums per grid cell for lower zooms, per-building detail fetched on click (horizons and shade masks), heat-map tiles (0.4 m sun per pixel, straight from the LiDAR), addresses and a summary. Panels are shrunk 4 cm so gaps show.

**Source:** `src/emit_region.py · src/build_heatmap_raster.py`

### Combine the regions

Tiles are joined, cell sums added and tiled once, addresses concatenated. It is built in a temporary folder and swapped in whole, so the served set is never half old, half new.

**Source:** `src/combine_regions.py`

### Gate against the live map

Lists every building that lost all its panels, dropped more than 30% (and 20 panels), or was newly withheld. Fails if more than 25 buildings or any region vanish, or the district's panels fall more than 5%. A person renders and judges the list before publishing.

**Source:** `tools/predeploy_check.py`

### Publish

The data goes to a new version folder (v<N>) in a Google Cloud Storage bucket; the newest three are kept. The site is on GitHub Pages, and site-config.js points it at the current version. Rolling back is pointing it at an older folder.

**Source:** `tools/deploy_from_vm.sh · tools/publish_served.py · site-config.js`

Maintained by hand: update this page in the same change as the step it describes. If a step here disagrees with the code, the code is authoritative. [Open an issue](https://github.com/rewiring-nz/nz-solar-potential/issues) with the step name when you find a discrepancy.
