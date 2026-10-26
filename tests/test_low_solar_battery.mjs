/**
 * Resilience's battery (low_solar.js battery): enough to carry the home from
 * the sunny evening before a run of low-solar days to the next day's sun --
 * and not the whole winter's shortfall when the roof is short every day.
 *
 * Run:  node tests/test_low_solar_battery.mjs
 */

import { createRequire } from "node:module";
import assert from "node:assert/strict";
const require = createRequire(import.meta.url);
const LS = require("../low_solar.js");

const SEASONS = ["summer", "autumn", "winter", "spring"];
// a roof making 20 kWh on a clear day, 2 kW for ten hours from 7am
const day = Array.from({ length: 24 }, (_, h) => (h >= 7 && h < 17 ? 2 : 0));
const curve = { seasons: SEASONS, gen: SEASONS.map(() => day.slice()) };
// a home using 1 kWh every hour of the year (24 a day)
const flat = Object.fromEntries(SEASONS.map(s => [s, new Array(24).fill(1)]));

function cellWith(dark, avg = 25) {
  const n = 365 * 2, t0 = Date.UTC(2001, 0, 1);
  const month = new Uint8Array(n), year = new Uint16Array(n);
  for (let i = 0; i < n; i++) { const d = new Date(t0 + i * 86400000); month[i] = d.getUTCMonth(); year[i] = d.getUTCFullYear(); }
  const gen = new Float64Array(n).fill(20);
  for (const i of dark) gen[i] = 0;
  const runs = [];
  for (let i = 0; i < n;) { if (gen[i] < 4) { let j = i; while (j < n && gen[j] < 4) j++; runs.push({ start: i, len: j - i }); i = j; } else i++; }
  return { cell: { month, year }, r: { gen, runs, years: 2, y0: 2001, roof: new Array(12).fill(avg) } };
}

// three dark days from 10 Jul 2001: the evening before (17:00-24:00, 7 kWh),
// three days of 24 kWh, and the next morning until 7am (7 kWh) = 86 kWh
// drawn, over 0.95 discharge efficiency
{
  const { cell, r } = cellWith([190, 191, 192]);
  const b = LS.battery(cell, r, curve, flat);
  assert.ok(Math.abs(b.record.kwh - 86 / 0.95) < 1e-6, b.record.kwh);
  assert.equal(b.record.len, 3);
  assert.equal(b.record.start, 190);
  assert.equal(b.short.length, 0);
}

// a home using more than the roof makes every day (30 kWh against 20): the
// run's battery stops at the next midday, and the season's shortfall is
// reported apart, not added to it
{
  const big = Object.fromEntries(SEASONS.map(s => [s, new Array(24).fill(1.25)]));
  const { cell, r } = cellWith([190], 20);
  const b = LS.battery(cell, r, curve, big);
  assert.ok(b.record.kwh < 60, b.record.kwh);          // a day and a half of use, not months
  assert.equal(b.short.length, 12);
  assert.ok(Math.abs(b.shortKwh - 10 * 365) < 1e-6, b.shortKwh);
}

// no runs at all: nothing needed
{
  const { cell, r } = cellWith([]);
  const b = LS.battery(cell, r, curve, flat);
  assert.equal(b.record.kwh, 0);
  assert.equal(b.typical.kwh, 0);
}
console.log("ok: low-solar battery");
