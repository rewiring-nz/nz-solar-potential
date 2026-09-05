# Using the web map

The map estimates rooftop solar potential for buildings covered by the
project's datasets. It is a planning aid, not a system design, engineering
certificate, quote, or guarantee of energy production.

## Find and inspect a building

1. Open the deployed map and navigate to the building.
2. Select its rooftop to view estimated panel count, installed capacity in
   kWp, average daily output, and annual output.
3. Use the map's heat-map view for a broad comparison and its panel-layout
   view to inspect the modelled roof facets, excluded obstructions, and panel
   positions for a selected building.
4. In the building detail panel, toggle between **Average** and **Sunny day**
   seasonal generation curves, or switch to the **Horizon** tab to inspect the
   skyline silhouette (mountain terrain and nearby trees/buildings) plotted
   against seasonal sun paths (summer, equinox, winter).

## Interpret the estimate

- `kWp` is the panels' nameplate capacity at Standard Test Conditions. It is
  not the amount of power produced continuously.
- Daily and annual `kWh` are model estimates. They combine roof slope and
  aspect, a pvlib irradiance model, NASA POWER cloud-adjusted data,
  per-building horizon shading (terrain and nearby obstacles), and the PV
  assumptions stored in `config.py`.
- The **Horizon** tab shows the percentage of annual direct-beam solar radiation
  retained after surrounding topography and nearby structures are accounted for.
- A roof with no placed panels is not necessarily unsuitable for solar. It
  may be too small after setbacks, too steep, shaded, unresolved by the
  available data, or excluded by the current model.

## Important limitations

- Estimates depend on source captures with a specific date. New construction,
  removals, vegetation growth, and rooftop changes may not be represented.
- Roof facets are inferred from LiDAR and imagery. Small or complex roofs,
  curved roofs, and roofs with subtle equipment can be resolved imperfectly.
- Imagery-based obstruction detection is deliberately conservative. It can
  exclude benign roof areas and can miss equipment with similar colour to the
  surrounding roof.
- The result does not assess structural capacity, electrical design, consent,
  fire access, tariffs, export limits, site access, or installation cost.

For an installation decision, have a qualified solar installer inspect the
current site and design the system.