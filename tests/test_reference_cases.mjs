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
c("15 years of those repayments", 15444.41, r2(B.btAnnuity(10000, 0.06, 15) * 15),
  "15 × 1,029.6276 = 15,444.41: $10,000 borrowed plus $5,444.41 interest", 0.01);

// ---- regional prices (regional_prices.js, economics.js applyRegionalPrices) ----
const { REGIONAL_PRICES: RP } = require("../regional_prices.js");
const q = RP.regions[34];
c("Queenstown is EA network region 34", "Queenstown (Aurora Energy)", q.name, "regional_prices.js, from the EA's CSV");
c("peak and off-peak keep the EA's average for a typical home", r2(q.variable_c),
  r2(q.peak_share * q.peak_c + (1 - q.peak_share) * q.offpeak_c),
  `${q.peak_share} × ${q.peak_c}c + ${(1 - q.peak_share).toFixed(4)} × ${q.offpeak_c}c = ${q.variable_c}c`, 0.011);
c("peak is the set ratio above off-peak", r2(RP.peak_ratio), r2(q.peak_c / q.offpeak_c), "peak twice off-peak (50c : 25c)", 0.002);
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
c("solar export starts at 12c a kWh", "12/12", `${E.ECON_DEFAULTS.home_offpeak_sell_c}/${E.ECON_DEFAULTS.home_peak_sell_c}`,
  "Powerswitch retailer buy-back rates");
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
