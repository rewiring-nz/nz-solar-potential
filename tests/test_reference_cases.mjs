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
// a $10,000 system: the loan covers it and its one replacement inverter (18%)
c("borrowed for a $10,000 system", 11800, 10000 * (1 + E.ECON_DEFAULTS.inverter_cost_pct / 100),
  "10,000 + 18% for the replacement inverter");
c("...repaid at 5.5% over 30 years, a year", 811.90, r2(B.btAnnuity(11800, 0.055, 30)),
  "11,800 × 0.055 ÷ (1 − 1.055^−30) = 649 ÷ 0.79936 = 811.90");
c("the repayments add up to more than the loan", true, B.btAnnuity(10000, 0.06, 15) * 15 > 10000,
  "15 × 1,029.63 = 15,444 > 10,000: the difference is the interest");

// ---- regional prices (regional_prices.js, economics.js applyRegionalPrices) ----
const { REGIONAL_PRICES: RP } = require("../regional_prices.js");
const q = RP.regions[34];
c("Queenstown is EA network region 34", "Queenstown (Aurora Energy)", q.name, "regional_prices.js, from the EA's CSV");
c("peak and off-peak keep the EA's average for a typical home", r2(q.variable_c),
  r2(q.peak_share * q.peak_c + (1 - q.peak_share) * q.offpeak_c),
  `${q.peak_share} × ${q.peak_c}c + ${(1 - q.peak_share).toFixed(4)} × ${q.offpeak_c}c = ${q.variable_c}c`, 0.011);
c("peak is the set ratio above off-peak", r2(RP.peak_ratio), r2(q.peak_c / q.offpeak_c), "34c : 26c = 1.3077", 0.002);
{
  const d = JSON.parse(JSON.stringify(E.ECON_DEFAULTS));
  E.applyRegionalPrices(d, RP, 34);
  c("the site's region sets the home prices", `${q.peak_c}/${q.offpeak_c}/${q.daily}`,
    `${d.home_peak_c}/${d.home_offpeak_c}/${d.home_daily}`, "SITE.priceRegion = 34");
  c("businesses keep their own prices", E.ECON_DEFAULTS.biz_peak_c, d.biz_peak_c, "the EA set is residential");
}
c("solar export starts at 12c a kWh", "12/12", `${E.ECON_DEFAULTS.home_offpeak_sell_c}/${E.ECON_DEFAULTS.home_peak_sell_c}`,
  "Powerswitch retailer buy-back rates");
c("the prices are no more than eight months old", true,
  (Date.now() - Date.parse(q.month)) / 864e5 < 245, `EA month ${q.month} (the EA publishes about five months behind): run tools/fetch_regional_prices.mjs`);

// ---- money on the card (bill_tool.js btMoney) --------------------------------
for (const [v, want, why] of [[521.4, "$520", "under $1,000: nearest $10"], [5742, "$5,700", "under $10,000: nearest $100"],
                              [51750, "$52,000", "over $10,000: nearest $1,000"], [-1340, "-$1,300", "negative keeps its sign"]]) {
  c(`$${v} shown as`, want, B.btMoney(v), why);
}
const nb = B.btNetShown(5742, 5330);
c("green box: savings − repayments add up on screen", 400, nb.net, "$5,700 − $5,300 = $400 (both to the nearest $100)");

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

// ---- workings add up (workings.js) -------------------------------------------
const a = Wk.input("a", 2, "kWh"), b = Wk.input("b", 3, "kWh");
const s = Wk.sum("a + b", "kWh", [a, b]), p = Wk.product("a × b", "kWh", [a, b]);
c("a sum's breakdown adds up", 5, s.value, "2 + 3");
c("a product's breakdown adds up", 6, p.value, "2 × 3");
c("verify finds nothing wrong", true, Wk.verify(s).ok && Wk.verify(p).ok, "every step recomputes");
s.value = 6;
c("verify catches a changed figure", 1, Wk.verify(s).problems.length, "5 shown as 6");

// ---- report --------------------------------------------------------------------
const w = Math.max(...rows.map(r => r[1].length));
for (const [st, name, exp, got, how] of rows) {
  console.log(`${st} ${name.padEnd(w)}  expected ${String(exp).padEnd(10)} got ${String(got).padEnd(10)} ${how}`);
}
console.log(`\n${rows.length - failures.length} passed, ${failures.length} failed`);
if (failures.length) process.exit(1);
