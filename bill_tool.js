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

// Daily kWh of each part, per season, and the hourly load (kW) it adds up to.
function btBuildLoad(p, opts) {
  const D = BT_DEFAULTS;
  const lat = opts && opts.lat, roofM2 = (opts && opts.roofM2) || 150;
  const parts = { appliance: {}, water: {}, heat: {}, ev: {} };
  const shapes = {};
  const hdd = btHdd(lat);
  if (p.type === "business") {
    const yearKwh = Math.max(D.biz_min_kwh, D.biz_kwh_per_m2 * roofM2);
    for (const s of BT_SEASONS) parts.appliance[s] = yearKwh / 365 * BT_SEASONAL.appliance[s];
    shapes.appliance = BT_SHAPES.business;
    const heatWinter = p.heating === "heatpump" ? hdd * D.heat_kwh_per_hdd * (roofM2 / 150) * 0.6
      : p.heating === "electric" ? hdd * D.heat_kwh_per_hdd * (roofM2 / 150) * 0.6 * D.resistive_vs_heatpump : 0;
    for (const s of BT_SEASONS) {
      parts.water[s] = 0;
      parts.heat[s] = heatWinter * BT_SEASONAL.heat[s];
    }
    shapes.heat = BT_SHAPES.business_heat;
    shapes.water = BT_SHAPES.water_night;
  } else {
    const people = Math.max(1, Math.min(8, p.people || 1));
    const app = D.appliance_base_kwh + D.appliance_per_person_kwh * people;
    const cyl = D.water_base_kwh + D.water_per_person_kwh * people;
    const water = p.water === "electric" ? cyl : p.water === "heatpump" ? cyl / D.water_heatpump_cop : 0;
    const size = Math.min(2.0, Math.max(0.6, roofM2 / 150));
    const heatHp = hdd * D.heat_kwh_per_hdd * size;
    const heatWinter = p.heating === "heatpump" ? heatHp
      : p.heating === "electric" ? heatHp * D.resistive_vs_heatpump : 0;
    for (const s of BT_SEASONS) {
      parts.appliance[s] = app * BT_SEASONAL.appliance[s];
      parts.water[s] = water * BT_SEASONAL.water[s];
      parts.heat[s] = heatWinter * BT_SEASONAL.heat[s];
    }
    shapes.appliance = BT_SHAPES.appliance;
    shapes.water = p.waterOnSun ? BT_SHAPES.water_sun : BT_SHAPES.water_night;
    shapes.heat = BT_SHAPES.heat;
  }
  const evDay = (p.evs || 0) * D.ev_km_per_year * D.ev_kwh_per_km / 365;
  for (const s of BT_SEASONS) parts.ev[s] = evDay * BT_SEASONAL.ev[s];
  shapes.ev = p.evOnSun ? BT_SHAPES.ev_sun : BT_SHAPES.ev_night;
  return { parts, shapes, hdd };
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

// Scale factors per season so the modelled "now" matches the bills a user
// entered (monthly, dollars, before solar). Either bill alone stands for both.
function btCalibration(load, bills, tariff) {
  const scale = { summer: 1, autumn: 1, winter: 1, spring: 1 };
  if (!bills) return { scale, matched: false };
  const fromBill = (bill, s) => {
    if (!(bill > 0)) return null;
    const kwhDay = (bill / BT_MONTH_DAYS - tariff.daily) / (tariff.buy / 100);
    const model = btDailyKwh(load, s);
    if (!(model > 0)) return null;
    return Math.min(5, Math.max(0.2, kwhDay / model));
  };
  const w = fromBill(bills.winter, "winter");
  const su = fromBill(bills.summer, "summer");
  if (w == null && su == null) return { scale, matched: false };
  const kw = w != null ? w : su, ks = su != null ? su : w;
  scale.winter = kw; scale.summer = ks;
  scale.autumn = scale.spring = (kw + ks) / 2;
  return { scale, matched: true };
}

// Run every season through the day simulator; bills and kWh with and
// without solar (and battery). gen: [season][24] kW in BT_SEASONS order.
function btRun(gen, load, scale, tariff, batteryKwh) {
  const batt = Object.assign({}, typeof BATTERY_DEFAULTS !== "undefined" ? BATTERY_DEFAULTS : {
    kw: 5, round_trip_pct: 90, reserve_pct: 10, cost_per_kwh: 1000, life_years: 15 },
    { enabled: batteryKwh > 0, kwh: batteryKwh || 0 });
  const zero = new Array(24).fill(0);
  const res = { seasons: {}, year: {} };
  const tally = { nowKwh: 0, nowBill: 0, solarImport: 0, solarExport: 0, solarBill: 0,
                  battImport: 0, battExport: 0, battBill: 0, gen: 0 };
  let soc = 0, socB = 0;
  for (let pass = 0; pass < 2; pass++) {
    BT_SEASONS.forEach((s, i) => {
      const L = btSeasonLoad(load, s, scale[s]);
      const G = gen && gen[i] ? gen[i] : zero;
      const solar = simulateDay(G, L, { enabled: false, kwh: 0, kw: 0, round_trip_pct: 100, reserve_pct: 0 }, 0);
      const withB = simulateDay(G, L, batt, socB);
      socB = withB.socEnd;
      if (pass === 0) return;
      const sum = a => a.reduce((t, v) => t + v, 0);
      const dayLoad = sum(L), dayGen = sum(G);
      const si = sum(solar.impH), se = sum(solar.expH);
      const bi = sum(withB.impH), bxp = sum(withB.expH);
      const bill = (imp, exp) => imp * tariff.buy / 100 + tariff.daily - exp * tariff.sell / 100;
      res.seasons[s] = {
        loadKwh: dayLoad, genKwh: dayGen,
        nowMonth: bill(dayLoad, 0) * BT_MONTH_DAYS,
        solarMonth: bill(si, se) * BT_MONTH_DAYS,
        battMonth: bill(bi, bxp) * BT_MONTH_DAYS,
        selfShare: dayGen > 0 ? (dayGen - se) / dayGen : 0,
        load: L, gen: G, solarImp: solar.impH, battImp: withB.impH,
      };
      const days = BT_SEASON_DAYS[s];
      tally.nowKwh += dayLoad * days;
      tally.nowBill += bill(dayLoad, 0) * days;
      tally.solarImport += si * days; tally.solarExport += se * days;
      tally.solarBill += bill(si, se) * days;
      tally.battImport += bi * days; tally.battExport += bxp * days;
      tally.battBill += bill(bi, bxp) * days;
      tally.gen += dayGen * days;
    });
  }
  res.year = tally;
  return res;
}

// Payback and lifetime value of a saving that starts at `firstYear` dollars,
// with the page's own assumptions: retail inflating, export falling,
// panels ageing, everything discounted. `exportShare` is the part of the
// saving that comes from export (which does not inflate and declines).
function btReturns(cost, firstYear, exportDollars, extraCostYear, extraCost) {
  const E = (typeof econ !== "undefined") ? econ : { elec_inflation_pct: 4, discount_rate_pct: 3,
    degradation_pct: 0.5, life_years: 30, export_decline_pct: 2 };
  const infl = 1 + E.elec_inflation_pct / 100, disc = 1 + E.discount_rate_pct / 100;
  const deg = 1 - E.degradation_pct / 100, dec = 1 - E.export_decline_pct / 100;
  const retail = firstYear - exportDollars;
  let cum = 0, payback = null, life = 0;
  for (let y = 0; y < 60; y++) {
    const v = (retail * Math.pow(infl, y) + exportDollars * Math.pow(dec, y)) * Math.pow(deg, y)
              / Math.pow(disc, y);
    const extra = (extraCost && y === extraCostYear) ? extraCost / Math.pow(disc, y) : 0;
    if (y < E.life_years) life += v - extra;
    cum += v - extra;
    if (payback === null && cum >= cost) payback = y + 1 - (cum - cost) / Math.max(v, 1e-9);
  }
  return { payback, lifetime: life, net: life - cost, years: E.life_years };
}

// Everything the panel shows, for one building.
function btEvaluate(ctx, prof, bills, prices) {
  const load = btBuildLoad(prof, { lat: ctx.lat, roofM2: ctx.roofM2 });
  const tariff = prices;
  const cal = btCalibration(load, bills, tariff);
  const gen = ctx.gen;   // [season][24] kW, BT_SEASONS order
  const run = btRun(gen, load, cal.scale, tariff, prof.battery || 0);
  const Y = run.year;
  const solarCost = (typeof systemCost === "function") ? systemCost(ctx.kwp) : ctx.kwp * 2000;
  const battPer = (typeof BATTERY_DEFAULTS !== "undefined") ? BATTERY_DEFAULTS.cost_per_kwh : 1000;
  const battLife = (typeof BATTERY_DEFAULTS !== "undefined") ? BATTERY_DEFAULTS.life_years : 15;
  const battCost = (prof.battery || 0) * battPer;
  const solarSave = Y.nowBill - Y.solarBill;
  const solarRet = btReturns(solarCost, solarSave, Y.solarExport * tariff.sell / 100);
  const battSave = Y.nowBill - Y.battBill;
  const battRet = prof.battery > 0
    ? btReturns(solarCost + battCost, battSave, Y.battExport * tariff.sell / 100, battLife, battCost)
    : null;
  const yearParts = {};
  for (const part of Object.keys(load.parts)) {
    yearParts[part] = BT_SEASONS.reduce((t, s) => t + load.parts[part][s] * cal.scale[s] * BT_SEASON_DAYS[s], 0);
  }
  return { load, cal, run, solarCost, battCost, solarSave, battSave, solarRet, battRet,
           yearParts, useKwh: Y.nowKwh, genKwh: Y.gen };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { BT_SEASONS, BT_DEFAULTS, BT_SHAPES, btHdd, btBuildLoad, btSeasonLoad,
                     btDailyKwh, btCalibration, btRun, btReturns, btEvaluate };
}

// ---------------------------------------------------------------------------
// The panel. Everything below touches the page; everything above does not.
// ---------------------------------------------------------------------------

const BT_CSS = `
.bt { margin-top: var(--s2, 8px); }
.bt-head { display:grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 14px; }
.bt-tile { background: var(--surface-2, rgba(255,255,255,.05)); border-radius: 8px; padding: 10px 10px 9px; }
.bt-tile .k { font-size: 10.5px; color: var(--ink-2, #b6bdc9); letter-spacing:.02em; }
.bt-tile .v { font-size: 20px; font-weight: 750; color: var(--ink, #f2f4f7); font-variant-numeric: tabular-nums; line-height:1.15; margin-top: 2px; }
.bt-tile .s { font-size: 10.5px; color: var(--ink-3, #7e8794); margin-top: 2px; }
.bt-tile.hero { background: linear-gradient(135deg, rgba(255,210,74,.22), rgba(255,150,40,.16)); }
.bt-tile.hero .v { color: var(--accent, #ffd24a); }
.bt-sec { font-size: 11.5px; font-weight: 700; color: var(--ink-2, #b6bdc9); text-transform: uppercase; letter-spacing: .07em; margin: 14px 0 7px; }
.bt-bills { display:grid; gap: 9px; }
.bt-bill .lab { display:flex; justify-content:space-between; font-size: 12.5px; color: var(--ink, #f2f4f7); margin-bottom: 4px; }
.bt-bill .lab span:last-child { color: var(--ink-3, #7e8794); font-size: 11px; }
.bt-bar { display:grid; grid-template-columns: 74px 1fr 62px; align-items:center; gap: 7px; font-size: 11.5px; color: var(--ink-2, #b6bdc9); margin: 2px 0; }
.bt-bar .track { height: 12px; background: rgba(255,255,255,.06); border-radius: 6px; overflow:hidden; }
.bt-bar .fill { height: 100%; border-radius: 6px; }
.bt-bar .amt { text-align:right; font-variant-numeric: tabular-nums; color: var(--ink, #f2f4f7); font-weight: 650; }
.bt-bar .amt.credit { color: var(--good, #7fd18e); }
.bt-q { display:grid; grid-template-columns: 96px 1fr; align-items:center; gap: 8px; margin: 7px 0; }
.bt-q > span { font-size: 12px; color: var(--ink-2, #b6bdc9); }
.bt-seg { display:flex; flex-wrap: wrap; gap: 4px; }
.bt-seg button { background: rgba(255,255,255,.06); color: var(--ink, #f2f4f7); border: 1px solid rgba(255,255,255,.10);
  border-radius: 14px; padding: 4px 10px; font-size: 11.5px; cursor: pointer; }
.bt-seg button:hover { border-color: rgba(255,210,74,.5); }
.bt-seg button.on { background: var(--accent, #ffd24a); color: #1a1a1a; border-color: var(--accent, #ffd24a); font-weight: 700; }
.bt-seg button:focus-visible { outline: 2px solid var(--accent, #ffd24a); outline-offset: 1px; }
.bt-step { display:flex; align-items:center; gap: 6px; }
.bt-step button { width: 26px; height: 26px; border-radius: 13px; background: rgba(255,255,255,.08); color: var(--ink,#fff); border: 1px solid rgba(255,255,255,.12); cursor:pointer; font-size: 14px; }
.bt-step b { min-width: 18px; text-align:center; font-size: 14px; }
.bt-tick { display:flex; align-items:center; gap: 7px; font-size: 12px; color: var(--ink-2,#b6bdc9); margin: 5px 0 5px 104px; cursor:pointer; }
.bt-use { margin-top: 10px; }
.bt-use .stack { display:flex; height: 10px; border-radius: 5px; overflow:hidden; background: rgba(255,255,255,.06); }
.bt-use .key { display:flex; flex-wrap:wrap; gap: 4px 12px; font-size: 11px; color: var(--ink-3,#7e8794); margin-top: 5px; }
.bt-use .key i { display:inline-block; width:8px; height:8px; border-radius:2px; margin-right:4px; vertical-align: 0; }
.bt details { margin-top: 10px; border-top: 1px solid var(--line, rgba(255,255,255,.09)); padding-top: 8px; }
.bt summary { cursor:pointer; font-size: 12.5px; color: var(--ink, #f2f4f7); font-weight: 650; }
.bt .row { display:flex; justify-content:space-between; align-items:center; gap: 10px; margin: 7px 0; font-size: 12px; color: var(--ink-2,#b6bdc9); }
.bt .row input { width: 78px; background:#2a2f38; color:#fff; border:1px solid #4a505c; border-radius:5px; padding:4px 6px; font-size: 12px; text-align:right; }
.bt .note { font-size: 11px; color: var(--ink-3,#7e8794); margin-top: 6px; line-height: 1.45; }
.bt .ok { color: var(--good,#7fd18e); }
.bt-today { background: var(--surface-2, rgba(255,255,255,.05)); border: 1px solid rgba(255,210,74,.28); border-radius: 8px; padding: 10px 12px; margin: 4px 0 6px; }
.bt-today-h { font-size: 14px; font-weight: 750; color: var(--accent, #ffd24a); margin-bottom: 7px; }
.bt-today-row { display:grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.bt-today-row label { display:flex; flex-direction:column; gap: 4px; font-size: 11.5px; color: var(--ink-2,#b6bdc9); }
.bt-today-row .in { display:flex; align-items:center; gap: 4px; color: var(--ink,#f2f4f7); font-size: 13px; }
.bt-today-row input { width: 100%; background:#2a2f38; color:#fff; border:1px solid #4a505c; border-radius:6px; padding:6px 8px; font-size: 14px; text-align:right; }
.bt-today-row input::placeholder { color: #6f7783; }
@media (max-width: 420px) { .bt-today-row { grid-template-columns: 1fr; } .bt-head { grid-template-columns: 1fr 1fr; } .bt-q { grid-template-columns: 1fr; } .bt-tick { margin-left: 0; } }
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

function btStoreKey(bid) { return "billtool:v1:" + bid; }
function btLoadState(bid) {
  try { const s = localStorage.getItem(btStoreKey(bid)); return s ? JSON.parse(s) : null; }
  catch (e) { return null; }
}
function btSaveState(bid, st) {
  try { localStorage.setItem(btStoreKey(bid), JSON.stringify(st)); } catch (e) { /* private window */ }
}

// box: an element to fill. ctx: { buildingId, isBusiness, lat, roofM2,
// kwp (the roof's whole system), panelKw (one panel's kW), gen (the roof's
// [season][24] kW, or null), prices: { home: {buy, sell, daily}, business } }
function renderBillTool(box, ctx) {
  if (!document.getElementById("bt-css")) {
    const st = document.createElement("style");
    st.id = "bt-css"; st.textContent = BT_CSS;
    document.head.appendChild(st);
  }
  const saved = btLoadState(ctx.buildingId) || {};
  const type0 = saved.type || (ctx.isBusiness ? "business" : "home");
  const state = {
    type: type0,
    home: Object.assign({}, BT_DEFAULTS.home, saved.home || {}),
    business: Object.assign({}, BT_DEFAULTS.business, saved.business || {}),
    bills: Object.assign({ winter: null, summer: null }, saved.bills || {}),
    size: Object.assign({ mode: "match", kw: null }, saved.size || {}),   // match | roof | custom
    prices: {
      home: Object.assign({}, ctx.prices.home, (saved.prices || {}).home || {}),
      business: Object.assign({}, ctx.prices.business, (saved.prices || {}).business || {}),
    },
  };
  const save = () => btSaveState(ctx.buildingId, state);

  const seg = (key, options, current) =>
    `<div class="bt-seg" data-key="${key}">` + options.map(([v, l]) =>
      `<button type="button" data-v="${v}" class="${String(v) === String(current) ? "on" : ""}">${l}</button>`).join("") +
    `</div>`;

  const draw = () => {
    const isBiz = state.type === "business";
    const prof = Object.assign({ type: state.type }, isBiz ? state.business : state.home);
    const prices = isBiz ? state.prices.business : state.prices.home;
    const haveGen = ctx.gen && ctx.gen.length === 4 && ctx.kwp > 0;
    // SYSTEM SIZE. The roof's whole capacity is rarely what a house would
    // install (16 Aspen Grove: 39.7 kW of roof against 8,100 kWh a year of
    // use). The default makes about what the building uses in a year,
    // capped by the roof; "whole roof" and a slider are one tap away.
    const roofKw = ctx.kwp;
    const whole = haveGen ? btEvaluate({ lat: ctx.lat, roofM2: ctx.roofM2, kwp: roofKw, gen: ctx.gen },
                                       prof, state.bills, prices) : null;
    const perKw = whole && roofKw > 0 ? whole.genKwh / roofKw : 0;
    const matchKw = perKw > 0 ? Math.min(roofKw, Math.max(Math.min(3, roofKw),
                      Math.round(2 * whole.useKwh / perKw) / 2)) : roofKw;
    const sizeKw = state.size.mode === "roof" ? roofKw
      : state.size.mode === "custom" && state.size.kw ? Math.min(roofKw, Math.max(0.5, state.size.kw))
      : matchKw;
    const k = roofKw > 0 ? sizeKw / roofKw : 0;
    const gen = haveGen ? ctx.gen.map(row => row.map(v => v * k)) : null;
    const r = btEvaluate({ lat: ctx.lat, roofM2: ctx.roofM2, kwp: sizeKw, gen }, prof, state.bills, prices);
    const panelsAt = kw => ctx.panelKw ? Math.round(kw / ctx.panelKw) : null;
    const W = r.run.seasons.winter, S = r.run.seasons.summer;
    const withBatt = prof.battery > 0;
    // Only claim a match that holds. A bill below the plan's daily charge
    // alone cannot be matched by any amount of use (a business plan's $8 a
    // day against a $210 summer bill), and saying "matched" then is false.
    const entered = [["summer", S], ["winter", W]].filter(([k]) => state.bills[k] > 0);
    const off = entered.filter(([k, sv]) => Math.abs(sv.nowMonth - state.bills[k]) > 0.03 * state.bills[k]);
    const matchNote = !entered.length
      ? "Electricity only, before solar. Leave blank to use our estimate (shown greyed)."
      : !off.length ? `<span class="ok">Estimate matched to your ${entered.map(([k]) => k).join(" and ")} bill${entered.length > 1 ? "s" : ""}.</span>`
      : `Your ${off.map(([k]) => k).join(" and ")} bill is lower than ${isBiz ? "this business plan's" : "this plan's"} daily charge allows &mdash; check Prices below.`;
    const save1 = withBatt ? r.battSave : r.solarSave;
    const ret = withBatt ? r.battRet : r.solarRet;
    const cost = r.solarCost + (withBatt ? r.battCost : 0);
    const maxBill = Math.max(W.nowMonth, S.nowMonth, 1);
    const bar = (label, v, colour) => {
      const w = Math.max(0, v) / maxBill * 100;
      return `<div class="bt-bar"><span>${label}</span><div class="track">` +
        `<div class="fill" style="width:${w.toFixed(1)}%;background:${colour}"></div></div>` +
        `<span class="amt${v < 0 ? " credit" : ""}">${v < 0 ? btMoney(-v) + " cr" : btMoney(v)}</span></div>`;
    };
    const billBlock = (name, s) =>
      `<div class="bt-bill"><div class="lab"><span>${name}</span>` +
      `<span>${Math.round(s.loadKwh)} kWh/day used${haveGen ? `, ${s.genKwh.toFixed(0)} made` : ""}</span></div>` +
      bar("Today", s.nowMonth, "#7e8794") +
      (haveGen ? bar("With solar", s.solarMonth, "var(--accent,#ffd24a)") : "") +
      (haveGen && withBatt ? bar("+ battery", s.battMonth, "var(--good,#7fd18e)") : "") +
      `</div>`;
    const totalUse = Object.values(r.yearParts).reduce((a, b) => a + b, 0) || 1;
    const stack = Object.entries(r.yearParts).filter(([, v]) => v > 1).map(([k, v]) =>
      `<div style="width:${(100 * v / totalUse).toFixed(1)}%;background:${BT_PART_STYLE[k].colour}" title="${BT_PART_STYLE[k].label}"></div>`).join("");
    const key = Object.entries(r.yearParts).filter(([, v]) => v > 1).map(([k, v]) =>
      `<span><i style="background:${BT_PART_STYLE[k].colour}"></i>${BT_PART_STYLE[k].label} ${Math.round(v).toLocaleString()}</span>`).join("");

    const q = (label, inner) => `<div class="bt-q"><span>${label}</span>${inner}</div>`;
    const tick = (key, on, text) =>
      `<label class="bt-tick"><input type="checkbox" data-tick="${key}" ${on ? "checked" : ""}> ${text}</label>`;
    const P = isBiz ? state.business : state.home;
    const paybackTxt = !haveGen ? "–" : ret.payback == null ? "Not within " + ret.years + " yrs"
      : ret.payback.toFixed(1) + " yrs";

    box.innerHTML =
      `<div class="bt">` +
      `<div class="bt-head">` +
        `<div class="bt-tile hero"><div class="k">Saves about</div><div class="v">${haveGen ? btMoney(save1) : "–"}</div><div class="s">a year</div></div>` +
        `<div class="bt-tile"><div class="k">Pays back in</div><div class="v">${paybackTxt}</div><div class="s">${haveGen ? (ret.net >= 0 ? btMoney(ret.net) + " ahead over " + ret.years + " yrs" : "costs more than it saves") : ""}</div></div>` +
        `<div class="bt-tile"><div class="k">Costs about</div><div class="v">${btMoney(cost)}</div><div class="s">${sizeKw.toFixed(1)} kW solar${withBatt ? " + " + prof.battery + " kWh battery" : ""}</div></div>` +
      `</div>` +
      (haveGen ? `<div class="bt-sec">Solar size</div>` +
        `<div class="bt-seg" data-key="sizemode">` +
          `<button type="button" data-v="match" class="${state.size.mode === "match" ? "on" : ""}">Sized for your use &middot; ${matchKw.toFixed(1)} kW</button>` +
          `<button type="button" data-v="roof" class="${state.size.mode === "roof" ? "on" : ""}">Whole roof &middot; ${roofKw.toFixed(1)} kW</button>` +
        `</div>` +
        `<div class="row"><input type="range" data-size="kw" min="0.5" max="${roofKw.toFixed(1)}" step="0.5" value="${sizeKw.toFixed(1)}" style="flex:1; width:auto" aria-label="Solar size in kW">` +
        `<span style="min-width:120px;text-align:right">${sizeKw.toFixed(1)} kW${panelsAt(sizeKw) ? " &middot; " + panelsAt(sizeKw) + " panels" : ""}</span></div>` +
        `<div class="note" style="margin-top:0">Makes ${Math.round(r.genKwh).toLocaleString()} kWh a year; this ${isBiz ? "business" : "home"} uses about ${Math.round(r.useKwh).toLocaleString()}.</div>`
        : "") +
      `<div class="bt-today">` +
        `<div class="bt-today-h">Your bills today?</div>` +
        `<div class="bt-today-row">` +
          `<label><span>Summer &middot; lowest month</span><span class="in">$ <input type="number" min="0" step="10" data-bill="summer" value="${state.bills.summer || ""}" placeholder="${Math.round(S.nowMonth)}" aria-label="Summer lowest monthly bill in dollars"></span></label>` +
          `<label><span>Winter &middot; highest month</span><span class="in">$ <input type="number" min="0" step="10" data-bill="winter" value="${state.bills.winter || ""}" placeholder="${Math.round(W.nowMonth)}" aria-label="Winter highest monthly bill in dollars"></span></label>` +
        `</div>` +
        `<div class="note" style="margin-top:4px">${matchNote}</div>` +
      `</div>` +
      `<div class="bt-sec">Bills before and after</div>` +
      `<div class="bt-bills">${billBlock("Winter &middot; highest month", W)}${billBlock("Summer &middot; lowest month", S)}</div>` +
      `<div class="bt-sec">About this ${isBiz ? "business" : "home"}</div>` +
      q("Building", seg("type", [["home", "Home"], ["business", "Business"]], state.type)) +
      (isBiz ? "" : q("People", `<div class="bt-step" data-step="people"><button type="button" data-d="-1" aria-label="Fewer people">&minus;</button><b>${P.people}</b><button type="button" data-d="1" aria-label="More people">+</button></div>`)) +
      (isBiz ? "" : q("Hot water", seg("water", [["electric", "Electric"], ["heatpump", "Heat pump"], ["gas", "Gas"]], P.water))) +
      (isBiz || P.water === "gas" ? "" : tick("waterOnSun", P.waterOnSun, "Heat water while the sun's out (timer)")) +
      q("Heating", seg("heating", [["heatpump", "Heat pumps"], ["electric", "Electric heaters"], ["gas", isBiz ? "Gas" : "Gas / wood"], ["none", "None"]], P.heating)) +
      q(isBiz ? "Electric vehicles" : "Electric cars", seg("evs", (isBiz ? [0, 1, 2, 4, 8] : [0, 1, 2, 3]).map(n => [n, String(n)]), P.evs)) +
      (P.evs > 0 ? tick("evOnSun", P.evOnSun, isBiz ? "Charge during the working day" : "Charge when it's sunny") : "") +
      q("Battery", seg("battery", [[0, "None"], [5, "5 kWh"], [10, "10 kWh"], [13.5, "13.5 kWh"]].concat(isBiz ? [[30, "30 kWh"]] : []), P.battery)) +
      `<div class="bt-use"><div class="note" style="margin:0 0 5px">Estimated use: ${Math.round(r.useKwh).toLocaleString()} kWh a year.</div>` +
        `<div class="stack">${stack}</div><div class="key">${key}</div></div>` +
      `<details><summary>Prices</summary>` +
        `<div class="row"><span>Power price</span><span><input type="number" min="0" step="1" data-price="buy" value="${prices.buy}"> c/kWh</span></div>` +
        `<div class="row"><span>Daily charge</span><span>$ <input type="number" min="0" step="0.1" data-price="daily" value="${prices.daily}"> /day</span></div>` +
        `<div class="row"><span>Buyback (export)</span><span><input type="number" min="0" step="0.5" data-price="sell" value="${prices.sell}"> c/kWh</span></div>` +
        `<div class="note">Including GST. Over the years power prices rise ${typeof econ !== "undefined" ? econ.elec_inflation_pct : 4}%/yr and buyback falls ${typeof econ !== "undefined" ? econ.export_decline_pct : 2}%/yr (Assumptions &amp; pricing).</div>` +
      `</details>` +
      `<div class="note">${haveGen ? "" : "Open the generation chart above to include this roof's sun. "}An estimate from the roof, the local climate and the answers above, not a quote. Bills are a typical month in each season.</div>` +
      `</div>`;

    // wiring
    box.querySelectorAll(".bt-seg").forEach(g => g.addEventListener("click", ev => {
      const b = ev.target.closest("button"); if (!b) return;
      const k = g.dataset.key, v = b.dataset.v;
      if (k === "sizemode") { state.size.mode = v; state.size.kw = null; save(); draw(); return; }
      if (k === "type") state.type = v;
      else {
        const tgt = state.type === "business" ? state.business : state.home;
        tgt[k] = (k === "evs" || k === "battery") ? +v : v;
      }
      save(); draw();
      const again = box.querySelector(`.bt-seg[data-key="${k}"] button[data-v="${v}"]`);
      if (again) again.focus();
    }));
    box.querySelectorAll(".bt-step button").forEach(b => b.addEventListener("click", () => {
      state.home.people = Math.max(1, Math.min(8, state.home.people + (+b.dataset.d)));
      save(); draw();
    }));
    box.querySelectorAll("input[data-tick]").forEach(c => c.addEventListener("change", () => {
      const tgt = state.type === "business" ? state.business : state.home;
      tgt[c.dataset.tick] = c.checked; save(); draw();
    }));
    box.querySelectorAll("input[data-size]").forEach(inp => {
      inp.addEventListener("input", () => {
        const lab = inp.nextElementSibling;
        if (lab) lab.textContent = (+inp.value).toFixed(1) + " kW";
      });
      inp.addEventListener("change", () => {
        state.size.mode = "custom"; state.size.kw = +inp.value; save(); draw();
      });
    });
    box.querySelectorAll("input[data-bill]").forEach(inp => inp.addEventListener("change", () => {
      const v = parseFloat(inp.value);
      state.bills[inp.dataset.bill] = v > 0 ? v : null; save(); draw();
    }));
    box.querySelectorAll("input[data-price]").forEach(inp => inp.addEventListener("change", () => {
      const v = parseFloat(inp.value);
      if (!(v >= 0)) return;
      (state.type === "business" ? state.prices.business : state.prices.home)[inp.dataset.price] = v;
      save(); draw();
    }));
  };
  draw();
  return { redraw: draw, state };
}
