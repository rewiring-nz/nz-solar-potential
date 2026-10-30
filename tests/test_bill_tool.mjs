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
globalThis.Wk = require("../workings.js").Wk;
Object.assign(globalThis, { simulateDay: E.simulateDay, systemCost: E.systemCost, costPerKw: E.costPerKw,
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

check("every figure the tool shows re-checks, step by step", () => {
  const ctx = { lat: -45, roofM2: 150, kwp: 6, gen: genFor(6) };
  for (const bills of [null, { winter: 420 }, { winter: 420, summer: 190 }, { total: 9000 }]) {
    const r = B.btEvaluate(ctx, home({ battery: 10, evs: 1 }), bills,
                           { peak: 34, offpeak: 26, peakSell: 14, offpeakSell: 12, daily: 2.5 });
    const nodes = [r.nodes.solarSave, r.nodes.battSave, r.nodes.useYear, r.nodes.genYear, r.nodes.solarCost,
                   r.nodes.allCost, r.nodes.dayKw, r.nodes.dayShare,
                   ...Object.values(r.nodes.parts),
                   ...B.BT_SEASONS.flatMap(s => ["nowMonth", "solarMonth", "battMonth"].map(k => r.nodes.seasons[s][k])),
                   ...Object.values(r.solarRet.nodes), ...Object.values(r.battRet.nodes)];
    for (const n of nodes) {
      const v = Wk.verify(n);
      if (!v.ok) throw new Error(`${JSON.stringify(bills)}: ${v.problems[0].node.label}`);
    }
    // the legacy numbers are the nodes' values
    near(r.solarSave, r.nodes.solarSave.value, 0, "solarSave");
    near(r.useKwh, r.nodes.useYear.value, 0, "useKwh");
    near(Object.values(r.yearParts).reduce((a, b) => a + b, 0), r.useKwh, 1e-6, "parts add to the year");
  }
});

check("time-of-use: peak power costs more, and the bill knows when it is used", () => {
  const ctx = { lat: -45, roofM2: 150, kwp: 6, gen: null };
  const flat = B.btEvaluate(ctx, home(), null, { peak: 30, offpeak: 30, peakSell: 14, offpeakSell: 14, daily: 2.5 });
  const tou = B.btEvaluate(ctx, home(), null, { peak: 40, offpeak: 30, peakSell: 14, offpeakSell: 14, daily: 2.5 });
  if (!(tou.run.seasons.winter.nowMonth > flat.run.seasons.winter.nowMonth)) throw new Error("peak not priced");
});

check("an entered bill is matched exactly under time-of-use prices", () => {
  const ctx = { lat: -45, roofM2: 150, kwp: 6, gen: genFor(6) };
  const r = B.btEvaluate(ctx, home(), { winter: 500, summer: 180 },
                         { peak: 38, offpeak: 24, peakSell: 16, offpeakSell: 12, daily: 2.2 });
  near(r.run.seasons.winter.nowMonth, 500, 1e-6, "winter");
  near(r.run.seasons.summer.nowMonth, 180, 1e-6, "summer");
});

check("yearly use entered sets the year's use exactly", () => {
  const ctx = { lat: -45, roofM2: 150, kwp: 6, gen: null };
  const r = B.btEvaluate(ctx, home(), { total: 8200 }, prices);
  near(r.useKwh, 8200, 1e-6, "year");
});

check("real money is smaller than nominal over a long life", () => {
  const save = 1500, exp = 400;
  const keep = E.getEcon().real_prices;
  E.getEcon().real_prices = true;  const real = B.btReturns(12000, save, exp);
  E.getEcon().real_prices = false; const nom = B.btReturns(12000, save, exp);
  E.getEcon().real_prices = keep;
  if (!(real.lifetime < nom.lifetime)) throw new Error(`${real.lifetime} vs ${nom.lifetime}`);
});

check("the return per year makes cost and savings come out even", () => {
  const r = B.btReturns(12000, 1500, 400);
  if (!(r.irr > 0.05 && r.irr < 0.25)) throw new Error(`irr ${r.irr}`);
  const v = Wk.verify(r.nodes.irr);
  if (!v.ok) throw new Error("irr not re-checked");
});

check("lifetime savings split into power not bought + export income, exactly", () => {
  const r = B.btReturns(12000, 1500, 400);
  near(r.lifeRetail + r.lifeExport, r.lifetime, 1e-6, "split");
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

// THE EXPLORER (season charts, the year hour by hour) draws from the bills'
// own hour rows; these hold it to the same figures.
check("each season's chart parts add up to its hour rows, and the rows to the day's use", () => {
  const ctx = { lat: -45.03, roofM2: 150, kwp: 6, gen: genFor(6) };
  const r = B.btEvaluate(ctx, home({ evs: 1 }), { winter: 420 }, prices);
  for (const s of B.BT_SEASONS) {
    const sd = r.run.seasons[s];
    for (let h = 0; h < 24; h++) {
      const parts = Object.values(sd.partsH).reduce((t, a) => t + a[h], 0);
      near(parts, sd.rows[h].use, 1e-9, `${s} ${h}:00 parts vs use`);
    }
    near(sum(sd.rows.map(x => x.use)), r.run.nodes.seasons[s].use.value, 1e-9, `${s} rows vs use node`);
    near(sum(sd.rows.map(x => x.imp)), r.run.nodes.seasons[s].imp.value, 1e-9, `${s} rows vs import node`);
  }
});

check("the year, hour by hour, adds up to the bills (with and without a battery)", () => {
  const ctx = { lat: -45.03, roofM2: 150, kwp: 6, gen: genFor(6) };
  for (const batt of [0, 10]) {
    const r = B.btEvaluate(ctx, home({ battery: batt }), null, prices);
    const Y = B.btYearHours(r.run, batt > 0);
    if (Y.rows.length !== 8760) throw new Error(`${Y.rows.length} hours`);
    if (!Y.check.ok) throw new Error(`does not add up: ${Y.check.off.join(", ")} ${Y.check.daysOff.join(", ")}`);
    near(Y.totals.bill, (batt ? r.run.nodes.battBill : r.run.nodes.solarBill).value, 1e-6, "bill");
  }
});

check("a change of assumption moves the year and the bills together", () => {
  const ctx = { lat: -45.03, roofM2: 150, kwp: 6, gen: genFor(6) };
  const a = B.btEvaluate(ctx, home(), null, prices);
  const b = B.btEvaluate(ctx, home(), null, { buy: 40, sell: 10, daily: 3 });
  const Ya = B.btYearHours(a.run, false), Yb = B.btYearHours(b.run, false);
  if (!(Ya.check.ok && Yb.check.ok)) throw new Error("a year does not add up");
  if (!(Yb.totals.bill > Ya.totals.bill)) throw new Error("dearer power did not raise the year's bill");
});

check("the year as CSV has a header and 8,760 hours", () => {
  const ctx = { lat: -45.03, roofM2: 150, kwp: 6, gen: genFor(6) };
  const r = B.btEvaluate(ctx, home(), null, prices);
  const lines = B.btYearCsv(B.btYearHours(r.run, false)).trim().split("\n");
  if (lines.length !== 8761) throw new Error(`${lines.length} lines`);
  if (!lines[0].startsWith("date,hour,season")) throw new Error("no header");
});

console.log(`\n${pass} passed, ${failures.length} failed`);
if (failures.length) process.exit(1);
