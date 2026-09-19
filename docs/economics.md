# How the money is worked out

Every dollar figure on the map comes from one file, [`economics.js`](../economics.js),
about 500 lines of arithmetic with no map and no page in it. You can read it
top to bottom, and you can run it:

```bash
node tests/test_economics.mjs
```

That suite exists because a 2.4× error in the yearly figure once survived
long enough to be spotted by eye on the live map. Anything you change here
should keep all of it passing.

Nothing below is a quote. It is a model, every input is on screen, and the
inputs are the part most worth arguing with.

## What gets calculated

For a roof, the model needs three things: how much electricity it makes and
when, how much of that the building uses rather than exports, and what each
of those is worth.

**Generation** comes from the roof geometry, not from this file — panels are
fitted to the actual faces, each face's tilt and aspect give its irradiance,
and shading is applied per panel. The economics receives the result as a
generation profile: kilowatts for each hour of a representative day in each
season, which is exactly the curve drawn in the building panel. If the chart
and the savings ever disagreed, one of them would be lying; they read the
same array.

**Self-consumption** is simulated hour by hour. A household load shape (the
familiar double peak — breakfast, then a bigger evening one) is scaled to
the building's annual use. In each hour, generation meets load first; the
surplus charges a battery if there is one, then exports; any remaining
deficit is imported.

This matters more than it sounds. Self-consumption is an *output* of the
model, not a dial: a bigger array does not make a house use more
electricity, it only exports more. Getting this backwards is the single
easiest way to make a large system look better than it is.

**Value** is then per hour: electricity used on site avoids that hour's
retail price, electricity exported earns the buyback rate. Over the system's
life, retail prices inflate, the export rate declines, panels degrade, and
everything is discounted back to today's money.

## The assumptions, and why each is there

| Assumption | Default | Why |
|---|---|---|
| System cost | $1,200–3,000/kW by size | Smaller systems cost more per kW; the tiers are on screen |
| Retail price | 30c home / 17c business | Homes and businesses buy at very different prices |
| Export price | 14c, declining 2%/yr | Buyback is falling; a flat rate would flatter every system |
| Electricity inflation | 4%/yr | Applied to retail only — export is already on a declining path |
| Discount rate | 3%/yr | Money later is worth less. Set it to 0 for an undiscounted view |
| Panel degradation | 0.5%/yr | Standard for modern modules |
| Inverter replacement | year 15, 18% of install | An inverter does not last 30 years; omitting it flatters everything |
| System life | 30 years | |
| Battery | 10 kWh, $1,000/kW, 90% round trip, 10% reserve, replaced at 15 years | A battery that is never replaced looks far better than a real one |

## Home or business?

Not by roof size. A 450 m² house in a suburb and a 450 m² business look
identical from above, so a size rule mislabels large houses by construction.
The classifier uses the **council district plan zone** the building sits in —
what may legally be built there, from the authority that decides it
([`tools/fetch_zoning.py`](../tools/fetch_zoning.py)). Where a zone genuinely
holds both uses, or no zoning is published, it falls back to the size test.

Measured on the pilot area, zoning disagrees with the old size rule on 205 of
1,066 buildings, in both directions.

## Retail plans

The plans offered are **shapes of plan sold in New Zealand** — flat, day/
night, peak/off-peak, high-buyback, a free hour — not offers from any
retailer, which is why none is named. Every rate is editable, and the panel
shows all 24 hours with the price of each, so you can see whether a
particular roof produces when power is dear or when it is cheap.

A plan only changes the answer because the model prices each hour separately.
On an annual model every plan is the same number.

## What this model does not do

- No half-hourly metering data. The load is a shape, not your actual usage.
- No network or daily fixed charges in the savings figure; solar does not
  usually avoid them.
- No feed-in caps, no export limits, no phase constraints.
- No maintenance, insurance, or cleaning.
- No tax, depreciation or commercial financing.
- The battery never charges from the grid, so plans with cheap night rates
  are undervalued for battery owners.

Each of these would move the answer. They are left out because putting them
in badly would be worse than leaving them visible as gaps.

## Checking it yourself

- `node tests/test_economics.mjs` — 17 assertions over the whole model.
- Open any building, change a rate band, and watch the figures move.
- `panel_count × 0.5 kW = kWp`, and annual kWh ÷ panels should land in
  550–800 kWh/panel/yr for New Zealand.
- [`docs/quickstart.md`](quickstart.md) runs the entire pipeline, geometry
  included, on a small area you choose.
