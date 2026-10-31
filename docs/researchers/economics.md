# How the money is worked out

Every dollar figure on the map comes from the bill tool,
[`bill_tool.js`](../../bill_tool.js). It models a power bill with and without
solar (and a battery), then runs the savings out over the panels' life. Cost
tiers, prices and the battery simulation it calls are in
[`economics.js`](../../economics.js).

It is a model, not a quote. Every input is on screen and editable, and every
figure opens to its workings.

Tests: `node tests/test_bill_tool.mjs` and `node tests/test_economics.mjs`.

## The steps

1. **Household use.** The load is built from what people know about their
   home: how many people live there, how water is heated, how rooms are
   heated, and how many electric cars there are. Each part has its own
   daily kWh per season and its own hour-of-day shape. Heating scales with
   the town's heating degree-days, so a Queenstown home heats more than an
   Auckland one. A business uses 75 kWh per m² of footprint a year (at least
   6,000 kWh), plus heating and cars.
2. **Your bills (optional).** Enter a winter and a summer bill, or a yearly
   kWh figure, and the modelled load is scaled to match.
3. **Solar.** The roof's own average day in each season: the generation
   curve drawn on the building card, scaled to the system shown. Its year
   equals the roof's annual generation figure.
4. **Hour by hour.** For an average day in each season, solar serves the
   load first, then charges the battery, then exports. What is left comes
   from the grid. The bill is imports × price + daily charge − exports ×
   buyback.
5. **Over the years.** Bill savings are run out for 30 years: power prices
   rise, export rates fall, panels lose output, and the inverter (and any
   battery) is replaced. Figures are in today's dollars by default.
6. **Results.** Winter and summer monthly bills now and with solar, bill
   savings, net savings over 30 years, payback year and return on
   investment (the yearly rate at which the savings, discounted back,
   equal the cost).

## Default assumptions

| Assumption | Default |
|---|---|
| Install cost | $3,000/kW under 3 kW; $2,000 for 3–8 kW; $1,800 for 8–25 kW; $1,500 for 25–50 kW; $1,200 over 50 kW (incl. GST) |
| Home prices | 34c/kWh peak, 26c off-peak, $2.50/day |
| Business prices | 19c/kWh peak, 14c off-peak, $8.00/day |
| Export price | 14c/kWh, falling 2% a year |
| Peak hours | 7–10am and 6–9pm |
| Power price rise | 4% a year |
| General inflation (to today's dollars) | 2% a year |
| Panel output loss | 0.5% a year |
| System life | 30 years |
| Inverter | replaced after 15 years at 18% of the solar cost |
| Battery | $1,000/kWh, 5 kW, 90% round trip, 10% reserve, replaced after 15 years; charges from solar only, never the grid |
| Household | 3 people, electric hot water on a midday timer, heat pump, no car, no battery |

## Home or business

The council's district plan zone decides it
([`tools/fetch_zoning.py`](../../tools/fetch_zoning.py), Queenstown Lakes
District Council). Roof size is not a use signal: a 450 m² house and a 450 m²
shop look the same from above. Where a zone allows both, or no zoning exists,
a roof over 400 m² or a system over 100 kW counts as a business.

## System size

Until you move the size slider, a home (one household, under 350 m² of roof)
shows its best panels up to 14 kW. Businesses and larger residential
buildings show every panel that fits. The building card also works out the
economic size, the panels that pay for themselves, and offers it with one
tap.

## Panel colours

Every roof's panel colours come from the same bill model. Panels that cover
power used on site, sunniest first, are valued at the retail price. The rest
are valued at the export price. A panel's colour shows whether its
generation repays its share of the cost. A roof that is not open uses the
daily shape of a sunny-side roof in its area, scaled to its own yearly kWh,
so it keeps its colours when you open it.

## Battery for resilience

Low-solar runs come from ERA5 daily sunlight, 1940–2025
([`low_solar.js`](../../low_solar.js)). For a home, the battery is sized to run
on roof and battery alone through the year's hardest low-solar run (a normal
year, once a decade, or the record), using the bill tool's hour-by-hour
household use, winter heating included. If the roof makes less than the home
uses on an average winter day, that shortfall is shown separately.

## What the model leaves out

- Real metering data. The load is a modelled shape, scaled to your bills if
  you enter them.
- Export limits, feed-in caps and network constraints.
- Maintenance, insurance and cleaning.
- Tax, depreciation and financing.
- Charging the battery from the grid at cheap night rates.

## Check it yourself

- Open a building, change a price or the household, and watch the figures
  move. Click any figure to see how it is worked out.
- kWp = panels × 0.5 kW. Annual kWh per panel should be 550–800 in New
  Zealand.
