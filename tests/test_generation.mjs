/**
 * The browser's generation arithmetic against the Python pipeline, on real
 * panels exported from a build (tests/fixtures/workings_kinloch.json, from
 * tools/export_workings_fixture.py) -- and the curve, the heat-map estimate
 * and the bills against the same figures. Every number on the map that is a
 * kWh must be the same kWh, whichever way it is reached.
 *
 * Run:  node tests/test_generation.mjs
 */

import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
const require = createRequire(import.meta.url);
const { Wk } = require("../workings.js");
globalThis.Wk = Wk;
const G = require("../generation.js");
const E = require("../economics.js");
Object.assign(globalThis, { simulateDay: E.simulateDay, systemCost: E.systemCost, costPerKw: E.costPerKw,
                            BATTERY_DEFAULTS: E.BATTERY_DEFAULTS, econ: E.getEcon() });
const B = require("../bill_tool.js");

const fx = JSON.parse(readFileSync(new URL("./fixtures/workings_kinloch.json", import.meta.url)));
const A = fx.assumptions, sun = fx.sun;

let pass = 0;
const failures = [];
function check(name, fn) {
  try { fn(); console.log(`  pass  ${name}`); pass++; }
  catch (e) { console.log(`  FAIL  ${name}: ${e.message}`); failures.push(name); }
}
const close = (a, b, rel, what) => {
  if (Math.abs(a - b) > rel * Math.max(1, Math.abs(a), Math.abs(b))) throw new Error(`${what}: ${a} vs ${b}`);
};
const verified = (n, what) => {
  const v = Wk.verify(n);
  if (!v.ok) throw new Error(`${what}: "${v.problems[0].node.label}" ${v.problems[0].expected} vs ${v.problems[0].got}`);
  return v;
};
const flagsIn = n => { const out = []; const seen = new Set();
  const w = x => { if (seen.has(x)) return; seen.add(x); out.push(...x.flags); x.children.forEach(w); }; w(n); return out; };

check("every panel: the browser's kWh is Python's kWh, from the shipped inputs", () => {
  let n = 0;
  for (const panels of Object.values(fx.panels)) for (const p of panels) {
    const r = G.genFromPanels([p], sun, A, {});
    close(r.perPanel[0].exact, p.py_exact, 1e-12, "panel kWh");
    if (Math.abs(p.py_exact - p.ac_kwh_year) > 0.5 + 0.05) throw new Error(`panel off its rounded value: ${p.py_exact} vs ${p.ac_kwh_year}`);
    n++;
  }
  if (n < 1000) throw new Error(`only ${n} panels in the fixture`);
});

check("every building: the step-by-step figure is the map's figure, exactly", () => {
  for (const [bid, panels] of Object.entries(fx.panels)) {
    const r = G.genFromPanels(panels, sun, A, { asBuilt: true });
    const baked = panels.reduce((t, p) => t + p.ac_kwh_year, 0);
    close(r.node.value, baked, 1e-12, `building ${bid} vs its panels`);
    close(r.node.value, fx.buildings[bid].ac_kwh_year, 1e-12, `building ${bid} vs solar_potential`);
    close(r.kwp.value, fx.buildings[bid].kwp, 1e-9, `building ${bid} kW`);
    verified(r.node, `building ${bid}`);
    const fl = flagsIn(r.node);
    if (fl.length) throw new Error(`building ${bid} flagged: ${fl[0]}`);
  }
});

check("the step-by-step chain equals the panel-by-panel sum", () => {
  const [bid, panels] = Object.entries(fx.panels).sort((a, b) => b[1].length - a[1].length)[0];
  const r = G.genFromPanels(panels, sun, A, {});
  const exact = r.perPanel.reduce((t, q) => t + q.exact, 0);
  close(r.node.value, exact, 1e-9, `building ${bid}`);
});

check("a subset of panels (the density slider) still adds up", () => {
  for (const panels of Object.values(fx.panels)) {
    const half = panels.filter(p => p.fill_rank <= 50);
    if (!half.length) continue;
    const r = G.genFromPanels(half, sun, A, { asBuilt: true });
    close(r.node.value, half.reduce((t, p) => t + p.ac_kwh_year, 0), 1e-12, "subset");
  }
});

check("the curve's year is the annual figure, and its shape needs no real rescaling", () => {
  for (const panels of Object.values(fx.panels)) {
    const r = G.genFromPanels(panels, sun, A, { asBuilt: true });
    const c = G.genCurve(sun, G.genWeightsFromPanels(r.perPanel, A), r.node, null);
    let yr = 0;
    c.gen.forEach((row, s) => row.forEach(v => { yr += v * c.seasonDays[s]; }));
    close(yr, r.node.value, 1e-9, "curve year");
    verified(c.node, "curve");
    // without a terrain mask the only difference is each panel's rounding
    if (Math.abs(c.scale - 1) > 0.5 * panels.length / r.node.value + 1e-9) throw new Error(`curve scale ${c.scale}`);
  }
});

check("the season days are a whole year", () => {
  if (sun.season_days.reduce((a, b) => a + b, 0) !== 365) throw new Error(sun.season_days.join(","));
});

check("the heat-map estimate is the old formula, with workings", () => {
  for (const props of Object.values(fx.buildings)) {
    if (!props.facet_area_m2) continue;
    for (const pct of [10, 25, 60, 100]) {
      const r = G.genFromCoverage(props, pct, sun, A);
      const kwp = props.facet_area_m2 * pct / 100 * (A.panel_rated_power_w / A.panel_area_m2 / 1000);
      const old = kwp * G.genBestPoa(props, pct).value * (A.inverter_efficiency_pct / 100) * (1 - A.system_derate_pct / 100);
      close(r.node.value, old, 1e-12, "heat-map kWh");
      close(r.kwp.value, kwp, 1e-12, "heat-map kW");
      verified(r.node, "heat-map");
    }
  }
});

check("the bills run on the roof's own generation, to the kWh", () => {
  const [, panels] = Object.entries(fx.panels).sort((a, b) => b[1].length - a[1].length)[0];
  const g = G.genFromPanels(panels, sun, A, { asBuilt: true });
  const c = G.genCurve(sun, G.genWeightsFromPanels(g.perPanel, A), g.node, null);
  const r = B.btEvaluate({ lat: sun.lat, roofM2: 150, kwp: g.kwp.value, kwpNode: g.kwp, gen: c.gen,
                           seasonDays: c.seasonDays, genNode: g.node },
    Object.assign({ type: "home" }, B.BT_DEFAULTS.home, { battery: 10 }), { winter: 450, summer: 160 },
    { peak: 34, offpeak: 26, peakSell: 14, offpeakSell: 12, daily: 2.5 }, null);
  close(r.nodes.genYear.value, g.node.value, 1e-9, "bills' solar vs roof");
  for (const k of ["solarSave", "battSave", "useYear", "nowBill", "solarBill", "battBill", "solarCost", "allCost", "dayKw", "dayShare"]) {
    verified(r.nodes[k], k);
  }
  for (const k of ["payback", "lifetime", "net", "irr"]) verified(r.battRet.nodes[k], k);
  if (flagsIn(r.nodes.genYear).length) throw new Error(flagsIn(r.nodes.genYear)[0]);
});

check("Python rounding is reproduced (halves to even)", () => {
  if (G.genRoundHalfEven(2.5) !== 2 || G.genRoundHalfEven(3.5) !== 4 || G.genRoundHalfEven(2.4) !== 2) throw new Error("rounding");
});

console.log(`\n${pass} passed, ${failures.length} failed`);
if (failures.length) process.exit(1);
