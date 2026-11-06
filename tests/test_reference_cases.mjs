/**
 * Reference cases for the money and the page's arithmetic: small worked
 * examples a person can check with a calculator. Each states the hand
 * working beside the expected answer; the output is a table of case,
 * expected, got and working. The build-side cases (panels, sunlight,
 * slope, packing) are in tests/test_reference_cases.py.
 *
 * Run:  node tests/test_reference_cases.mjs
 */
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const E = require("../economics.js");
globalThis.Wk = require("../workings.js").Wk;
Object.assign(globalThis, { simulateDay: E.simulateDay, systemCost: E.systemCost, costPerKw: E.costPerKw,
                            BATTERY_DEFAULTS: E.BATTERY_DEFAULTS, econ: E.getEcon() });
const B = require("../bill_tool.js");

const rows = [], failures = [];
function c(name, expected, got, how, tol = 1e-6) {
  const ok = typeof expected === "number" ? Math.abs(expected - got) <= tol : expected === got;
  rows.push([ok ? "ok  " : "FAIL", name, expected, got, how]);
  if (!ok) failures.push(name);
}
const r2 = x => Math.round(x * 100) / 100;

// ---- a loan for the system (bill_tool.js btAnnuity) --------------------------
c("$10,000 loan at 6% over 15 years, a year", 1029.63, r2(B.btAnnuity(10000, 0.06, 15)),
  "10,000 × 0.06 ÷ (1 − 1.06^−15) = 600 ÷ 0.58273 = 1,029.63");
c("$9,000 at 0% over 10 years, a year", 900, B.btAnnuity(9000, 0, 10), "9,000 ÷ 10");
c("the default loan: 5.5% over 30 years", "5.5/30", `${E.ECON_DEFAULTS.loan_rate_pct}/${E.ECON_DEFAULTS.loan_years}`,
  "economics.js ECON_DEFAULTS");
// A $10,000 SYSTEM (5 kW at $2,000/kW), and a 10 kWh battery at $900/kWh
{
  const gen = ["summer", "autumn", "winter", "spring"].map((s, i) =>
    Array.from({ length: 24 }, (_, h) => (h >= 9 && h < 17 ? [3, 2, 1, 2.5][i] : 0)));
  const tariff = { peak: 52, offpeak: 26, peakSell: 23, offpeakSell: 12, daily: 2.02 };
  const home0 = Object.assign({ type: "home" }, B.BT_DEFAULTS.home);
  const ev = batt => B.btEvaluate({ lat: -45.03, roofM2: 150, kwp: 5, gen }, Object.assign({}, home0, { battery: batt }),
    null, tariff, { solarPerKw: 2000, battPerKwh: null });
  const r = ev(0), N = r.nodes;
  c("borrowed for a $10,000 system", 10000, N.solarRepay.children[0].value,
    "the system only: replacements are paid in their year, not borrowed for");
  c("...repaid at 5.5% over 30 years, a year", 688.05, r2(N.solarRepay.value),
    "10,000 × 0.055 ÷ (1 − 1.055^−30) = 550 ÷ 0.79936 = 688.05");
  c("...on average a year in today's dollars, at 2% inflation", 523.94, r2(N.repAvg.value),
    "688.05 × (1 + 1/1.02 + … + 1/1.02^29) ÷ 30 = 688.05 × 0.76148 = 523.94", 0.01);
  c("the replacement inverter, on average a year", 60, r2(N.replAvg.value),
    "18% of 10,000 = 1,800 in year 16, ÷ 30 years = 60");
  const rb = ev(10);
  c("a 10 kWh battery costs", 9000, rb.nodes.battCost.value, "10 × $900");
  c("...replaced in year 15 at 70% of today's price", 6300, r2(-rb.battRet.rows[14].out), "9,000 × 0.7");
  c("...inverter and battery replacements, on average a year", 270, r2(rb.nodes.replAvg.value),
    "(1,800 + 6,300) ÷ 30 = 270");
  c("the net a year = savings − repayments − replacements", r2(N.saveAvg.value - N.repAvg.value - N.replAvg.value),
    r2(N.solarNet.value), "each an average year over the loan, in today's dollars");
}
c("15 years of those repayments", 15444.41, r2(B.btAnnuity(10000, 0.06, 15) * 15),
  "15 × 1,029.6276 = 15,444.41: $10,000 borrowed plus $5,444.41 interest", 0.01);

// ---- regional prices (regional_prices.js, economics.js applyRegionalPrices) ----
const { REGIONAL_PRICES: RP } = require("../regional_prices.js");
const q = RP.regions[34];
c("Queenstown is EA network region 34", "Queenstown (Aurora Energy)", q.name, "regional_prices.js, from the EA's CSV");
c("peak and off-peak keep the EA's average for a typical home", r2(q.variable_c),
  r2(q.peak_share * q.peak_c + (1 - q.peak_share) * q.offpeak_c),
  `${q.peak_share} × ${q.peak_c}c + ${(1 - q.peak_share).toFixed(4)} × ${q.offpeak_c}c ≈ ${q.variable_c}c (prices to the whole cent)`, 0.5);
c("peak is twice off-peak, to the cent", RP.peak_ratio, Math.round(q.peak_c / q.offpeak_c * 10) / 10, "50c : 25c, whole cents", 0.05);
{
  const d = JSON.parse(JSON.stringify(E.ECON_DEFAULTS));
  E.applyRegionalPrices(d, RP, 34);
  c("the site's region sets the home prices", `${q.peak_c}/${q.offpeak_c}/${q.daily}`,
    `${d.home_peak_c}/${d.home_offpeak_c}/${d.home_daily}`, "SITE.priceRegion = 34");
  c("businesses keep their own prices", E.ECON_DEFAULTS.biz_peak_c, d.biz_peak_c, "the EA set is residential");
}
{
  // each map area priced for its own network region (site-config.js priceRegionByArea)
  globalThis.window = globalThis.window || {};
  const fs = require("node:fs");
  const vm = require("node:vm");
  const box = { window: {} };
  vm.runInNewContext(fs.readFileSync(new URL("../site-config.js", import.meta.url), "utf8"), box);
  const site = box.window.SITE;
  const at = area => E.homePricesFor(area, { prices: RP, site, econ: E.ECON_DEFAULTS });
  for (const [area, code] of [["town_south_lake", 34], ["glenorchy", 34], ["wanaka_town", 35], ["hawea", 35],
                              ["cromwell_east", 35], ["bannockburn", 35], ["kingston", 38]]) {
    const p = at(area);
    c(`${area} is priced for EA region ${code}`, `${code}: ${RP.regions[code].peak_c}c`, p ? `${p.code}: ${p.peak_c}c` : "none",
      `${RP.regions[code].name}`);
  }
  const own = Object.assign({}, E.ECON_DEFAULTS, { home_peak_c: 45 });
  c("a reader's own home prices apply everywhere", null,
    E.homePricesFor("wanaka_town", { prices: RP, site, econ: own }), "home_peak_c changed in Assumptions & pricing");
  c("every served area with its own region names a region that exists", true,
    Object.values(site.priceRegionByArea).every(k => RP.regions[k]), "site-config.js against regional_prices.js");
}
{
  // the plan switch: time of use by default; a flat plan is one price all day
  const fs = require("node:fs"), vm = require("node:vm");
  const box = { window: {} };
  vm.runInNewContext(fs.readFileSync(new URL("../site-config.js", import.meta.url), "utf8"), box);
  globalThis.window = { SITE: box.window.SITE };
  globalThis.REGIONAL_PRICES = RP;
  globalThis.homePricesFor = E.homePricesFor;
  c("the default plan is time of use", "tou", E.ECON_DEFAULTS.plan, "economics.js ECON_DEFAULTS.plan");
  const econ0 = globalThis.econ;
  globalThis.econ = Object.assign({}, econ0, { plan: "flat" });
  const qt = B.btTariffFromEcon(false, "town_south_lake"), wt = B.btTariffFromEcon(false, "wanaka_town");
  c("flat plan, Queenstown home: one price all day", "34/34", `${qt.peak}/${qt.offpeak}`, `the EA's ${RP.regions[34].variable_c}c to the cent`);
  c("flat plan, Wanaka home", "41/41", `${wt.peak}/${wt.offpeak}`, `Central Otago's ${RP.regions[35].variable_c}c to the cent`);
  const ctx0 = { lat: -45.03, roofM2: 150, kwp: 0, gen: null };
  const homeP = Object.assign({ type: "home" }, B.BT_DEFAULTS.home);
  const flatBill = B.btEvaluate(ctx0, homeP, null, qt, null).nodes.nowBill;
  globalThis.econ = Object.assign({}, econ0, { plan: "tou" });
  const touBill = B.btEvaluate(ctx0, homeP, null, B.btTariffFromEcon(false, "town_south_lake"), null).nodes.nowBill.value;
  c("no solar: about the same yearly bill on either plan", true, Math.abs(flatBill.value / touBill - 1) < 0.02,
    `flat $${Math.round(flatBill.value)} vs time of use $${Math.round(touBill)}: the split keeps a typical home's average`);
  c("the flat plan's workings add up", true, Wk.verify(flatBill).ok, "every step recomputes");
  globalThis.econ = econ0;
  delete globalThis.REGIONAL_PRICES;
}
c("a business pays energy prices: 17c peak, 10c off-peak, no daily charge", "17/10/0",
  `${E.ECON_DEFAULTS.biz_peak_c}/${E.ECON_DEFAULTS.biz_offpeak_c}/${E.ECON_DEFAULTS.biz_daily}`,
  "network and connection charges are a separate bill that solar does not change");
{
  const sh = B.BT_SHAPES.business, pk = h => (h >= 7 && h < 10) || (h >= 18 && h < 21);
  const share = sh.reduce((t, v, h) => t + (pk(h) ? v : 0), 0);
  c("a business's flat price", Math.round(share * 17 + (1 - share) * 10), E.ECON_DEFAULTS.biz_buy_c,
    `${(share * 100).toFixed(0)}% of a typical business's use in peak hours: ${share.toFixed(2)} × 17c + ${(1 - share).toFixed(2)} × 10c`);
}
c("home export: 12c in the day, 23c at peak; business 12c and 15c", "12/23 12/15",
  `${E.ECON_DEFAULTS.home_offpeak_sell_c}/${E.ECON_DEFAULTS.home_peak_sell_c} ${E.ECON_DEFAULTS.biz_offpeak_sell_c}/${E.ECON_DEFAULTS.biz_peak_sell_c}`,
  "2026 retailer buy-back rates (peak 20-23c, otherwise 10-12c)");
{
  const keep = globalThis.econ;
  globalThis.econ = Object.assign({}, keep, { home_peak_sell_c: 8, home_offpeak_sell_c: 12 });
  const t = B.btTariffFromEcon(false, null);
  globalThis.econ = keep;
  c("peak export is never below daytime export", 12, t.peakSell, "8c typed for peak, 12c in the day: peak paid 12c");
}
c("the prices are no more than eight months old", true,
  (Date.now() - Date.parse(q.month)) / 864e5 < 245, `EA month ${q.month} (the EA publishes about five months behind): run tools/fetch_regional_prices.mjs`);

// ---- money on the card (bill_tool.js btMoney) --------------------------------
for (const [v, want, why] of [[521.4, "$520", "under $1,000: nearest $10"], [5742, "$5,700", "under $10,000: nearest $100"],
                              [51750, "$52,000", "over $10,000: nearest $1,000"], [-1340, "-$1,300", "negative keeps its sign"]]) {
  c(`$${v} shown as`, want, B.btMoney(v), why);
}
let nb = B.btNetShown(5742, 5330);
c("green box: $5,742 − $5,330 shown", 410, nb.net, "net $412 → nearest $10 for all three: $5,740 − $5,330 = $410");
nb = B.btNetShown(10453, 9520);
c("green box: a small net between large parts", 930, nb.net, "true $933: $10,450 − $9,520 = $930 (the larger step said $0)");
{
  let worst = 0, at = null;
  for (let s = 0; s <= 20000; s += 37) for (let r = 0; r <= 20000; r += 41) {
    const d = Math.abs(B.btNetShown(s, r).net - (s - r)) / B.btMoneyStep(s - r);
    if (d > worst) { worst = d; at = [s, r]; }
  }
  c("the shown net is never more than one step from the true net", true, worst <= 1,
    `worst ${worst.toFixed(2)} steps at savings $${at[0]}, repayments $${at[1]} (290,000 pairs)`);
}

// ---- system cost (economics.js costPerKw, systemCost) ------------------------
const A = E.costAnchors();
c("the 8–25 kW rate holds at the middle of its band", 1800, E.costPerKw(A[2][0]),
  `√(8 × 25) = ${A[2][0].toFixed(2)} kW → $1,800/kW (Assumptions & pricing)`);
c("a 10 kW system costs 10 × its rate", r2(E.costPerKw(10) * 10), r2(E.systemCost(10)),
  `10 kW × $${E.costPerKw(10).toFixed(0)}/kW, the rate between the 3–8 and 8–25 kW bands`);
c("25.5 kW costs more than 25 kW", true, E.systemCost(25.5) > E.systemCost(25),
  "adding panels never lowers the cost (stepped bands made 25.5 kW cheaper)");

// ---- an hour of solar and use (economics.js simulateDay) ---------------------
const z = new Array(24).fill(0), at = (h, v) => z.map((_, i) => (i === h ? v : 0));
let d = E.simulateDay(at(12, 3), at(12, 1), null, 0);
c("noon: 3 kWh made, 1 kWh used → used on site", 1, d.selfH[12], "the smaller of made and used");
c("...exported", 2, d.expH[12], "3 − 1");
c("...from the grid", 0, d.impH[12], "use is covered");
d = E.simulateDay(at(18, 0.5), at(18, 2), null, 0);
c("evening: 0.5 kWh made, 2 kWh used → from the grid", 1.5, d.impH[18], "2 − 0.5");
const batt = { enabled: true, kwh: 10, kw: 5, reserve_pct: 0, round_trip_pct: 100 };
const g = z.slice(), l = z.slice(); g[12] = 5; l[12] = 1; l[19] = 3;
d = E.simulateDay(g, l, batt, 0);
c("battery: noon's spare 4 kWh is stored", 0, d.expH[12], "5 made − 1 used = 4, all into the battery");
c("...and powers the evening", 3, d.selfH[19], "3 kWh at 7 pm come from the battery");
c("...leaving 1 kWh", 1, d.socEnd, "4 stored − 3 used");

// ---- a battery on time of use saves its charge for the peak ------------------
{
  // 1 kW used every hour; 6 kW of solar 10am-3pm; peaks 7-10am and 6-9pm;
  // a 10 kWh battery, 5 kW, no losses, no reserve. Run the day until it settles.
  const gen = Array.from({ length: 24 }, (_, h) => (h >= 10 && h < 15 ? 6 : 0));
  const use = new Array(24).fill(1);
  const bt = { enabled: true, kwh: 10, kw: 5, reserve_pct: 0, round_trip_pct: 100 };
  const peak = h => (h >= 7 && h < 10) || (h >= 18 && h < 21);
  const settle = opts => { let soc = 0, d; for (let k = 0; k < 40; k++) { d = E.simulateDay(gen, use, bt, soc, opts); soc = d.socEnd; } return d; };
  const tou = settle({ isPeak: peak }), any = settle(null);
  const morning = d => d.dischargeH[7] + d.dischargeH[8] + d.dischargeH[9];
  c("time of use: the battery powers the whole morning peak", 3, r2(morning(tou)),
    "10 kWh stored by 3pm; 6 kWh held for 6-9pm and 7-10am; 1 kWh a peak hour");
  c("...and the evening peak", 3, r2(tou.dischargeH[18] + tou.dischargeH[19] + tou.dischargeH[20]), "18:00-21:00, 1 kWh each");
  c("covering any shortfall as it comes leaves the morning peak to the grid", 0, r2(morning(any)),
    "it empties by 1am (3pm + 10 hours at 1 kWh)");
  c("the battery charges only from spare solar", 10, r2(tou.chargeH.reduce((a, b) => a + b, 0)),
    "5 kWh spare an hour 10am-3pm, until full");
  c("what goes in comes out (no losses here)", r2(tou.chargeH.reduce((a, b) => a + b, 0)),
    r2(tou.dischargeH.reduce((a, b) => a + b, 0)), "a settled day: charge in = charge out");
}

// ---- a year of battery flows (bill_tool.js btRun, btYearHours) ---------------
{
  const gen = ["summer", "autumn", "winter", "spring"].map((s, i) =>
    Array.from({ length: 24 }, (_, h) => (h >= 9 && h < 17 ? [3, 2, 1, 2.5][i] : 0)));
  const r = B.btEvaluate({ lat: -45.03, roofM2: 150, kwp: 6, gen },
    Object.assign({ type: "home" }, B.BT_DEFAULTS.home, { battery: 10 }), null,
    { peak: 52, offpeak: 26, peakSell: 12, offpeakSell: 12, daily: 2.02 }, null);
  const Y = B.btYearHours(r.run, true);
  const daysOf = { summer: 90, autumn: 92, winter: 92, spring: 91 };
  const seasonSum = Object.keys(daysOf).reduce((t, s) => t + r.nodes.seasons[s].fromB.value * daysOf[s], 0);
  const yearSum = Y.rows.reduce((t, row) => t + row.fromB, 0);
  c("a year from the battery = each season's day × its days", r2(seasonSum), r2(yearSum),
    "the 8,760 hours lay each season's day on the calendar", 0.01);
  c("the year CSV carries the battery's flows", true,
    B.btYearCsv(Y).split("\n")[0].includes("into_battery_kwh,from_battery_kwh"), "two columns, kWh each hour");
  c("battery seasons' workings add up", true, ["summer", "winter"].every(s => Wk.verify(r.nodes.seasons[s].battDay).ok),
    "each hour: use = direct + battery + grid; solar = direct + into battery + exported");
}

// ---- workings add up (workings.js) -------------------------------------------
const a = Wk.input("a", 2, "kWh"), b = Wk.input("b", 3, "kWh");
const s = Wk.sum("a + b", "kWh", [a, b]), p = Wk.product("a × b", "kWh", [a, b]);
c("a sum's breakdown adds up", 5, s.value, "2 + 3");
c("a product's breakdown adds up", 6, p.value, "2 × 3");
c("verify finds nothing wrong", true, Wk.verify(s).ok && Wk.verify(p).ok, "every step recomputes");
s.value = 6;
c("verify catches a changed figure", 1, Wk.verify(s).problems.length, "5 shown as 6");

// ---- no check that only returns its own figure --------------------------------
{
  const fs = require("node:fs");
  const bad = [];
  for (const f of ["bill_tool.js", "generation.js", "low_solar.js", "workings.js", "economics.js", "rooftop-solar.html"]) {
    const src = fs.readFileSync(new URL("../" + f, import.meta.url), "utf8");
    for (const m of src.matchAll(/check:\s*\(\)\s*=>\s*([A-Za-z_$][\w$.]*)\s*[}),]/g)) bad.push(`${f}: check: () => ${m[1]}`);
  }
  c("no workings check just returns a stored value", "", bad.join("; "),
    "a check must re-work the figure; one that returns it can never fail");
}

// ---- report --------------------------------------------------------------------
const w = Math.max(...rows.map(r => r[1].length));
for (const [st, name, exp, got, how] of rows) {
  console.log(`${st} ${name.padEnd(w)}  expected ${String(exp).padEnd(10)} got ${String(got).padEnd(10)} ${how}`);
}
console.log(`\n${rows.length - failures.length} passed, ${failures.length} failed`);
if (failures.length) process.exit(1);
