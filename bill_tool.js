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
  return (typeof econ !== "undefined" && econ.peak_hours) || [[7, 11], [17, 21]];
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
      BT_SEASONS.map(s => Wk.product(BT_CAP(s), "kWh/yr", [btModelledDay(load, s), days(s)])));
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
  const yearOf = (label, unit, f) => Wk.sum(label, unit,
    BT_SEASONS.map(s => Wk.product(BT_CAP(s), unit, [f(s), days[s]])));
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
      BT_SEASONS.map(s => Wk.product(BT_CAP(s), "kWh/yr", [load.nodes[part][s], cal.nodes[s],
        btIn(`Days of ${s}`, ctx.seasonDays ? ctx.seasonDays[BT_SEASONS.indexOf(s)] : BT_SEASON_DAYS[s], "days")])));
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
.sba-kpis { display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 8px; margin: 12px 0 4px; }
.sba-kpi { background: var(--surface-2, rgba(255,255,255,.05)); border-radius: 9px; padding: 10px 10px 9px; min-width: 0; }
.sba-kpi .k { font-size: 11px; color: var(--ink-2, #b6bdc9); line-height: 1.25; min-height: 2.5em; }
.sba-kpi .v { font-size: 19px; font-weight: 760; color: var(--ink, #f2f4f7); font-variant-numeric: tabular-nums; margin-top: 3px; white-space: nowrap; }
.sba-kpi .s { font-size: 10.5px; color: var(--ink-3, #7e8794); margin-top: 2px; }
.sba-kpi.hero .v { color: var(--accent, #ffd24a); }
.sba-h { font-size: 15px; font-weight: 750; color: var(--ink, #f2f4f7); margin: 18px 0 8px; letter-spacing: -.1px; }
.sba-sub { font-size: 11.5px; font-weight: 700; color: var(--ink-2, #b6bdc9); text-transform: uppercase; letter-spacing: .07em; margin: 16px 0 7px; }
.sba-ba { display:grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.sba-col { display:grid; gap: 7px; align-content: start; }
.sba-colh { font-size: 11.5px; font-weight: 700; color: var(--ink-2, #b6bdc9); text-transform: uppercase; letter-spacing: .07em; }
.sba-box { background: var(--surface-2, rgba(255,255,255,.05)); border-radius: 8px; padding: 8px 10px; min-width: 0; }
.sba-box .k { font-size: 11px; color: var(--ink-2, #b6bdc9); display:flex; justify-content: space-between; gap: 6px; }
.sba-box .v { font-size: 17px; font-weight: 720; color: var(--ink, #f2f4f7); font-variant-numeric: tabular-nums; margin-top: 3px; }
.sba-box.edit { border: 1px solid rgba(255,210,74,.28); }
.sba-box .in { display:flex; align-items: baseline; gap: 4px; margin-top: 3px; color: var(--ink, #f2f4f7); font-size: 14px; }
.sba-box input, .sba-box select { width: 100%; min-width: 0; background: #2a2f38; color: #fff; border: 1px solid #4a505c; border-radius: 6px;
  padding: 5px 7px; font-size: 15px; font-weight: 650; font-variant-numeric: tabular-nums; text-align: right; }
.sba-box select { text-align: left; font-size: 13px; }
.sba-box input:focus-visible, .sba-box select:focus-visible { outline: 2px solid var(--accent, #ffd24a); outline-offset: 1px; }
.sba-box .u { font-size: 11.5px; color: var(--ink-3, #7e8794); white-space: nowrap; }
.sba-box .tag { font-size: 10px; color: var(--ink-3, #7e8794); }
.sba-box .tag.yours { color: var(--good, #7fd18e); }
.sba-how { font-size: 10.5px; color: var(--accent, #ffd24a); cursor: pointer; text-decoration: underline dotted; background: none; border: 0; padding: 0; }
.sba-g5 { display:grid; grid-template-columns: repeat(5, minmax(0,1fr)); gap: 7px; }
.sba-g3 { display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 7px; }
.sba-g2 { display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 7px; }
.sba-note { font-size: 11px; color: var(--ink-3, #7e8794); margin-top: 6px; line-height: 1.45; }
.sba-note .ok { color: var(--good, #7fd18e); }
.sba-adv { margin-top: 8px; border-top: 1px solid var(--line, rgba(255,255,255,.09)); padding-top: 7px; }
.sba-adv summary { cursor: pointer; font-size: 12px; color: var(--ink-2, #b6bdc9); font-weight: 650; }
.sba-adv[open] summary { margin-bottom: 7px; }
.sba-seg { display:flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.sba-seg button { background: rgba(255,255,255,.06); color: var(--ink, #f2f4f7); border: 1px solid rgba(255,255,255,.10);
  border-radius: 14px; padding: 4px 10px; font-size: 11.5px; cursor: pointer; }
.sba-seg button.on { background: var(--accent, #ffd24a); color: #1a1a1a; border-color: var(--accent, #ffd24a); font-weight: 700; }
.sba-seg button:focus-visible { outline: 2px solid var(--accent, #ffd24a); outline-offset: 1px; }
.sba-q { display:grid; grid-template-columns: 110px 1fr; align-items:center; gap: 8px; margin: 6px 0; font-size: 12px; color: var(--ink-2, #b6bdc9); }
.sba-tick { display:flex; align-items:center; gap: 7px; font-size: 12px; color: var(--ink-2,#b6bdc9); margin: 4px 0 4px 118px; cursor:pointer; }
.sba-step { display:flex; align-items:center; gap: 6px; }
.sba-step button { width: 26px; height: 26px; border-radius: 13px; background: rgba(255,255,255,.08); color: var(--ink,#fff); border: 1px solid rgba(255,255,255,.12); cursor:pointer; font-size: 14px; }
.sba-use { display:flex; height: 9px; border-radius: 5px; overflow:hidden; background: rgba(255,255,255,.06); margin-top: 8px; }
.sba-key { display:flex; flex-wrap:wrap; gap: 4px 12px; font-size: 11px; color: var(--ink-3,#7e8794); margin-top: 5px; }
.sba-key i { display:inline-block; width:8px; height:8px; border-radius:2px; margin-right:4px; }
@media (max-width: 720px) { .sba-kpis { grid-template-columns: 1fr 1fr; } .sba-g5 { grid-template-columns: repeat(3, minmax(0,1fr)); } }
@media (max-width: 420px) { .sba-g5, .sba-g3 { grid-template-columns: 1fr 1fr; } .sba-q { grid-template-columns: 1fr; } .sba-tick { margin-left: 0; } }
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
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const seg = (key, options, current) =>
    `<div class="sba-seg" data-key="${key}">` + options.map(([v, l]) =>
      `<button type="button" data-v="${v}" class="${String(v) === String(current) ? "on" : ""}">${l}</button>`).join("") + `</div>`;

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
    // only claim a match that holds: a bill below the daily charge alone
    // cannot be matched by any amount of use
    const entered = state.calib && !state.calib.total
      ? [["summer", S.summer.nowMonth], ["winter", S.winter.nowMonth]].filter(([k]) => state.calib[k] > 0) : [];
    const off = entered.filter(([k, n]) => Math.abs(n.value - state.calib[k]) > 0.03 * state.calib[k]);
    const matchNote = calibBy === "total" ? `<span class="ok">Matched to your yearly use.</span>`
      : !entered.length ? "These start as our estimate for this building. Change them to your own bills and everything below follows."
      : !off.length ? `<span class="ok">Matched to your ${entered.map(([k]) => k).join(" and ")} bill${entered.length > 1 ? "s" : ""}.</span>`
      : `Your ${off.map(([k]) => k).join(" and ")} bill is below what the daily charge alone comes to &mdash; check the prices.`;

    const id = k => `bt-${k}`;
    const kpi = (k, label, node, text, sub, hero) =>
      `<div class="sba-kpi${hero ? " hero" : ""}"><div class="k">${label}</div>` +
      `<div class="v" data-wk="${k}">${haveGen ? text : "–"}</div><div class="s">${sub || ""}</div></div>`;
    const box2 = (k, label, text, sub) =>
      `<div class="sba-box"><div class="k"><span>${label}</span>${sub || ""}</div><div class="v" data-wk="${k}">${text}</div></div>`;
    const inBox = (k, label, value, unitL, unitR, attrs, tag, how) =>
      `<div class="sba-box edit"><div class="k"><label for="${id(k)}">${label}</label>${how ? `<button type="button" class="sba-how" data-wk="${how}">how?</button>` : ""}</div>` +
      `<div class="in">${unitL ? `<span class="u">${unitL}</span>` : ""}<input id="${id(k)}" type="number" ${attrs} value="${value}">` +
      `${unitR ? `<span class="u">${unitR}</span>` : ""}</div>${tag ? `<div class="tag ${tag.cls || ""}">${tag.text}</div>` : ""}</div>`;
    const money = n => btMoney(n.value);
    // a bill below zero is money back: say so rather than print a minus
    const bill = n => n.value < -0.5 ? btMoney(-n.value) + `<span class="u"> credit /month</span>`
                                     : btMoney(n.value) + `<span class="u"> /month</span>`;
    const billIn = s => {
      const mine = state.calib && !state.calib.total && state.calib[s] > 0;
      return inBox(`bill-${s}`, s === "summer" ? "Summer minimum bill" : "Winter maximum bill",
        Math.round(S[s].nowMonth.value), "$", "/month", `min="0" step="10" data-bill="${s}"`,
        mine ? { cls: "yours", text: "yours" } : { text: calibBy === "total" ? "from your yearly use" : "our estimate" },
        `now-${s}`);
    };
    const priceIn = (k, label, key) => inBox(k, label, +(E[key] || 0).toFixed(2), "", "c/kWh",
      `min="0" step="0.5" data-econ="${key}"`);
    const totalMine = calibBy === "total";
    const P = isBiz ? state.business : state.home;
    const q = (label, inner) => `<div class="sba-q"><span>${label}</span>${inner}</div>`;
    const tick = (key, on, text) => `<label class="sba-tick"><input type="checkbox" data-tick="${key}" ${on ? "checked" : ""}> ${text}</label>`;
    const totalUse = Object.values(r.yearParts).reduce((a, b) => a + b, 0) || 1;
    const stack = Object.entries(r.yearParts).filter(([, v]) => v > 1).map(([k, v]) =>
      `<div style="width:${(100 * v / totalUse).toFixed(1)}%;background:${BT_PART_STYLE[k].colour}" title="${BT_PART_STYLE[k].label}"></div>`).join("");
    const key = Object.entries(r.yearParts).filter(([, v]) => v > 1).map(([k, v]) =>
      `<span data-wk="part-${k}"><i style="background:${BT_PART_STYLE[k].colour}"></i>${BT_PART_STYLE[k].label} ${Math.round(v).toLocaleString()} kWh</span>`).join("");
    const pbText = ret.payback == null ? `doesn't pay back in ${ret.years} yrs` : `pays back in ${ret.payback.toFixed(1)} yrs`;

    box.innerHTML =
      `<div class="sba-kpis">` +
        kpi("save", "Bill savings per year", save1, money(save1), withBatt ? "solar + battery" : "with solar", true) +
        kpi("lifetime", "Savings over panel lifetime", ret.nodes.lifetime, money(ret.nodes.lifetime), `${ret.years} years`) +
        kpi("net", "Net savings over lifetime", ret.nodes.net, money(ret.nodes.net), pbText) +
        kpi("irr", "Return on investment per year", ret.nodes.irr, ret.irr == null ? "–" : Wk.fmt(ret.irr, "share", 1), ret.real ? "real, after inflation" : "nominal") +
      `</div>` +
      (haveGen ? "" : `<div class="sba-note">Waiting for this roof's sun to load&hellip;</div>`) +
      `<div class="sba-h">Assumptions</div>` +
      `<div class="sba-ba">` +
        `<div class="sba-col"><div class="sba-colh">Before</div>${billIn("summer")}${billIn("winter")}</div>` +
        `<div class="sba-col"><div class="sba-colh">After ${withBatt ? "solar + battery" : "solar"}</div>` +
          box2("after-summer", "Summer minimum bill", haveGen ? bill(S.summer[after]) : "–") +
          box2("after-winter", "Winter maximum bill", haveGen ? bill(S.winter[after]) : "–") +
        `</div>` +
      `</div>` +
      `<div class="sba-note">${matchNote}</div>` +

      `<div class="sba-sub">Pricing</div>` +
      `<div class="sba-g5">` +
        priceIn("peak", "Peak price", pfx + "_peak_c") +
        priceIn("offpeak", "Off-peak price", pfx + "_offpeak_c") +
        priceIn("offsell", "Off-peak export price", pfx + "_offpeak_sell_c") +
        priceIn("peaksell", "Peak export price", pfx + "_peak_sell_c") +
        inBox("daily", "Daily charge", +(E[pfx + "_daily"] || 0).toFixed(2), "$", "/day", `min="0" step="0.1" data-econ="${pfx}_daily"`) +
      `</div>` +
      `<div class="sba-note">Including GST. Peak hours: ${btPeakText()}. ${isBiz ? "Business" : "Home"} prices; they apply across the whole map.</div>` +
      `<details class="sba-adv" id="bt-adv-price"><summary>Advanced pricing</summary>` +
        `<div class="sba-g3">` +
          inBox("infl", "Power prices rise", E.elec_inflation_pct, "", "%/yr", `step="0.5" data-econ="elec_inflation_pct"`) +
          inBox("edec", "Export rates fall", E.export_decline_pct, "", "%/yr", `step="0.5" data-econ="export_decline_pct"`) +
          inBox("cpi", "General inflation", E.cpi_pct, "", "%/yr", `step="0.5" data-econ="cpi_pct"`) +
          inBox("life", "Panel lifetime", E.life_years, "", "years", `min="5" max="40" step="1" data-econ="life_years"`) +
          inBox("deg", "Panels lose output", E.degradation_pct, "", "%/yr", `step="0.1" data-econ="degradation_pct"`) +
          `<div class="sba-box"><div class="k"><span>Money in</span></div>` + seg("real", [["1", "Real (today's $)"], ["0", "Nominal"]], E.real_prices ? "1" : "0") + `</div>` +
        `</div>` +
        `<div class="sba-note">Real: every future year is divided by general inflation, so all figures are in today's dollars. Nominal: the dollars of each year.</div>` +
      `</details>` +

      `<div class="sba-sub">Consumption assumptions</div>` +
      `<div class="sba-g3">` +
        box2("daykw", "Daytime load (9am–5pm)", Wk.show(N.dayKw)) +
        box2("dayshare", "Share of the day's use", Wk.show(N.dayShare) + `<span class="u"> of daily load</span>`) +
        inBox("total", "Total electrical load", Math.round(N.useYear.value), "", "kWh/yr", `min="0" step="100" data-total="1"`,
          totalMine ? { cls: "yours", text: "yours" } : { text: calibBy === "bills" ? "from your bills" : "our estimate" }, "use") +
      `</div>` +
      `<div class="sba-use">${stack}</div><div class="sba-key">${key}</div>` +
      `<details class="sba-adv" id="bt-adv-home"><summary>About this ${isBiz ? "business" : "home"}</summary>` +
        q("Building", seg("type", [["home", "Home"], ["business", "Business"]], state.type)) +
        (isBiz ? "" : q("People", `<div class="sba-step" data-step="people"><button type="button" data-d="-1" aria-label="Fewer people">&minus;</button><b>${P.people}</b><button type="button" data-d="1" aria-label="More people">+</button></div>`)) +
        (isBiz ? "" : q("Hot water", seg("water", [["electric", "Electric"], ["heatpump", "Heat pump"], ["gas", "Gas"]], P.water))) +
        (isBiz || P.water === "gas" ? "" : tick("waterOnSun", P.waterOnSun, "Heat water while the sun's out (timer)")) +
        q("Heating", seg("heating", [["heatpump", "Heat pumps"], ["electric", "Electric heaters"], ["gas", isBiz ? "Gas" : "Gas / wood"], ["none", "None"]], P.heating)) +
        q(isBiz ? "Electric vehicles" : "Electric cars", seg("evs", (isBiz ? [0, 1, 2, 4, 8] : [0, 1, 2, 3]).map(n => [n, String(n)]), P.evs)) +
        (P.evs > 0 ? tick("evOnSun", P.evOnSun, isBiz ? "Charge during the working day" : "Charge when it's sunny") : "") +
        `<div class="sba-note">These shape the estimate. Entering your own bills or yearly use above scales it to match.</div>` +
      `</details>` +

      `<div class="sba-sub">System pricing</div>` +
      `<div class="sba-g3">` +
        inBox("solarrate", "Solar cost per kW", Math.round(N.solarRate.value), "$", "/kW", `min="0" step="50" data-sys="solarPerKw"`,
          state.sys.solarPerKw > 0 ? { cls: "yours", text: "yours" } : { text: "typical for this size" }) +
        box2("solarcost", "Solar total cost", money(N.solarCost), `<span class="u">${Wk.show(N.kwp)}</span>`) +
        `<div class="sba-box"><div class="k"><span>Battery</span></div>` +
          seg("battery", [[0, "None"], [5, "5 kWh"], [10, "10 kWh"], [13.5, "13.5"]].concat(isBiz ? [[30, "30"]] : []), P.battery) + `</div>` +
        inBox("battrate", "Battery cost per kWh", Math.round(N.battRate.value), "$", "/kWh", `min="0" step="50" data-sys="battPerKwh"`,
          state.sys.battPerKwh > 0 ? { cls: "yours", text: "yours" } : { text: "typical" }) +
        box2("battcost", "Battery total cost", money(N.battCost)) +
        box2("allcost", "Total cost", money(withBatt ? N.allCost : N.solarCost)) +
      `</div>` +
      `<div class="sba-note">An estimate from the roof, the local climate and the assumptions above, not a quote. Tap any figure to see how it was worked out.</div>`;

    // every result opens its workings
    const nodeFor = {
      save: () => save1, lifetime: () => ret.nodes.lifetime, net: () => ret.nodes.net, irr: () => ret.nodes.irr,
      "now-summer": () => S.summer.nowMonth, "now-winter": () => S.winter.nowMonth,
      "after-summer": () => S.summer[after], "after-winter": () => S.winter[after],
      daykw: () => N.dayKw, dayshare: () => N.dayShare, use: () => N.useYear,
      solarcost: () => N.solarCost, battcost: () => N.battCost, allcost: () => withBatt ? N.allCost : N.solarCost,
    };
    Object.keys(r.yearParts).forEach(k => { nodeFor["part-" + k] = () => N.parts[k]; });
    box.querySelectorAll("[data-wk]").forEach(elx => {
      const f = nodeFor[elx.dataset.wk];
      if (f && typeof WkView !== "undefined") WkView.bind(elx, f);
    });

    // wiring
    const keepOpen = () => ["bt-adv-price", "bt-adv-home"].filter(i => { const d = box.querySelector("#" + i); return d && d.open; });
    const redraw = (focusSel) => {
      const open = keepOpen();
      draw();
      open.forEach(i => { const d = box.querySelector("#" + i); if (d) d.open = true; });
      if (focusSel) { const f = box.querySelector(focusSel); if (f) f.focus(); }
    };
    box.querySelectorAll(".sba-seg").forEach(g => g.addEventListener("click", ev => {
      const b = ev.target.closest("button"); if (!b) return;
      const k = g.dataset.key, v = b.dataset.v;
      if (k === "real") { econ.real_prices = v === "1"; if (ctx.onEcon) ctx.onEcon(); else redraw(); return; }
      if (k === "type") state.type = v;
      else (state.type === "business" ? state.business : state.home)[k] = (k === "evs" || k === "battery") ? +v : v;
      save(); redraw(`.sba-seg[data-key="${k}"] button[data-v="${v}"]`);
    }));
    box.querySelectorAll(".sba-step button").forEach(b => b.addEventListener("click", () => {
      state.home.people = Math.max(1, Math.min(8, state.home.people + (+b.dataset.d)));
      save(); redraw();
    }));
    box.querySelectorAll("input[data-tick]").forEach(c => c.addEventListener("change", () => {
      (state.type === "business" ? state.business : state.home)[c.dataset.tick] = c.checked; save(); redraw();
    }));
    box.querySelectorAll("input[data-bill]").forEach(inp => inp.addEventListener("change", () => {
      const v = parseFloat(inp.value);
      const c = (state.calib && !state.calib.total) ? state.calib : {};
      c[inp.dataset.bill] = v > 0 ? v : null;
      state.calib = (c.winter > 0 || c.summer > 0) ? c : null;
      save(); redraw();
    }));
    box.querySelectorAll("input[data-total]").forEach(inp => inp.addEventListener("change", () => {
      const v = parseFloat(inp.value);
      state.calib = v > 0 ? { total: v } : null;
      save(); redraw();
    }));
    box.querySelectorAll("input[data-sys]").forEach(inp => inp.addEventListener("change", () => {
      const v = parseFloat(inp.value);
      state.sys[inp.dataset.sys] = v > 0 ? v : null;
      save(); redraw();
    }));
    box.querySelectorAll("input[data-econ]").forEach(inp => inp.addEventListener("change", () => {
      const v = parseFloat(inp.value);
      if (!(v >= 0) && inp.dataset.econ !== "elec_inflation_pct" && inp.dataset.econ !== "cpi_pct") return;
      if (!Number.isFinite(v)) return;
      econ[inp.dataset.econ] = v;
      if (ctx.onEcon) ctx.onEcon(); else redraw();
    }));
    if (ctx.onResult) ctx.onResult(r);
  };
  draw();
  return { redraw: draw, state, result: () => last };
}
