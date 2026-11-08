/**
 * Regional power prices from the Electricity Authority, for the bill model.
 *
 * The EA publishes, monthly, each network region's average residential
 * prices ("Regional power prices", ea.govt.nz/data-and-insights/datasets/
 * retail/regional-power-prices/): a fixed $/day and one variable $/kWh,
 * incl. GST, for a low and a standard user. This reads the newest CSV that
 * page links to, takes each region's latest month for the standard user,
 * and writes regional_prices.js, which the page loads before economics.js.
 *
 * PEAK AND OFF-PEAK. The EA gives one price per kWh. The bill model prices
 * peak hours (econ.peak_hours) above the rest, so that one price is split
 * at the peak : off-peak ratio of the home prices in ECON_DEFAULTS (2 : 1; a
 * Queenstown time-of-use plan in Nov 2026 was 55c : 25c), solved
 * so a default home in that region, with its own share of use in peak
 * hours, pays exactly the EA's average:
 *     share x peak + (1 - share) x offpeak = EA price,  peak = ratio x offpeak.
 *
 * Run (needs Node 18+ and the network):
 *     node tools/fetch_regional_prices.mjs
 * The monthly workflow (.github/workflows/regional-prices.yml) runs it and
 * commits the file when the EA has published a new month.
 */
import { createRequire } from "node:module";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PAGE = "https://www.ea.govt.nz/data-and-insights/datasets/retail/regional-power-prices/";
const CSV_RE = /https:\/\/emidatasets\.blob\.core\.windows\.net\/publicdata\/Datasets\/Retail\/RegionalPowerPrices\/[0-9]{8}_AveragePowerUseAndCosts\.csv/g;
// where each region's default home is placed for its share of use in peak
// hours (heating follows the town's climate): latitude and longitude
const REGION_TOWN = {
  34: [-45.03, 168.66],   // Queenstown (Aurora Energy)
  35: [-44.70, 169.13],   // Central Otago (Aurora Energy): Wanaka, Cromwell
  38: [-45.33, 168.72],   // Southland (The Power Company): Kingston
};

const E = require(join(ROOT, "economics.js"));
globalThis.Wk = require(join(ROOT, "workings.js")).Wk;
Object.assign(globalThis, { simulateDay: E.simulateDay, systemCost: E.systemCost, costPerKw: E.costPerKw,
                            BATTERY_DEFAULTS: E.BATTERY_DEFAULTS, econ: E.getEcon() });
try { Object.assign(globalThis, require(join(ROOT, "load_profiles.js"))); } catch (e) { /* optional */ }
const B = require(join(ROOT, "bill_tool.js"));

// the default home's share of its year's use in peak hours
export function peakShare(lat, lon) {
  const load = B.btBuildLoad(Object.assign({ type: "home" }, B.BT_DEFAULTS.home), { lat, lon, roofM2: 150 });
  const peak = h => E.ECON_DEFAULTS.peak_hours.some(([a, b]) => h >= a && h < b);
  let all = 0, inPeak = 0;
  B.BT_SEASONS.forEach(s => {
    const day = B.btSeasonLoad(load, s, 1), d = { summer: 90, autumn: 92, winter: 92, spring: 91 }[s];
    day.forEach((kw, h) => { all += kw * d; if (peak(h)) inPeak += kw * d; });
  });
  return inPeak / all;
}

// one price per kWh as peak and off-peak, keeping the default home's average
// Shown and used to the whole cent, as a plan quotes them; the rounding moves
// the typical home's average off the EA's by at most half a cent.
export function splitPrice(variableC, share, ratio) {
  const off = variableC / (share * ratio + (1 - share));
  return { peak_c: Math.round(off * ratio), offpeak_c: Math.round(off) };
}

async function main() {
  const html = await (await fetch(PAGE)).text();
  const links = [...new Set(html.match(CSV_RE) || [])].sort();
  if (!links.length) throw new Error("no CSV link on " + PAGE);
  const csvUrl = links[links.length - 1];
  const text = (await (await fetch(csvUrl)).text()).replace(/^﻿/, "");
  const [head, ...lines] = text.trim().split(/\r?\n/);
  const cols = head.split(",");
  const at = n => cols.indexOf(n);
  const latest = {};
  for (const line of lines) {
    const v = line.split(",");
    if (v[at("LoadType")] !== "Standard user") continue;
    const code = +v[at("NetworkReportingRegionCode")], month = v[at("MonthEnd")];
    if (!latest[code] || month > latest[code].month) {
      latest[code] = { name: v[at("NetworkReportingRegion")], month,
                       daily: Math.round(+v[at("FixedDollarPerDayInclGST")] * 100) / 100, variable_c: +v[at("VariableDollarPerkWhInclGST")] * 100 };
    }
  }
  const ratio = E.ECON_DEFAULTS.home_peak_c / E.ECON_DEFAULTS.home_offpeak_c;
  const regions = {};
  for (const [code, r] of Object.entries(latest)) {
    const [lat, lon] = REGION_TOWN[code] || [-41.3, 174.8];
    const share = peakShare(lat, lon);
    regions[code] = Object.assign({}, r, { variable_c: Math.round(r.variable_c * 100) / 100,
      peak_share: Math.round(share * 10000) / 10000 }, splitPrice(r.variable_c, share, ratio));
  }
  const out = { source: PAGE, csv: csvUrl, load_type: "Standard user", peak_ratio: Math.round(ratio * 10000) / 10000,
                peak_hours: E.ECON_DEFAULTS.peak_hours, regions };
  const js = "// Written by tools/fetch_regional_prices.mjs from the Electricity Authority's\n" +
    "// regional power prices. Do not edit by hand: run the tool.\n" +
    "const REGIONAL_PRICES = " + JSON.stringify(out, null, 1) + ";\n" +
    "if (typeof module !== \"undefined\" && module.exports) module.exports = { REGIONAL_PRICES };\n";
  writeFileSync(join(ROOT, "regional_prices.js"), js);
  const q = regions[34];
  console.log(`${Object.keys(regions).length} regions from ${csvUrl.split("/").pop()}; ` +
              (q ? `Queenstown ${q.month}: ${q.variable_c}c -> ${q.peak_c}c peak / ${q.offpeak_c}c off-peak, $${q.daily}/day` : ""));
}

// --resplit: the EA's prices as already fetched, split again for the current
// home shape (after load_profiles.js changes), without fetching anything.
function resplit() {
  const file = join(ROOT, "regional_prices.js");
  const { REGIONAL_PRICES: RP } = require(file);
  const ratio = E.ECON_DEFAULTS.home_peak_c / E.ECON_DEFAULTS.home_offpeak_c;
  for (const [code, r] of Object.entries(RP.regions)) {
    const [lat, lon] = REGION_TOWN[code] || [-41.3, 174.8];
    const share = peakShare(lat, lon);
    Object.assign(r, { peak_share: Math.round(share * 10000) / 10000 }, splitPrice(r.variable_c, share, ratio));
  }
  const head = readFileSync(file, "utf8").split("const REGIONAL_PRICES")[0];
  writeFileSync(file, head + "const REGIONAL_PRICES = " + JSON.stringify(RP, null, 1) + ";\n" +
    "if (typeof module !== \"undefined\" && module.exports) module.exports = { REGIONAL_PRICES };\n");
  const q = RP.regions[34];
  console.log(`re-split ${Object.keys(RP.regions).length} regions; Queenstown ${q.variable_c}c -> ${q.peak_c}c peak / ${q.offpeak_c}c off-peak (peak share ${q.peak_share})`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  if (process.argv.includes("--resplit")) resplit();
  else main().catch(e => { console.error(e.message); process.exit(1); });
}
