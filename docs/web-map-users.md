# Using the web map

The map estimates rooftop solar for each building. It is a planning aid,
not a system design, quote or guarantee.

## Look at a building

1. Find the building by searching or moving the map.
2. Click its roof to see panel count, system size (kWp), and daily and
   yearly output.
3. Use the heat map to compare roofs, and the panel layout to see roof
   faces, obstructions and panels.
4. In the building panel, switch the seasonal curves between **Average**
   and **Sunny day**, or open **Horizon** to see the skyline (hills, trees,
   buildings) against the sun's path in summer, at the equinox and in
   winter.

## Read the numbers

- `kWp` is the panels' nameplate rating at Standard Test Conditions, not
  continuous output.
- `kWh` figures are model estimates. They use each roof face's slope and
  direction, sunlight calibrated to NIWA measurements, shading from hills,
  trees and buildings, and the PV assumptions in `config.py`.
- **Horizon** shows the share of the year's direct sun the roof keeps after
  hills and nearby objects.
- A roof with no panels is not necessarily bad for solar. It may be too
  small after setbacks, too steep, too shaded, or not readable from the data.

## Limits

- The data has capture dates. New buildings, demolitions, tree growth and
  roof changes may be missing.
- Roof faces are inferred from LiDAR and photos. Small, curved or complex
  roofs can be read wrongly.
- Photo-based obstruction detection is cautious. It can block usable roof
  and miss equipment the same colour as the roof.
- It does not check structure, wiring, consent, fire access, export limits
  or site access.

Before installing, get a qualified installer to inspect the site and design
the system.
