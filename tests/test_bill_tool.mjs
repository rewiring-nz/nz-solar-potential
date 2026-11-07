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

check("a Queenstown home uses EMI's Otago average, scaled for its people", () => {
  const L = B.btBuildLoad(home({ people: 2.6 }), { lat: -45.03, lon: 168.66, roofM2: 170 });
  near(L.region.key === "queenstown" ? 1 : 0, 1, 0, "region");
  const year = B.BT_SEASONS.reduce((t, s, i) => t + B.btDailyKwh(L, s) * [90, 92, 92, 91][i], 0);
  near(year, 7829, 160, "kWh a year for 2.6 people in a typical house");
});

check("a bigger house uses more, in round steps", () => {
  const yr = (m2, people = 3) => { const L = B.btBuildLoad(home({ people }), { lat: -45.03, lon: 168.66, roofM2: m2 });
    return B.BT_SEASONS.reduce((t, s, i) => t + B.btDailyKwh(L, s) * [90, 92, 92, 91][i], 0); };
  const typ = yr(170);
  [[80, 0.7], [120, 0.85], [200, 1], [250, 1.15], [350, 1.3], [600, 1.5]].forEach(([m2, f]) =>
    near(yr(m2) / typ, f, 0.001, `${m2} m²`));
  // a person alone in a small house about half the average home, a big family in a big house about double
  const avg = yr(170, 2.6);
  if (yr(80, 1) / avg > 0.55) throw new Error(`small ${yr(80, 1) / avg}`);
  if (yr(350, 6) / avg < 2) throw new Error(`big ${yr(350, 6) / avg}`);
});

check("a winter day uses more than a summer day, as the town's metered load does", () => {
  const L = B.btBuildLoad(home(), { lat: -45.03, lon: 168.66, roofM2: 150 });
  const ratio = L.parts.home.winter / L.parts.home.summer;
  if (ratio < 1.5 || ratio > 2.5) throw new Error(`winter/summer ${ratio}`);
});

check("hot water is not put at midday: the night and peaks carry the load", () => {
  const L = B.btBuildLoad(home(), { lat: -45.03, lon: 168.66, roofM2: 150 });
  const sh = L.shapes.home.winter;
  if (!(sh[19] > sh[13] && sh[8] > sh[13])) throw new Error("no morning/evening peak over midday");
});

check("away from a town with EMI data, the national shape and average are used", () => {
  const L = B.btBuildLoad(home(), { lat: -38.0, lon: 176.0, roofM2: 150 });
  near(L.region.key === "national" ? 1 : 0, 1, 0, "region");
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
  near(W.nowMonth, (W.loadKwh * 0.30 + 2.5) * 365 / 12, 0.01, "winter bill: a month is 365 ÷ 12 days");
  near(W.solarMonth, W.nowMonth, 1e-6, "no gen, no change");
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

check("a business's use grows with its floors, from its measured height", () => {
  const one = B.btBuildLoad({ type: "business", heating: "none", evs: 0 }, { lat: -45, roofM2: 600, btype: "business", heightM: 4 });
  const three = B.btBuildLoad({ type: "business", heating: "none", evs: 0 }, { lat: -45, roofM2: 600, btype: "business", heightM: 11 });
  near(three.storeys, 3, 0, "storeys from 11 m");
  near(B.btDailyKwh(three, "autumn") / B.btDailyKwh(one, "autumn"), 3, 0.01, "use x storeys");
  near(B.btDailyKwh(one, "autumn") * 365 / 1.0, 600 * 100, 600 * 100 * 0.15, "100 kWh/m2 a year");
});

check("a school uses less per m2 than a business", () => {
  const s = B.btBuildLoad({ type: "business", heating: "none", evs: 0 }, { lat: -45, roofM2: 2000, btype: "school", heightM: 5 });
  const b = B.btBuildLoad({ type: "business", heating: "none", evs: 0 }, { lat: -45, roofM2: 2000, btype: "business", heightM: 5 });
  if (!(B.btDailyKwh(s, "autumn") < B.btDailyKwh(b, "autumn") * 0.5)) throw new Error("school not lighter");
});

check("a shed is one tall storey at 35 kWh/m2, with little of it heated", () => {
  const P = { type: "business", heating: "heatpump", evs: 0 };
  const shed = B.btBuildLoad(Object.assign({}, P, { heating: "none" }), { lat: -45, roofM2: 1000, btype: "industrial", heightM: 7.5 });
  near(shed.storeys, 1, 0, "a 7.5 m shed is one storey (6 m a storey)");
  near(B.btDailyKwh(shed, "autumn") * 365, 1000 * 35, 1000 * 35 * 0.15, "35 kWh/m2 a year");
  const office = B.btBuildLoad(Object.assign({}, P, { heating: "none" }), { lat: -45, roofM2: 1000, btype: "business", heightM: 7.5 });
  near(office.storeys, 2, 0, "the same height is two office storeys");
  // heating: a quarter of the business's share of the floor, per m2 of floor
  const heat = (btype, h) => {
    const w = B.btBuildLoad(P, { lat: -45, roofM2: 1000, btype, heightM: h });
    const n = B.btBuildLoad(Object.assign({}, P, { heating: "none" }), { lat: -45, roofM2: 1000, btype, heightM: h });
    return B.btDailyKwh(w, "winter") - B.btDailyKwh(n, "winter");
  };
  near(heat("industrial", 4) / heat("business", 4), 0.15 / 0.6, 1e-6, "heated share 0.15 vs 0.6");
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

check("the year: use, solar and today's bill are the bills' own (with and without a battery)", () => {
  const ctx = { lat: -45.03, roofM2: 150, kwp: 6, gen: genFor(6) };
  for (const batt of [0, 10]) {
    const r = B.btEvaluate(ctx, home({ battery: batt }), null, prices);
    for (const weather of [null, { f: Array.from({ length: 365 }, (_, d) => 0.3 + 1.4 * ((d * 7919) % 97) / 96) }]) {
      const Y = B.btYearHours(r.run, batt > 0, weather);
      if (Y.rows.length !== 8760) throw new Error(`${Y.rows.length} hours`);
      if (!Y.check.ok) throw new Error(`does not add up: ${Y.check.off.join(", ")} ${Y.check.daysOff.join(", ")}`);
      near(Y.totals.use, r.run.nodes.useYear.value, 1e-6, "use");
      near(Y.totals.solar, r.run.nodes.genYear.value, 1e-6, "solar");
      // the bill with solar comes out of the days: near the bills', not equal
      const want = (batt ? r.run.nodes.battBill : r.run.nodes.solarBill).value;
      if (Math.abs(Y.totals.bill - want) > 0.15 * Math.abs(r.run.nodes.nowBill.value))
        throw new Error(`year bill ${Y.totals.bill.toFixed(0)} far from the bills' ${want.toFixed(0)}`);
      // energy balance every hour
      for (const row of Y.rows) {
        const fromSolar = Math.min(row.use, row.solar);
        if (Math.abs(row.use - (fromSolar + row.fromB + row.imp)) > 1e-9) throw new Error(`use balance day ${row.doy} ${row.h}:00`);
        if (Math.abs(row.solar - (fromSolar + row.toB + row.exp - row.bExp)) > 1e-9) throw new Error(`solar balance day ${row.doy} ${row.h}:00`);
      }
    }
  }
});

check("the year blends the seasons: every day differs, each season keeps its average day", () => {
  const ctx = { lat: -45.03, roofM2: 150, kwp: 6, gen: genFor(6) };
  const r = B.btEvaluate(ctx, home(), null, prices);
  const Y = B.btYearHours(r.run, false);
  const day = (d, k) => Y.rows.slice(d * 24, d * 24 + 24).reduce((t, x) => t + x[k], 0);
  // 1 Jan against 31 Jan, and no two neighbouring days the same (bar the
  // season middles' turning points)
  for (const k of ["use", "solar"]) {
    if (Math.abs(day(0, k) - day(30, k)) < 0.01) throw new Error(`${k}: 1 and 31 January match`);
    let same = 0;
    for (let d = 1; d < 365; d++) if (Math.abs(day(d, k) - day(d - 1, k)) < 1e-9) same++;
    if (same > 4) throw new Error(`${k}: ${same} days repeat the day before`);
  }
  // no jump at a season's edge bigger than a few days' drift
  for (const [a, b] of [[58, 59], [150, 151], [242, 243], [333, 334]]) {
    const jump = Math.abs(day(b, "use") - day(a, "use")), drift = Math.abs(day(a, "use") - day(a - 3, "use")) / 3;
    if (jump > 3 * drift + 0.05) throw new Error(`use jumps ${jump.toFixed(2)} kWh at day ${b}`);
  }
  // each season, hour by hour, is its average day times its days
  const seasons = { summer: [], autumn: [], winter: [], spring: [] };
  Y.rows.forEach(x => seasons[x.season].push(x));
  for (const s of Object.keys(seasons)) for (let h = 0; h < 24; h++) {
    const rs = seasons[s].filter(x => x.h === h);
    near(rs.reduce((t, x) => t + x.use, 0) / rs.length, r.run.seasons[s].load[h], 1e-9, `${s} ${h}:00 use`);
    near(rs.reduce((t, x) => t + x.solar, 0) / rs.length, r.run.seasons[s].gen[h], 1e-9, `${s} ${h}:00 solar`);
  }
});

check("the year's weather: a grey day makes less, a season still makes the bills' solar", () => {
  const ctx = { lat: -45.03, roofM2: 150, kwp: 6, gen: genFor(6) };
  const r = B.btEvaluate(ctx, home({ battery: 10 }), null, prices);
  const f = new Array(365).fill(1); f[20] = 1.6; f[21] = 0.1;
  const Y = B.btYearHours(r.run, true, { f });
  const day = d => Y.rows.slice(d * 24, d * 24 + 24).reduce((t, x) => t + x.solar, 0);
  if (!(day(21) < 0.2 * day(22))) throw new Error("the grey day is not grey");
  if (!(day(20) > 1.4 * day(19))) throw new Error("the bright day is not bright");
  const w = Y.rows.filter(x => x.season === "winter").reduce((t, x) => t + x.solar, 0);
  near(w, r.run.nodes.seasons.winter.made.value * 92, 1e-6, "winter solar");
  near(Y.rows.filter(x => x.season === "summer").reduce((t, x) => t + x.solar, 0), r.run.nodes.seasons.summer.made.value * 90, 1e-6, "summer solar");
  // the battery carries charge over midnight: the grey day starts with what
  // the bright day before left (kept for the morning peak, 7-9am), so it
  // draws on the battery before its own sun is up
  if (!(Y.rows.slice(21 * 24, 21 * 24 + 8).some(x => x.fromB > 0))) throw new Error("no battery the grey morning");
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

check("money on the card is rounded to the estimate's precision", () => {
  const cases = [[521.4, "$520"], [5742, "$5,700"], [1552, "$1,600"], [9960, "$10,000"],
                 [51750, "$52,000"], [208639, "$209,000"], [-1340, "-$1,300"], [4, "$0"]];
  for (const [v, want] of cases) {
    const got = B.btMoney(v);
    if (got !== want) throw new Error(`${v}: ${got}, want ${want}`);
  }
});

check("loan repayments: the standard fixed repayment, and the net a year", () => {
  // $10,000 at 6% over 15 years: 1,029.63 a year (cost x r / (1 - (1+r)^-n))
  near(B.btAnnuity(10000, 0.06, 15), 1029.63, 0.01, "6% over 15 years");
  near(B.btAnnuity(9000, 0, 10), 900, 1e-9, "zero rate");
  const ctx = { lat: -45.03, roofM2: 150, kwp: 6, gen: genFor(6) };
  const r = B.btEvaluate(ctx, home(), null, prices);
  const N = r.nodes;
  // the headline is an average year over the loan, both sides in today's
  // dollars: savings from the year-by-year rows, the fixed repayment
  // deflated by 2% CPI a year (years 1-30, 1.02^-(year-1) averages 0.76148)
  near(N.solarNet.value, N.saveAvg.value - N.repAvg.value, 1e-9, "net = savings - repayments");
  const [baseRep, invTop] = N.repAvg.children;
  near(baseRep.value, N.solarRepay.value * 0.76148, N.solarRepay.value * 1e-4, "repayments deflated by 2% a year");
  near(N.saveAvg.value, r.solarRet.rows.slice(0, 30).reduce((t, x) => t + x.saving, 0) / 30, 1e-9, "average of the 30 years' savings");
  near(r.netShown, B.btNetShown(N.saveAvg.value, N.repAvg.value).net, 1e-9, "the card's figure is that net, rounded");
  // the year-by-year repayments average to the same figure
  near(r.repaySched.reduce((a, b) => a + b, 0) / 30, N.repAvg.value, 1e-9, "the years average to the headline");
  // the loan covers only what is bought on day one: the solar system
  const borrowed = N.solarRepay.children[0].value;
  near(borrowed, N.solarCost.value, 1e-6, "only the system is borrowed for");
  near(N.solarRepay.value, B.btAnnuity(borrowed, 0.055, 30), 1e-6, "5.5% over 30 years");
  // the replacement inverter (18% of the cost) is added to the loan in year
  // 16 and repaid over the last 15 years: cost × 5.5%/(1 − 1.055^−15) ×
  // Σ 1.02^−j (j < 15) ÷ 30 = cost × 1.305727 ÷ 30 (inflation to year 16
  // and back cancels)
  near(invTop.value, N.solarCost.value * 0.18 * 1.305727 / 30, N.solarCost.value * 1e-6, "inverter top-up");
  if (r.repaySched[15] <= r.repaySched[14]) throw new Error("repayments should step up in year 16");
  // a battery: borrowed for with the system, replaced in year 15 at 70% of today's price
  const rb = B.btEvaluate(ctx, Object.assign(home(), { battery: 10 }), null, prices);
  near(rb.nodes.battCost.value, 9000, 1e-9, "10 kWh at $900");
  near(rb.nodes.allRepay.children[0].value, rb.nodes.solarCost.value + 9000, 1e-6, "system and battery borrowed");
  const bRow = rb.battRet.rows[14];
  near(-bRow.out, 9000 * 0.7, 1e-6, "battery replaced in year 15 at 70%");
  // the battery top-up: 6,300 × 5.5%/(1 − 1.055^−16) × Σ 1.02^−j (j < 16) ÷ 30
  near(rb.nodes.repAvg.children[2].value, 6300 * 1.323773 / 30, 0.01, "battery top-up");
  const v = Wk.verify(N.solarNet);
  if (!v.ok) throw new Error("net does not verify: " + JSON.stringify(v.problems.map(b => b.node.label)));
  // and the check can fail: a repayment shown wrong is caught
  const kept = N.solarRepay.value;
  N.solarRepay.value = kept + 1;
  const caught = !Wk.verify(N.solarNet).ok;
  N.solarRepay.value = kept;
  if (!caught) throw new Error("verify did not catch a wrong repayment");
});

check("battery stepper: 5 kWh steps to 50, 10 above, typed sizes snap", () => {
  const cases = [[10, 1, 15], [10, -1, 5], [5, -1, 5], [13.5, 1, 15], [13.5, -1, 10], [45, 1, 50],
                 [50, 1, 60], [50, -1, 45], [60, -1, 50], [55, 1, 60], [200, 1, 200]];
  for (const [k, d, want] of cases) {
    const got = B.btBattStep(k, d);
    if (got !== want) throw new Error(`${k} ${d > 0 ? "+" : "−"} gave ${got}, want ${want}`);
  }
});

check("a battery exports spare charge at peak, and fades 2% a year", () => {
  const ctx = { lat: -45, roofM2: 150, kwp: 8, gen: genFor(8) };
  const tou = { peak: 52, offpeak: 26, peakSell: 23, offpeakSell: 12, daily: 2.02 };
  const big = B.btEvaluate(ctx, home({ battery: 30 }), null, tou);
  const sold = B.BT_SEASONS.reduce((t, s) => t + big.run.seasons[s].rowsB.reduce((a, x) => a + (x.bExp || 0), 0), 0);
  if (!(sold > 0)) throw new Error("a big battery never sold spare charge at peak");
  for (const s of B.BT_SEASONS) for (const x of big.run.seasons[s].rowsB) {
    if (x.bExp > 1e-9 && !(x.imp < 1e-9)) throw new Error("sold to the grid while buying from it");
  }
  // equal export prices: nothing to gain, so nothing sold
  const flatSell = B.btEvaluate(ctx, home({ battery: 30 }), null, Object.assign({}, tou, { peakSell: 12 }));
  const sold0 = B.BT_SEASONS.reduce((t, s) => t + flatSell.run.seasons[s].rowsB.reduce((a, x) => a + (x.bExp || 0), 0), 0);
  near(sold0, 0, 1e-12, "no peak export when it pays no more");
  // fade: the battery's yearly saving falls until it is replaced (year 15), then recovers
  // against the same battery that never fades (power prices rise either way)
  const kept = E.BATTERY_DEFAULTS.fade_pct;
  const r = B.btEvaluate(ctx, home({ battery: 10 }), null, tou);
  E.BATTERY_DEFAULTS.fade_pct = 0;
  const r0 = B.btEvaluate(ctx, home({ battery: 10 }), null, tou);
  E.BATTERY_DEFAULTS.fade_pct = kept;
  const sv = r.battRet.rows, s0 = r0.battRet.rows;
  near(sv[0].saving, s0[0].saving, 1e-9, "a new battery saves the same in year 1");
  if (!(sv[13].saving < s0[13].saving)) throw new Error("a worn battery should save less");
  near(sv[14].saving / s0[14].saving, sv[0].saving / s0[0].saving, 1e-9, "the replacement starts new");
});

console.log(`\n${pass} passed, ${failures.length} failed`);
if (failures.length) process.exit(1);
