// Your power bill, with and without solar (and a battery): the per-building
// economics tool.
//
// WHY A NEW TOOL. The old block asked for numbers nobody knows about their
// own house -- "daytime load in kW", a 24-hour rate strip -- and answered in
// lifetime ROI. People know how many live there, how the water is heated,
// whether there is a heat pump or an EV, and what last winter's bill was. So
// the household is described in those terms, its electricity use is built
// from them hour by hour for each season, and the answer is the thing people
// actually look at: a winter and a summer monthly bill, now and with solar.
//
// THE MODEL (pure: numbers in, numbers out; tests/test_bill_tool.mjs):
//   load   = appliances + hot water + room heating + electric cars, each with
//            its own daily kWh per season and its own hour-of-day shape;
//   solar  = the roof's own average day per season (window.__genProfile, the
//            curve drawn in the building panel), scaled to the system shown;
//   hour by hour: solar serves the load, then the battery, then the grid
//            (simulateDay, economics.js); bills = imports x price + daily
//            charge - exports x buyback;
//   bills entered by the user scale the modelled load so "now" matches them.
// Indicative, not a quote -- and the panel says so.

const BT_SEASONS = ["summer", "autumn", "winter", "spring"];
const BT_SEASON_DAYS = { summer: 90, autumn: 92, winter: 92, spring: 91 };
const BT_MONTH_DAYS = 30.4;

// Defaults, each with where it comes from. NZ households average about
// 7,000 kWh a year (MBIE); hot water is roughly a third of it in a home with
// an electric cylinder and heating a quarter to a third in a cold-climate
// home with a heat pump (EECA).
const BT_DEFAULTS = {
  home: {
    people: 3,
    water: "electric",        // electric | heatpump | gas
    heating: "heatpump",      // heatpump | electric | gas | none
    evs: 0,
    waterOnSun: true,         // cylinder on a timer that runs it midday
    evOnSun: false,           // cars charged when the sun is out
    battery: 0,               // kWh
  },
  business: {
    heating: "heatpump",
    evs: 0,
    evOnSun: true,
    battery: 0,
  },
  // appliances, lighting, cooking, fridge: kWh/day = base + per person
  appliance_base_kwh: 2.5, appliance_per_person_kwh: 1.9,
  // electric cylinder: kWh/day = base (standing losses) + per person
  water_base_kwh: 1.2, water_per_person_kwh: 1.6,
  water_heatpump_cop: 3.0,
  // room heating with a heat pump, kWh per winter day per degree-day of the
  // town's year (Auckland ~1,200 HDD -> ~5 kWh/day; Queenstown ~3,200 -> ~13)
  heat_kwh_per_hdd: 0.0042,
  resistive_vs_heatpump: 2.5,   // plug-in heaters use ~2.5x a heat pump for the same warmth
  ev_km_per_year: 11000,        // NZ average (Waka Kotahi)
  ev_kwh_per_km: 0.165,
  // business electricity per m2 of footprint per year, before heating and cars
  biz_kwh_per_m2: 75,
  biz_min_kwh: 6000,
};

// Season multipliers on each part's daily kWh.
const BT_SEASONAL = {
  appliance: { summer: 0.92, autumn: 1.0, winter: 1.12, spring: 1.0 },  // lighting
  water:     { summer: 0.85, autumn: 1.0, winter: 1.2,  spring: 1.0 },  // colder inlet water
  heat:      { summer: 0.03, autumn: 0.45, winter: 1.0, spring: 0.5 },
  ev:        { summer: 0.95, autumn: 1.0, winter: 1.1,  spring: 1.0 },
};

// Hour-of-day shapes (fractions of the day's kWh, normalised below).
const BT_SHAPES = {
  appliance: [0.020, 0.018, 0.017, 0.017, 0.018, 0.024, 0.038, 0.055, 0.052, 0.042, 0.036, 0.034,
              0.034, 0.033, 0.033, 0.036, 0.045, 0.062, 0.085, 0.082, 0.068, 0.054, 0.040, 0.027],
  // ripple-controlled cylinder: mostly overnight, topped up morning and evening
  water_night: [0.10, 0.12, 0.12, 0.10, 0.08, 0.06, 0.05, 0.05, 0.04, 0.015, 0.015, 0.015,
                0.015, 0.015, 0.015, 0.015, 0.02, 0.03, 0.04, 0.04, 0.03, 0.03, 0.06, 0.08],
  // on a timer (or diverter) that heats it while the sun is on the roof
  water_sun: [0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.02, 0.02, 0.03, 0.06, 0.13, 0.15,
              0.15, 0.14, 0.11, 0.06, 0.03, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01],
  heat: [0.010, 0.010, 0.010, 0.010, 0.012, 0.030, 0.075, 0.085, 0.070, 0.035, 0.028, 0.025,
         0.025, 0.025, 0.028, 0.040, 0.070, 0.085, 0.090, 0.085, 0.075, 0.060, 0.035, 0.015],
  ev_night: [0.11, 0.11, 0.11, 0.11, 0.10, 0.08, 0.02, 0, 0, 0, 0, 0,
             0, 0, 0, 0, 0, 0, 0.01, 0.02, 0.04, 0.06, 0.10, 0.12],
  ev_sun: [0.04, 0.04, 0.04, 0.04, 0.03, 0.02, 0, 0, 0.02, 0.06, 0.11, 0.13,
           0.13, 0.12, 0.10, 0.06, 0.02, 0, 0, 0, 0.01, 0.01, 0.01, 0.01],
  business: [0.012, 0.011, 0.011, 0.011, 0.012, 0.016, 0.026, 0.042, 0.062, 0.073, 0.077, 0.078,
             0.074, 0.076, 0.075, 0.070, 0.058, 0.044, 0.030, 0.022, 0.018, 0.015, 0.014, 0.013],
  business_heat: [0.005, 0.005, 0.005, 0.005, 0.008, 0.030, 0.080, 0.100, 0.095, 0.085, 0.075, 0.070,
                  0.065, 0.065, 0.065, 0.060, 0.050, 0.035, 0.020, 0.012, 0.008, 0.006, 0.005, 0.005],
};
for (const k of Object.keys(BT_SHAPES)) {
  const s = BT_SHAPES[k].reduce((a, b) => a + b, 0);
  BT_SHAPES[k] = BT_SHAPES[k].map(v => v / s);
}

// Heating degree-days a year, from latitude: ~1,200 at Auckland (36.9 S),
// ~3,200 at Queenstown (45.0 S), linear between and held beyond. Coarse, and
// labelled as such; a town that knows better can carry its own value.
function btHdd(lat) {
  const a = Math.abs(lat || 41);
  const t = Math.min(1, Math.max(0, (a - 36.9) / (45.0 - 36.9)));
  return 1200 + t * 2000;
}

// EVERY FIGURE BELOW IS A WORKINGS NODE (workings.js). The numbers the panel
// shows are node values and the breakdown behind each one is the same node
// opened up -- there is no separate description of the sums to fall out of
// step with them. The plain values (parts, scale, seasons, year) are read
// off the nodes for the code that only needs numbers.
const BT_CAP = s => s[0].toUpperCase() + s.slice(1);
const btIn = (label, value, unit, o) => Wk.input(label, value, unit, o);
const btZero = (label, why) => Wk.input(label, 0, "kWh/day", { note: why });

// Daily kWh of each part, per season, and the hourly shapes they follow.
function btBuildLoad(p, opts) {
  const D = BT_DEFAULTS;
  const lat = opts && opts.lat, roofM2 = (opts && opts.roofM2) || 150;
  const nodes = { appliance: {}, water: {}, heat: {}, ev: {} };
  const shapes = {};
  const hdd = Wk.given("Heating degree-days a year", btHdd(lat), "°C·days",
    [btIn("Latitude", Math.abs(lat || 41), "°", { dp: 2, source: "This building's town." })], {
      formula: "1,200 at Auckland (36.9°S) rising to 3,200 at Queenstown (45.0°S) in a straight line, held beyond",
      note: "How cold the year is: degrees below 18 °C, added up over every day of the year. Coarse, from latitude.",
      check: () => btHdd(lat) });
  const seasonal = (part, s, what) => btIn(`${BT_CAP(s)} adjustment`, BT_SEASONAL[part][s], "×",
    { dp: 2, note: what });
  const roof = btIn("Roof area", roofM2, "m²", { source: "Measured from the laser scan." });
  const perKwhHdd = btIn("Heat pump electricity per degree-day", D.heat_kwh_per_hdd, "kWh/°C·day",
    { source: "Calibrated to EECA: about 5 kWh a winter day in Auckland and 13 in Queenstown for a typical home." });
  const resistive = btIn("Plug-in heaters vs a heat pump", D.resistive_vs_heatpump, "×",
    { dp: 1, source: "A heat pump delivers about 2.5 units of heat per unit of electricity; a plug-in heater 1." });
  let heatWinter;
  if (p.type === "business") {
    const year = Wk.maxOf("Electricity for the business a year (before heating and cars)", "kWh/yr", [
      btIn("Smallest business", D.biz_min_kwh, "kWh/yr"),
      Wk.product("By floor area", "kWh/yr", [btIn("Electricity per m²", D.biz_kwh_per_m2, "kWh/m²",
        { dp: 0, source: "Typical NZ commercial use per m² of floor, before space heating." }), roof])]);
    const day = Wk.quotient("A typical day", "kWh/day", year, btIn("Days in a year", 365, "days"));
    for (const s of BT_SEASONS) nodes.appliance[s] = Wk.product(`Business use, ${s}`, "kWh/day",
      [day, seasonal("appliance", s, "Lighting is used more in the darker months.")]);
    shapes.appliance = BT_SHAPES.business;
    const base = Wk.product("Heat pump heating on the coldest days", "kWh/day", [hdd, perKwhHdd,
      Wk.quotient("Size vs a 150 m² house", "×", roof, btIn("Typical house", 150, "m²")),
      btIn("Share of the floor heated", 0.6, "share", { dp: 0 })]);
    heatWinter = p.heating === "heatpump" ? base
      : p.heating === "electric" ? Wk.product("Electric heaters on the coldest days", "kWh/day", [base, resistive])
      : null;
    for (const s of BT_SEASONS) nodes.water[s] = btZero(`Hot water, ${s}`, "Not modelled separately for a business.");
    shapes.heat = BT_SHAPES.business_heat;
    shapes.water = BT_SHAPES.water_night;
  } else {
    const people = btIn("People", Math.max(1, Math.min(8, p.people || 1)), "people", { source: "Your answer." });
    const app = Wk.sum("Appliances, lights and cooking, a typical day", "kWh/day", [
      btIn("Base (fridge, standby, lights)", D.appliance_base_kwh, "kWh/day",
        { source: "Sized so a 3-person home lands near MBIE's ~7,000 kWh a year average." }),
      Wk.product("For the people", "kWh/day", [btIn("Each person", D.appliance_per_person_kwh, "kWh/day"), people])]);
    const cyl = Wk.sum("Electric cylinder, a typical day", "kWh/day", [
      btIn("Standing losses", D.water_base_kwh, "kWh/day", { source: "EECA: a cylinder loses heat all day." }),
      Wk.product("For the people", "kWh/day", [btIn("Each person", D.water_per_person_kwh, "kWh/day",
        { source: "EECA: hot water is about a third of a home's electricity with an electric cylinder." }), people])]);
    const water = p.water === "electric" ? cyl
      : p.water === "heatpump" ? Wk.quotient("Heat pump water heater, a typical day", "kWh/day", cyl,
          btIn("Heat pump efficiency (units of heat per unit of electricity)", D.water_heatpump_cop, "×", { dp: 1 }))
      : null;
    const size = Wk.clamp("Size vs a typical 150 m² house", "×",
      Wk.quotient("Roof area ÷ 150 m²", "×", roof, btIn("Typical house", 150, "m²")), 0.6, 2.0, { dp: 2 });
    const hp = Wk.product("Heat pump heating on the coldest days", "kWh/day", [hdd, perKwhHdd, size]);
    heatWinter = p.heating === "heatpump" ? hp
      : p.heating === "electric" ? Wk.product("Electric heaters on the coldest days", "kWh/day", [hp, resistive])
      : null;
    for (const s of BT_SEASONS) {
      nodes.appliance[s] = Wk.product(`Appliances, ${s}`, "kWh/day",
        [app, seasonal("appliance", s, "Lighting is used more in the darker months.")]);
      nodes.water[s] = water ? Wk.product(`Hot water, ${s}`, "kWh/day",
        [water, seasonal("water", s, "Colder inlet water takes more heating in winter.")])
        : btZero(`Hot water, ${s}`, "Heated with gas: no electricity.");
    }
    shapes.appliance = BT_SHAPES.appliance;
    shapes.water = p.waterOnSun ? BT_SHAPES.water_sun : BT_SHAPES.water_night;
    shapes.heat = BT_SHAPES.heat;
  }
  for (const s of BT_SEASONS) {
    nodes.heat[s] = heatWinter ? Wk.product(`Heating, ${s}`, "kWh/day",
      [heatWinter, seasonal("heat", s, "Share of the coldest days' heating needed in this season.")])
      : btZero(`Heating, ${s}`, p.heating === "none" ? "No heating." : "Heated with gas or wood: no electricity.");
  }
  const evs = btIn(p.type === "business" ? "Electric vehicles" : "Electric cars", p.evs || 0, "", { dp: 0, source: "Your answer." });
  const evDay = Wk.quotient("Charging, a typical day", "kWh/day", Wk.product("Charging a year", "kWh/yr", [evs,
    btIn("Distance per car", D.ev_km_per_year, "km/yr", { source: "Waka Kotahi: NZ average." }),
    btIn("Electricity per km", D.ev_kwh_per_km, "kWh/km", { source: "A typical small-to-mid EV." })]),
    btIn("Days in a year", 365, "days"));
  for (const s of BT_SEASONS) nodes.ev[s] = Wk.product(`Electric cars, ${s}`, "kWh/day",
    [evDay, seasonal("ev", s, "Batteries use a little more in the cold.")]);
  shapes.ev = p.evOnSun ? BT_SHAPES.ev_sun : BT_SHAPES.ev_night;
  const parts = {};
  for (const k of Object.keys(nodes)) {
    parts[k] = {};
    for (const s of BT_SEASONS) parts[k][s] = nodes[k][s].value;
  }
  return { parts, nodes, shapes, hdd: hdd.value };
}

// The hourly load (kW) of one season, optionally scaled.
function btSeasonLoad(load, s, scale) {
  const k = scale == null ? 1 : scale;
  const out = new Array(24).fill(0);
  for (const part of Object.keys(load.parts)) {
    const day = load.parts[part][s] * k;
    const sh = load.shapes[part];
    for (let h = 0; h < 24; h++) out[h] += day * sh[h];
  }
  return out;
}

function btDailyKwh(load, s) {
  return Object.keys(load.parts).reduce((t, k) => t + load.parts[k][s], 0);
}

function btModelledDay(load, s) {
  return Wk.sum(`Estimated use, a ${s} day`, "kWh/day",
    Object.keys(load.nodes).map(k => load.nodes[k][s]));
}

function btPeakHours() {
  return (typeof econ !== "undefined" && econ.peak_hours) || [[7, 10], [18, 21]];
}
function btIsPeak(h) { return btPeakHours().some(([a, b]) => h >= a && h < b); }
function btPeakText() { return btPeakHours().map(([a, b]) => `${a}:00–${b}:00`).join(" and "); }

// A plan: { peak, offpeak, peakSell, offpeakSell } in c/kWh and daily in $.
// A flat { buy, sell, daily } is read as the same price at every hour.
function btTariff(t) {
  if (t && t.buy != null && t.peak == null) {
    return { peak: t.buy, offpeak: t.buy, peakSell: t.sell, offpeakSell: t.sell, daily: t.daily };
  }
  return t;
}

function btPriceNodes(tariff) {
  const t = btTariff(tariff);
  const src = `Your plan (Prices), including GST. Peak hours: ${btPeakText()}.`;
  const P = {
    peak: btIn("Peak price", t.peak / 100, "$/kWh", { dp: 3, source: src }),
    off: btIn("Off-peak price", t.offpeak / 100, "$/kWh", { dp: 3, source: src }),
    peakSell: btIn("Peak export price", t.peakSell / 100, "$/kWh", { dp: 3, source: src }),
    offSell: btIn("Off-peak export price", t.offpeakSell / 100, "$/kWh", { dp: 3, source: src }),
    daily: btIn("Daily charge", t.daily, "$/day", { source: "Your plan (Prices), including GST." }),
    month: btIn("Days in a typical month", BT_MONTH_DAYS, "days", { dp: 1, note: "365 ÷ 12." }),
  };
  P.buyAt = h => btIsPeak(h) ? P.peak.value : P.off.value;
  P.sellAt = h => btIsPeak(h) ? P.peakSell.value : P.offSell.value;
  return P;
}

// What a kWh costs on average for THIS home's pattern of use in a season:
// the hours it uses power, priced peak or off-peak. Scale-free, so it turns
// a bill into kWh exactly.
function btAveragePrice(load, s, P) {
  const L = btSeasonLoad(load, s, 1);
  const tot = L.reduce((a, b) => a + b, 0);
  const v = tot > 0 ? L.reduce((t, l, h) => t + l * P.buyAt(h), 0) / tot : P.off.value;
  const peakShare = tot > 0 ? L.reduce((t, l, h) => t + (btIsPeak(h) ? l : 0), 0) / tot : 0;
  return Wk.given(`Average price for when this home uses power, ${s}`, v, "$/kWh", [P.peak, P.off], {
    dp: 3,
    formula: `${Math.round(peakShare * 100)}% of a ${s} day's use falls in peak hours, the rest off-peak`,
    check: () => { const L2 = btSeasonLoad(load, s, 1); const t2 = L2.reduce((a, b) => a + b, 0);
                   return t2 > 0 ? L2.reduce((t, l, h) => t + l * P.buyAt(h), 0) / t2 : P.off.value; } });
}

// Scale factors per season so the modelled "now" matches what the user told
// us: their bills (monthly, before solar; either one stands for both), or
// their yearly use. calib: {winter, summer} | {total} | null.
function btCalibration(load, calib, tariff, seasonDays) {
  const scale = { summer: 1, autumn: 1, winter: 1, spring: 1 };
  const P = btPriceNodes(tariff);
  const one = s => btIn(`Matching to your bills, ${s}`, 1, "×", { dp: 2, note: "Nothing entered: our estimate as it is." });
  const nodes = {};
  BT_SEASONS.forEach(s => { nodes[s] = one(s); });
  const done = (matched, by) => { BT_SEASONS.forEach(s => { scale[s] = nodes[s].value; }); return { scale, matched, by, nodes }; };
  if (!calib) return done(false, null);
  if (calib.total > 0) {
    const days = s => btIn(`Days of ${s}`, seasonDays ? seasonDays[BT_SEASONS.indexOf(s)] : BT_SEASON_DAYS[s], "days");
    const est = Wk.sum("Our estimate of use a year", "kWh/yr",
      BT_SEASONS.map(s => Wk.product(`${BT_CAP(s)} (${days(s).value} days)`, "kWh", [btModelledDay(load, s), days(s)])));
    const k = Wk.clamp("Matching to your yearly use", "×", Wk.quotient("Your use ÷ our estimate", "×",
      btIn("Your electricity use a year", calib.total, "kWh/yr", { source: "You entered it." }), est, { dp: 3 }),
      0.2, 5, { dp: 3, note: "Every part of the estimated use is scaled by this, in every season." });
    BT_SEASONS.forEach(s => { nodes[s] = k; });
    return done(true, "total");
  }
  const fromBill = (bill, s) => {
    if (!(bill > 0)) return null;
    const model = btModelledDay(load, s);
    if (!(model.value > 0)) return null;
    const entered = btIn(`Your ${s} bill`, bill, "$/month", { source: "You entered it." });
    const kwh = Wk.quotient(`Electricity your ${s} bill pays for, a day`, "kWh/day",
      Wk.diff("Bill a day, less the daily charge", "$/day",
        Wk.quotient("Bill a day", "$/day", entered, P.month), P.daily), btAveragePrice(load, s, P));
    return Wk.clamp(`Matching to your bills, ${s}`, "×",
      Wk.quotient("Your use ÷ our estimate", "×", kwh, model, { dp: 3 }), 0.2, 5, { dp: 3,
        note: "Every part of the estimated use is scaled by this, so the estimate's bill equals yours." });
  };
  const w = fromBill(calib.winter, "winter");
  const su = fromBill(calib.summer, "summer");
  if (w == null && su == null) return done(false, null);
  nodes.winter = w || su; nodes.summer = su || w;
  const mid = Wk.quotient("Matching to your bills, autumn and spring", "×",
    Wk.sum("Winter + summer", "×", [nodes.winter, nodes.summer]), btIn("Two seasons", 2, ""), { dp: 3,
      note: "Halfway between the winter and summer matches." });
  nodes.autumn = mid; nodes.spring = mid;
  return done(true, "bills");
}

const BT_HOUR_COLS = (withB) => [
  { key: "h", label: "Hour" }, { key: "use", label: "Use", unit: "kWh", dp: 2 },
  { key: "solar", label: "Solar", unit: "kWh", dp: 2 }, { key: "direct", label: "Used directly", unit: "kWh", dp: 2 },
].concat(withB ? [{ key: "fromB", label: "From battery", unit: "kWh", dp: 2 }] : []).concat([
  { key: "imp", label: "From the grid", unit: "kWh", dp: 2 }, { key: "exp", label: "Exported", unit: "kWh", dp: 2 },
  { key: "buy", label: "Price", unit: "$/kWh", dp: 3 }, { key: "sell", label: "Export price", unit: "$/kWh", dp: 3 }]);
const BT_COST_COLS = (col, label, withB) => BT_HOUR_COLS(withB).concat([{ key: col, label, unit: "$", dp: 2 }]);
const btNear = (a, b) => Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(a), Math.abs(b));
const btRowOk = r => btNear(r.useCost, r.use * r.buy) && btNear(r.impCost, r.imp * r.buy) && btNear(r.expPay, r.exp * r.sell);

// Run every season through the day simulator; bills and kWh with and
// without solar (and battery). gen: [season][24] kW in BT_SEASONS order.
function btRun(gen, load, cal, tariff, batteryKwh, seasonDays) {
  const batt = Object.assign({}, typeof BATTERY_DEFAULTS !== "undefined" ? BATTERY_DEFAULTS : {
    kw: 5, round_trip_pct: 90, reserve_pct: 10, cost_per_kwh: 1000, life_years: 15 },
    { enabled: batteryKwh > 0, kwh: batteryKwh || 0 });
  const zero = new Array(24).fill(0);
  const P = btPriceNodes(tariff);
  const days = {};
  BT_SEASONS.forEach((s, i) => { days[s] = btIn(`Days of ${s}`, seasonDays ? seasonDays[i] : BT_SEASON_DAYS[s], "days"); });
  const res = { seasons: {}, year: {}, nodes: {} };
  const perSeason = {};
  let socB = 0;
  for (let pass = 0; pass < 2; pass++) {
    BT_SEASONS.forEach((s, i) => {
      const L = btSeasonLoad(load, s, cal.scale[s]);
      const G = gen && gen[i] ? gen[i] : zero;
      const solar = simulateDay(G, L, { enabled: false, kwh: 0, kw: 0, round_trip_pct: 100, reserve_pct: 0 }, 0);
      const withB = simulateDay(G, L, batt, socB);
      socB = withB.socEnd;
      if (pass === 0) return;   // the first pass only settles the battery's charge
      perSeason[s] = { L, G, solar, withB };
    });
  }
  const seasonUnit = u => u === "$/yr" ? "$" : u === "kWh/yr" ? "kWh" : u;
  const yearOf = (label, unit, f) => Wk.sum(label, unit,
    BT_SEASONS.map(s => Wk.product(`${BT_CAP(s)} (${days[s].value} days)`, seasonUnit(unit), [f(s), days[s]])));
  const hourNote = "Hour by hour: the sun serves the home first; what's left is exported (and fills the battery first, " +
                   `if there is one). The rest comes from the grid. Peak hours (${btPeakText()}) use the peak prices.`;
  const S = {};
  for (const s of BT_SEASONS) {
    const { L, G, solar, withB } = perSeason[s];
    const use = Wk.product(`Use, a ${s} day`, "kWh/day", [btModelledDay(load, s), cal.nodes[s]]);
    const mk = (sim, wb) => L.map((l, h) => {
      const r = { h: `${String(h).padStart(2, "0")}:00`, use: l, solar: G[h], direct: Math.min(G[h], l),
                  imp: sim.impH[h], exp: sim.expH[h], buy: P.buyAt(h), sell: P.sellAt(h) };
      if (wb) r.fromB = sim.selfH[h] - r.direct;
      r.useCost = r.use * r.buy; r.impCost = r.imp * r.buy; r.expPay = r.exp * r.sell;
      return r;
    });
    const rows = mk(solar, false), rowsB = mk(withB, true);
    const kids = [P.peak, P.off, P.peakSell, P.offSell];
    const tab = (label, unit, r, col, wb, cols) => Wk.table(label, unit, cols || BT_HOUR_COLS(wb), r, col,
      { dp: unit === "$/day" ? 2 : 1, note: hourNote, rowCheck: btRowOk, children: unit === "$/day" ? kids : [] });
    const made = tab(`Solar made, a ${s} day`, "kWh/day", rows, "solar", false);
    const exp = tab(`Exported, a ${s} day`, "kWh/day", rows, "exp", false);
    const expB = tab(`Exported with a battery, a ${s} day`, "kWh/day", rowsB, "exp", true);
    const imp = tab(`From the grid with solar, a ${s} day`, "kWh/day", rows, "imp", false);
    const impB = tab(`From the grid with a battery, a ${s} day`, "kWh/day", rowsB, "imp", true);
    const nowDay = Wk.sum(`Bill a ${s} day, today`, "$/day", [
      tab("Electricity, each hour at its price", "$/day", rows, "useCost", false, BT_COST_COLS("useCost", "Cost", false)), P.daily]);
    const solarDay = Wk.diff(`Bill a ${s} day, with solar`, "$/day",
      Wk.sum("Electricity from the grid + daily charge", "$/day", [
        tab("From the grid, each hour at its price", "$/day", rows, "impCost", false, BT_COST_COLS("impCost", "Cost", false)), P.daily]),
      tab("Paid for exports, each hour at its price", "$/day", rows, "expPay", false, BT_COST_COLS("expPay", "Paid", false)));
    const battDay = Wk.diff(`Bill a ${s} day, with solar and a battery`, "$/day",
      Wk.sum("Electricity from the grid + daily charge", "$/day", [
        tab("From the grid, each hour at its price", "$/day", rowsB, "impCost", true, BT_COST_COLS("impCost", "Cost", true)), P.daily]),
      tab("Paid for exports, each hour at its price", "$/day", rowsB, "expPay", true, BT_COST_COLS("expPay", "Paid", true)));
    const month = (label, d) => Wk.product(label, "$/month", [d, P.month]);
    S[s] = { use, made, imp, exp, impB, expB, nowDay, solarDay, battDay,
             nowMonth: month(`${BT_CAP(s)} bill a month, today`, nowDay),
             solarMonth: month(`${BT_CAP(s)} bill a month, with solar`, solarDay),
             battMonth: month(`${BT_CAP(s)} bill a month, with solar and a battery`, battDay) };
    const f = Wk.same(rows.reduce((t, r) => t + r.use, 0), use, `Hourly and daily use, ${s}`, 1e-6);
    if (f) use.flags.push(f);
    res.seasons[s] = {
      loadKwh: use.value, genKwh: made.value,
      nowMonth: S[s].nowMonth.value, solarMonth: S[s].solarMonth.value, battMonth: S[s].battMonth.value,
      selfShare: made.value > 0 ? (made.value - exp.value) / made.value : 0,
      load: L, gen: G, solarImp: solar.impH, battImp: withB.impH,
    };
  }
  const N = res.nodes;
  N.prices = P;
  N.seasons = S;
  N.useYear = yearOf("Electricity used a year", "kWh/yr", s => S[s].use);
  N.genYear = yearOf("Solar made a year", "kWh/yr", s => S[s].made);
  N.nowBill = yearOf("Bills a year, today", "$/yr", s => S[s].nowDay);
  N.solarBill = yearOf("Bills a year, with solar", "$/yr", s => S[s].solarDay);
  N.battBill = yearOf("Bills a year, with solar and a battery", "$/yr", s => S[s].battDay);
  N.solarImport = yearOf("From the grid a year, with solar", "kWh/yr", s => S[s].imp);
  N.solarExport = yearOf("Exported a year", "kWh/yr", s => S[s].exp);
  N.battImport = yearOf("From the grid a year, with a battery", "kWh/yr", s => S[s].impB);
  N.battExport = yearOf("Exported a year, with a battery", "kWh/yr", s => S[s].expB);
  N.solarSave = yearOf("Bill savings a year, with solar", "$/yr",
    s => Wk.diff(`Saving a ${s} day`, "$/day", S[s].nowDay, S[s].solarDay));
  N.battSave = yearOf("Bill savings a year, with solar and a battery", "$/yr",
    s => Wk.diff(`Saving a ${s} day`, "$/day", S[s].nowDay, S[s].battDay));
  // export income a year: the "paid for exports" step of each season's bill
  const expPaid = (key) => yearOf("Paid for exports a year", "$/yr", s => S[s][key].children[1]);
  N.solarExportDollars = expPaid("solarDay");
  N.battExportDollars = expPaid("battDay");
  // the home's average day across the year, for the consumption figures
  const avgRows = [];
  const tot = BT_SEASONS.reduce((t, s) => t + days[s].value, 0);
  for (let h = 0; h < 24; h++) {
    const kw = BT_SEASONS.reduce((t, s) => t + perSeason[s].L[h] * days[s].value, 0) / tot;
    avgRows.push({ h: `${String(h).padStart(2, "0")}:00`, use: kw, day: h >= 9 && h < 17 ? 1 : 0, dayUse: h >= 9 && h < 17 ? kw : 0 });
  }
  const cols = [{ key: "h", label: "Hour" }, { key: "use", label: "Average use", unit: "kWh", dp: 2 },
                { key: "dayUse", label: "In the daytime (9–5)", unit: "kWh", dp: 2 }];
  N.avgDay = Wk.table("Average day's use, across the year", "kWh/day", cols, avgRows, "use");
  N.dayKwh = Wk.table("Used in the daytime, 9am–5pm", "kWh/day", cols, avgRows, "dayUse");
  N.dayKw = Wk.quotient("Average daytime load", "kW", N.dayKwh, btIn("Daytime hours", 8, "h"), { dp: 2,
    note: "The home's average draw while the sun is highest: the load solar can serve directly." });
  N.dayShare = Wk.quotient("Share of the day's use in the daytime", "share", N.dayKwh, N.avgDay, { dp: 0 });
  res.year = { nowKwh: N.useYear.value, nowBill: N.nowBill.value, solarImport: N.solarImport.value,
               solarExport: N.solarExport.value, solarBill: N.solarBill.value, battImport: N.battImport.value,
               battExport: N.battExport.value, battBill: N.battBill.value, gen: N.genYear.value };
  return res;
}

// Money over the system's life, year by year: bill savings (power prices
// rising, export rates falling, panels ageing), less replacements, in real
// (today's) or nominal dollars.
//   cost, save, exportD: Workings nodes (or numbers, for the tests)
//   opts.replacements: [{year (0-based), node, label}]
function btReturns(cost, save, exportD, opts) {
  const o = opts || {};
  const E = Object.assign({ elec_inflation_pct: 4, export_decline_pct: 2, degradation_pct: 0.5,
                            life_years: 30, real_prices: true, cpi_pct: 2 },
                          typeof econ !== "undefined" ? econ : {});
  const asNode = (v, label, unit) => (v && typeof v === "object") ? v : btIn(label, v || 0, unit);
  const costN = asNode(cost, "Cost", "$"), saveN = asNode(save, "Bill savings in the first year", "$/yr");
  const expN = asNode(exportD, "Export income in the first year", "$/yr");
  const repl = (o.replacements || []).filter(r => r.year < E.life_years);
  const rise = 1 + E.elec_inflation_pct / 100, dec = 1 - E.export_decline_pct / 100;
  const deg = 1 - E.degradation_pct / 100, cpi = 1 + E.cpi_pct / 100, real = !!E.real_prices;
  const retail = saveN.value - expN.value;
  const rows = [];
  let cum = 0, payback = null;
  for (let y = 0; y < 60; y++) {
    const defl = real ? Math.pow(cpi, y) : 1;
    const retailY = retail * Math.pow(rise, y) * Math.pow(deg, y) / defl;
    const exportY = expN.value * Math.pow(dec, y) * Math.pow(deg, y) / defl;
    const saving = retailY + exportY;
    const out = repl.filter(r => r.year === y).reduce((t, r) => t + r.node.value * (real ? 1 : Math.pow(cpi, y)), 0);
    cum += saving - out;
    rows.push({ year: y + 1, retailY, exportY, saving, out: -out, net: saving - out, cum, life: y < E.life_years ? 1 : 0 });
    if (payback === null && cum >= costN.value) payback = y + 1 - (cum - costN.value) / Math.max(saving, 1e-9);
  }
  const money = real ? "today's dollars" : "dollars of the day (nominal)";
  const rates = [
    btIn("Power prices rise", E.elec_inflation_pct / 100, "share", { dp: 1, source: "Advanced pricing." }),
    btIn("Export rates fall", E.export_decline_pct / 100, "share", { dp: 1, source: "Advanced pricing." }),
    btIn("Panels lose output", E.degradation_pct / 100, "share", { dp: 1, source: "A typical panel warranty." }),
  ].concat(real ? [btIn("General inflation (to today's dollars)", E.cpi_pct / 100, "share",
    { dp: 1, source: "Advanced pricing (real prices). RBNZ targets 1–3%." })] : []);
  const note = `Each year: the power you no longer buy at prices rising ${E.elec_inflation_pct}% a year, plus export income ` +
               `at rates falling ${E.export_decline_pct}% a year, both shrinking ${E.degradation_pct}% a year as the panels age` +
               (real ? `, then divided by ${E.cpi_pct}% a year of general inflation, so it is in today's dollars.` : ", in the dollars of each year.");
  const cols = [{ key: "year", label: "Year" }, { key: "saving", label: "Bill savings", unit: "$" },
                { key: "out", label: "Replacements", unit: "$" }, { key: "cum", label: "Running total", unit: "$" }];
  const lifeRows = rows.slice(0, E.life_years);
  const kids = [saveN, expN].concat(rates);
  const lifetime = Wk.table(`Savings over the panels' ${E.life_years}-year life`, "$", cols, lifeRows, "saving",
    { children: kids, note: note + ` In ${money}.` });
  const replN = Wk.table("Replacements over the panels' life", "$",
    cols, lifeRows, "out", { children: repl.map(r => r.node),
      note: "Parts that wear out before the panels: the inverter, and a battery if there is one. " +
            (real ? "Priced in today's dollars." : `Priced at today's cost rising ${E.cpi_pct}% a year.`) });
  const net = Wk.sum(`Net savings over ${E.life_years} years`, "$", [
    lifetime, replN, Wk.product("Less the cost", "$", [costN, btIn("", -1, "")])]);
  if (crossMark(rows, payback)) { /* marks the payback row */ }
  const pb = Wk.given("Pays back in", payback, "yrs", [costN,
    Wk.table("Year by year", "$", cols, rows, "net", { note, children: rates })], {
      formula: payback == null ? "the running total never reaches the cost within 60 years"
        : `the running total passes the cost of ${Wk.fmt(costN.value, "$")} during year ${Math.ceil(payback)}`,
      check: () => { let c = 0;
        for (const r of rows) { c += r.net; if (c >= costN.value) return r.year - (c - costN.value) / Math.max(r.saving, 1e-9); }
        return null; } });
  if (payback == null) pb.flags.push("Doesn't pay back within 60 years at these prices.");
  // the yearly rate of return: the interest rate at which paying the cost
  // now and getting each year's net saving back comes out even
  const npv = r => -costN.value + lifeRows.reduce((t, row, i) => t + row.net / Math.pow(1 + r, i + 1), 0);
  const solve = () => {
    let lo = -0.99, hi = 1.0;
    if (npv(lo) * npv(hi) > 0) return null;
    for (let i = 0; i < 200; i++) { const m = (lo + hi) / 2; if (npv(lo) * npv(m) <= 0) hi = m; else lo = m; }
    return (lo + hi) / 2;
  };
  const r = solve();
  const irr = Wk.given("Return per year", r, "share", [costN, lifetime, replN], {
    dp: 1,
    formula: r == null ? "no rate makes it come out even" :
      `the interest rate at which ${Wk.fmt(costN.value, "$")} paid now and each year's savings received come out even`,
    note: `Like a savings account paying this rate each year for ${E.life_years} years, ` +
          (real ? "on top of inflation (real)." : "before inflation (nominal).") +
          (r == null ? "" : ` Check: at ${Wk.fmt(r, "share", 2)} the savings, each discounted back to today, add up to the cost (${Wk.fmt(npv(r) + costN.value, "$")} vs ${Wk.fmt(costN.value, "$")}).`),
    check: () => solve() });
  // the two halves of the lifetime savings, for pricing single panels: one
  // that covers power used on site earns the first, one that exports the second
  const lifeRetail = lifeRows.reduce((t, row) => t + row.retailY, 0);
  const lifeExport = lifeRows.reduce((t, row) => t + row.exportY, 0);
  return { payback, lifetime: lifetime.value, net: net.value, irr: r, years: E.life_years, real,
           lifeRetail, lifeExport,
           nodes: { payback: pb, lifetime, replacements: replN, net, irr, first: saveN } };
}
function crossMark(rows, payback) {
  if (payback == null) return false;
  const r = rows[Math.ceil(payback) - 1];
  if (r) r._mark = true;
  return true;
}

// Everything the panel shows, for one building.
//   ctx:    { lat, roofM2, kwp, gen ([season][24] kW), seasonDays, genNode, kwpNode }
//   prof:   the household answers (type, people, water, heating, evs, battery, ...)
//   calib:  {winter, summer} bills | {total} kWh a year | null
//   tariff: {peak, offpeak, peakSell, offpeakSell, daily} (or flat {buy, sell, daily})
//   sys:    {solarPerKw, battPerKwh} -- the user's own prices, or null for ours
function btEvaluate(ctx, prof, calib, tariff, sys) {
  const load = btBuildLoad(prof, { lat: ctx.lat, roofM2: ctx.roofM2 });
  const cal = btCalibration(load, calib, tariff, ctx.seasonDays);
  const run = btRun(ctx.gen, load, cal, tariff, prof.battery || 0, ctx.seasonDays);
  const N = run.nodes, Y = run.year;
  const sy = sys || {};
  const E = typeof econ !== "undefined" ? econ : { life_years: 30, inverter_replace_year: 15, inverter_cost_pct: 18 };
  const kwpN = ctx.kwpNode || btIn("System size", ctx.kwp || 0, "kW", { source: "The panels on the map." });
  const rate = sy.solarPerKw > 0
    ? btIn("Solar cost per kW", sy.solarPerKw, "$/kW", { source: "You entered it (System pricing)." })
    : Wk.given("Solar cost per kW, for a system this size", typeof costPerKw === "function" ? costPerKw(kwpN.value) : 2000, "$/kW",
      [kwpN], { formula: typeof econ !== "undefined"
        ? econ.cost_tiers.map(t => `${t.label}: $${t.rate.toLocaleString()}/kW`).join("; ") : "flat $2,000/kW",
        source: "Indicative NZ installed prices including GST.",
        check: v => typeof costPerKw === "function" ? costPerKw(v[0]) : 2000 });
  const solarCostN = Wk.product("Solar costs", "$", [kwpN, rate]);
  const battDef = (typeof BATTERY_DEFAULTS !== "undefined") ? BATTERY_DEFAULTS : { cost_per_kwh: 1000, life_years: 15 };
  const battRate = btIn("Battery cost per kWh", sy.battPerKwh > 0 ? sy.battPerKwh : battDef.cost_per_kwh, "$/kWh",
    { source: sy.battPerKwh > 0 ? "You entered it (System pricing)." : "Indicative NZ installed price including GST." });
  const battCostN = Wk.product("Battery costs", "$", [
    btIn("Battery size", prof.battery || 0, "kWh", { dp: 1, source: "Your choice." }), battRate]);
  const allCostN = Wk.sum("Solar and battery cost", "$", [solarCostN, battCostN]);
  const inverter = { year: E.inverter_replace_year, label: "Inverter",
    node: Wk.product(`Inverter replaced in year ${E.inverter_replace_year + 1}`, "$", [solarCostN,
      btIn("Inverter, as a share of the solar cost", E.inverter_cost_pct / 100, "share", { source: "Assumptions & pricing." })]) };
  const battery = { year: battDef.life_years, label: "Battery",
    node: Wk.product(`Battery replaced in year ${battDef.life_years + 1}`, "$", [battCostN, btIn("Once", 1, "")]) };
  const solarRet = btReturns(solarCostN, N.solarSave, N.solarExportDollars, { replacements: [inverter] });
  const battRet = prof.battery > 0
    ? btReturns(allCostN, N.battSave, N.battExportDollars, { replacements: [inverter, battery] })
    : null;
  const yearParts = {}, partNodes = {};
  for (const part of Object.keys(load.nodes)) {
    partNodes[part] = Wk.sum(BT_PART_STYLE[part] ? BT_PART_STYLE[part].label : part, "kWh/yr",
      BT_SEASONS.map(s => {
        const nd = ctx.seasonDays ? ctx.seasonDays[BT_SEASONS.indexOf(s)] : BT_SEASON_DAYS[s];
        return Wk.product(`${BT_CAP(s)} (${nd} days)`, "kWh", [load.nodes[part][s], cal.nodes[s], btIn(`Days of ${s}`, nd, "days")]);
      }));
    yearParts[part] = partNodes[part].value;
  }
  // the bill tool's year of sun must be the roof's own generation figure
  if (ctx.genNode && ctx.gen) {
    const f = Wk.same(N.genYear, ctx.genNode, "The bills' solar and the roof's generation", 1e-6);
    if (f) N.genYear.flags.push(f);
    N.genYear.children.push(ctx.genNode);
  }
  return { load, cal, run, solarCost: solarCostN.value, battCost: battCostN.value,
           solarSave: N.solarSave.value, battSave: N.battSave.value, solarRet, battRet,
           yearParts, useKwh: Y.nowKwh, genKwh: Y.gen,
           nodes: Object.assign({}, N, { solarCost: solarCostN, battCost: battCostN, allCost: allCostN,
                                         solarRate: rate, battRate, parts: partNodes,
                                         gen: ctx.genNode || N.genYear, kwp: kwpN }) };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { BT_SEASONS, BT_DEFAULTS, BT_SHAPES, btHdd, btBuildLoad, btSeasonLoad,
                     btDailyKwh, btCalibration, btRun, btReturns, btEvaluate, btModelledDay };
}

// ---------------------------------------------------------------------------
// The panel. Everything below touches the page; everything above does not.
// ---------------------------------------------------------------------------
//
// THE BUILDING ANALYSIS (Josh's layout, 17 Oct): four headline figures, then
// the assumptions behind them -- bills before and after, prices, how the
// home uses power, what the system costs -- each one editable where it is an
// input and clickable (its workings) where it is a result. Every number
// shown is a Workings node's value; nothing here does arithmetic of its own.

const BT_CSS = `
.sx { --sx-fill: rgba(255,255,255,.055); --sx-fill-2: rgba(118,118,128,.20); --sx-sep: rgba(255,255,255,.09);
  --sx-ink: #f5f5f7; --sx-ink-2: rgba(235,235,245,.62); --sx-ink-3: rgba(235,235,245,.38);
  --sx-tint: #ffd60a; --sx-good: #30d158;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif;
  color: var(--sx-ink); margin-top: 14px; }
.sx .wk-num { text-decoration: none; }
.sx-summary { display: grid; grid-template-columns: 1fr 1fr; background: var(--sx-fill); border-radius: 14px; overflow: hidden; }
.sx-cell { padding: 13px 14px 12px; min-width: 0; cursor: pointer; transition: background .12s; }
.sx-cell:nth-child(odd) { border-right: 0.5px solid var(--sx-sep); }
.sx-cell:nth-child(-n+2) { border-bottom: 0.5px solid var(--sx-sep); }
.sx-cell:hover, .sx-cell:focus-visible { background: rgba(255,255,255,.04); outline: none; }
.sx-cell .l { font-size: 12.5px; color: var(--sx-ink-2); }
.sx-cell .v { font-size: 22px; font-weight: 650; letter-spacing: -.3px; font-variant-numeric: tabular-nums; margin-top: 3px; white-space: nowrap; }
.sx-cell .s { font-size: 11.5px; color: var(--sx-ink-3); margin-top: 1px; }
.sx-cell.hero .v { color: var(--sx-good); font-size: 26px; }
.sx-h { font-size: 19px; font-weight: 650; letter-spacing: -.2px; margin: 26px 2px 4px; }
.sx-sub { font-size: 13px; font-weight: 600; color: var(--sx-ink-2); margin: 18px 4px 7px; }
.sx-group { background: var(--sx-fill); border-radius: 12px; overflow: hidden; }
.sx-row { display: flex; align-items: center; gap: 10px; min-height: 44px; padding: 6px 14px; position: relative; font-size: 14.5px; }
.sx-row + .sx-row::before { content: ""; position: absolute; top: 0; left: 14px; right: 0; border-top: 0.5px solid var(--sx-sep); }
.sx-row .lbl { flex: 1; min-width: 0; }
.sx-row .lbl small { display: block; font-size: 12px; color: var(--sx-ink-3); margin-top: 1px; }
.sx-row .val { font-variant-numeric: tabular-nums; color: var(--sx-ink-2); white-space: nowrap; }
.sx-row .val b { color: var(--sx-ink); font-weight: 600; }
.sx-row.go { cursor: pointer; }
.sx-row.go:hover, .sx-row.go:focus-visible { background: rgba(255,255,255,.035); outline: none; }
.sx-row.go::after { content: "›"; color: var(--sx-ink-3); font-size: 19px; line-height: 1; margin-left: 2px; }
.sx-row.total .lbl, .sx-row.total .val b { font-weight: 650; }
.sx-field { display: inline-flex; align-items: baseline; gap: 3px; background: var(--sx-fill-2); border-radius: 8px; padding: 5px 9px; color: var(--sx-ink-2); font-size: 13px; }
.sx-field input { width: 64px; background: none; border: 0; color: var(--sx-ink); font: 600 15px/1.2 inherit; font-family: inherit;
  text-align: right; font-variant-numeric: tabular-nums; padding: 0; -moz-appearance: textfield; }
.sx-field input::-webkit-outer-spin-button, .sx-field input::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
.sx-field:focus-within { box-shadow: 0 0 0 2px var(--sx-tint); }
.sx-field input:focus { outline: none; }
.sx-field.est input { color: var(--sx-ink-2); font-weight: 500; }
.sx-info { background: none; border: 0; color: var(--sx-ink-3); font-size: 13px; cursor: pointer; padding: 2px 4px; border-radius: 6px; }
.sx-info:hover, .sx-info:focus-visible { color: var(--sx-tint); outline: none; }
.sx-bills .sx-row { display: grid; grid-template-columns: 1fr 108px 108px; }
.sx-bills .sx-row .val, .sx-bills .sx-row .sx-field { justify-self: end; }
.sx-bills .head { min-height: 30px; font-size: 12px; color: var(--sx-ink-3); }
.sx-bills .head span:not(:first-child) { justify-self: end; padding-right: 2px; }
.sx-bills .after { cursor: pointer; border-radius: 8px; padding: 4px 6px; margin-right: -6px; }
.sx-bills .after:hover, .sx-bills .after:focus-visible { background: rgba(255,255,255,.05); outline: none; }
.sx-bills .after b { color: var(--sx-good); font-weight: 650; font-size: 15px; }
.sx-bills .after small { color: var(--sx-ink-3); font-size: 11.5px; }
.sx-foot { font-size: 12px; color: var(--sx-ink-3); margin: 7px 4px 0; line-height: 1.45; }
.sx-foot .ok { color: var(--sx-good); }
.sx-foot .warn { color: #ff9f0a; }
.sx-seg { display: inline-flex; background: var(--sx-fill-2); border-radius: 9px; padding: 2px; gap: 0; flex-wrap: wrap; justify-content: flex-end; }
.sx-seg button { background: none; border: 0; color: var(--sx-ink); font: 500 13px/1 inherit; font-family: inherit; padding: 6px 10px; border-radius: 7px; cursor: pointer; }
.sx-seg button.on { background: #636366; box-shadow: 0 1px 3px rgba(0,0,0,.35); font-weight: 600; }
.sx-seg button:focus-visible { outline: 2px solid var(--sx-tint); outline-offset: 1px; }
.sx-switch { position: relative; width: 44px; height: 27px; flex: none; }
.sx-switch input { position: absolute; inset: 0; opacity: 0; margin: 0; cursor: pointer; }
.sx-switch i { position: absolute; inset: 0; background: var(--sx-fill-2); border-radius: 14px; transition: background .18s; pointer-events: none; }
.sx-switch i::after { content: ""; position: absolute; top: 2px; left: 2px; width: 23px; height: 23px; border-radius: 50%; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.4); transition: transform .18s; }
.sx-switch input:checked + i { background: var(--sx-good); }
.sx-switch input:checked + i::after { transform: translateX(17px); }
.sx-switch input:focus-visible + i { box-shadow: 0 0 0 2px var(--sx-tint); }
.sx-step { display: inline-flex; align-items: center; background: var(--sx-fill-2); border-radius: 9px; }
.sx-step button { background: none; border: 0; color: var(--sx-ink); width: 34px; height: 30px; font-size: 17px; cursor: pointer; border-radius: 8px; }
.sx-step button:focus-visible { outline: 2px solid var(--sx-tint); }
.sx-step b { min-width: 22px; text-align: center; font-weight: 600; font-variant-numeric: tabular-nums; }
.sx-dis { margin-top: 10px; }
.sx-dis > summary { list-style: none; cursor: pointer; display: flex; align-items: center; gap: 8px; background: var(--sx-fill);
  border-radius: 12px; padding: 11px 14px; font-size: 14.5px; }
.sx-dis > summary::-webkit-details-marker { display: none; }
.sx-dis > summary .t { flex: 1; }
.sx-dis > summary .sum { color: var(--sx-ink-3); font-size: 12.5px; text-align: right; }
.sx-dis > summary::after { content: "›"; color: var(--sx-ink-3); font-size: 19px; line-height: 1; transition: transform .15s; }
.sx-dis[open] > summary::after { transform: rotate(90deg); }
.sx-dis[open] > summary { border-radius: 12px 12px 0 0; }
.sx-dis[open] > .sx-group:first-of-type { border-radius: 0 0 12px 12px; }
.sx-dis > summary:focus-visible { outline: 2px solid var(--sx-tint); outline-offset: 1px; }
.sx-use { padding: 10px 14px 12px; }
.sx-use .bar { display: flex; height: 8px; border-radius: 4px; overflow: hidden; background: var(--sx-fill-2); }
.sx-use .key { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; color: var(--sx-ink-2); margin-top: 8px; }
.sx-use .key span { cursor: pointer; }
.sx-use .key i { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 5px; }
.sx-glance { margin-top: 10px; }
.sx-glance.sx-cost { margin: 0 0 10px; }
.sx-billrow { gap: 8px; }
.sx-billrow .sx-field input { width: 52px; }
.sx-arrow { color: var(--sx-ink-3); }
.sx-billrow .after { cursor: pointer; border-radius: 8px; padding: 4px 6px; margin-right: -6px; white-space: nowrap; font-variant-numeric: tabular-nums; }
.sx-billrow .after:hover, .sx-billrow .after:focus-visible { background: rgba(255,255,255,.05); outline: none; }
.sx-billrow .after b { color: var(--sx-good); font-weight: 650; }
.sx-billrow .after small { color: var(--sx-ink-3); font-size: 11.5px; }
.side-panel:not(.compact) .sx-glance-foot { display: none; }
.sx-peak .lbl small { color: var(--sx-ink-2); font-variant-numeric: tabular-nums; }
.sx-range { position: relative; width: 150px; height: 28px; flex: none; }
.sx-range::before { content: ""; position: absolute; left: 0; right: 0; top: 12px; height: 4px; border-radius: 2px; background: var(--sx-fill-2); }
.sx-range::after { content: ""; position: absolute; left: var(--lo); right: calc(100% - var(--hi)); top: 12px; height: 4px; border-radius: 2px; background: var(--sx-tint); }
.sx-range input { position: absolute; left: 0; top: 0; width: 100%; height: 28px; margin: 0; background: none; pointer-events: none; -webkit-appearance: none; appearance: none; z-index: 1; }
.sx-range input::-webkit-slider-runnable-track { background: none; height: 28px; }
.sx-range input::-moz-range-track { background: none; }
.sx-range input::-webkit-slider-thumb { -webkit-appearance: none; pointer-events: auto; width: 22px; height: 22px; margin-top: 3px; border-radius: 50%; background: #fff; box-shadow: 0 1px 4px rgba(0,0,0,.45); cursor: grab; }
.sx-range input::-moz-range-thumb { pointer-events: auto; width: 22px; height: 22px; border: 0; border-radius: 50%; background: #fff; box-shadow: 0 1px 4px rgba(0,0,0,.45); cursor: grab; }
.sx-range input:focus-visible::-webkit-slider-thumb { box-shadow: 0 0 0 3px var(--sx-tint); }
.side-panel:not(.compact) .sx-glance { display: none; }
.sx-more .sx-h:first-child { margin-top: 22px; }
@media (max-width: 1250px) { .sx-bills .sx-row { grid-template-columns: 1fr 96px 96px; } }
@media (max-width: 420px) { .sx-summary { grid-template-columns: 1fr 1fr; } .sx-cell .v { font-size: 19px; } }
@media (prefers-reduced-motion: reduce) { .sx * { transition: none !important; } }
`;

const BT_PART_STYLE = {
  appliance: { label: "Appliances & lights", colour: "#8fb3ff" },
  water: { label: "Hot water", colour: "#5fd0c0" },
  heat: { label: "Heating", colour: "#ff9b6b" },
  ev: { label: "Electric cars", colour: "#c79bff" },
};

function btMoney(v) {
  const neg = v < 0;
  const s = "$" + Math.round(Math.abs(v)).toLocaleString();
  return neg ? "-" + s : s;
}

function btStoreKey(bid) { return "billtool:v2:" + bid; }
function btLoadState(bid) {
  try { const s = localStorage.getItem(btStoreKey(bid)); return s ? JSON.parse(s) : null; }
  catch (e) { return null; }
}
function btSaveState(bid, st) {
  try { localStorage.setItem(btStoreKey(bid), JSON.stringify(st)); } catch (e) { /* private window */ }
}

// The plan's prices for a home or a business, from the map's one set of
// assumptions (economics.js `econ`): editing them here edits them for the
// whole map, so every figure everywhere is priced the same way.
function btTariffFromEcon(isBiz) {
  const E = typeof econ !== "undefined" ? econ : {};
  const p = isBiz ? "biz" : "home";
  return { peak: E[p + "_peak_c"], offpeak: E[p + "_offpeak_c"], peakSell: E[p + "_peak_sell_c"],
           offpeakSell: E[p + "_offpeak_sell_c"], daily: E[p + "_daily"] };
}

// box: the element to fill. ctx: { buildingId, isBusiness, lat, roofM2,
// kwp, kwpNode, genNode, gen ([season][24] kW, BT_SEASONS order),
// seasonDays, onEcon (call after editing `econ`), onResult(r) }
//
// THE LOOK (18 Oct, Josh: "more intuitive and better looking ... like Apple
// software"): one summary card with the yearly saving leading; then the
// assumptions as grouped lists -- a label on the left, the value or an
// editable field on the right, hairline separators -- with the rarely
// touched settings folded away behind a one-line summary. Plain words, one
// accent, tap any result to see its workings.
function renderBillTool(box, ctx) {
  if (!document.getElementById("bt-css")) {
    const st = document.createElement("style");
    st.id = "bt-css"; st.textContent = BT_CSS;
    document.head.appendChild(st);
  }
  const saved = btLoadState(ctx.buildingId) || {};
  const state = {
    type: saved.type || (ctx.isBusiness ? "business" : "home"),
    home: Object.assign({}, BT_DEFAULTS.home, saved.home || {}),
    business: Object.assign({}, BT_DEFAULTS.business, saved.business || {}),
    calib: saved.calib || null,          // {winter, summer} | {total} | null
    sys: Object.assign({ solarPerKw: null, battPerKwh: null }, saved.sys || {}),
  };
  const save = () => btSaveState(ctx.buildingId, state);
  const seg = (key, options, current, label) =>
    `<div class="sx-seg" role="group" aria-label="${label || key}" data-key="${key}">` + options.map(([v, l]) =>
      `<button type="button" data-v="${v}" class="${String(v) === String(current) ? "on" : ""}" aria-pressed="${String(v) === String(current)}">${l}</button>`).join("") + `</div>`;
  const hours = ([a, b]) => { const f = h => h % 24 === 0 ? "midnight" : (h % 12 || 12) + (h < 12 ? "am" : "pm"); return `${f(a)}–${f(b)}`; };

  let last = null;
  const draw = () => {
    const isBiz = state.type === "business";
    const prof = Object.assign({ type: state.type }, isBiz ? state.business : state.home);
    const tariff = btTariffFromEcon(isBiz);
    const haveGen = !!(ctx.gen && ctx.gen.length === 4 && ctx.kwp > 0);
    const r = btEvaluate({ lat: ctx.lat, roofM2: ctx.roofM2, kwp: ctx.kwp, kwpNode: ctx.kwpNode,
                           gen: haveGen ? ctx.gen : null, seasonDays: ctx.seasonDays, genNode: ctx.genNode },
                         prof, state.calib, tariff, state.sys);
    last = r;
    const N = r.nodes, S = N.seasons;
    const withBatt = prof.battery > 0;
    const ret = withBatt ? r.battRet : r.solarRet;
    const save1 = withBatt ? N.battSave : N.solarSave;
    const after = withBatt ? "battMonth" : "solarMonth";
    const E = typeof econ !== "undefined" ? econ : {};
    const pfx = isBiz ? "biz" : "home";
    const calibBy = r.cal.by;
    const money = n => btMoney(n.value);
    const entered = state.calib && !state.calib.total
      ? [["summer", S.summer.nowMonth], ["winter", S.winter.nowMonth]].filter(([k]) => state.calib[k] > 0) : [];
    const off = entered.filter(([k, n]) => Math.abs(n.value - state.calib[k]) > 0.03 * state.calib[k]);
    // No "matched to your bill" note once a bill is entered: it was easy to
    // forget and read as a stamp of accuracy (Josh, 18 Oct). Only the hint
    // before anything is entered, and a warning when a bill cannot be matched.
    const matchNote = off.length
      ? `<span class="warn">Your ${off.map(([k]) => k).join(" and ")} bill is below the daily charge alone &mdash; check the prices.</span>`
      : (!entered.length && calibBy !== "total")
        ? "Today's bills are our estimate for this building. Type in your own and everything updates."
        : "";
    const P = isBiz ? state.business : state.home;
    const field = (id, value, attrs, pre, post, est) =>
      `<label class="sx-field${est ? " est" : ""}">${pre ? `<span>${pre}</span>` : ""}<input id="bt-${id}" type="number" inputmode="decimal" ${attrs} value="${value}">${post ? `<span>${post}</span>` : ""}</label>`;
    const row = (label, right, opts) => {
      const o = opts || {};
      return `<div class="sx-row${o.go ? " go" : ""}${o.cls ? " " + o.cls : ""}"${o.go ? ` data-wk="${o.go}" tabindex="0" role="button"` : ""}>` +
             `<span class="lbl">${label}${o.small ? `<small>${o.small}</small>` : ""}</span>${right}</div>`;
    };
    const val = (text, sub) => `<span class="val"><b>${text}</b>${sub ? ` ${sub}` : ""}</span>`;
    const billIn = s => {
      const mine = state.calib && !state.calib.total && state.calib[s] > 0;
      return field(`bill-${s}`, Math.round(S[s].nowMonth.value), `min="0" step="10" data-bill="${s}" aria-label="${s} bill today, dollars a month"`, "$", "", !mine);
    };
    // the corner card's bill rows: today's bill is typed in right there;
    // the with-solar bill opens how it is worked out
    const billPair = (s, label, sub) => {
      const mine = state.calib && !state.calib.total && state.calib[s] > 0;
      return `<div class="sx-row sx-billrow"><span class="lbl">${label}<small>${sub}</small></span>` +
        field(`billq-${s}`, Math.round(S[s].nowMonth.value), `min="0" step="10" data-bill="${s}" aria-label="${label} today, dollars a month"`, "$", "", !mine) +
        `<span class="sx-arrow" aria-hidden="true">→</span>` + afterBill(s) + `</div>`;
    };
    const afterBill = s => {
      const n = S[s][after];
      return `<span class="val after" data-wk="after-${s}" tabindex="0" role="button">` +
        (n.value < -0.5 ? `<b>${btMoney(-n.value)}</b> <small>credit</small>` : `<b>${btMoney(n.value)}</b>`) + `</span>`;
    };
    const price = (label, key) => row(label, field(key, +(E[key] || 0).toFixed(2), `min="0" step="0.5" data-econ="${key}"`, "", "c/kWh"));
    const totalUse = Object.values(r.yearParts).reduce((a, b) => a + b, 0) || 1;
    const parts = Object.entries(r.yearParts).filter(([, v]) => v > 1);
    const pbText = ret.payback == null ? `over ${ret.years} yrs · doesn't pay back` : `over ${ret.years} yrs · pays back in ${ret.payback.toFixed(1)}`;
    const priceSum = `${(E[pfx + "_peak_c"] || 0)}c peak · ${(E[pfx + "_offpeak_c"] || 0)}c off-peak · ${(E[pfx + "_offpeak_sell_c"] || 0)}c export`;

    const SX_SPLIT = "<!--sx-split-->";
    const html =
      `<div class="sx">` +
      // the corner card leads with what it costs (Josh, 18 Oct)
      `<div class="sx-group sx-glance sx-cost">` +
        row("Costs about", val(money(withBatt ? N.allCost : N.solarCost), `<small>${N.kwp.value.toFixed(1)} kW${withBatt ? " + battery" : ""}</small>`), { go: "allcost" }) +
      `</div>` +
      `<div class="sx-summary">` +
        `<div class="sx-cell hero" data-wk="save" tabindex="0" role="button"><div class="l">Bill savings</div>` +
          `<div class="v">${haveGen ? money(save1) : "–"}</div><div class="s">a year${withBatt ? ", with the battery" : ""}</div></div>` +
        `<div class="sx-cell" data-wk="lifetime" tabindex="0" role="button"><div class="l">Savings over ${ret.years} years</div>` +
          `<div class="v">${haveGen ? money(ret.nodes.lifetime) : "–"}</div><div class="s">the panels' life</div></div>` +
        `<div class="sx-cell" data-wk="net" tabindex="0" role="button"><div class="l">Net savings</div>` +
          `<div class="v">${haveGen ? money(ret.nodes.net) : "–"}</div><div class="s">${haveGen ? pbText : ""}</div></div>` +
        `<div class="sx-cell" data-wk="irr" tabindex="0" role="button"><div class="l">Return per year</div>` +
          `<div class="v">${haveGen && ret.irr != null ? Wk.fmt(ret.irr, "share", 1) : "–"}</div><div class="s">${ret.real ? "after inflation" : "before inflation"}</div></div>` +
      `</div>` +
      (haveGen ? "" : `<div class="sx-foot">Loading this roof's sun&hellip;</div>`) +
      // the corner card's few key facts (shown only while compact)
      `<div class="sx-group sx-glance">` +
        (haveGen ? billPair("winter", "Winter bill", "highest month") : "") +
        (haveGen ? billPair("summer", "Summer bill", "lowest month") : "") +
      `</div>` +
      (haveGen ? `<div class="sx-foot sx-glance-foot">A month: today → ${withBatt ? "with solar and battery" : "with solar"}.${matchNote ? " " + matchNote : ""}</div>` : "") +
      `</div>` + SX_SPLIT + `<div class="sx sx-more">` +

      `<div class="sx-h">Assumptions</div>` +
      `<div class="sx-sub">Power bills</div>` +
      `<div class="sx-group sx-bills">` +
        `<div class="sx-row head"><span></span><span>Today</span><span>${withBatt ? "Solar + battery" : "With solar"}</span></div>` +
        `<div class="sx-row"><span class="lbl">Summer<small>lowest month</small></span>${billIn("summer")}${haveGen ? afterBill("summer") : `<span class="val">–</span>`}</div>` +
        `<div class="sx-row"><span class="lbl">Winter<small>highest month</small></span>${billIn("winter")}${haveGen ? afterBill("winter") : `<span class="val">–</span>`}</div>` +
      `</div>` +
      (matchNote ? `<div class="sx-foot">${matchNote}</div>` : "") +

      `<div class="sx-sub">Electricity prices</div>` +
      `<div class="sx-group">` +
        price("Peak", pfx + "_peak_c") +
        price("Off-peak", pfx + "_offpeak_c") +
        price("Export, off-peak", pfx + "_offpeak_sell_c") +
        price("Export, peak", pfx + "_peak_sell_c") +
        row("Daily charge", field("daily", +(E[pfx + "_daily"] || 0).toFixed(2), `min="0" step="0.1" data-econ="${pfx}_daily"`, "$", "/day")) +
        btPeakHours().map(([a, b], i) => {
          const lo = i === 0 ? 4 : 12, hi = i === 0 ? 13 : 24;
          return `<div class="sx-row sx-peak"><span class="lbl">${i === 0 ? "Morning peak" : "Evening peak"}<small data-peak-label="${i}">${hours([a, b])}</small></span>` +
            `<div class="sx-range" style="--lo:${(a - lo) / (hi - lo) * 100}%;--hi:${(b - lo) / (hi - lo) * 100}%">` +
            `<input type="range" min="${lo}" max="${hi}" step="1" value="${a}" data-peak="${i}" data-end="0" aria-label="${i === 0 ? "Morning" : "Evening"} peak starts">` +
            `<input type="range" min="${lo}" max="${hi}" step="1" value="${b}" data-peak="${i}" data-end="1" aria-label="${i === 0 ? "Morning" : "Evening"} peak ends">` +
            `</div></div>`;
        }).join("") +
      `</div>` +
      `<div class="sx-foot">Including GST. Peak prices apply ${btPeakHours().map(hours).join(" and ")}. ${isBiz ? "Business" : "Home"} prices, used across the map.</div>` +
      `<details class="sx-dis" id="bt-adv-price"><summary><span class="t">Prices over the years</span>` +
        `<span class="sum">${E.real_prices ? "today's dollars" : "nominal"} · power +${E.elec_inflation_pct}%/yr</span></summary>` +
        `<div class="sx-group">` +
          row("Power prices rise", field("infl", E.elec_inflation_pct, `step="0.5" data-econ="elec_inflation_pct"`, "", "%/yr")) +
          row("Export rates fall", field("edec", E.export_decline_pct, `step="0.5" data-econ="export_decline_pct"`, "", "%/yr")) +
          row("General inflation", field("cpi", E.cpi_pct, `step="0.5" data-econ="cpi_pct"`, "", "%/yr")) +
          row("Panel life", field("life", E.life_years, `min="5" max="40" step="1" data-econ="life_years"`, "", "years")) +
          row("Panels lose output", field("deg", E.degradation_pct, `step="0.1" data-econ="degradation_pct"`, "", "%/yr")) +
          row("Show money in", seg("real", [["1", "Today's $"], ["0", "Nominal"]], E.real_prices ? "1" : "0", "Money in")) +
        `</div>` +
        `<div class="sx-foot">Today's dollars take general inflation out of every future year; nominal shows the dollars of each year.</div>` +
      `</details>` +

      `<div class="sx-sub">Electricity use</div>` +
      `<div class="sx-group">` +
        row("Use a year", `<button type="button" class="sx-info" data-wk="use" aria-label="How the yearly use is worked out">ⓘ</button>` +
          field("total", Math.round(N.useYear.value), `min="0" step="100" data-total="1"`, "", "kWh", calibBy !== "total"),
          { small: calibBy === "total" ? "yours" : calibBy === "bills" ? "from your bills" : "our estimate" }) +
        row("Daytime use", val(Wk.show(N.dayKw)), { go: "daykw", small: "average, 9am–5pm" }) +
        row("Used in the daytime", val(Wk.show(N.dayShare), "of the day"), { go: "dayshare" }) +
        `<div class="sx-use"><div class="bar">` + parts.map(([k, v]) =>
          `<div style="width:${(100 * v / totalUse).toFixed(1)}%;background:${BT_PART_STYLE[k].colour}"></div>`).join("") +
        `</div><div class="key">` + parts.map(([k, v]) =>
          `<span data-wk="part-${k}" tabindex="0" role="button"><i style="background:${BT_PART_STYLE[k].colour}"></i>${BT_PART_STYLE[k].label} ${Math.round(v).toLocaleString()} kWh</span>`).join("") +
        `</div></div>` +
      `</div>` +
      `<details class="sx-dis" id="bt-adv-home"><summary><span class="t">About this ${isBiz ? "business" : "home"}</span>` +
        `<span class="sum">${isBiz ? "Business" : `${P.people} ${P.people === 1 ? "person" : "people"}`} · ${({ heatpump: "heat pumps", electric: "electric heaters", gas: "gas heating", none: "no heating" })[P.heating]}${P.evs ? ` · ${P.evs} EV${P.evs > 1 ? "s" : ""}` : ""}</span></summary>` +
        `<div class="sx-group">` +
          row("Building", seg("type", [["home", "Home"], ["business", "Business"]], state.type, "Building")) +
          (isBiz ? "" : row("People", `<div class="sx-step" data-step="people"><button type="button" data-d="-1" aria-label="Fewer people">−</button><b>${P.people}</b><button type="button" data-d="1" aria-label="More people">+</button></div>`)) +
          (isBiz ? "" : row("Hot water", seg("water", [["electric", "Electric"], ["heatpump", "Heat pump"], ["gas", "Gas"]], P.water, "Hot water"))) +
          (isBiz || P.water === "gas" ? "" : row("Heat water when it's sunny", `<label class="sx-switch"><input type="checkbox" data-tick="waterOnSun" ${P.waterOnSun ? "checked" : ""} aria-label="Heat water when it's sunny"><i></i></label>`, { small: "a timer on the cylinder" })) +
          row("Heating", seg("heating", [["heatpump", "Heat pump"], ["electric", "Electric"], ["gas", "Gas"], ["none", "None"]], P.heating, "Heating")) +
          row(isBiz ? "Electric vehicles" : "Electric cars", seg("evs", (isBiz ? [0, 1, 2, 4, 8] : [0, 1, 2, 3]).map(n => [n, String(n)]), P.evs, "Electric cars")) +
          (P.evs > 0 ? row(isBiz ? "Charge in the working day" : "Charge when it's sunny", `<label class="sx-switch"><input type="checkbox" data-tick="evOnSun" ${P.evOnSun ? "checked" : ""} aria-label="Charge when it's sunny"><i></i></label>`) : "") +
        `</div>` +
        `<div class="sx-foot">These shape our estimate. Entering your bills or yearly use scales it to match.</div>` +
      `</details>` +

      `<div class="sx-sub">System cost</div>` +
      `<div class="sx-group">` +
        row("Solar price", field("solarrate", Math.round(N.solarRate.value), `min="0" step="50" data-sys="solarPerKw"`, "$", "/kW", !(state.sys.solarPerKw > 0)),
          { small: state.sys.solarPerKw > 0 ? "yours" : "typical for this size" }) +
        row("Solar", val(money(N.solarCost)), { go: "solarcost", small: N.kwp.value.toFixed(1) + " kW" }) +
        row("Battery", seg("battery", [[0, "None"], [5, "5"], [10, "10"], [13.5, "13.5"]].concat(isBiz ? [[30, "30"]] : []), P.battery, "Battery size, kWh"), { small: "kWh" }) +
        (withBatt ? row("Battery price", field("battrate", Math.round(N.battRate.value), `min="0" step="50" data-sys="battPerKwh"`, "$", "/kWh", !(state.sys.battPerKwh > 0)),
          { small: state.sys.battPerKwh > 0 ? "yours" : "typical" }) +
          row("Battery", val(money(N.battCost)), { go: "battcost" }) : "") +
        row("Total", val(money(withBatt ? N.allCost : N.solarCost)), { go: "allcost", cls: "total" }) +
      `</div>` +
      `<div class="sx-foot">An estimate from the roof, the local climate and these assumptions, not a quote. Tap any result to see how it's worked out.</div>` +
      `</div>`;
    const [top, more] = html.split(SX_SPLIT);
    const moreBox = ctx.moreBox && ctx.moreBox.isConnected ? ctx.moreBox : null;
    if (moreBox) { box.innerHTML = top; moreBox.innerHTML = more; } else box.innerHTML = top + more;
    const roots = moreBox ? [box, moreBox] : [box];
    const qa = sel => roots.flatMap(rt => [...rt.querySelectorAll(sel)]);
    const q1 = sel => qa(sel)[0] || null;

    // every result opens its workings
    const nodeFor = {
      save: () => save1, lifetime: () => ret.nodes.lifetime, net: () => ret.nodes.net, irr: () => ret.nodes.irr,
      "after-summer": () => S.summer[after], "after-winter": () => S.winter[after],
      daykw: () => N.dayKw, dayshare: () => N.dayShare, use: () => N.useYear,
      solarcost: () => N.solarCost, battcost: () => N.battCost, allcost: () => withBatt ? N.allCost : N.solarCost,
    };
    Object.keys(r.yearParts).forEach(k => { nodeFor["part-" + k] = () => N.parts[k]; });
    qa("[data-wk]").forEach(elx => {
      const f = nodeFor[elx.dataset.wk];
      if (f && typeof WkView !== "undefined") WkView.bind(elx, f);
    });

    // wiring
    const keepOpen = () => ["bt-adv-price", "bt-adv-home"].filter(i => { const d = q1("#" + i); return d && d.open; });
    const redraw = (focusSel) => {
      const open = keepOpen();
      draw();
      const rq = sel => [box, ctx.moreBox].filter(Boolean).map(rt => rt.querySelector(sel)).find(Boolean);
      open.forEach(i => { const d = rq("#" + i); if (d) d.open = true; });
      if (focusSel) { const f = rq(focusSel); if (f) f.focus(); }
    };
    qa(".sx-seg").forEach(g => g.addEventListener("click", ev => {
      const b = ev.target.closest("button"); if (!b) return;
      const k = g.dataset.key, v = b.dataset.v;
      if (k === "real") { econ.real_prices = v === "1"; if (ctx.onEcon) ctx.onEcon(); else redraw(); return; }
      if (k === "type") state.type = v;
      else (state.type === "business" ? state.business : state.home)[k] = (k === "evs" || k === "battery") ? +v : v;
      save(); redraw(`.sx-seg[data-key="${k}"] button[data-v="${v}"]`);
    }));
    qa(".sx-step button").forEach(b => b.addEventListener("click", () => {
      state.home.people = Math.max(1, Math.min(8, state.home.people + (+b.dataset.d)));
      save(); redraw(`.sx-step button[data-d="${b.dataset.d}"]`);
    }));
    qa("input[data-tick]").forEach(c => c.addEventListener("change", () => {
      (state.type === "business" ? state.business : state.home)[c.dataset.tick] = c.checked; save(); redraw(`input[data-tick="${c.dataset.tick}"]`);
    }));
    qa("input[data-bill]").forEach(inp => inp.addEventListener("change", () => {
      const v = parseFloat(inp.value);
      const c = (state.calib && !state.calib.total) ? state.calib : {};
      c[inp.dataset.bill] = v > 0 ? v : null;
      state.calib = (c.winter > 0 || c.summer > 0) ? c : null;
      save(); redraw();
    }));
    qa("input[data-total]").forEach(inp => inp.addEventListener("change", () => {
      const v = parseFloat(inp.value);
      state.calib = v > 0 ? { total: v } : null;
      save(); redraw();
    }));
    qa("input[data-sys]").forEach(inp => inp.addEventListener("change", () => {
      const v = parseFloat(inp.value);
      state.sys[inp.dataset.sys] = v > 0 ? v : null;
      save(); redraw();
    }));
    // peak windows: the label follows the drag; the sums run when it is let go
    qa("input[data-peak]").forEach(inp => {
      const read = () => {
        const i = +inp.dataset.peak;
        const pair = qa(`input[data-peak="${i}"]`);
        let a = +pair.find(x => x.dataset.end === "0").value, b = +pair.find(x => x.dataset.end === "1").value;
        if (b <= a) { if (inp.dataset.end === "0") a = b - 1; else b = a + 1; }
        return [i, a, b];
      };
      inp.addEventListener("input", () => {
        const [i, a, b] = read();
        const lab = q1(`[data-peak-label="${i}"]`); if (lab) lab.textContent = hours([a, b]);
        const rg = inp.parentElement, lo = +inp.min, hi = +inp.max;
        rg.style.setProperty("--lo", (a - lo) / (hi - lo) * 100 + "%");
        rg.style.setProperty("--hi", (b - lo) / (hi - lo) * 100 + "%");
      });
      inp.addEventListener("change", () => {
        const [i, a, b] = read();
        const ph = btPeakHours().map(w => w.slice());
        ph[i] = [a, b];
        econ.peak_hours = ph;
        if (ctx.onEcon) ctx.onEcon(); else redraw();
      });
    });
    qa("input[data-econ]").forEach(inp => inp.addEventListener("change", () => {
      const v = parseFloat(inp.value);
      if (!Number.isFinite(v)) return;
      if (v < 0 && inp.dataset.econ !== "elec_inflation_pct" && inp.dataset.econ !== "cpi_pct") return;
      econ[inp.dataset.econ] = v;
      if (ctx.onEcon) ctx.onEcon(); else redraw();
    }));
    if (ctx.onResult) ctx.onResult(r);
  };
  draw();
  return { redraw: draw, state, result: () => last };
}
