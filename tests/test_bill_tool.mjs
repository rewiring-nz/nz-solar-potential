/**
 * Tests for bill_tool.js: the household load model, the bills, and the
 * calibration to a user's real bills.
 *
 * Run:  node tests/test_bill_tool.mjs
 */

import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const E = require("../economics.js");
// bill_tool.js reads these from the page's shared scope.
Object.assign(globalThis, { simulateDay: E.simulateDay, systemCost: E.systemCost,
                            BATTERY_DEFAULTS: E.BATTERY_DEFAULTS, econ: E.getEcon() });
const B = require("../bill_tool.js");

let pass = 0;
const failures = [];
function check(name, fn) {
  try { fn(); console.log(`  pass  ${name}`); pass++; }
  catch (e) { console.log(`  FAIL  ${name}: ${e.message}`); failures.push(name); }
}
const near = (a, b, tol, what) => {
  if (Math.abs(a - b) > tol) throw new Error(`${what}: ${a} vs ${b} (tol ${tol})`);
};
const sum = a => a.reduce((t, v) => t + v, 0);
const home = (o) => Object.assign({ type: "home" }, B.BT_DEFAULTS.home, o || {});
const prices = { buy: 30, sell: 14, daily: 2.5 };

// A plain 6 kW roof: a sine day, more in summer than winter.
function genFor(kwp) {
  const peak = { summer: 0.75, autumn: 0.55, winter: 0.35, spring: 0.6 };
  return B.BT_SEASONS.map(s => Array.from({ length: 24 }, (_, h) =>
    h >= 7 && h <= 19 ? kwp * peak[s] * Math.sin(Math.PI * (h - 7) / 12) : 0));
}

check("shapes are fractions of a day", () => {
  for (const [k, s] of Object.entries(B.BT_SHAPES)) near(sum(s), 1, 1e-9, k);
});

check("a typical home uses about what NZ homes use", () => {
  // 3 people, electric cylinder, heat pumps, Queenstown: MBIE's ~7,000 kWh
  // average sits inside the range a cold-climate electric home lands in.
  const L = B.btBuildLoad(home(), { lat: -45.03, roofM2: 150 });
  const year = B.BT_SEASONS.reduce((t, s, i) => t + B.btDailyKwh(L, s) * [90, 92, 92, 91][i], 0);
  if (year < 6000 || year > 11000) throw new Error(`year ${Math.round(year)} kWh`);
});

check("gas hot water and gas heating take electricity off the bill", () => {
  const el = B.btBuildLoad(home(), { lat: -45, roofM2: 150 });
  const gas = B.btBuildLoad(home({ water: "gas", heating: "gas" }), { lat: -45, roofM2: 150 });
  if (!(B.btDailyKwh(gas, "winter") < B.btDailyKwh(el, "winter") * 0.6)) throw new Error("gas home not lighter");
  near(gas.parts.water.winter, 0, 1e-9, "gas water"); near(gas.parts.heat.winter, 0, 1e-9, "gas heat");
});

check("Queenstown heats more than Auckland", () => {
  const q = B.btBuildLoad(home(), { lat: -45.03, roofM2: 150 });
  const a = B.btBuildLoad(home(), { lat: -36.88, roofM2: 150 });
  const ratio = q.parts.heat.winter / a.parts.heat.winter;
  if (ratio < 2.2 || ratio > 3.2) throw new Error(`ratio ${ratio}`);
});

check("an EV adds about 5 kWh a day", () => {
  const L = B.btBuildLoad(home({ evs: 1 }), { lat: -45, roofM2: 150 });
  near(L.parts.ev.autumn, 11000 * 0.165 / 365, 0.01, "ev kWh/day");
});

check("solar lowers both bills, and summer more than winter", () => {
  const ctx = { lat: -45.03, roofM2: 150, kwp: 6, gen: genFor(6) };
  const r = B.btEvaluate(ctx, home(), null, prices);
  const W = r.run.seasons.winter, S = r.run.seasons.summer;
  if (!(W.solarMonth < W.nowMonth && S.solarMonth < S.nowMonth)) throw new Error("solar did not lower bills");
  if (!((S.nowMonth - S.solarMonth) > (W.nowMonth - W.solarMonth))) throw new Error("summer saving not larger");
  if (!(r.solarSave > 0)) throw new Error("no annual saving");
});

check("the bill with no solar is imports x price + daily charge", () => {
  const ctx = { lat: -45, roofM2: 150, kwp: 6, gen: null };
  const r = B.btEvaluate(ctx, home(), null, prices);
  const W = r.run.seasons.winter;
  near(W.nowMonth, (W.loadKwh * 0.30 + 2.5) * 30.4, 0.01, "winter bill");
  near(W.solarMonth, W.nowMonth, 1e-6, "no gen, no change");
});

check("timing hot water to the sun raises what the roof covers", () => {
  const ctx = { lat: -45, roofM2: 150, kwp: 6, gen: genFor(6) };
  const night = B.btEvaluate(ctx, home({ waterOnSun: false }), null, prices);
  const sun = B.btEvaluate(ctx, home({ waterOnSun: true }), null, prices);
  if (!(sun.solarSave > night.solarSave)) throw new Error(`${sun.solarSave} vs ${night.solarSave}`);
});

check("a battery lowers the bill further, and costs more", () => {
  const ctx = { lat: -45, roofM2: 150, kwp: 6, gen: genFor(6) };
  const r = B.btEvaluate(ctx, home({ battery: 10 }), null, prices);
  if (!(r.battSave > r.solarSave)) throw new Error("battery did not add savings");
  near(r.battCost, 10 * E.BATTERY_DEFAULTS.cost_per_kwh, 1e-6, "battery cost");
});

check("entered bills set the 'now' bills exactly", () => {
  const ctx = { lat: -45, roofM2: 150, kwp: 6, gen: genFor(6) };
  const r = B.btEvaluate(ctx, home(), { winter: 420, summer: 190 }, prices);
  near(r.run.seasons.winter.nowMonth, 420, 0.5, "winter");
  near(r.run.seasons.summer.nowMonth, 190, 0.5, "summer");
  if (!r.cal.matched) throw new Error("not flagged as matched");
});

check("one bill alone scales the whole year", () => {
  const ctx = { lat: -45, roofM2: 150, kwp: 6, gen: null };
  const r = B.btEvaluate(ctx, home(), { winter: 400 }, prices);
  near(r.run.seasons.winter.nowMonth, 400, 0.5, "winter");
  near(r.cal.scale.summer, r.cal.scale.winter, 1e-9, "same scale");
});

check("payback is cost over a growing saving, within reason", () => {
  const ret = B.btReturns(10000, 1000, 300);
  if (!(ret.payback > 7 && ret.payback < 12)) throw new Error(`payback ${ret.payback}`);
  const never = B.btReturns(100000, 100, 50);
  if (never.payback !== null && never.payback < 60) throw new Error("should not pay back");
});

check("a business uses more than a home and peaks in the day", () => {
  const biz = B.btBuildLoad({ type: "business", heating: "heatpump", evs: 0 }, { lat: -45, roofM2: 600 });
  const h = B.btBuildLoad(home(), { lat: -45, roofM2: 150 });
  if (!(B.btDailyKwh(biz, "autumn") > B.btDailyKwh(h, "autumn"))) throw new Error("business lighter");
  const L = B.btSeasonLoad(biz, "autumn");
  if (!(L[11] > L[2] * 3)) throw new Error("no daytime peak");
});

console.log(`\n${pass} passed, ${failures.length} failed`);
if (failures.length) process.exit(1);
