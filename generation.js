// How much electricity a roof makes, with the workings.
//
// ONE CALCULATION, SHOWN. The pipeline prices every panel as
//
//     kWh = sunlight[tilt, direction] x shading x rated kW x inverter x (1 - losses)
//
// (src/solar_model.py, SolarModel.facet_yield) and rounds it to a whole kWh.
// The tiles carry each panel's tilt/direction bin (sb, ab) and shading (sh);
// data/sun/<region>.json carries that region's sunlight table, captured from
// the model as it was built (src/workings_data.py). This file does the same
// arithmetic in the same order and returns it as Workings nodes (workings.js),
// so the figure on screen and its breakdown are the same calculation.
//
// The emit stage refuses to ship a region whose panels do not reproduce
// their kWh from these inputs; tests/test_generation.mjs re-checks it here,
// in JavaScript, on real panels from a build.
//
// AND THE CURVE IS THE SAME NUMBER. The day-by-hour curve (the chart, the
// bill tool, the battery) is built from the same panels: each panel's share
// of the hourly sunlight for its own tilt and direction, from the same model
// run that made the annual table, scaled so the year under the curve is the
// annual figure exactly. The curve and the number cannot disagree; where the
// scaling has to do real work, the breakdown says by how much.

const GEN_COVERAGE_STEPS = [5, 10, 15, 25, 50, 75, 100];
const GEN_COMPASS = ["north", "north-northeast", "northeast", "east-northeast", "east",
  "east-southeast", "southeast", "south-southeast", "south", "south-southwest", "southwest",
  "west-southwest", "west", "west-northwest", "northwest", "north-northwest"];

function genCompass(deg) { return GEN_COMPASS[Math.round(((deg % 360) + 360) % 360 / 22.5) % 16]; }
function genFace(sb, ab) { return sb === 0 ? "flat" : `${sb}° facing ${genCompass(ab)}`; }

// Python's round(): halves go to the even neighbour (round(2.5) == 2). The
// pipeline rounds every panel with it, so the browser must too.
function genRoundHalfEven(x) {
  const r = Math.round(x);
  return (Math.abs(x % 1) === 0.5 && r % 2 !== 0) ? r - 1 : r;
}

// Annual sunlight on a tilt/direction bin, kWh/m2/yr, from the region table.
function genAnnual(sun, sb, ab) {
  const i = sun.slope_bins.indexOf(sb), j = sun.aspect_bins.indexOf(ab);
  if (i < 0 || j < 0) return null;
  return sun.annual_kwh_m2[i][j];
}

function genAssumptionNodes(A) {
  const ratedKw = Wk.input("Rated power of one panel", A.panel_rated_power_w / 1000, "kW",
    { dp: 3, source: `Panel spec: ${A.panel_rated_power_w} W at standard test conditions (1,000 W/m² of sunlight, 25 °C).`,
      edit: { key: "gen:panel_rated_power_w", scale: 1000, unit: "W", step: 5, dp: 0 } });
  const areaEach = Wk.input("Size of one panel", A.panel_area_m2, "m²",
    { dp: 3, source: "Panel spec: 1.134 m × 1.961 m, a typical modern residential panel." });
  const stc = Wk.input("Standard test sunlight", 1, "kW/m²",
    { dp: 0, note: "Panels are rated under 1,000 W of sunlight per m², so rated power ÷ (area × 1 kW/m²) is the panel's efficiency." });
  const eff = Wk.quotient("Panel efficiency", "share", ratedKw,
    Wk.product("Sunlight on one panel at test conditions", "kW", [areaEach, stc], { dp: 3 }), { dp: 2 });
  const inv = Wk.input("Inverter efficiency", A.inverter_efficiency_pct / 100, "share",
    { source: "Typical string inverter (the inverter turns the panels' DC into household AC).",
      edit: { key: "gen:inverter_efficiency_pct", scale: 100, unit: "%", step: 0.5, dp: 2 } });
  const loss = Wk.input("Other system losses", A.system_derate_pct / 100, "share",
    { dp: 2, source: "PVWatts convention: cell temperature, dirt, wiring, mismatch, panel tolerance and downtime.",
      note: "Shading is not in this number: it is measured per panel from the laser scan, above.",
      edit: { key: "gen:system_derate_pct", scale: 100, unit: "%", step: 0.1, dp: 2 } });
  const kept = Wk.complement("Kept after other losses", loss, { dp: 2 });
  return { ratedKw, areaEach, eff, inv, kept };
}

function genGroundNode(sun) {
  const g = Wk.input("Sunlight on flat ground", genAnnual(sun, 0, 0), "kWh/m²/yr", {
    source: sun.source && sun.source.text,
    note: "An average year at this town, hour by hour: clear-sky sun from its latitude and height, " +
          "scaled month by month to the measured record" +
          (sun.terrain_horizon ? ", with the sun cut off when it is behind the surrounding hills." : "."),
  });
  if (sun.monthly_factor) {
    const months = Object.keys(sun.monthly_factor);
    g.children.push(Wk.table("How much of the clear-sky sun gets through the cloud, by month", "share",
      [{ key: "m", label: "Month" }, { key: "f", label: "Share of clear-sky sun", unit: "share", dp: 0 }],
      months.map(m => ({ m: m[0] + m.slice(1).toLowerCase(), f: sun.monthly_factor[m] })), "f",
      { note: "Measured sunlight ÷ clear-sky sunlight, month by month. Shown for reference; the yearly figure above already includes it." }));
  }
  return g;
}

// THE GENERATION OF A SET OF PANELS (the ones drawn on the roof right now).
//   panels: [{sb, ab, sh, ac_kwh_year}]  -- tile properties
//   sun:    data/sun/<region>.json
//   A:      the assumptions in force (panel W and area, inverter, losses)
//   opts.asBuilt: the assumptions are the build's own, so each panel's
//           pipeline kWh (ac_kwh_year) applies and its rounding is shown.
// Returns { node, kwp, perPanel, missing } or null when no panel has inputs.
function genFromPanels(panels, sun, A, opts) {
  const o = opts || {};
  if (!sun || !panels || !panels.length) return null;
  const withIn = panels.filter(p => p.sb != null && p.sh != null && genAnnual(sun, p.sb, p.ab) != null);
  if (!withIn.length) return null;
  const N = panels.length, missing = N - withIn.length;

  // the per-panel sum, exactly as the pipeline does it (in its order)
  const kwpEach = 1 * A.panel_rated_power_w / 1000;
  const fInv = A.inverter_efficiency_pct / 100, fKept = 1 - A.system_derate_pct / 100;
  // panels added in the editor have no reading of their own: they take this
  // roof's average sunlight and shading, and the breakdown says so
  const mean = (f) => withIn.reduce((t, p) => t + f(p), 0) / withIn.length;
  const avgOpen = mean(p => genAnnual(sun, p.sb, p.ab));
  const avgShade = mean(p => genAnnual(sun, p.sb, p.ab) * p.sh) / avgOpen;
  const perPanel = panels.map(p => {
    const has = p.sb != null && p.sh != null && genAnnual(sun, p.sb, p.ab) != null;
    const open = has ? genAnnual(sun, p.sb, p.ab) : avgOpen;
    const sh = has ? p.sh : avgShade;
    const poa = open * sh;
    const dc = poa * kwpEach;
    const exact = dc * fInv * fKept;
    return { sb: has ? p.sb : null, ab: has ? p.ab : null, open, sh, exact,
             baked: p.ac_kwh_year, has };
  });

  // groups of panels that share a tilt and direction
  const groups = new Map();
  for (const q of perPanel) {
    const k = q.has ? `${q.sb}_${q.ab}` : "added";
    if (!groups.has(k)) groups.set(k, { face: q.has ? genFace(q.sb, q.ab) : "added in the editor (roof average)",
                                        n: 0, open: q.open, shSum: 0, sb: q.sb, ab: q.ab });
    const g = groups.get(k); g.n++; g.shSum += q.sh;
  }
  const rows = [...groups.values()].sort((a, b) => b.n - a.n).map(g => ({
    face: g.face, panels: g.n, share: g.n / N, open: g.open, shade: g.shSum / g.n,
    shaded: g.open * g.shSum / g.n, _bin: g.sb == null ? null : `${g.sb}_${g.ab}`,
  }));

  const ground = genGroundNode(sun);
  const openMean = Wk.table("Sunlight on the panels' own tilt and direction", "kWh/m²/yr",
    [{ key: "face", label: "Roof face" }, { key: "panels", label: "Panels", unit: "panels" },
     { key: "share", label: "Share of panels", unit: "share", dp: 0 },
     { key: "open", label: "Sunlight", unit: "kWh/m²/yr" }],
    rows, "open", { weightCol: "share",
      note: "Each tilt and direction gets its own yearly sunlight from the same hour-by-hour model " +
            "(sunlight split into direct and diffuse, then onto the slope with the Perez sky model). " +
            "Tilts are taken to the nearest 5° and directions to the nearest 10°. Averaged over the panels." });
  const tilt = Wk.quotient("Gain from the panels' tilt and direction", "×", openMean, ground, { dp: 3 });
  const shadedMean = Wk.table("Sunlight after shading", "kWh/m²/yr",
    [{ key: "face", label: "Roof face" }, { key: "share", label: "Share of panels", unit: "share", dp: 0 },
     { key: "open", label: "Sunlight", unit: "kWh/m²/yr" },
     { key: "shade", label: "Kept after shading", unit: "share", dp: 1 },
     { key: "shaded", label: "After shading", unit: "kWh/m²/yr" }],
    rows, "shaded", { weightCol: "share" });
  const shade = Wk.quotient("Kept after shading", "share", shadedMean, openMean, {
    dp: 1,
    note: "Measured for every panel from the laser scan: neighbouring buildings and trees within 100 m, " +
          "and the hills out to 20 km as seen from this roof. It is the share of the direct sun the panel still gets.",
  });
  if (missing) shade.flags.push(`${missing} panel${missing > 1 ? "s were" : " was"} added in the editor and use${missing > 1 ? "" : "s"} this roof's average sunlight and shading.`);
  const perM2 = Wk.product("Sunlight on each m² of panel", "kWh/m²/yr", [ground, tilt, shade]);

  const a = genAssumptionNodes(A);
  const count = Wk.input("Panels", N, "panels", { source: "The panels drawn on the roof right now." });
  const area = Wk.product("Panel area", "m²", [count, a.areaEach]);
  const onPanels = Wk.product("Sunlight hitting the panels", "kWh/yr", [perM2, area]);
  const dc = Wk.product("Electricity from the panels (DC)", "kWh/yr", [onPanels, a.eff]);
  const exactNode = Wk.product("Electricity after the inverter and losses", "kWh/yr", [dc, a.inv, a.kept]);
  const kwp = Wk.product("System size", "kW", [count, a.ratedKw], {
    note: "Panels × rated power: the most the panels make in full test sunlight. Real output is lower most of the time." });

  // The pipeline rounds every panel to a whole kWh, and the map's totals add
  // those up. Shown as its own step, so the final figure is the map's figure.
  const exactSum = perPanel.reduce((t, q) => t + q.exact, 0);
  const baked = perPanel.every(q => q.has && q.baked != null);
  let node = exactNode;
  if (o.asBuilt && baked) {
    const bakedSum = perPanel.reduce((t, q) => t + q.baked, 0);
    const rounding = Wk.given("Each panel rounded to a whole kWh", bakedSum - exactSum, "kWh/yr", [], {
      dp: 1,
      formula: `${N} panels, each rounded as the build does`,
      check: () => perPanel.reduce((t, q) => t + q.baked, 0) - perPanel.reduce((t, q) => t + q.exact, 0),
    });
    if (Math.abs(rounding.value) > 0.51 * N) rounding.flags.push("More than rounding can explain: the panels' figures and their inputs disagree.");
    node = Wk.sum("Electricity generated a year", "kWh/yr", [exactNode, rounding]);
  } else {
    exactNode.label = "Electricity generated a year";
  }
  // the long way round (panel by panel) must equal the step-by-step chain
  const f = Wk.same(exactNode.value, exactSum, "Step-by-step and panel-by-panel generation", 1e-9);
  if (f) node.flags.push(f);
  return { node, kwp, perPanel, missing, groups: rows };
}

// Mean shaded sunlight over the sunniest pct of the roof's faces (baked as
// cov_poa_N by src/bake_density_deciles.py), interpolated between steps.
function genBestPoa(props, pct) {
  const steps = GEN_COVERAGE_STEPS.filter(c => props["cov_poa_" + c] != null);
  if (!steps.length) return { value: props.avg_poa_kwh_m2 || 0, lo: null, hi: null };
  const p = Math.max(steps[0], Math.min(steps[steps.length - 1], pct));
  for (let i = 0; i < steps.length; i++) {
    if (p === steps[i]) return { value: props["cov_poa_" + steps[i]], lo: steps[i], hi: steps[i] };
    if (p < steps[i]) {
      const lo = steps[i - 1], hi = steps[i];
      const vLo = props["cov_poa_" + lo], vHi = props["cov_poa_" + hi];
      return { value: vLo + (vHi - vLo) * (p - lo) / (hi - lo), lo, hi, p };
    }
  }
  const last = steps[steps.length - 1];
  return { value: props["cov_poa_" + last], lo: last, hi: last };
}

// The heat-map estimate as plain numbers: the page sums this over every
// building in view, where building a breakdown per roof would be waste.
// genFromCoverage below is the same figure with its workings, and checks
// itself against this on every call.
function genCoverageValues(props, pct, A) {
  const kwp = (props.facet_area_m2 || 0) * (pct / 100) * (A.panel_rated_power_w / A.panel_area_m2 / 1000);
  const kwhYear = kwp * genBestPoa(props, pct).value
    * (A.inverter_efficiency_pct / 100) * (1 - A.system_derate_pct / 100);
  return { kwp, kwhYear };
}

// THE HEAT-MAP ESTIMATE: no panels laid out; a share of the roof covered.
function genFromCoverage(props, pct, sun, A) {
  const a = genAssumptionNodes(A);
  const roof = Wk.input("Roof area", props.facet_area_m2 || 0, "m²",
    { source: "The roof faces measured from the laser scan." });
  const cover = Wk.input("Covered with panels", pct / 100, "share",
    { dp: 0, source: "The roof coverage setting.", note: "Panels go on the sunniest part of the roof first." });
  const area = Wk.product("Panel area", "m²", [roof, cover]);
  const b = genBestPoa(props, pct);
  const kids = [];
  if (b.lo != null) {
    kids.push(Wk.input(`Sunniest ${b.lo}% of the roof`, props["cov_poa_" + b.lo], "kWh/m²/yr"));
    if (b.hi !== b.lo) kids.push(Wk.input(`Sunniest ${b.hi}% of the roof`, props["cov_poa_" + b.hi], "kWh/m²/yr"));
  }
  const poa = Wk.given(`Sunlight on the sunniest ${pct}% of the roof, after shading`, b.value, "kWh/m²/yr", kids, {
    formula: b.lo == null ? "the roof's average" : b.lo === b.hi ? `the sunniest ${b.lo}%`
      : `between the sunniest ${b.lo}% and ${b.hi}%, ${pct}% of the way`,
    note: "Each roof face's sunlight for its own tilt and direction, after shading measured from the laser scan, averaged over the sunniest faces first.",
    check: () => genBestPoa(props, pct).value,
  });
  let perM2 = poa;
  if (sun) {
    const ground = genGroundNode(sun);
    const both = Wk.quotient("Tilt, direction and shading together", "×", poa, ground, { dp: 3 });
    perM2 = Wk.product("Sunlight on each m² of panel", "kWh/m²/yr", [ground, both]);
  }
  const onPanels = Wk.product("Sunlight hitting the panels", "kWh/yr", [perM2, area]);
  const dc = Wk.product("Electricity from the panels (DC)", "kWh/yr", [onPanels, a.eff]);
  const node = Wk.product("Electricity generated a year", "kWh/yr", [dc, a.inv, a.kept]);
  const kwPerM2 = Wk.quotient("Rated power per m² of panel", "kW/m²", a.ratedKw, a.areaEach, { dp: 3 });
  const kwp = Wk.product("System size", "kW", [area, kwPerM2]);
  const fast = genCoverageValues(props, pct, A);
  const f = Wk.same(node.value, fast.kwhYear, "The heat-map estimate and its workings", 1e-9)
         || Wk.same(kwp.value, fast.kwp, "The heat-map system size and its workings", 1e-9);
  if (f) node.flags.push(f);
  return { node, kwp };
}

// THE CURVE: an average day per season, kW each hour, whose year is the
// annual figure exactly.
//   weights: {"slope_aspect": kWh per (kWh/m2/yr)} -- each bin's share of the
//            system, i.e. sum over its panels of shading x kW x inverter x kept
//   total:   the annual figure the curve must add up to (a Workings node)
//   tshade:  optional building terrain mask (4 x 24 digits, 9 = sun clear)
function genCurve(sun, weights, total, tshade) {
  const S = sun.seasons.length, days = sun.season_days;
  const gen = Array.from({ length: S }, () => new Array(24).fill(0));
  let used = 0, gaps = 0;
  for (const [bin, w] of Object.entries(weights)) {
    const day = sun.day_wh_m2[bin], dif = sun.day_diffuse_wh_m2 && sun.day_diffuse_wh_m2[bin];
    const [sb, ab] = bin.split("_").map(Number);
    const annual = genAnnual(sun, sb, ab);
    if (!day || annual == null) { gaps += w; continue; }
    let yr = 0;
    for (let s = 0; s < S; s++) for (let h = 0; h < 24; h++) yr += day[s][h] * days[s];
    if (!(yr > 0)) continue;
    const k = annual * 1000 / yr;           // so this bin's year is its annual figure
    for (let s = 0; s < S; s++) for (let h = 0; h < 24; h++) {
      let v = day[s][h];
      if (tshade && tshade.length === S * 24 && dif) {
        const frac = (+tshade[s * 24 + h]) / 9;   // behind the hills: diffuse only
        v = dif[s][h] + frac * (day[s][h] - dif[s][h]);
      }
      gen[s][h] += w * v * k / 1000;
    }
    used += w;
  }
  let year = 0;
  for (let s = 0; s < S; s++) for (let h = 0; h < 24; h++) year += gen[s][h] * days[s];
  const scale = year > 0 ? total.value / year : 0;
  for (let s = 0; s < S; s++) for (let h = 0; h < 24; h++) gen[s][h] *= scale;
  const rows = [];
  for (let h = 0; h < 24; h++) {
    const r = { h: `${String(h).padStart(2, "0")}:00` };
    for (let s = 0; s < S; s++) r[sun.seasons[s]] = gen[s][h];
    rows.push(r);
  }
  const node = Wk.given("Spread over an average day in each season", total.value, "kWh/yr", [
    total,
    Wk.table("Average day, kW in each hour", "kW",
      [{ key: "h", label: "Hour" }].concat(sun.seasons.map(s => ({ key: s, label: s[0].toUpperCase() + s.slice(1), unit: "kW", dp: 2 }))),
      rows, sun.seasons[0], { note: "Shown for reference: the year adds each season's day × its days." }),
  ], {
    dp: 0,
    formula: `each season's day × its days (${sun.seasons.map((s, i) => `${s} ${days[i]}`).join(", ")}), added up`,
    note: "Each panel's hourly sunlight for its own tilt and direction, from the same model run that made the yearly figure" +
          (tshade ? ", with this roof's hours behind the hills taken from its own horizon" : "") +
          `. Then scaled by ${Wk.fmt(scale, "×", 4)} so the year under the curve is exactly the yearly figure.`,
    check: () => { let y = 0; for (let s = 0; s < S; s++) for (let h = 0; h < 24; h++) y += gen[s][h] * days[s]; return y; },
  });
  if (Math.abs(scale - 1) > 0.15 && year > 0) {
    node.flags.push(`The hourly shape had to be scaled by ${Wk.fmt(scale, "×", 3)} to match the yearly figure: the two differ more than they should.`);
  }
  if (gaps > 0) node.flags.push("Part of the roof faces a direction with no hourly curve on file; the rest of the roof's shape is used for it.");
  return { gen, seasonDays: days.slice(), seasons: sun.seasons.slice(), node, scale };
}

// Bin weights for the curve from the panels genFromPanels priced.
function genWeightsFromPanels(perPanel, A) {
  const kwpEach = 1 * A.panel_rated_power_w / 1000;
  const f = kwpEach * (A.inverter_efficiency_pct / 100) * (1 - A.system_derate_pct / 100);
  const w = {};
  const roofAvgBin = perPanel.find(q => q.has);
  for (const q of perPanel) {
    const bin = q.has ? `${q.sb}_${q.ab}` : (roofAvgBin ? `${roofAvgBin.sb}_${roofAvgBin.ab}` : null);
    if (bin) w[bin] = (w[bin] || 0) + q.sh * f;
  }
  return w;
}

// Bin weights for the heat-map estimate: the roof faces, by area x shading.
function genWeightsFromFaces(faces) {
  const w = {};
  for (const fc of faces) {
    if (fc.sb == null) continue;
    const bin = `${fc.sb}_${fc.ab}`;
    w[bin] = (w[bin] || 0) + (fc.area || 1) * (fc.sh == null ? 1 : fc.sh);
  }
  return w;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { genFromPanels, genFromCoverage, genCoverageValues, genCurve, genWeightsFromPanels,
                     genWeightsFromFaces, genBestPoa, genAnnual, genRoundHalfEven, genFace,
                     GEN_COVERAGE_STEPS };
}
