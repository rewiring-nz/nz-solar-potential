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
// An assumption the reader can change in the workings sheet (WkView): the
// key says which of the map's settings it is (the page applies it).
const pctEd = k => ({ key: `econ:${k}`, scale: 100, unit: "%", step: 0.5, dp: 2 });
const btZero = (label, why) => Wk.input(label, 0, "kWh/day", { note: why });

// Daily kWh of each part, per season, and the hourly shapes they follow.
function btBuildLoad(p, opts) {
  const D = BT_DEFAULTS;
  const lat = opts && opts.lat, roofM2 = (opts && opts.roofM2) || 150;
  const nodes = { appliance: {}, water: {}, heat: {}, ev: {} };
  const shapes = {};
  const hdd = Wk.given("Heating degree-days a year", btHdd(lat), "°C·days",
    [btIn("Latitude", Math.abs(lat || 41), "°", { dp: 2, source: "This building's town." })], {
      formula: "a straight line from 1,200 at Auckland (36.9°S) to 3,200 at Queenstown (45.0°S), held beyond",
      note: "How cold the year is (degrees below 18 °C, summed over the year). Estimated from latitude.",
      check: () => btHdd(lat) });
  const seasonal = (part, s, what) => btIn(`${BT_CAP(s)} adjustment`, BT_SEASONAL[part][s], "×",
    { dp: 2, note: what });
  const roof = btIn("Roof area", roofM2, "m²", { source: "Measured from the laser scan." });
  const perKwhHdd = btIn("Heat pump electricity per degree-day", D.heat_kwh_per_hdd, "kWh/°C·day",
    { source: "Set so a typical home uses about 5 kWh a winter day in Auckland and 13 in Queenstown (EECA)." });
  const resistive = btIn("Plug-in heaters vs a heat pump", D.resistive_vs_heatpump, "×",
    { dp: 1, source: "A heat pump gives about 2.5 units of heat per unit of electricity. A plug-in heater gives 1." });
  let heatWinter;
  if (p.type === "business") {
    const year = Wk.maxOf("Business electricity a year, before heating and cars", "kWh/yr", [
      btIn("Smallest business", D.biz_min_kwh, "kWh/yr"),
      Wk.product("By floor area", "kWh/yr", [btIn("Electricity per m²", D.biz_kwh_per_m2, "kWh/m²",
        { dp: 0, source: "Typical NZ commercial use per m² of floor, before heating." }), roof])]);
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
        { source: "Set so a 3-person home uses about 7,000 kWh a year, the NZ average (MBIE)." }),
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
      [heatWinter, seasonal("heat", s, "Share of the coldest days' heating used in this season.")])
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
  const ed = k => (t && t.pfx ? { key: `econ:${t.pfx}_${k}`, scale: 100, unit: "c/kWh", step: 0.5, dp: 2 } : null);
  const t = btTariff(tariff);
  const src = `Your plan (Prices), including GST. Peak hours: ${btPeakText()}.`;
  const P = {
    peak: btIn("Peak price", t.peak / 100, "$/kWh", { dp: 3, source: src, edit: ed("peak_c") }),
    off: btIn("Off-peak price", t.offpeak / 100, "$/kWh", { dp: 3, source: src, edit: ed("offpeak_c") }),
    peakSell: btIn("Peak export price", t.peakSell / 100, "$/kWh", { dp: 3, source: src, edit: ed("peak_sell_c") }),
    offSell: btIn("Off-peak export price", t.offpeakSell / 100, "$/kWh", { dp: 3, source: src, edit: ed("offpeak_sell_c") }),
    daily: btIn("Daily charge", t.daily, "$/day", { source: "Your plan (Prices), including GST.",
      edit: t.pfx ? { key: `econ:${t.pfx}_daily`, scale: 1, unit: "$/day", step: 0.1, dp: 2 } : null }),
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
  const one = s => btIn(`Matching to your bills, ${s}`, 1, "×", { dp: 2, note: "Nothing entered, so our estimate is used as is." });
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
      0.2, 5, { dp: 3, note: "Every part of our estimate is scaled by this, in every season." });
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
        note: "Every part of our estimate is scaled by this, so its bill matches yours." });
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
  const hourNote = "Each hour, solar powers the home first. Any spare charges the battery (if there is one), then is exported. " +
                   `The rest comes from the grid. Peak prices apply ${btPeakText()}.`;
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
    // each part's share of every hour, for the season charts: the same day
    // split by what uses it (it adds back up to the hour's use; tested)
    const partsH = {};
    for (const k of Object.keys(load.parts)) {
      const day = load.parts[k][s] * cal.scale[s];
      partsH[k] = load.shapes[k].map(f => day * f);
    }
    res.seasons[s] = {
      loadKwh: use.value, genKwh: made.value,
      nowMonth: S[s].nowMonth.value, solarMonth: S[s].solarMonth.value, battMonth: S[s].battMonth.value,
      selfShare: made.value > 0 ? (made.value - exp.value) / made.value : 0,
      load: L, gen: G, solarImp: solar.impH, battImp: withB.impH,
      // the hour rows the bills are added up from (the same objects the
      // Workings tables hold), so a chart drawn from them cannot disagree
      rows, rowsB, partsH,
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
    note: "The home's average use in the middle of the day. Solar can power this directly." });
  N.dayShare = Wk.quotient("Share of the day's use in the daytime", "share", N.dayKwh, N.avgDay, { dp: 0 });
  res.days = BT_SEASONS.map(s => days[s].value);
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
  const rates = [
    btIn("Power prices rise", E.elec_inflation_pct / 100, "share", { dp: 1, source: "Advanced pricing.", edit: pctEd("elec_inflation_pct") }),
    btIn("Export rates fall", E.export_decline_pct / 100, "share", { dp: 1, source: "Advanced pricing.", edit: pctEd("export_decline_pct") }),
    btIn("Panels lose output", E.degradation_pct / 100, "share", { dp: 1, source: "A typical panel warranty.", edit: pctEd("degradation_pct") }),
  ].concat(real ? [btIn("General inflation (to today's dollars)", E.cpi_pct / 100, "share",
    { dp: 1, source: "Advanced pricing. The Reserve Bank targets 1–3%." })] : []);
  const note = `Each year: power you no longer buy (prices rise ${E.elec_inflation_pct}% a year) plus export income ` +
               `(rates fall ${E.export_decline_pct}% a year). Both drop ${E.degradation_pct}% a year as the panels age.` +
               (real ? ` Inflation of ${E.cpi_pct}% a year is taken out, to give today's dollars.` : " In the dollars of each year.");
  const cols = [{ key: "year", label: "Year" }, { key: "saving", label: "Bill savings", unit: "$" },
                { key: "out", label: "Replacements", unit: "$" }, { key: "cum", label: "Running total", unit: "$" }];
  const lifeRows = rows.slice(0, E.life_years);
  const kids = [saveN, expN].concat(rates);
  const lifetime = Wk.table(`Savings over the panels' ${E.life_years}-year life`, "$", cols, lifeRows, "saving",
    { children: kids, note });
  const replN = Wk.table("Replacements over the panels' life", "$",
    cols, lifeRows, "out", { children: repl.map(r => r.node),
      note: "Parts that wear out before the panels: the inverter, and the battery if there is one. " +
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
  const irr = Wk.given("Return on investment, a year", r, "share", [costN, lifetime, replN], {
    dp: 1,
    formula: r == null ? "no rate breaks even" :
      `the yearly rate at which paying ${Wk.fmt(costN.value, "$")} now breaks even with the savings that follow`,
    note: `Like a savings account paying this rate for ${E.life_years} years, ` +
          (real ? "after inflation." : "before inflation.") +
          (r == null ? "" : ` Check: at ${Wk.fmt(r, "share", 2)}, the savings discounted to today add up to the cost (${Wk.fmt(npv(r) + costN.value, "$")} vs ${Wk.fmt(costN.value, "$")}).`),
    check: () => solve() });
  // the two halves of the lifetime savings, for pricing single panels: one
  // that covers power used on site earns the first, one that exports the second
  const lifeRetail = lifeRows.reduce((t, row) => t + row.retailY, 0);
  const lifeExport = lifeRows.reduce((t, row) => t + row.exportY, 0);
  return { payback, lifetime: lifetime.value, net: net.value, irr: r, years: E.life_years, real,
           lifeRetail, lifeExport,
           nodes: { payback: pb, lifetime, replacements: replN, net, irr, first: saveN } };
}
// One panel's economics from a bill-model result (btEvaluate): its share of
// the cost, how many panels the site's own use absorbs (sunniest first), and
// the kWh/yr a panel must make to repay its share on each side of that
// cutoff -- retail if it covers power used on site, export if not. The map's
// panel colours for EVERY roof come from here, the open one included, so a
// roof keeps its colours when clicked (audit, 28 Oct: the other roofs were
// priced by economics.js's older flat-price, discounted model).
function btPanelEcon(r, count) {
  const Y = r.run.year, selfKwh = Y.gen - Y.solarExport;
  if (!(count > 0) || !(Y.gen > 0)) return null;
  const costPerPanel = r.solarCost / count;
  return {
    costPerPanel,
    cutoff: Math.min(count, selfKwh / (Y.gen / count)),
    retailBE: costPerPanel / Math.max(r.solarRet.lifeRetail / Math.max(selfKwh, 1e-9), 1e-9),
    exportBE: costPerPanel / Math.max(r.solarRet.lifeExport / Math.max(Y.solarExport, 1e-9), 1e-9),
    perKwh: r.solarRet.lifetime / Y.gen,
  };
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
    ? btIn("Solar cost per kW", sy.solarPerKw, "$/kW", { source: "You entered it (System pricing).",
        edit: { key: "sys:solarPerKw", scale: 1, unit: "$/kW", step: 50, dp: 0 } })
    : Wk.given("Solar cost per kW, for a system this size", typeof costPerKw === "function" ? costPerKw(kwpN.value) : 2000, "$/kW",
      [kwpN], { formula: typeof econ !== "undefined"
        ? econ.cost_tiers.map(t => `${t.label}: $${t.rate.toLocaleString()}/kW`).join("; ") : "flat $2,000/kW",
        source: "Typical NZ installed prices, including GST.",
        check: v => typeof costPerKw === "function" ? costPerKw(v[0]) : 2000 });
  const solarCostN = Wk.product("Solar costs", "$", [kwpN, rate]);
  const battDef = (typeof BATTERY_DEFAULTS !== "undefined") ? BATTERY_DEFAULTS : { cost_per_kwh: 1000, life_years: 15 };
  const battRate = btIn("Battery cost per kWh", sy.battPerKwh > 0 ? sy.battPerKwh : battDef.cost_per_kwh, "$/kWh",
    { source: sy.battPerKwh > 0 ? "You entered it (System pricing)." : "Typical NZ installed price, including GST." });
  const battCostN = Wk.product("Battery costs", "$", [
    btIn("Battery size", prof.battery || 0, "kWh", { dp: 1, source: "Your choice." }), battRate]);
  const allCostN = Wk.sum("Solar and battery cost", "$", [solarCostN, battCostN]);
  const inverter = { year: E.inverter_replace_year, label: "Inverter",
    node: Wk.product(`Inverter replaced in year ${E.inverter_replace_year + 1}`, "$", [solarCostN,
      btIn("Inverter, as a share of the solar cost", E.inverter_cost_pct / 100, "share", { source: "Assumptions & pricing.", edit: pctEd("inverter_cost_pct") })]) };
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

// THE YEAR, HOUR BY HOUR. The bills are worked out on an average day in each
// season, so the year is those days laid end to end on the calendar
// (December to February summer, March to May autumn, June to August winter,
// September to November spring: 90, 92, 92 and 91 days). Nothing is worked
// out again: every hour is a row of its season's bill table, so the year
// adds up to the same totals as the bills, and `check` says so (or what
// differs). Day-to-day weather is not in it; Resilience has that.
const BT_MONTH_SEASON = ["summer", "summer", "autumn", "autumn", "autumn", "winter",
                         "winter", "winter", "spring", "spring", "spring", "summer"];
const BT_MONTH_LEN = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
const BT_MONTH_NAME = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function btYearHours(run, withBatt) {
  const N = run.nodes;
  const P = N.prices;
  const rows = [];
  const calDays = {};
  BT_MONTH_SEASON.forEach((s, m) => { calDays[s] = (calDays[s] || 0) + BT_MONTH_LEN[m]; });
  const tot = { use: 0, solar: 0, imp: 0, exp: 0, billNow: 0, bill: 0 };
  let doy = 0;
  for (let m = 0; m < 12; m++) {
    const s = BT_MONTH_SEASON[m];
    const hr = withBatt ? run.seasons[s].rowsB : run.seasons[s].rows;
    for (let d = 1; d <= BT_MONTH_LEN[m]; d++, doy++) {
      for (let h = 0; h < 24; h++) {
        const r = hr[h];
        const cost = r.impCost - r.expPay + (h === 0 ? P.daily.value : 0);
        const costNow = r.useCost + (h === 0 ? P.daily.value : 0);
        rows.push({ doy, month: m, day: d, h, season: s, use: r.use, solar: r.solar, imp: r.imp, exp: r.exp,
                    buy: r.buy, sell: r.sell, costNow, cost });
        tot.use += r.use; tot.solar += r.solar; tot.imp += r.imp; tot.exp += r.exp;
        tot.billNow += costNow; tot.bill += cost;
      }
    }
  }
  // the year must add up to the bills' own figures
  const want = {
    use: N.useYear.value, solar: N.genYear.value,
    imp: (withBatt ? N.battImport : N.solarImport).value, exp: (withBatt ? N.battExport : N.solarExport).value,
    billNow: N.nowBill.value, bill: (withBatt ? N.battBill : N.solarBill).value,
  };
  const off = Object.keys(want).filter(k => Math.abs(tot[k] - want[k]) > 1e-6 * Math.max(1, Math.abs(want[k])));
  const daysOff = BT_SEASONS.filter((s, i) => calDays[s] !== run.days[i]);
  return { rows, totals: tot, want, check: { ok: !off.length, off, daysOff } };
}

function btYearCsv(year) {
  const head = "date,hour,season,use_kwh,solar_kwh,from_grid_kwh,exported_kwh,price_per_kwh,export_price_per_kwh,bill_today_dollars,bill_with_solar_dollars";
  const f = (v, dp) => (Math.round(v * 10 ** dp) / 10 ** dp).toString();
  return head + "\n" + year.rows.map(r =>
    [`${String(r.day).padStart(2, "0")} ${BT_MONTH_NAME[r.month]}`, `${String(r.h).padStart(2, "0")}:00`, r.season,
     f(r.use, 4), f(r.solar, 4), f(r.imp, 4), f(r.exp, 4), f(r.buy, 4), f(r.sell, 4), f(r.costNow, 4), f(r.cost, 4)].join(",")).join("\n") + "\n";
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { BT_SEASONS, BT_DEFAULTS, BT_SHAPES, btHdd, btBuildLoad, btSeasonLoad,
                     btDailyKwh, btCalibration, btRun, btReturns, btEvaluate, btModelledDay, btPanelEcon,
                     btYearHours, btYearCsv };
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
.sx-why > summary { cursor: pointer; color: var(--sx-ink-2); list-style: none; display: inline; }
.sx-why > summary::-webkit-details-marker { display: none; }
.sx-why > summary::after { content: " ›"; }
.sx-why[open] > summary::after { content: " ⌄"; }
.sx-why[open] { color: var(--sx-ink-2); }
.sx-why.sx-inline { display: inline; }
.sx-seg { display: inline-flex; background: var(--sx-fill-2); border-radius: 9px; padding: 2px; gap: 0; flex-wrap: nowrap; justify-content: flex-end; flex: none; }
/* A choice never breaks inside itself: in a narrow column the whole group
   drops under its label, right-aligned (it had wrapped "None" onto a second
   line and squeezed "Hot water" into two, 20 Oct). */
.sx-row:has(> .sx-seg) { flex-wrap: wrap; row-gap: 6px; }
.sx-row:has(> .sx-seg) > .lbl { min-width: max-content; }
.sx-row > .sx-seg { margin-left: auto; }
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
.sx-dis > summary .t { flex: 1 0 auto; white-space: nowrap; }
.sx-dis > summary .sum { color: var(--sx-ink-3); font-size: 12.5px; text-align: right; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
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
.sx-more .sx-h:first-child { margin-top: 22px; }
.sx-explore { display: block; width: 100%; margin-top: 10px; background: var(--sx-fill); border: 0; border-radius: 12px;
  color: var(--sx-ink); font: 500 13.5px/1.3 inherit; font-family: inherit; padding: 10px 14px; text-align: left; cursor: pointer; }
.sx-explore span { float: right; color: var(--sx-ink-3); font-size: 17px; line-height: 1; }
.sx-explore:hover, .sx-explore:focus-visible { background: rgba(255,255,255,.08); outline: none; }
.sx-lead { margin: 2px 4px 10px; }
.sx-key { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 12px; color: var(--sx-ink-2); margin: 0 4px 8px; }
.sx-key i { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }
.sx-key i.pk { background: rgba(255,255,255,.12); }
.sx-key .u { margin-left: auto; color: var(--sx-ink-3); }
.sx-seasons { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 10px; }
.sx-season { background: var(--sx-fill); border-radius: 12px; padding: 10px 10px 8px; min-width: 0; }
.sx-season-h { display: flex; justify-content: space-between; align-items: baseline; font-size: 14px; margin: 0 2px 4px; }
.sx-season-h span { font-size: 11.5px; color: var(--sx-ink-3); }
.sx-chartbox { position: relative; }
.sx-chart { width: 100%; height: auto; display: block; touch-action: pan-y; cursor: zoom-in; }
.sx-h-row { display: flex; align-items: center; gap: 10px; }
.sx-h-row > span { flex: 1; }
.sx-big { font-size: 12.5px; padding: 6px 10px; }
.sx-bigcard { width: min(1600px, 100%); }
.sx-bigfoot { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; margin-top: 12px; }
.sx-seasons-big { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.sx-seasons-big .sx-season { padding: 12px 14px 10px; }
.sx-seasons-big .sx-season-h { font-size: 16px; }
.sx-seasons-big .sx-figs b { font-size: 15px; }
.sx-seasons-big .sx-figs small { font-size: 11.5px; }
.sx-seasons-big .sx-sbill { font-size: 14px; }
.sx-seasons-big .sx-tip { width: 200px; font-size: 12.5px; }
.sx-seasons-big .sx-season { display: grid; grid-template-columns: minmax(0, 1fr) 150px; column-gap: 14px;
  grid-template-areas: "h h" "chart figs" "chart bill"; grid-template-rows: auto 1fr auto; }
.sx-seasons-big .sx-season-h { grid-area: h; }
.sx-seasons-big .sx-chartbox { grid-area: chart; }
.sx-seasons-big .sx-figs { grid-area: figs; grid-template-columns: 1fr; align-content: start; row-gap: 6px; margin-top: 4px; }
.sx-seasons-big .sx-sbill { grid-area: bill; flex-wrap: wrap; row-gap: 2px; }
@media (max-width: 760px) {
  .sx-seasons-big { grid-template-columns: 1fr; }
  .sx-seasons-big .sx-season { display: block; }
}
body.clb-open .wk-sheet { z-index: 4600; }
.sx-chart .pk { fill: rgba(255,255,255,.06); }
.sx-chart .gl { stroke: rgba(255,255,255,.08); stroke-width: .6; }
.sx-chart .ax { fill: rgba(235,235,245,.45); font-size: 8.5px; font-family: inherit; }
.sx-chart .cur { stroke: rgba(255,255,255,.55); stroke-width: .8; }
.sx-tip { position: absolute; top: 0; z-index: 3; width: 170px; background: rgba(28,28,30,.96); border: 0.5px solid var(--sx-sep);
  border-radius: 9px; padding: 7px 9px; font-size: 11.5px; line-height: 1.45; color: var(--sx-ink-2); pointer-events: none; box-shadow: 0 4px 14px rgba(0,0,0,.4); }
.sx-tip b { color: var(--sx-ink); font-weight: 600; font-variant-numeric: tabular-nums; }
.sx-tip .parts { display: flex; flex-wrap: wrap; gap: 0 8px; font-size: 10.5px; color: var(--sx-ink-3); }
.sx-tip i { display: inline-block; width: 7px; height: 7px; border-radius: 2px; margin-right: 3px; }
.sx-figs { display: grid; grid-template-columns: repeat(auto-fit, minmax(62px, 1fr)); gap: 2px; margin-top: 6px; }
.sx-figs span { cursor: pointer; border-radius: 7px; padding: 4px 4px 3px; min-width: 0; }
.sx-figs span:hover, .sx-figs span:focus-visible { background: rgba(255,255,255,.05); outline: none; }
.sx-figs small { display: block; font-size: 10.5px; color: var(--sx-ink-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sx-figs b { font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
.sx-sbill { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 6px; font-size: 13px; margin: 4px 4px 0; color: var(--sx-ink-2); font-variant-numeric: tabular-nums; }
.sx-sbill > span:first-child { color: var(--sx-ink-3); font-size: 12px; white-space: nowrap; }
.sx-sbill .sx-hours { white-space: nowrap; }
.sx-sbill [data-wk] { cursor: pointer; border-radius: 6px; padding: 1px 3px; }
.sx-sbill [data-wk]:hover, .sx-sbill [data-wk]:focus-visible { background: rgba(255,255,255,.05); outline: none; }
.sx-sbill .after { color: var(--sx-good); font-weight: 650; }
.sx-sbill .after small { color: var(--sx-ink-3); font-weight: 400; }
.sx-sbill .sx-hours { margin-left: auto; font-size: 12px; color: var(--sx-ink-3); }
.sx-year .sx-row { justify-content: flex-start; }
.sx-yearbox { position: relative; padding: 4px 14px 0; }
.sx-yearbox canvas { width: 100%; height: 120px; image-rendering: pixelated; border-radius: 6px; display: block; cursor: crosshair; }
.sx-year-ax { display: grid; grid-template-columns: 31fr 28fr 31fr 30fr 31fr 30fr 31fr 31fr 30fr 31fr 30fr 31fr; font-size: 10px; color: var(--sx-ink-3); margin-top: 3px; }
.sx-yearbox .sx-tip { width: 200px; top: -6px; }
#bt-year-sum { margin: 8px 14px 4px; }
.sx-btn { background: var(--sx-fill-2); border: 0; border-radius: 9px; color: var(--sx-ink); font: 500 13px/1 inherit; font-family: inherit; padding: 8px 12px; cursor: pointer; }
.sx-btn:hover, .sx-btn:focus-visible { background: #636366; outline: none; }
.sx-method { padding: 6px 14px 12px; font-size: 13.5px; line-height: 1.55; color: var(--sx-ink-2); }
.sx-method p { margin: 8px 0; }
.sx-method b { color: var(--sx-ink); font-weight: 600; }
.sx-method .sx-n { color: var(--sx-tint); cursor: pointer; border-bottom: 1px dotted rgba(255,214,10,.5); }
.sx-method .sx-n:hover, .sx-method .sx-n:focus-visible { outline: none; color: #fff; }
.sx-method a { color: var(--sx-tint); }
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

// A DAY IN EACH SEASON: the bills' own hour rows drawn as an area chart.
// Use is stacked by what uses it; the roof's solar is drawn over it, so the
// overlap is power the roof covers, use above the solar line comes from the
// grid (or the battery) and solar above use is exported. All four seasons
// share one scale so they compare at a glance. Peak hours are shaded.
const BT_SOLAR_COLOUR = "#ffd60a";
function btSeasonChart(s, sd, ymax, withBatt, size) {
  // size {W, H} in viewBox units: the lightbox draws a wider, taller box
  // so the chart fills the screen while its labels stay their normal size.
  const W = (size && size.W) || 300, H = (size && size.H) || 132, L = 26, R = 4, T = 6, B = 18;
  const x = h => L + (h + 0.5) * (W - L - R) / 24;
  const y = v => T + (H - T - B) * (1 - v / ymax);
  const rows = withBatt ? sd.rowsB : sd.rows;
  const parts = Object.keys(sd.partsH).filter(k => sd.partsH[k].some(v => v > 1e-9));
  const base = new Array(24).fill(0);
  const pathFor = (lo, hi) => {
    const top = hi.map((v, h) => `${x(h).toFixed(1)},${y(v).toFixed(1)}`);
    const bot = lo.map((v, h) => `${x(h).toFixed(1)},${y(v).toFixed(1)}`).reverse();
    return `M${top.join("L")}L${bot.join("L")}Z`;
  };
  let svg = "";
  btPeakHours().forEach(([a, b]) => {
    svg += `<rect x="${(L + a * (W - L - R) / 24).toFixed(1)}" y="${T}" width="${((b - a) * (W - L - R) / 24).toFixed(1)}" height="${H - T - B}" class="pk"/>`;
  });
  const step = ymax > 6 ? (ymax > 12 ? 5 : 2) : ymax > 2 ? 1 : 0.5;
  for (let v = step; v < ymax; v += step) {
    svg += `<line x1="${L}" x2="${W - R}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="gl"/>` +
           `<text x="${L - 4}" y="${(y(v) + 3).toFixed(1)}" class="ax" text-anchor="end">${v}</text>`;
  }
  for (const k of parts) {
    const hi = base.map((b, h) => b + sd.partsH[k][h]);
    svg += `<path d="${pathFor(base, hi)}" fill="${BT_PART_STYLE[k].colour}" fill-opacity=".55"/>`;
    hi.forEach((v, h) => { base[h] = v; });
  }
  const sol = rows.map(r => r.solar);
  svg += `<path d="${pathFor(new Array(24).fill(0), sol)}" fill="${BT_SOLAR_COLOUR}" fill-opacity=".28"/>` +
         `<polyline points="${sol.map((v, h) => `${x(h).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}" fill="none" stroke="${BT_SOLAR_COLOUR}" stroke-width="1.8"/>`;
  [[6, "6am"], [12, "noon"], [18, "6pm"]].forEach(([h, t]) => {
    svg += `<text x="${(L + h * (W - L - R) / 24).toFixed(1)}" y="${H - 4}" class="ax" text-anchor="middle">${t}</text>`;
  });
  svg += `<line class="cur" x1="0" x2="0" y1="${T}" y2="${H - B}" style="display:none"/>` +
         `<rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent"/>`;
  return `<svg viewBox="0 0 ${W} ${H}" class="sx-chart" data-season="${s}" role="img" ` +
    `aria-label="${BT_CAP(s)}: an average day, use and solar each hour">${svg}</svg>`;
}

// THE METHOD, WITH TODAY'S NUMBERS. Written from the same objects the sums
// use (the household, its Workings nodes, the prices, the battery), so the
// text cannot say one thing while the figures do another. A number with
// workings opens them.
function btMethodHtml(r, prof, isBiz) {
  const N = r.nodes, D = BT_DEFAULTS, P = N.prices;
  const E = typeof econ !== "undefined" ? econ : {};
  const BD = typeof BATTERY_DEFAULTS !== "undefined" ? BATTERY_DEFAULTS : { kw: 5, round_trip_pct: 90, reserve_pct: 10 };
  const go = (key, text) => `<span class="sx-n" data-wk="${key}" tabindex="0" role="button">${text}</span>`;
  const n1 = v => (Math.round(v * 10) / 10).toLocaleString();
  const c = v => `${(v * 100).toFixed(1).replace(/\.0$/, "")}c`;
  const days = r.run.days;
  const scale = r.cal.scale;
  const calText = r.cal.by === "bills"
    ? `Your bills scale this estimate by ${BT_SEASONS.map(s => `${n1(scale[s])}× in ${s}`).join(", ")}.`
    : r.cal.by === "total" ? `Your yearly use scales this estimate by ${n1(scale.winter)}× in every season.`
    : "Nothing entered, so the estimate is used as is. Enter a bill or your yearly use and every figure is scaled to match.";
  const useParts = isBiz
    ? `A business uses ${D.biz_kwh_per_m2} kWh per m² of roof a year (at least ${D.biz_min_kwh.toLocaleString()} kWh), spread across the working day.`
    : `Appliances, lights and cooking: ${D.appliance_base_kwh} kWh a day plus ${D.appliance_per_person_kwh} per person (${prof.people} here). ` +
      (prof.water === "gas" ? "Hot water is gas, so it uses no electricity. "
        : `Hot water: ${D.water_base_kwh} kWh a day plus ${D.water_per_person_kwh} per person${prof.water === "heatpump" ? `, divided by ${D.water_heatpump_cop} for a heat pump cylinder` : ""}, heated ${prof.waterOnSun ? "in the middle of the day on a timer" : "mostly overnight"}. `);
  const heatText = prof.heating === "gas" || prof.heating === "none"
    ? "Rooms are not heated with electricity."
    : `Heating follows how cold the town is: ${go("use", Math.round(r.load.hdd).toLocaleString() + " heating degree-days")} a year × ${D.heat_kwh_per_hdd} kWh each for a heat pump${prof.heating === "electric" ? `, × ${D.resistive_vs_heatpump} for plug-in heaters` : ""}, nearly all of it in winter.`;
  const evText = prof.evs > 0
    ? `${prof.evs} electric car${prof.evs > 1 ? "s" : ""}: ${D.ev_km_per_year.toLocaleString()} km a year each at ${D.ev_kwh_per_km} kWh/km, charged ${prof.evOnSun ? "while the sun is out" : "overnight"}.`
    : "No electric car.";
  const battText = prof.battery > 0
    ? `A ${prof.battery} kWh battery takes spare solar and gives it back when the ${isBiz ? "building" : "home"} needs it: ${BD.round_trip_pct}% of what goes in comes out, it keeps ${BD.reserve_pct}% in reserve and moves at most ${BD.kw} kW.`
    : "No battery: spare solar is exported.";
  return `<div class="sx-method">` +
    `<p><b>Electricity use.</b> ${useParts.trim()} ${heatText} ${evText} Each part has its own shape across the day and its own change by season. Together: ${go("use", Math.round(N.useYear.value).toLocaleString() + " kWh a year")}. ${calText}</p>` +
    `<p><b>Solar.</b> The roof's own average day in each season, from its tilt, direction and shading (the curve on the building card): ${go("gen", Math.round(N.genYear.value).toLocaleString() + " kWh a year")}.</p>` +
    `<p><b>Hour by hour.</b> For an average day in each season (${BT_SEASONS.map((s, i) => `${s} ${days[i]} days`).join(", ")}), each hour the solar powers the ${isBiz ? "building" : "home"} first. ${battText} The rest comes from the grid.</p>` +
    `<p><b>Bills.</b> Power from the grid at ${c(P.peak.value)} a kWh in peak hours (${btPeakText()}) and ${c(P.off.value)} off-peak; exports earn ${c(P.peakSell.value)} at peak and ${c(P.offSell.value)} off-peak; plus $${P.daily.value.toFixed(2)} a day. A month is ${P.month.value.toFixed(1)} days. A year: ${go("nowbill", btMoney(N.nowBill.value) + " today")}, ${go("solarbill", btMoney((prof.battery > 0 ? N.battBill : N.solarBill).value) + (prof.battery > 0 ? " with solar and the battery" : " with solar"))}.</p>` +
    `<p><b>Over the years.</b> Power prices rise ${E.elec_inflation_pct}% a year, export rates fall ${E.export_decline_pct}% a year and panels lose ${E.degradation_pct}% of their output a year, over a ${E.life_years}-year life. The inverter is replaced in year ${(E.inverter_replace_year || 0) + 1} at ${E.inverter_cost_pct}% of the solar cost${prof.battery > 0 ? `, the battery after ${BD.life_years || 15} years` : ""}. Money is shown ${E.real_prices ? `in today's dollars (general inflation ${E.cpi_pct}% a year taken out)` : "in each year's own dollars"}.</p>` +
    `<p class="sx-foot">Every figure in this panel comes from these steps. Tap any number to see its working. <a href="method.html#money" target="_blank" rel="noopener">The full method</a>.</p>` +
    `</div>`;
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
           offpeakSell: E[p + "_offpeak_sell_c"], daily: E[p + "_daily"], pfx: p };
}

// The bill model for a roof that isn't open: the household its reader saved
// on its card, if any, else the defaults for its type -- the same inputs the
// card itself would start from (renderBillTool).
function btEvaluateFor(bid, isBizDefault, ctx) {
  const saved = btLoadState(bid) || {};
  const type = saved.type || (isBizDefault ? "business" : "home");
  const prof = Object.assign({ type }, BT_DEFAULTS[type], saved[type] || {});
  const sys = Object.assign({ solarPerKw: null, battPerKwh: null }, saved.sys || {});
  return btEvaluate(ctx, prof, saved.calib || null, btTariffFromEcon(type === "business"), sys);
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
// The latest panel's workings, so a sheet left open while an assumption is
// changed re-works from the new calculation.
const BT_LATEST = { nodeFor: {} };
const BT_YEAR_VIEW = { measure: "use" };   // which measure the year view shows (kept across redraws)
// which of the panel's sections are open, kept across a full rebuild (a price
// or assumption change re-renders the whole panel through onEcon)
const BT_OPEN = new Set();
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
    // which way it missed: a bill under the model's floor (the daily charge
    // and a minimum of use) or over its ceiling
    const low = off.filter(([k, n]) => state.calib[k] < n.value).map(([k]) => k);
    const high = off.filter(([k, n]) => state.calib[k] > n.value).map(([k]) => k);
    const matchNote = off.length
      ? `<span class="warn">${[
          low.length ? `Your ${low.join(" and ")} bill is lower than we can match for this building. Check the prices and daily charge.` : "",
          high.length ? `Your ${high.join(" and ")} bill is higher than we can match for this building. Check the prices.` : ""
        ].filter(Boolean).join(" ")}</span>`
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
    // a payback after the panels' life is no payback
    const pbText = ret.payback == null || ret.payback > ret.years ? `over ${ret.years} yrs · doesn't pay back` : `over ${ret.years} yrs · pays back in ${ret.payback.toFixed(1)}`;
    const priceSum = `${(E[pfx + "_peak_c"] || 0)}c peak · ${(E[pfx + "_offpeak_c"] || 0)}c off-peak · ${(E[pfx + "_offpeak_sell_c"] || 0)}c export`;

    const SX_SPLIT = "<!--sx-split-->";
    // A day in each season (the next layer down from the card)
    const SD = r.run.seasons;
    const peakOf = sd => Math.max(...(withBatt ? sd.rowsB : sd.rows).map(x => Math.max(x.use, x.solar)));
    const ymaxRaw = Math.max(0.5, ...BT_SEASONS.map(s => peakOf(SD[s]))) * 1.08;
    const ymax = ymaxRaw > 6 ? Math.ceil(ymaxRaw) : Math.ceil(ymaxRaw * 2) / 2;
    const usedParts = Object.keys(r.yearParts).filter(k => r.yearParts[k] > 1);
    const fig = (key, label, n) => `<span data-wk="${key}" tabindex="0" role="button"><small>${label}</small><b>${Wk.fmt(n.value, "kWh", 1)}</b></span>`;
    const seasonCard = (s, i, size) => {
      const nowM = S[s].nowMonth, aft = S[s][after];
      return `<div class="sx-season">` +
        `<div class="sx-season-h"><b>${BT_CAP(s)}</b><span>${r.run.days[i]} days</span></div>` +
        `<div class="sx-chartbox">${btSeasonChart(s, SD[s], ymax, withBatt, size)}<div class="sx-tip" hidden></div></div>` +
        `<div class="sx-figs">` +
          fig(`s-use-${s}`, "Use", S[s].use) + fig(`s-made-${s}`, "Solar", S[s].made) +
          fig(`s-imp-${s}`, "From the grid", withBatt ? S[s].impB : S[s].imp) + fig(`s-exp-${s}`, "Exported", withBatt ? S[s].expB : S[s].exp) +
        `</div>` +
        `<div class="sx-sbill"><span>A month</span>` +
          `<span data-wk="s-now-${s}" tabindex="0" role="button">${btMoney(nowM.value)}</span><span class="sx-arrow">→</span>` +
          `<span class="after" data-wk="s-after-${s}" tabindex="0" role="button">${aft.value < -0.5 ? btMoney(-aft.value) + " <small>credit</small>" : btMoney(aft.value)}</span>` +
          `<span class="sx-hours" data-wk="s-hours-${s}" tabindex="0" role="button">the hours ›</span>` +
        `</div></div>`;
    };
    const keyHtml = `<div class="sx-key">` + usedParts.map(k => `<span><i style="background:${BT_PART_STYLE[k].colour}"></i>${BT_PART_STYLE[k].label}</span>`).join("") +
      `<span><i style="background:${BT_SOLAR_COLOUR}"></i>Solar</span><span><i class="pk"></i>Peak price</span><span class="u">kWh each hour</span></div>`;
    const seasonsHtml = haveGen
      ? `<div class="sx-h sx-h-row" id="bt-seasons"><span>A day in each season</span><button type="button" class="sx-btn sx-big" data-bigseasons="1">Open large <span aria-hidden="true">⤢</span></button></div>` +
        `<div class="sx-foot sx-lead">An average day: what this ${isBiz ? "business" : "home"} uses each hour, coloured by what uses it, and what the roof makes (yellow). Where the yellow covers the colour, the roof is powering the ${isBiz ? "business" : "home"}; colour above the yellow comes from the grid${withBatt ? " or the battery" : ""}; yellow above the colour is exported. Shaded: peak hours. Change anything under Assumptions and every figure here updates.</div>` +
        keyHtml + `<div class="sx-seasons">${BT_SEASONS.map((s, i) => seasonCard(s, i)).join("")}</div>` +
        `<details class="sx-dis" id="bt-adv-year"><summary><span class="t">The whole year, hour by hour</span><span class="sum">8,760 hours · download</span></summary>` +
          `<div class="sx-group sx-year">` +
            `<div class="sx-row">${seg("ymeasure", [["use", "Use"], ["solar", "Solar"], ["imp", "From grid"], ["exp", "Exported"], ["cost", "Bill"]], BT_YEAR_VIEW.measure, "Show")}</div>` +
            `<div class="sx-yearbox"><canvas id="bt-year-canvas" width="365" height="24" aria-label="The year, hour by hour"></canvas>` +
              `<div class="sx-year-ax">${BT_MONTH_NAME.map(m => `<span>${m}</span>`).join("")}</div><div class="sx-tip" hidden></div></div>` +
            `<div class="sx-foot" id="bt-year-sum"></div>` +
            `<div class="sx-row"><button type="button" class="sx-btn" id="bt-year-csv">Download the 8,760 hours (CSV)</button></div>` +
          `</div>` +
          `<div class="sx-foot">Each day is its season's average day, because that is how the bills are worked out, so the year adds up to the same totals. Midnight at the bottom, the evening at the top. Day-to-day weather is in Resilience.</div>` +
        `</details>`
      : "";
    const methodHtml = haveGen
      ? `<div class="sx-h">How it's worked out</div>` +
        `<details class="sx-dis" id="bt-adv-method"><summary><span class="t">The method, with the numbers used now</span><span class="sum">updates with every change</span></summary>` +
          `<div class="sx-group">${btMethodHtml(r, prof, isBiz)}</div></details>`
      : "";
    const html =
      `<div class="sx">` +
      // the corner card leads with what it costs (Josh, 18 Oct)
      `<div class="sx-group sx-glance sx-cost">` +
        row("Costs about", val(money(withBatt ? N.allCost : N.solarCost), `<small>${N.kwp.value.toFixed(1)} kW${withBatt ? " + battery" : ""}</small>`), { go: "allcost" }) +
      `</div>` +
      `<div class="sx-summary">` +
        `<div class="sx-cell hero" data-wk="save" tabindex="0" role="button"><div class="l">Bill savings</div>` +
          `<div class="v">${haveGen ? money(save1) : "–"}</div><div class="s">a year${withBatt ? ", with the battery" : ""}</div></div>` +
        `<div class="sx-cell" data-wk="lifetime" tabindex="0" role="button"><div class="l">Total bill savings</div>` +
          `<div class="v">${haveGen ? money(ret.nodes.lifetime) : "–"}</div><div class="s">over ${ret.years} years</div></div>` +
        `<div class="sx-cell" data-wk="net" tabindex="0" role="button"><div class="l">Net savings</div>` +
          `<div class="v">${haveGen ? money(ret.nodes.net) : "–"}</div><div class="s">${haveGen ? pbText : ""}</div></div>` +
        `<div class="sx-cell" data-wk="irr" tabindex="0" role="button"><div class="l">Return on investment</div>` +
          `<div class="v">${haveGen && ret.irr != null ? Wk.fmt(ret.irr, "share", 1) : "–"}</div><div class="s">a year, ${ret.real ? "after inflation" : "before inflation"}</div></div>` +
      `</div>` +
      (haveGen ? "" : `<div class="sx-foot">Loading this roof's sun&hellip;</div>`) +
      // the corner card's few key facts (shown only while compact)
      `<div class="sx-group sx-glance">` +
        (haveGen ? billPair("winter", "Winter bill", "highest month") : "") +
        (haveGen ? billPair("summer", "Summer bill", "lowest month") : "") +
      `</div>` +
      (haveGen ? `<div class="sx-foot sx-glance-foot">A month: today → ${withBatt ? "with solar and battery" : "with solar"}.${matchNote ? " " + matchNote : ""}</div>` : "") +
      (haveGen ? `<button type="button" class="sx-explore" data-explore="1">A day in each season, and how it's worked out <span aria-hidden="true">›</span></button>` : "") +
      `</div>` + SX_SPLIT + `<div class="sx sx-more">` + seasonsHtml +

      `<div class="sx-h">Over the panels' life</div>` +
      `<div class="sx-group">` +
        row("Total bill savings", val(money(ret.nodes.lifetime)), { go: "lifetime", small: `over ${ret.years} years, ` + (ret.real ? "in today's dollars" : "nominal") }) +
        row("Replacements", val(btMoney(-ret.nodes.replacements.value)), { go: "repl", small: withBatt ? "inverter and battery" : "inverter" }) +
        row("Cost", val(money(withBatt ? N.allCost : N.solarCost)), { go: "allcost" }) +
        row("Net savings", val(money(ret.nodes.net)), { go: "net", cls: "total" }) +
        row("Pays back in", val(ret.payback == null ? "not within 60 years" : ret.payback > ret.years ? `${ret.payback.toFixed(1)} years, after its ${ret.years}-year life` : ret.payback.toFixed(1) + " years"), { go: "payback" }) +
        row("Return on investment", val(ret.irr == null ? "–" : Wk.fmt(ret.irr, "share", 1)), { go: "irr", small: "a year, " + (ret.real ? "after inflation" : "before inflation") }) +
      `</div>` +
      `<div class="sx-h">Assumptions</div>` +
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
        `<details class="sx-why sx-foot"><summary>Today's dollars or nominal?</summary>Today's dollars take inflation out, so a dollar in year 20 buys what a dollar buys now. Nominal shows each year's actual dollars.</details>` +
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
        `<details class="sx-why sx-foot"><summary>How this is used</summary>These answers set how much power the home uses, and when. If you enter your bills or yearly use, we scale the estimate to match.</details>` +
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
      `<div class="sx-foot">An estimate, not a quote. <details class="sx-why sx-inline"><summary>More</summary>Based on the roof, the local climate and the assumptions above. Tap any result to see the working.</details></div>` +
      methodHtml +
      `</div>`;
    const [top, more] = html.split(SX_SPLIT);
    const moreBox = ctx.moreBox && ctx.moreBox.isConnected ? ctx.moreBox : null;
    if (moreBox) { box.innerHTML = top; moreBox.innerHTML = more; } else box.innerHTML = top + more;
    const roots = moreBox ? [box, moreBox] : [box];
    const qa = sel => roots.flatMap(rt => [...rt.querySelectorAll(sel)]);
    const q1 = sel => qa(sel)[0] || null;
    qa("details.sx-dis[id]").forEach(d => {
      if (BT_OPEN.has(d.id)) d.open = true;
      d.addEventListener("toggle", () => { if (d.open) BT_OPEN.add(d.id); else BT_OPEN.delete(d.id); });
    });

    // every result opens its workings
    const nodeFor = {
      save: () => save1, lifetime: () => ret.nodes.lifetime, net: () => ret.nodes.net, irr: () => ret.nodes.irr,
      "after-summer": () => S.summer[after], "after-winter": () => S.winter[after],
      daykw: () => N.dayKw, dayshare: () => N.dayShare, use: () => N.useYear,
      solarcost: () => N.solarCost, battcost: () => N.battCost, allcost: () => withBatt ? N.allCost : N.solarCost,
      repl: () => ret.nodes.replacements, payback: () => ret.nodes.payback,
    };
    Object.keys(r.yearParts).forEach(k => { nodeFor["part-" + k] = () => N.parts[k]; });
    BT_SEASONS.forEach(s => {
      nodeFor[`s-use-${s}`] = () => S[s].use;
      nodeFor[`s-made-${s}`] = () => S[s].made;
      nodeFor[`s-imp-${s}`] = () => withBatt ? S[s].impB : S[s].imp;
      nodeFor[`s-exp-${s}`] = () => withBatt ? S[s].expB : S[s].exp;
      nodeFor[`s-now-${s}`] = () => S[s].nowMonth;
      nodeFor[`s-after-${s}`] = () => S[s][after];
      nodeFor[`s-hours-${s}`] = () => withBatt ? S[s].battDay : S[s].solarDay;
    });
    nodeFor.gen = () => N.genYear;
    nodeFor.nowbill = () => N.nowBill;
    nodeFor.solarbill = () => withBatt ? N.battBill : N.solarBill;
    // the getter reads the LATEST calculation: a sheet left open while an
    // assumption is changed in it re-works from the new figures, not from
    // the draw it was opened from
    // (shared across re-renders: a change rebuilds the whole panel)
    BT_LATEST.nodeFor = nodeFor;
    qa("[data-wk]").forEach(elx => {
      const k = elx.dataset.wk, f = nodeFor[k];
      if (f && typeof WkView !== "undefined") WkView.bind(elx, () => (BT_LATEST.nodeFor[k] || f)());
    });

    // wiring
    const keepOpen = () => ["bt-adv-price", "bt-adv-home", "bt-adv-year", "bt-adv-method"].filter(i => { const d = q1("#" + i); return d && d.open; });
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
      if (k === "ymeasure") {
        BT_YEAR_VIEW.measure = v;
        g.querySelectorAll("button").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", String(x === b)); });
        drawYear();
        return;
      }
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
    // the workings sheet changes the system prices through the same state
    window.__billToolSys = (k, v) => { state.sys[k] = v > 0 ? v : null; save(); redraw(); };
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

    // the card's way down a layer: the four seasons, large, in the lightbox
    qa("[data-explore]").forEach(b => b.addEventListener("click", () => openBig()));

    // season charts: the hour under the pointer, from the same hour rows
    const bindCharts = (charts, zoom) => charts.forEach(svg => {
      const s = svg.dataset.season, sd = r.run.seasons[s];
      const rows = withBatt ? sd.rowsB : sd.rows;
      const tip = svg.parentElement.querySelector(".sx-tip");
      const cur = svg.querySelector(".cur");
      const show = ev => {
        const rect = svg.getBoundingClientRect();
        const px = (ev.touches ? ev.touches[0].clientX : ev.clientX) - rect.left;
        const VW = svg.viewBox.baseVal.width || 300;
        const vx = px / rect.width * VW;
        const h = Math.max(0, Math.min(23, Math.floor((vx - 26) / (VW - 30) * 24)));
        const row = rows[h];
        const cx = 26 + (h + 0.5) * (VW - 30) / 24;
        cur.setAttribute("x1", cx); cur.setAttribute("x2", cx); cur.style.display = "";
        const k = v => Wk.fmt(v, "kWh", 2);
        const partsLine = Object.keys(sd.partsH).filter(p => sd.partsH[p][h] > 0.005)
          .map(p => `<span><i style="background:${BT_PART_STYLE[p].colour}"></i>${BT_PART_STYLE[p].label} ${k(sd.partsH[p][h])}</span>`).join("");
        tip.innerHTML = `<b>${row.h}–${String(h + 1).padStart(2, "0")}:00 · ${btIsPeak(h) ? "peak" : "off-peak"} ${(row.buy * 100).toFixed(1)}c</b>` +
          `<div>Use <b>${k(row.use)}</b></div><div class="parts">${partsLine}</div>` +
          `<div>Solar <b>${k(row.solar)}</b> · used here <b>${k(row.direct)}</b></div>` +
          (withBatt ? `<div>From the battery <b>${k(row.fromB || 0)}</b></div>` : "") +
          `<div>From the grid <b>${k(row.imp)}</b> · exported <b>${k(row.exp)}</b></div>`;
        tip.hidden = false;
        tip.style.left = Math.min(Math.max(0, px - 80), rect.width - 170) + "px";
      };
      const hide = () => { tip.hidden = true; cur.style.display = "none"; };
      svg.addEventListener("mousemove", show);
      svg.addEventListener("touchstart", show, { passive: true });
      svg.addEventListener("touchmove", show, { passive: true });
      svg.addEventListener("mouseleave", hide);
      if (zoom) svg.addEventListener("click", () => openBig());
    });
    bindCharts(qa(".sx-chart"), true);

    // ALL FOUR SEASONS LARGE: the same cards, from the same calculation, in
    // the page's chart lightbox; each figure still opens its workings.
    const openBig = () => {
      if (!haveGen) return;
      let lb = document.getElementById("chart-lightbox");
      if (!lb) {
        lb = document.createElement("div");
        lb.id = "chart-lightbox";
        document.body.appendChild(lb);
      }
      const close = () => {
        lb.hidden = true;
        document.body.classList.remove("clb-open");
        if (lb.__return && lb.__return.focus) lb.__return.focus();
      };
      lb.__return = document.activeElement;
      // FILL THE SCREEN: the four charts share the window's height (two rows
      // of two, one column on a phone), less the heading, the key and each
      // season's figures. The viewBox is sized to the chart's on-screen
      // shape at 1.35 px per unit, so axis text reads at about 11 px.
      // On a wide screen each season's figures sit in a 150 px column beside
      // its chart, so the chart takes the card's full height.
      const twoCol = innerWidth > 760;
      const cardW = Math.min(1600, innerWidth - 48);
      const chartPx = twoCol ? (cardW - 44 - 14) / 2 - 28 - 164 : cardW - 44 - 20;
      const hPx = twoCol ? Math.max(240, (innerHeight - 48 - 170 - 2 * 50) / 2) : 220;
      const size = { W: Math.round(chartPx / 1.35), H: Math.round(hPx / 1.35) };
      lb.innerHTML =
        `<div class="clb-card sx sx-bigcard" role="dialog" aria-modal="true" aria-label="A day in each season">` +
          `<div class="clb-head"><h3>A day in each season</h3><button type="button" class="clb-x" aria-label="Close">&times;</button></div>` +
          `<div class="sx-foot sx-lead">An average day, hour by hour. Colours: what the ${isBiz ? "business" : "home"} uses. Yellow: what the roof makes. Where the yellow covers the colour, the roof is powering it; colour above the yellow comes from the grid${withBatt ? " or the battery" : ""}; yellow above the colour is exported. Shaded: peak hours. Tap a figure to see how it is worked out.</div>` +
          keyHtml + `<div class="sx-seasons sx-seasons-big">${BT_SEASONS.map((s, i) => seasonCard(s, i, size)).join("")}</div>` +
          `<div class="sx-bigfoot"><button type="button" class="sx-btn" data-tomethod="year">The whole year, hour by hour ›</button>` +
          `<button type="button" class="sx-btn" data-tomethod="method">How it's worked out ›</button></div>` +
        `</div>`;
      lb.hidden = false;
      document.body.classList.add("clb-open");
      lb.onclick = e => { if (e.target === lb) close(); };
      lb.onkeydown = e => { if (e.key === "Escape") close(); };
      lb.querySelector(".clb-x").addEventListener("click", close);
      // the next layer down lives in the panel: close, open it there
      lb.querySelectorAll("[data-tomethod]").forEach(b => b.addEventListener("click", () => {
        close();
        const sp = document.getElementById("side-panel");
        const ex = document.getElementById("side-panel-expand");
        if (sp && sp.classList.contains("compact") && ex) ex.click();
        setTimeout(() => {
          const t = q1(b.dataset.tomethod === "year" ? "#bt-adv-year" : "#bt-adv-method");
          if (!t) return;
          if (t.tagName === "DETAILS") t.open = true;   // fires its own toggle
          t.scrollIntoView({ behavior: "smooth", block: "start" });
        }, 80);
      }));
      const big = [...lb.querySelectorAll(".sx-chart")];
      bindCharts(big, false);
      big.forEach(svg => { svg.style.cursor = "crosshair"; });
      lb.querySelectorAll("[data-wk]").forEach(elx => {
        const k = elx.dataset.wk, f = nodeFor[k];
        if (f && typeof WkView !== "undefined") WkView.bind(elx, () => (BT_LATEST.nodeFor[k] || f)());
      });
      requestAnimationFrame(() => lb.querySelector(".clb-x").focus());
    };
    qa("[data-bigseasons]").forEach(b => b.addEventListener("click", openBig));

    // the whole year: drawn when opened, from btYearHours (the bills' own rows)
    let yearData = null;
    const getYear = () => yearData || (yearData = btYearHours(r.run, withBatt));
    const drawYear = () => {
      const cv = q1("#bt-year-canvas");
      const det = q1("#bt-adv-year");
      if (!cv || !det || !det.open) return;
      const Y = getYear(), m = BT_YEAR_VIEW.measure;
      const val = row => row[m];
      const vmax = Math.max(1e-9, ...Y.rows.map(val));
      const ctx2 = cv.getContext("2d");
      const img = ctx2.createImageData(365, 24);
      const col = { use: [143, 179, 255], solar: [255, 214, 10], imp: [255, 155, 107], exp: [48, 209, 88], cost: [255, 105, 97] }[m];
      Y.rows.forEach(row => {
        const t = Math.max(0, val(row)) / vmax;
        const i = ((23 - row.h) * 365 + row.doy) * 4;
        img.data[i] = Math.round(28 + (col[0] - 28) * t); img.data[i + 1] = Math.round(28 + (col[1] - 28) * t);
        img.data[i + 2] = Math.round(30 + (col[2] - 30) * t); img.data[i + 3] = 255;
      });
      ctx2.putImageData(img, 0, 0);
      const T = Y.totals;
      const sumEl = q1("#bt-year-sum");
      if (sumEl) {
        const ok = Y.check.ok
          ? `<span class="ok">✓ These add up to the same year as the bills above.</span>`
          : `<span class="warn">These do not add up to the bills above (${Y.check.off.join(", ")}${Y.check.daysOff.length ? "; season lengths differ from the calendar" : ""}).</span>`;
        sumEl.innerHTML = `The year: use ${Math.round(T.use).toLocaleString()} kWh, solar ${Math.round(T.solar).toLocaleString()} kWh, ` +
          `from the grid ${Math.round(T.imp).toLocaleString()} kWh, exported ${Math.round(T.exp).toLocaleString()} kWh; ` +
          `bills ${btMoney(T.billNow)} today, ${btMoney(T.bill)} ${withBatt ? "with solar and the battery" : "with solar"}. ${ok}`;
      }
    };
    const yd = q1("#bt-adv-year");
    if (yd) {
      yd.addEventListener("toggle", drawYear);
      drawYear();
      const cv = q1("#bt-year-canvas"), tip = yd.querySelector(".sx-yearbox .sx-tip");
      cv.addEventListener("mousemove", ev => {
        const rect = cv.getBoundingClientRect();
        const doy = Math.max(0, Math.min(364, Math.floor((ev.clientX - rect.left) / rect.width * 365)));
        const h = Math.max(0, Math.min(23, 23 - Math.floor((ev.clientY - rect.top) / rect.height * 24)));
        const row = getYear().rows[doy * 24 + h];
        const unit = BT_YEAR_VIEW.measure === "cost" ? "" : " kWh";
        const v = BT_YEAR_VIEW.measure === "cost" ? "$" + row.cost.toFixed(2) : row[BT_YEAR_VIEW.measure].toFixed(2);
        tip.innerHTML = `<b>${row.day} ${BT_MONTH_NAME[row.month]}, ${String(h).padStart(2, "0")}:00</b> (${row.season}) · ${v}${unit}`;
        tip.hidden = false;
        tip.style.left = Math.min(Math.max(0, ev.clientX - rect.left - 80), rect.width - 200) + "px";
      });
      cv.addEventListener("mouseleave", () => { tip.hidden = true; });
      q1("#bt-year-csv").addEventListener("click", () => {
        const blob = new Blob([btYearCsv(getYear())], { type: "text/csv" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `solar-hours-${ctx.buildingId}.csv`;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      });
    }
    if (ctx.onResult) ctx.onResult(r);
  };
  draw();
  return { redraw: draw, state, result: () => last };
}
