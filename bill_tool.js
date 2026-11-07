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
//   load   = the household's use (EMI: its regional council's average home,
//            shaped hour by hour and season by season by its town's metered
//            half-hourly load -- load_profiles.js, tools/build_load_profiles.py)
//            plus electric cars; a business, its floor area's use and heating,
//            shaped across the working day;
//   solar  = the roof's own average day per season (window.__genProfile, the
//            curve drawn in the building panel), scaled to the system shown;
//   hour by hour: solar serves the load, then the battery, then the grid
//            (simulateDay, economics.js); bills = imports x price + daily
//            charge - exports x buyback;
//   bills entered by the user scale the modelled load so "now" matches them.
// Indicative, not a quote -- and the panel says so.

const BT_SEASONS = ["summer", "autumn", "winter", "spring"];
const BT_SEASON_DAYS = { summer: 90, autumn: 92, winter: 92, spring: 91 };
const BT_MONTH_DAYS = 365 / 12;   // 30.42: twelve months make the 365-day year

// Defaults, each with where it comes from. NZ households average about
// 7,000 kWh a year (MBIE); hot water is roughly a third of it in a home with
// an electric cylinder and heating a quarter to a third in a cold-climate
// home with a heat pump (EECA).
// the battery the green box adds with one tap, kWh
const BT_BATT_ADD_KWH = 10;
// the green box's battery stepper: 5 kWh steps up to 50 kWh, 10 kWh above;
// a size typed in between steps moves to the next step in that direction
const BT_BATT_MIN_KWH = 5, BT_BATT_MAX_KWH = 200;
function btBattStep(kwh, dir) {
  const step = v => (v < 50 || (v === 50 && dir < 0)) ? 5 : 10;
  const st = step(kwh);
  const next = dir > 0 ? Math.floor(kwh / st + 1e-9) * st + st : Math.ceil(kwh / st - 1e-9) * st - st;
  return Math.max(BT_BATT_MIN_KWH, Math.min(BT_BATT_MAX_KWH, next));
}
const BT_DEFAULTS = {
  home: {
    people: 3,
    evs: 0,
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
  biz_min_kwh: 6000,
};

// EMI profiles (load_profiles.js): on the page a global, in the tests required.
const BT_EMI_DATA = typeof BT_EMI !== "undefined" ? BT_EMI
  : (typeof require !== "undefined" ? require("./load_profiles.js").BT_EMI : null);
// An average NZ home holds about 2.6 people (Stats NZ, 2023 Census). A home's
// use is scaled by its people with a fixed share that does not grow with
// them (fridge, standby, space heating): use x (fixed + (1 - fixed) x people / 2.6).
// The fixed share is an estimate, not an EMI figure.
const BT_AVG_PEOPLE = 2.6, BT_FIXED_SHARE = 0.4;
// A bigger house uses more: more rooms to heat and light. Use steps by roof
// area in round bands, not a smooth curve, since the size of a house only
// roughly predicts its use. The typical house roof is 170 m² (median of the
// 923 house-sized outlines, 60-400 m², in the Queenstown pilot), so the
// 140-220 m² band is unchanged. With the people scaling, a person alone in a
// small house uses about half the average home and a big family in a large
// house about double -- the spread metered NZ homes show.
const BT_HOUSE_SIZE_BANDS = [   // [roof area up to, m², factor]
  [100, 0.7], [140, 0.85], [220, 1.0], [300, 1.15], [400, 1.3], [Infinity, 1.5]];
function btHouseSizeFactor(roofM2) {
  for (const [upTo, f] of BT_HOUSE_SIZE_BANDS) if (roofM2 < upTo) return f;
  return 1;
}
// The town whose metered load shapes this building's day: the nearest with
// EMI data within 150 km, else the whole country. Wanaka and Cromwell take
// Queenstown's: the Cromwell grid exit point carries orchard irrigation and
// frost fans, which no house has.
function btEmiRegion(lat, lon) {
  const R = BT_EMI_DATA && BT_EMI_DATA.regions;
  if (!R) return null;
  let best = "national", bd = 150;
  for (const k of ["queenstown", "wellington"]) {
    const r = R[k];
    if (!r || r.lat == null || lat == null || lon == null) continue;
    const d = Math.hypot((lat - r.lat) * 111.1, (lon - r.lon) * 111.1 * Math.cos(lat * Math.PI / 180));
    if (d < bd) { bd = d; best = k; }
  }
  // shapes are stored rounded; each day's fractions are made to add to exactly 1
  const shape = {};
  for (const s of BT_SEASONS) {
    const a = R[best].shape[s], tot = a.reduce((x, y) => x + y, 0);
    shape[s] = a.map(v => v / tot);
  }
  return Object.assign({ key: best }, R[best], { shape });
}
function btCouncilKwh(reg) {
  const C = BT_EMI_DATA.council_kwh;
  if (reg.council && C[reg.council]) return { kwh: C[reg.council], label: reg.council };
  const v = Object.values(C);
  return { kwh: v.reduce((a, b) => a + b, 0) / v.length, label: "the average of NZ's 16 regions" };
}

// A NON-HOME BUILDING'S ELECTRICITY, PER m2 OF FLOOR A YEAR, BEFORE SPACE
// HEATING (heating is added by degree-days below). Floor = roof area x storeys,
// storeys from the building's LiDAR height (height_m, src/emit_region.py)
// over the type's floor-to-floor height (storey_m). heated: the share of the
// floor kept warm.
//   business   100  BRANZ BEES (SR297/1, 2014), NZ office and retail
//                   premises: median electricity 115 kWh/m2 (office) and
//                   146 (retail) a year with heating and cooling, of which
//                   the median HVAC is 23 (Tables 19, 69); 96 for general
//                   retail, 398 where food is cooked or chilled.
//   industrial  35  sheds, workshops and stores: 35 kWh/m2 of electricity
//                   for a workshop or storage warehouse (CIBSE TM46); BEES's
//                   office/warehouse sites about 25. Tall single floors
//                   (6 m a storey) and little of the floor heated.
//   school      35  UK schools 39-43 kWh/m2 of electricity (CIBSE 2025), the
//                   Australian Green Star standard-practice benchmark 54.
//   hospital   150  medical centres and rest homes run all day and night; an
//                   estimate, not a published NZ benchmark.
//   community   40  halls, churches, clubs: occasional use; an estimate.
const BT_BIZ_KWH_M2 = {
  business: { kwh: 100, storey_m: 3.6, heated: 0.6,
    source: "BRANZ BEES: NZ offices use a median 115 kWh/m² of electricity a year and shops 146, heating and cooling included (median 23)." },
  industrial: { kwh: 35, storey_m: 6, heated: 0.15,
    source: "CIBSE TM46: workshops and storage warehouses use 35 kWh/m² of electricity a year; BEES office/warehouse sites about 25." },
  school: { kwh: 35, storey_m: 3.6, heated: 0.6, source: "Schools use 39–43 kWh/m² of electricity a year in the UK (CIBSE 2025); 54 is Australia's Green Star benchmark." },
  hospital: { kwh: 150, storey_m: 3.6, heated: 0.6, source: "An estimate for medical centres and rest homes, which run day and night." },
  community: { kwh: 40, storey_m: 3.6, heated: 0.6, source: "An estimate for halls, churches and clubs, used part of the week." },
};
const btBizKind = btype => BT_BIZ_KWH_M2[btype] ? btype : "business";
const btStoreys = (hM, kind) => hM ? Math.max(1, Math.min(40, Math.round(hM / BT_BIZ_KWH_M2[kind].storey_m))) : 1;

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
// Fixed yearly repayment on a loan of `cost` at yearly `rate` (a share) over
// `years`; at a zero rate, the cost spread evenly.
function btAnnuity(cost, rate, years) {
  if (!(years > 0)) return 0;
  if (!(rate > 0)) return cost / years;
  return cost * rate / (1 - Math.pow(1 + rate, -years));
}
// An assumption the reader can change in the workings sheet (WkView): the
// key says which of the map's settings it is (the page applies it).
const pctEd = k => ({ key: `econ:${k}`, scale: 100, unit: "%", step: 0.5, dp: 2 });
const btZero = (label, why) => Wk.input(label, 0, "kWh/day", { note: why });

// Daily kWh of each part, per season, and the hourly shapes they follow.
function btBuildLoad(p, opts) {
  const D = BT_DEFAULTS;
  const lat = opts && opts.lat, roofM2 = (opts && opts.roofM2) || 150;
  const nodes = p.type === "business" ? { appliance: {}, home: {}, ev: {} } : { home: {}, ev: {} };
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
    const kind = btBizKind(opts && opts.btype), K = BT_BIZ_KWH_M2[kind];
    const hM = opts && opts.heightM > 0 ? opts.heightM : null;
    const storeys = Wk.given("Storeys", btStoreys(hM, kind), "",
      hM ? [btIn("Height above the ground", hM, "m", { dp: 1, source: "Measured from the laser scan." }),
            btIn(`Floor-to-floor height (${kind})`, K.storey_m, "m", { dp: 1 })] : [],
      { dp: 0, formula: hM ? "height ÷ floor-to-floor height, rounded, 1 to 40" : "no height measured: one storey",
        check: () => btStoreys(hM, kind) });
    var floorM2 = Wk.product("Floor area", "m²", [roof, storeys]);
    const year = Wk.maxOf("Electricity a year, before heating and cars", "kWh/yr", [
      btIn("Smallest business", D.biz_min_kwh, "kWh/yr"),
      Wk.product("By floor area", "kWh/yr", [btIn(`Electricity per m² of floor (${kind})`, K.kwh, "kWh/m²",
        { dp: 0, source: K.source }), floorM2])]);
    const day = Wk.quotient("A typical day", "kWh/day", year, btIn("Days in a year", 365, "days"));
    for (const s of BT_SEASONS) nodes.appliance[s] = Wk.product(`Working-day use, ${s}`, "kWh/day",
      [day, seasonal("appliance", s, "Lighting is used more in the darker months.")]);
    shapes.appliance = BT_SHAPES.business;
    const base = Wk.product("Heat pump heating on the coldest days", "kWh/day", [hdd, perKwhHdd,
      Wk.quotient("Floor vs a 150 m² house", "×", floorM2, btIn("Typical house", 150, "m²")),
      btIn("Share of the floor heated", K.heated, "share", { dp: 2 })]);
    heatWinter = p.heating === "heatpump" ? base
      : p.heating === "electric" ? Wk.product("Electric heaters on the coldest days", "kWh/day", [base, resistive])
      : null;

  } else {
    // THE WHOLE HOUSE, FROM EMI. A home's use is not built up from guessed
    // appliances, cylinder and heater figures any more: its year is EMI's
    // average for its regional council, and each season's day and its shape
    // are its town's metered load (load_profiles.js). That shape carries the
    // real timing -- hot water on ripple control overnight and at the peaks,
    // heating morning and evening -- which the hand-made appliance and
    // cylinder curves did not (they put most hot water at midday).
    const reg = btEmiRegion(lat, opts && opts.lon);
    const ck = btCouncilKwh(reg);
    const people = btIn("People", Math.max(1, Math.min(8, p.people || 1)), "people", { source: "Your answer." });
    const year = Wk.product("A home like this, a year", "kWh/yr", [
      btIn(`Average home, ${ck.label}`, Math.round(ck.kwh), "kWh/yr",
        { source: "EMI (Electricity Authority), Residential consumption league table, year to 31 Dec 2025: average use per home, metered." }),
      Wk.sum("For this many people", "×", [
        btIn("Share that does not grow with people", BT_FIXED_SHARE, "×", { dp: 2, note: "Fridge, standby, heating the rooms. An estimate." }),
        Wk.product("Share that does", "×", [btIn("Rest of the use", 1 - BT_FIXED_SHARE, "×", { dp: 2 }),
          Wk.quotient("People vs an average home", "×", people,
            btIn("Average home", BT_AVG_PEOPLE, "people", { dp: 1, source: "Stats NZ, 2023 Census." }))])]),
      Wk.given("For the size of the house", btHouseSizeFactor(roofM2), "×", [roof],
        { dp: 2, formula: "by roof area: under 100 m² ×0.7, 100–140 ×0.85, 140–220 ×1, 220–300 ×1.15, 300–400 ×1.3, over 400 ×1.5",
          note: "A bigger house has more rooms to heat and light. A typical house roof is 170 m².", check: () => btHouseSizeFactor(roofM2) })]);
    const day = Wk.quotient("An average day", "kWh/day", year, btIn("Days in a year", 365, "days"));
    for (const s of BT_SEASONS) {
      nodes.home[s] = Wk.product(`Household use, ${s}`, "kWh/day", [day,
        btIn(`${BT_CAP(s)} vs the year`, reg.season_factor[s], "×",
          { dp: 2, source: `EMI metered half-hourly load, ${reg.label}, ${BT_EMI_DATA.source.split(";")[0].replace(/^.*Grid_export /, "")}.` })]);
    }
    shapes.home = reg.shape;
  }
  if (p.type === "business") {
    // one line for the business, not lights and heating apart: its working
    // day and its heating, added hour by hour into one shape per season
    for (const s of BT_SEASONS) {
      const heat = heatWinter ? Wk.product(`Heating, ${s}`, "kWh/day",
        [heatWinter, seasonal("heat", s, "Share of the coldest days' heating used in this season.")])
        : btZero(`Heating, ${s}`, p.heating === "none" ? "No heating." : "Heated with gas or wood: no electricity.");
      nodes.home[s] = Wk.sum(`Business use, ${s}`, "kWh/day", [nodes.appliance[s], heat]);
      const a = nodes.appliance[s].value, hh = heat.value, tot = a + hh || 1;
      shapes.home = shapes.home || {};
      shapes.home[s] = BT_SHAPES.business.map((v, h) => (a * v + hh * BT_SHAPES.business_heat[h]) / tot);
    }
    delete nodes.appliance; delete nodes.water; delete nodes.heat;
    delete shapes.appliance; delete shapes.water; delete shapes.heat;
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
  const bizKind = p.type === "business" ? btBizKind(opts && opts.btype) : null;
  const hM = opts && opts.heightM > 0 ? opts.heightM : null;
  return { parts, nodes, shapes, hdd: hdd.value, bizKind, heightKnown: !!hM,
           storeys: p.type === "business" ? btStoreys(hM, bizKind) : 1,
           region: p.type === "business" ? null : btEmiRegion(lat, opts && opts.lon) };
}

// The hourly load (kW) of one season, optionally scaled.
function btSeasonLoad(load, s, scale) {
  const k = scale == null ? 1 : scale;
  const out = new Array(24).fill(0);
  for (const part of Object.keys(load.parts)) {
    const day = load.parts[part][s] * k;
    const sh = Array.isArray(load.shapes[part]) ? load.shapes[part] : load.shapes[part][s];
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
  // where the home prices start from (economics.js applyRegionalPrices), and
  // whether they are still that or the reader's own
  // (btTariffFromEcon sets t.source when the area's EA prices are in use)
  const src = (t && t.source ? `${t.source}. ` : "Your plan (Prices), including GST. ") +
    `Peak hours: ${btPeakText()}. Export starts at ${t.offpeakSell}c a kWh in the day and ${t.peakSell}c at peak ` +
    `(retailer buy-back rates).`;
  const P = {
    peak: btIn("Peak price", t.peak / 100, "$/kWh", { dp: 3, source: src, edit: ed("peak_c") }),
    off: btIn("Off-peak price", t.offpeak / 100, "$/kWh", { dp: 3, source: src, edit: ed("offpeak_c") }),
    peakSell: btIn("Peak export price", t.peakSell / 100, "$/kWh", { dp: 3, source: src, edit: ed("peak_sell_c") }),
    offSell: btIn("Off-peak export price", t.offpeakSell / 100, "$/kWh", { dp: 3, source: src, edit: ed("offpeak_sell_c") }),
    daily: btIn("Daily charge", t.daily, "$/day", { source: "Your plan (Prices), including GST.",
      edit: t.pfx ? { key: `econ:${t.pfx}_daily`, scale: 1, unit: "$/day", step: 0.1, dp: 2 } : null }),
    month: btIn("Days in a typical month", BT_MONTH_DAYS, "days", { dp: 1, note: "365 ÷ 12." }),
  };
  // a flat plan is one price: one node, used for every hour
  if (t && t.flat) {
    P.peak = P.off = btIn("Price (flat plan)", t.peak / 100, "$/kWh", { dp: 3, source: src, edit: ed("buy_c") });
  }
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
function btCalibration(load, calib, tariff, seasonDays, prevScale) {
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
  // THE BILL PEOPLE KNOW is their dearest winter month and their cheapest
  // summer one, not a season's average month. The entered bill is that
  // month's (btExtremeMonth): its electricity a day, over the month's ratio
  // to the season's average day, is the average day's. The ratio is taken
  // from the load as matched so far (prevScale; btEvaluate runs this twice,
  // so the neighbouring seasons' matches shape the month too).
  const sc = prevScale || { summer: 1, autumn: 1, winter: 1, spring: 1 };
  const shape = btMonthEnergy(BT_SEASONS.map(x => btSeasonLoad(load, x, sc[x])), P);
  const fromBill = (bill, s) => {
    if (!(bill > 0)) return null;
    const model = btModelledDay(load, s);
    if (!(model.value > 0)) return null;
    const ex = btExtremeMonth(shape, s, P.daily.value);
    const avgDay = btSeasonLoad(load, s, sc[s]).reduce((t, l, h) => t + l * P.buyAt(h), 0);
    const ratio = Wk.given(`${BT_MONTH_FULL[ex.m]} vs an average ${s} day`, avgDay > 0 ? ex.energy / ex.days / avgDay : 1, "×",
      [...new Set([P.peak, P.off])], { dp: 3, formula: `the electricity cost of a day in ${BT_MONTH_FULL[ex.m]}, the ${BT_PICK[s].word} ${s} month, ` +
        `over an average ${s} day's, from the year's days blended smoothly through the seasons` });
    const entered = btIn(`Your ${BT_PICK[s].word} ${s} bill (${BT_MONTH_FULL[ex.m]})`, bill, "$/month", { source: "You entered it." });
    const kwh = Wk.quotient(`Electricity an average ${s} day uses`, "kWh/day",
      Wk.quotient("A day of that month, less the daily charge, over the month's ratio", "$/day",
        Wk.diff("Bill a day, less the daily charge", "$/day",
          Wk.quotient("Bill a day", "$/day", entered, btIn(`Days in ${BT_MONTH_FULL[ex.m]}`, ex.days, "days")), P.daily), ratio),
      btAveragePrice(load, s, P));
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
].concat(withB ? [{ key: "toB", label: "Into battery", unit: "kWh", dp: 2 },
                  { key: "fromB", label: "From battery", unit: "kWh", dp: 2 },
                  { key: "bExp", label: "Battery to grid", unit: "kWh", dp: 2 }] : []).concat([
  { key: "imp", label: "From the grid", unit: "kWh", dp: 2 }, { key: "exp", label: "Exported", unit: "kWh", dp: 2 },
  { key: "buy", label: "Price", unit: "$/kWh", dp: 3 }, { key: "sell", label: "Export price", unit: "$/kWh", dp: 3 }]);
const BT_COST_COLS = (col, label, withB) => BT_HOUR_COLS(withB).concat([{ key: col, label, unit: "$", dp: 2 }]);
const btNear = (a, b) => Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(a), Math.abs(b));
// each hour's money, and its energy balance: use = solar used directly +
// from the battery + from the grid; solar = used directly + into the
// battery + exported, less what the battery exports at peak
const btRowOk = r => btNear(r.useCost, r.use * r.buy) && btNear(r.impCost, r.imp * r.buy) && btNear(r.expPay, r.exp * r.sell)
  && btNear(r.use, r.direct + (r.fromB || 0) + r.imp) && btNear(r.solar, r.direct + (r.toB || 0) + r.exp - (r.bExp || 0));

// Run every season through the day simulator; bills and kWh with and
// without solar (and battery). gen: [season][24] kW in BT_SEASONS order.
function btRun(gen, load, cal, tariff, batteryKwh, seasonDays) {
  const batt = Object.assign({}, typeof BATTERY_DEFAULTS !== "undefined" ? BATTERY_DEFAULTS : {
    kw: 5, round_trip_pct: 90, reserve_pct: 10, cost_per_kwh: 900, life_years: 15 },
    // power grows with size: a battery charges or discharges in two hours or
    // less (a 10 kWh home battery at 5 kW), never under the default's kW
    { enabled: batteryKwh > 0, kwh: batteryKwh || 0 });
  batt.kw = Math.max(batt.kw, (batteryKwh || 0) / 2);
  const zero = new Array(24).fill(0);
  const P = btPriceNodes(tariff);
  const days = {};
  BT_SEASONS.forEach((s, i) => { days[s] = btIn(`Days of ${s}`, seasonDays ? seasonDays[i] : BT_SEASON_DAYS[s], "days"); });
  const res = { seasons: {}, year: {}, nodes: {} };
  const perSeason = {};
  // THE BATTERY'S SETTLED DAY. Each season is one typical day that repeats
  // for that season's days, so the battery starts each day where the same
  // day left it: the day is run again from its own end until the charge
  // settles. (It started each season from the previous season's one day.)
  // On a time-of-use plan the battery saves its charge for peak hours.
  const dispatch = tariff && !tariff.flat
    ? { isPeak: h => btIsPeak(h), exportPeak: tariff.peakSell > tariff.offpeakSell } : null;
  BT_SEASONS.forEach((s, i) => {
    const L = btSeasonLoad(load, s, cal.scale[s]);
    const G = gen && gen[i] ? gen[i] : zero;
    const solar = simulateDay(G, L, { enabled: false, kwh: 0, kw: 0, round_trip_pct: 100, reserve_pct: 0 }, 0);
    let soc = 0, withB = null;
    for (let k = 0; k < 40; k++) {
      withB = simulateDay(G, L, batt, soc, dispatch);
      if (Math.abs(withB.socEnd - soc) < 1e-9) break;
      soc = withB.socEnd;
    }
    perSeason[s] = { L, G, solar, withB };
  });
  const seasonUnit = u => u === "$/yr" ? "$" : u === "kWh/yr" ? "kWh" : u;
  const yearOf = (label, unit, f) => Wk.sum(label, unit,
    BT_SEASONS.map(s => Wk.product(`${BT_CAP(s)} (${days[s].value} days)`, seasonUnit(unit), [f(s), days[s]])));
  const hourNote = "Each hour, solar powers the home first. Any spare charges the battery (if there is one), then is exported. " +
                   (dispatch && dispatch.exportPeak ? "At peak, charge the home will not need before the sun refills the battery is exported, as peak export pays more. " : "") +
                   `The rest comes from the grid. Peak prices apply ${btPeakText()}.`;
  const S = {};
  for (const s of BT_SEASONS) {
    const { L, G, solar, withB } = perSeason[s];
    const use = Wk.product(`Use, a ${s} day`, "kWh/day", [btModelledDay(load, s), cal.nodes[s]]);
    const mk = (sim, wb) => L.map((l, h) => {
      const r = { h: `${String(h).padStart(2, "0")}:00`, use: l, solar: G[h], direct: Math.min(G[h], l),
                  imp: sim.impH[h], exp: sim.expH[h], buy: P.buyAt(h), sell: P.sellAt(h) };
      if (wb) { r.fromB = sim.selfH[h] - r.direct; r.toB = sim.chargeH ? sim.chargeH[h] : 0; r.bExp = sim.battExpH ? sim.battExpH[h] : 0; }
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
    const toB = tab(`Into the battery from solar, a ${s} day`, "kWh/day", rowsB, "toB", true);
    const fromB = tab(`From the battery, a ${s} day`, "kWh/day", rowsB, "fromB", true);
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
    S[s] = { use, made, imp, exp, impB, expB, toB, fromB, nowDay, solarDay, battDay,
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
      partsH[k] = (Array.isArray(load.shapes[k]) ? load.shapes[k] : load.shapes[k][s]).map(f => day * f);
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
  // what the day simulator was given, so the whole year (btYearHours) runs
  // the same battery, dispatch and prices
  res.sim = { batt, dispatch, P };
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
//   opts.fade: a battery losing capacity with age, {cap(y): its capacity
//     in year y (0-based) as a share of new, capMin, retailLoss, exportLoss:
//     the first year's power bought and export income lost at capMin
//     against new, nodes}. The loss at capacity c is (1 − c) ÷ (1 − capMin)
//     of that: the bills are worked out at both sizes, straight between.
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
  const fd = o.fade;
  for (let y = 0; y < 60; y++) {
    const defl = real ? Math.pow(cpi, y) : 1;
    const lost = fd ? (1 - fd.cap(y)) / Math.max(1e-9, 1 - fd.capMin) : 0;
    const retailY = (retail - (fd ? fd.retailLoss * lost : 0)) * Math.pow(rise, y) * Math.pow(deg, y) / defl;
    const exportY = (expN.value - (fd ? fd.exportLoss * lost : 0)) * Math.pow(dec, y) * Math.pow(deg, y) / defl;
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
               (fd ? ` The battery loses capacity as it ages, and with it some of what it saves; a new one starts full.` : "") +
               (real ? ` Inflation of ${E.cpi_pct}% a year is taken out, to give today's dollars.` : " In the dollars of each year.");
  const cols = [{ key: "year", label: "Year" }, { key: "saving", label: "Bill savings", unit: "$" },
                { key: "out", label: "Replacements", unit: "$" }, { key: "cum", label: "Running total", unit: "$" }];
  const lifeRows = rows.slice(0, E.life_years);
  const kids = [saveN, expN].concat(rates).concat(fd ? fd.nodes : []);
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
           lifeRetail, lifeExport, rows, cpiPct: E.cpi_pct,
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
  const load = btBuildLoad(prof, { lat: ctx.lat, lon: ctx.lon, roofM2: ctx.roofM2, btype: ctx.btype, heightM: ctx.heightM });
  // a bill is one month's (btExtremeMonth), and which month and its share
  // of the season depend on the matches themselves: repeat until they settle
  let cal = btCalibration(load, calib, tariff, ctx.seasonDays);
  for (let k = 0; k < 12 && cal.by === "bills"; k++) {
    const next = btCalibration(load, calib, tariff, ctx.seasonDays, cal.scale);
    const moved = BT_SEASONS.some(x => Math.abs(next.scale[x] - cal.scale[x]) > 1e-12 * Math.max(1, cal.scale[x]));
    cal = next;
    if (!moved) break;
  }
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
        ? "a smooth curve through the size bands, each band's rate at its middle: " +
          econ.cost_tiers.map(t => `${t.label} $${t.rate.toLocaleString()}/kW`).join("; ") : "flat $2,000/kW",
        source: "Typical NZ installed prices, including GST.",
        check: v => typeof costPerKw === "function" ? costPerKw(v[0]) : 2000 });
  const solarCostN = Wk.product("Solar costs", "$", [kwpN, rate]);
  const battDef = (typeof BATTERY_DEFAULTS !== "undefined") ? BATTERY_DEFAULTS : { cost_per_kwh: 900, life_years: 15 };
  const battRate = btIn("Battery cost per kWh", sy.battPerKwh > 0 ? sy.battPerKwh : battDef.cost_per_kwh, "$/kWh",
    { source: sy.battPerKwh > 0 ? "You entered it (System pricing)." : "Typical NZ installed price, including GST." });
  const battCostN = Wk.product("Battery costs", "$", [
    btIn("Battery size", prof.battery || 0, "kWh", { dp: 1, source: "Your choice." }), battRate]);
  const allCostN = Wk.sum("Solar and battery cost", "$", [solarCostN, battCostN]);
  const inverter = { year: E.inverter_replace_year, label: "Inverter",
    node: Wk.product(`Inverter replaced in year ${E.inverter_replace_year + 1}`, "$", [solarCostN,
      btIn("Inverter, as a share of the solar cost", E.inverter_cost_pct / 100, "share", { source: "Assumptions & pricing.", edit: pctEd("inverter_cost_pct") })]) };
  const bYear = E.battery_replace_year != null ? E.battery_replace_year : 14;
  const battery = { year: bYear, label: "Battery",
    node: Wk.product(`Battery replaced in year ${bYear + 1}`, "$", [battCostN,
      btIn("Its price by then, as a share of today's", (E.battery_replace_cost_pct != null ? E.battery_replace_cost_pct : 70) / 100, "share",
        { source: "Assumptions & pricing: batteries keep getting cheaper.", edit: pctEd("battery_replace_cost_pct") })]) };
  const solarRet = btReturns(solarCostN, N.solarSave, N.solarExportDollars, { replacements: [inverter] });
  // THE BATTERY AGES: it loses BATTERY_DEFAULTS.fade_pct of its capacity a
  // year (of new), and the replacement in year bYear + 1 starts full. The
  // bills are worked out again at the capacity it has just before it is
  // replaced; a year in between takes its share of the difference. Only
  // (100 − reserve_pct)% of the capacity is ever used (simulateDay).
  let fade = null;
  if (prof.battery > 0) {
    const BDf = typeof BATTERY_DEFAULTS !== "undefined" ? BATTERY_DEFAULTS : {};
    const fadeN = btIn("Battery loses capacity, a year", (BDf.fade_pct != null ? BDf.fade_pct : 2) / 100, "share",
      { dp: 1, source: "A typical lithium home battery warranty keeps 70% or more after 10 years." });
    const age = y => (y >= bYear ? y - bYear : y);
    const cap = y => Math.pow(1 - fadeN.value, age(y));
    const capMin = Math.pow(1 - fadeN.value, Math.max(1, bYear));
    const old = btRun(ctx.gen, load, cal, tariff, prof.battery * capMin, ctx.seasonDays).nodes;
    const retailLoss = (N.battSave.value - N.battExportDollars.value) - (old.battSave.value - old.battExportDollars.value);
    const exportLoss = N.battExportDollars.value - old.battExportDollars.value;
    const oldN = Wk.given(`Bill savings a year with the battery at ${Math.round(capMin * 100)}% of its capacity`, old.battSave.value, "$/yr",
      [fadeN], { note: `Worked out the same way as the first year, with a ${Wk.fmt(prof.battery * capMin, "kWh", 1)} battery: its capacity in year ${bYear}, before it is replaced.` });
    fade = { cap, capMin, retailLoss, exportLoss, nodes: [fadeN, oldN] };
  }
  const battRet = prof.battery > 0
    ? btReturns(allCostN, N.battSave, N.battExportDollars, { replacements: [inverter, battery], fade })
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
    // said in a note, not added as a child: this node is a SUM of the four
    // seasons, and a fifth child made its own check read it as double
    // ("Doesn't add up" on every open roof)
    N.genYear.note = `The same figure as the roof's generation in the yellow box ` +
      `(${Math.round(ctx.genNode.value).toLocaleString()} kWh a year).`;
  }
  // PAYING FOR IT WITH A LOAN: one fixed-repayment loan (econ.loan_rate_pct
  // over econ.loan_years) for what is bought on day one -- the solar system,
  // and the battery where there is one. A REPLACEMENT IS ADDED TO THE LOAN IN
  // THE YEAR IT FALLS DUE (the inverter, the battery) and repaid, with
  // interest, by the end of the term: someone with nothing up front has no
  // cash for it then either. Borrowing for it on day one would pay interest
  // for years on a part not yet bought. One falling after the term is paid
  // in its year, outside the loan's average. Paid upfront (btReturns: net,
  // irr) replacements are paid in cash in their year.
  const EL = typeof econ !== "undefined" ? econ : {};
  const loanRate = btIn("Loan interest rate", (EL.loan_rate_pct != null ? EL.loan_rate_pct : 5.5) / 100, "share",
    { source: "Assumptions & pricing (econ.loan_rate_pct)", edit: { key: "econ:loan_rate_pct", scale: 100, unit: "%", step: 0.1 } });
  // at least one year: the workings sheet lets the term be typed, and 0 or
  // less made the repayments $0
  const loanYears = btIn("Loan term", Math.max(1, Math.round(EL.loan_years != null ? EL.loan_years : 30)), "yrs",
    { dp: 0, source: "Assumptions & pricing (econ.loan_years)", edit: { key: "econ:loan_years", scale: 1, unit: "yrs", step: 1 } });
  const repayNode = (costN, label) => Wk.given(label, btAnnuity(costN.value, loanRate.value, loanYears.value), "$/yr",
    [costN, loanRate, loanYears],
    { formula: "amount × rate ÷ (1 − (1 + rate)^−years): the fixed yearly repayment that clears the loan in its term",
      check: k => btAnnuity(k[0], k[1], k[2]) });
  const solarLoan = Wk.sum("Borrowed: the solar system", "$", [solarCostN]);
  const allLoan = Wk.sum("Borrowed: the solar system and battery", "$", [allCostN]);
  const solarRepay = repayNode(solarLoan, "Loan repayments a year, solar");
  const allRepay = repayNode(allLoan, "Loan repayments a year, solar and battery");
  // THE HEADLINE IS AN AVERAGE YEAR OVER THE LOAN, IN TODAY'S DOLLARS.
  // The repayment is fixed in dollars of the day, so with general inflation
  // (econ.cpi_pct) it costs less each year in today's dollars, while the
  // bill savings follow power prices (btReturns: prices rise, export rates
  // fall, panels age, all in today's dollars). Year 1 is the worst year of
  // the loan; the average of the loan's years, both sides in today's
  // dollars, is the fair yearly figure. Until 7 Nov the headline set year
  // 1's savings against the nominal repayment, which understated it.
  const avgOver = (ret, save, repay, who, repls) => {
    const L = loanYears.value, rows = ret.rows.slice(0, L);
    const cpi = 1 + (ret.real ? ret.cpiPct : 0) / 100;
    // general inflation, for the dollars a replacement costs in its year
    // (btReturns prices it at today's cost rising cpi_pct a year)
    const cpiT = 1 + ret.cpiPct / 100;
    const saveAvg = Wk.given(`Bill savings, average a year over the ${L}-year loan`, rows.reduce((t, row) => t + row.saving, 0) / L, "$/yr",
      [save, ret.nodes.lifetime], {
        formula: `each year's bill savings (${ret.real ? "in today's dollars" : "in dollars of the day"}), added over the loan's ${L} years, ÷ ${L}`,
        note: `Year 1 is ${Wk.fmt(save.value, "$")}. Then power prices rise, export rates fall and the panels age, as in the savings over the panels' life.`,
        check: () => ret.rows.slice(0, L).reduce((t, row) => t + row.saving, 0) / L });
    const baseRep = Wk.given(`Repayments on ${who}, average a year${ret.real ? ", in today's dollars" : ""}`,
      repay.value * rows.reduce((t, row, y) => t + Math.pow(cpi, -y), 0) / L, "$/yr", [repay, loanYears], {
        formula: ret.real ? `the fixed repayment ÷ (1 + ${ret.cpiPct}% inflation)^(year − 1), averaged over the ${L} years`
                          : "the fixed repayment, every year",
        note: ret.real ? `The repayment stays ${Wk.fmt(repay.value, "$")} a year in dollars of the day; inflation makes each one worth less in today's dollars.` : "",
        check: k => k[0] * Array.from({ length: Math.min(k[1], ret.rows.length) }, (_, y) => Math.pow(cpi, -y)).reduce((a, b) => a + b, 0) / k[1] });
    // each year's repayments in the dollars shown, the top-ups included:
    // the year-by-year chart draws these
    const sched = rows.map((row, y) => repay.value / Math.pow(cpi, y));
    // a top-up: cost (today's dollars) in year y (0-based), borrowed at that
    // year's prices, repaid over the term's remaining years; its average a
    // year over the whole term in the dollars shown
    const topUp = (cost, rateV, term, y, add) => {
      if (!(y < term)) return 0;
      const pay = btAnnuity(cost * Math.pow(cpiT, y), rateV, term - y);
      let t = 0;
      for (let k = y; k < Math.min(term, ret.rows.length); k++) {
        const v = pay / Math.pow(cpi, k); t += v; if (add) sched[k] += v;
      }
      return t / term;
    };
    const tops = (repls || []).filter(r => r.year < L).map(r => Wk.given(
      `${r.label} replacement, added to the loan in year ${r.year + 1}: repayments, average a year`,
      topUp(r.node.value, loanRate.value, L, r.year, true), "$/yr", [r.node, loanRate, loanYears], {
        formula: `its cost at year ${r.year + 1}'s prices (today's × (1 + ${ret.cpiPct}%)^${r.year}), repaid over the last ${L - r.year} years of the loan at the same rate` +
                 (ret.real ? ", each repayment in today's dollars" : "") + `, added up and ÷ ${L}`,
        note: "Borrowed when it is bought, not on day one, so there is no interest before then.",
        check: k => topUp(k[0], k[1], k[2], r.year, false) }));
    const repAvg = tops.length
      ? Wk.sum(`Loan repayments, average a year${ret.real ? ", in today's dollars" : ""}`, "$/yr", [baseRep].concat(tops))
      : baseRep;
    const net = Wk.sum(`Net saving a year, ${who}: average over the loan`, "$/yr", [saveAvg,
      Wk.product("Less the loan repayments", "$/yr", [repAvg, btIn("", -1, "")])]);
    return { saveAvg, repAvg, net, sched };
  };
  const solarAvg = avgOver(solarRet, N.solarSave, solarRepay, "solar", [inverter]);
  const battAvg = battRet ? avgOver(battRet, N.battSave, allRepay, "solar and battery", [inverter, battery]) : null;
  const solarNet = solarAvg.net, battNet = battAvg ? battAvg.net : solarNet;
  const pickAvg = prof.battery > 0 ? battAvg : solarAvg;
  return { load, cal, run, solarCost: solarCostN.value, battCost: battCostN.value,
           solarSave: N.solarSave.value, battSave: N.battSave.value, solarRet, battRet,
           // the card's headline: net a year while the loan is repaid
           netYear: (prof.battery > 0 ? battNet : solarNet).value,
           netShown: btNetShown(pickAvg.saveAvg.value, pickAvg.repAvg.value).net,
           repaySched: pickAvg.sched,
           yearParts, useKwh: Y.nowKwh, genKwh: Y.gen,
           nodes: Object.assign({}, N, { solarCost: solarCostN, battCost: battCostN, allCost: allCostN,
                                         solarRate: rate, battRate, parts: partNodes,
                                         solarRepay, allRepay, solarNet, battNet, loanRate, loanYears,
                                         saveAvg: pickAvg.saveAvg, repAvg: pickAvg.repAvg,
                                         gen: ctx.genNode || N.genYear, kwp: kwpN }) };
}

// THE YEAR, DAY BY DAY. The bills are worked out on an average day in each
// season. The year is 365 different days built from them:
//   SEASONS BLEND. Each hour's use and solar follow a smooth curve through
//     the year (btYearBasis), so early January differs from late January.
//     The curve is solved so that each season (December to February summer, March to May autumn,
//     June to August winter, September to November spring) still adds up to
//     its average day times its days, hour by hour: the year's use and solar
//     are the bills' own, and `check` says so.
//   WEATHER. With `weather` (LowSolar.weatherYear: each day's sunlight on
//     this roof against its month's average, one real year of ERA5), each
//     day's solar is scaled by that day's share, rescaled so each season
//     still makes what the bills say. Without it the days follow the blend.
//   THE BATTERY carries its charge from one day to the next (the year is run
//     twice so it starts where it ends).
// Grid use, exports and bills come out of the 365 days and differ from the
// bills' average days: a sunny day's spare solar is exported and cannot
// cover the next grey day. `check.diff` is that difference.
const BT_MONTH_SEASON = ["summer", "summer", "autumn", "autumn", "autumn", "winter",
                         "winter", "winter", "spring", "spring", "spring", "summer"];
const BT_MONTH_LEN = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
const BT_MONTH_NAME = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const BT_MONTH_FULL = ["January", "February", "March", "April", "May", "June", "July", "August",
                       "September", "October", "November", "December"];
// The bills people remember: the dearest winter month, the cheapest summer one.
const BT_PICK = { winter: { months: [5, 6, 7], max: true, word: "highest" },
                  summer: { months: [11, 0, 1], max: false, word: "lowest" } };
let BT_CAL_DAYS = null;
function btCalendar() {
  if (!BT_CAL_DAYS) {
    const seasonOfDay = [], monthOfDay = [], dayOfMonth = [];
    for (let m = 0; m < 12; m++) for (let d = 1; d <= BT_MONTH_LEN[m]; d++) {
      seasonOfDay.push(BT_SEASONS.indexOf(BT_MONTH_SEASON[m])); monthOfDay.push(m); dayOfMonth.push(d);
    }
    BT_CAL_DAYS = { seasonOfDay, monthOfDay, dayOfMonth };
  }
  return BT_CAL_DAYS;
}
// each calendar month's electricity cost before solar (no daily charge):
// the four seasons' days blended through the year, priced hour by hour
function btMonthEnergy(seasonLoads, P) {
  const { seasonOfDay, monthOfDay } = btCalendar();
  const L = btBlendYear(seasonLoads, seasonOfDay);
  const cost = new Array(12).fill(0);
  for (let d = 0; d < 365; d++) for (let h = 0; h < 24; h++) cost[monthOfDay[d]] += L[d][h] * P.buyAt(h);
  return cost;
}
// the highest winter or lowest summer month, by its bill with the daily charge
function btExtremeMonth(cost, s, daily) {
  const B = BT_PICK[s];
  const bill = m => cost[m] + BT_MONTH_LEN[m] * daily;
  const m = B.months.reduce((a, b) => (B.max ? bill(b) > bill(a) : bill(b) < bill(a)) ? b : a);
  return { m, days: BT_MONTH_LEN[m], energy: cost[m], bill: bill(m) };
}
// THE CARD'S BILLS: that month today and after the install, from the 365
// days (btYearHours), so the bill after is the same month's with its own
// sun. {winter: {m, now, after}, summer: {...}}, each a Workings node.
function btBillMonths(run, withBatt) {
  const P = run.nodes.prices;
  const sum = () => {
    const yr = btYearHours(run, withBatt, null);
    const now = new Array(12).fill(0), aft = new Array(12).fill(0);
    for (const r of yr.rows) { now[r.month] += r.costNow; aft[r.month] += r.cost; }
    return { now, aft };
  };
  const t = sum();
  const out = {};
  for (const s of ["winter", "summer"]) {
    const B = BT_PICK[s];
    const m = B.months.reduce((a, b) => (B.max ? t.now[b] > t.now[a] : t.now[b] < t.now[a]) ? b : a);
    const name = BT_MONTH_FULL[m];
    out[s] = {
      m,
      now: Wk.given(`Your ${B.word} ${s} bill, today (${name})`, t.now[m], "$/month", [...new Set([P.peak, P.off, P.daily])],
        { formula: `${name}'s ${BT_MONTH_LEN[m]} days, each hour's use at its price, plus the daily charge; ` +
            `the ${B.word} of ${B.months.map(k => BT_MONTH_FULL[k]).join(", ")}`, check: () => sum().now[m] }),
      after: Wk.given(`${name}'s bill after the install${withBatt ? ", with the battery" : ""}`, t.aft[m], "$/month",
        [...new Set([P.peak, P.off, P.peakSell, P.offSell, P.daily])],
        { formula: `${name}'s ${BT_MONTH_LEN[m]} days, hour by hour: power from the grid at its price, less exports at theirs, plus the daily charge` +
            (withBatt ? "; the battery carries its charge from day to day" : ""), check: () => sum().aft[m] }),
    };
  }
  return out;
}
// A SMOOTH YEAR through the four seasons: a yearly wave and a half-yearly
// one, the fewest terms that can match four seasons exactly
// (1, cos θ, sin θ, cos 2θ; θ = 2π × day ÷ 365). Straight lines between the
// seasons' middle days left a sharp corner at each middle.
function btYearBasis(d) {
  const t = 2 * Math.PI * (d + 0.5) / 365;
  return [1, Math.cos(t), Math.sin(t), Math.cos(2 * t)];
}
function btSolve4(M, t) {
  const A = M.map((row, i) => row.concat([t[i]]));
  for (let c = 0; c < 4; c++) {
    let p = c; for (let r = c + 1; r < 4; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
    [A[c], A[p]] = [A[p], A[c]];
    for (let r = 0; r < 4; r++) if (r !== c) { const f = A[r][c] / A[c][c]; for (let k = c; k < 5; k++) A[r][k] -= f * A[c][k]; }
  }
  return A.map((row, i) => row[4] / row[i]);
}
// target: [season][24] average-day values; returns [365][24] whose days in
// each season average to exactly the target, hour by hour
function btBlendYear(target, seasonOfDay) {
  const W = Array.from({ length: 365 }, (_, d) => btYearBasis(d));
  const M = BT_SEASONS.map(() => [0, 0, 0, 0]), n = [0, 0, 0, 0];
  for (let d = 0; d < 365; d++) { const s = seasonOfDay[d]; n[s]++; for (let k = 0; k < 4; k++) M[s][k] += W[d][k]; }
  M.forEach((row, s) => row.forEach((_, k) => { row[k] /= n[s]; }));
  const out = Array.from({ length: 365 }, () => new Array(24).fill(0));
  for (let h = 0; h < 24; h++) {
    const C = btSolve4(M, target.map(r => r[h]));
    const sum = [0, 0, 0, 0];
    for (let d = 0; d < 365; d++) { out[d][h] = Math.max(0, W[d].reduce((t, w, k) => t + w * C[k], 0)); sum[seasonOfDay[d]] += out[d][h]; }
    // the wave can dip below zero (summer heating); held at zero, and each
    // season rescaled so it is still exact
    for (let d = 0; d < 365; d++) {
      const s = seasonOfDay[d], want = target[s][h] * n[s];
      out[d][h] = sum[s] > 0 ? out[d][h] * want / sum[s] : target[s][h];
    }
  }
  return out;
}

function btYearHours(run, withBatt, weather) {
  const N = run.nodes, P = N.prices;
  const sim = run.sim || {};
  const { seasonOfDay, monthOfDay, dayOfMonth } = btCalendar();
  const calDays = [0, 0, 0, 0]; seasonOfDay.forEach(s => { calDays[s]++; });
  const load = btBlendYear(BT_SEASONS.map(s => run.seasons[s].load), seasonOfDay);
  const gen = btBlendYear(BT_SEASONS.map(s => run.seasons[s].gen), seasonOfDay);
  // the weather: each day's solar scaled by its share, each season rescaled
  const f = weather && weather.f && weather.f.length === 365 ? weather.f : null;
  if (f) {
    const day = gen.map(r => r.reduce((a, b) => a + b, 0));
    const want = [0, 0, 0, 0], got = [0, 0, 0, 0];
    for (let d = 0; d < 365; d++) { want[seasonOfDay[d]] += day[d]; got[seasonOfDay[d]] += day[d] * f[d]; }
    for (let d = 0; d < 365; d++) {
      const s = seasonOfDay[d], k = got[s] > 0 ? f[d] * want[s] / got[s] : 1;
      for (let h = 0; h < 24; h++) gen[d][h] *= k;
    }
  }
  const none = { enabled: false, kwh: 0, kw: 0, round_trip_pct: 100, reserve_pct: 0 };
  const batt = withBatt && sim.batt && sim.batt.enabled ? sim.batt : none;
  const runYear = soc0 => {
    let soc = soc0; const days = [];
    for (let d = 0; d < 365; d++) {
      const r = simulateDay(gen[d], load[d], batt, soc, batt.enabled ? sim.dispatch : null);
      days.push(r); soc = r.socEnd;
    }
    return { days, soc };
  };
  let yr = runYear(0);
  if (batt.enabled) yr = runYear(yr.soc);
  const rows = [];
  const tot = { use: 0, solar: 0, imp: 0, exp: 0, billNow: 0, bill: 0 };
  for (let d = 0; d < 365; d++) {
    const s = BT_SEASONS[seasonOfDay[d]], r = yr.days[d];
    for (let h = 0; h < 24; h++) {
      const use = load[d][h], solar = gen[d][h], buy = P.buyAt(h), sell = P.sellAt(h);
      const direct = Math.min(use, solar);
      const row = { doy: d, month: monthOfDay[d], day: dayOfMonth[d], h, season: s, use, solar, imp: r.impH[h], exp: r.expH[h],
                    toB: batt.enabled ? r.chargeH[h] : 0, fromB: batt.enabled ? r.selfH[h] - direct : 0,
                    bExp: batt.enabled && r.battExpH ? r.battExpH[h] : 0, buy, sell };
      row.costNow = use * buy + (h === 0 ? P.daily.value : 0);
      row.cost = row.imp * buy - row.exp * sell + (h === 0 ? P.daily.value : 0);
      rows.push(row);
      tot.use += use; tot.solar += solar; tot.imp += row.imp; tot.exp += row.exp;
      tot.billNow += row.costNow; tot.bill += row.cost;
    }
  }
  // use, solar and today's bill are the bills' own; grid, exports and the
  // bill with solar come out of the 365 days
  const want = {
    use: N.useYear.value, solar: N.genYear.value, billNow: N.nowBill.value,
    imp: (withBatt ? N.battImport : N.solarImport).value, exp: (withBatt ? N.battExport : N.solarExport).value,
    bill: (withBatt ? N.battBill : N.solarBill).value,
  };
  const off = ["use", "solar", "billNow"].filter(k => Math.abs(tot[k] - want[k]) > 1e-6 * Math.max(1, Math.abs(want[k])));
  const daysOff = BT_SEASONS.filter((s, i) => calDays[i] !== run.days[i]);
  const diff = { imp: tot.imp - want.imp, exp: tot.exp - want.exp, bill: tot.bill - want.bill };
  return { rows, totals: tot, want, weather: f ? weather : null, check: { ok: !off.length && !daysOff.length, off, daysOff, diff } };
}

// THE WHOLE YEAR AS A LINE CHART, zoomable: the year (each day's total),
// a month, a week or a day (each hour). view = {zoom, start}: start is the
// first day shown (0-based day of the year). Drawn like a season's day: use
// (blue), solar (yellow), the battery's flows, the net load dotted.
const BT_YEAR_ZOOMS = { year: 365, month: null, week: 7, day: 1 };
function btMonthOf(doy) { let m = 0, d = doy; while (m < 11 && d >= BT_MONTH_LEN[m]) d -= BT_MONTH_LEN[m++]; return m; }
function btMonthStart(m) { return BT_MONTH_LEN.slice(0, m).reduce((a, b) => a + b, 0); }
function btYearSpan(view) {
  if (view.zoom === "year") return [0, 365];
  if (view.zoom === "month") { const m = btMonthOf(view.start), a = btMonthStart(m); return [a, a + BT_MONTH_LEN[m]]; }
  const n = BT_YEAR_ZOOMS[view.zoom], a = Math.max(0, Math.min(365 - n, view.start));
  return [a, a + n];
}
function btYearPoints(year, view) {
  const [a, b] = btYearSpan(view);
  if (view.zoom === "year") {
    const pts = [];
    for (let d = a; d < b; d++) {
      const p = { doy: d, use: 0, solar: 0, imp: 0, exp: 0, toB: 0, fromB: 0, bExp: 0, direct: 0 };
      for (let h = 0; h < 24; h++) {
        const r = year.rows[d * 24 + h];
        for (const k of ["use", "solar", "imp", "exp", "toB", "fromB", "bExp"]) p[k] += r[k];
        p.direct += Math.min(r.use, r.solar);
      }
      pts.push(p);
    }
    return { pts, daily: true, a, b };
  }
  return { pts: year.rows.slice(a * 24, b * 24).map(r => Object.assign({ direct: Math.min(r.use, r.solar) }, r)), daily: false, a, b };
}
function btYearSvg(year, view, W, H, withBatt) {
  const { pts, daily, a, b } = btYearPoints(year, view);
  const L = 34, R = 6, T = 8, B = 20, n = pts.length;
  const x = i => L + (i + 0.5) * (W - L - R) / n;
  let hi = Math.max(0.5, ...pts.map(p => Math.max(p.use, p.solar))) * 1.06;
  let lo = Math.min(0, ...pts.map(p => p.imp - p.exp)) * 1.06;
  const step = (() => { const raw = (hi - lo) / 5, p = Math.pow(10, Math.floor(Math.log10(raw))); return [1, 2, 2.5, 5, 10].map(k => k * p).find(k => k >= raw); })();
  hi = Math.ceil(hi / step) * step; lo = Math.floor(lo / step) * step;
  const y = v => T + (H - T - B) * (hi - v) / (hi - lo);
  const line = vals => vals.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = (loV, hiV) => `M${hiV.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("L")}L` +
    loV.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).reverse().join("L") + "Z";
  let svg = "";
  if (!daily && n <= 24 * 7) {
    pts.forEach((p, i) => { if (btIsPeak(p.h)) svg += `<rect x="${(x(i) - (W - L - R) / n / 2).toFixed(1)}" y="${T}" width="${((W - L - R) / n + 0.3).toFixed(1)}" height="${H - T - B}" class="pk"/>`; });
  }
  for (let v = lo; v <= hi + 1e-9; v += step) {
    const lab = Math.abs(v) < 1e-9 ? "0" : (v < 0 ? "−" : "") + +Math.abs(v).toFixed(2);
    svg += `<line x1="${L}" x2="${W - R}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="${Math.abs(v) < 1e-9 && lo < 0 ? "zl" : "gl"}"/>` +
           `<text x="${L - 4}" y="${(y(v) + 3).toFixed(1)}" class="ax" text-anchor="end">${lab}</text>`;
  }
  const zero = pts.map(() => 0), use = pts.map(p => p.use), sol = pts.map(p => p.solar);
  svg += `<path d="${area(zero, use)}" fill="#8fb3ff" fill-opacity=".45"/>` +
         `<path d="${area(zero, sol)}" fill="${BT_SOLAR_COLOUR}" fill-opacity=".28"/>` +
         `<polyline points="${line(sol)}" fill="none" stroke="${BT_SOLAR_COLOUR}" stroke-width="1.6"/>`;
  if (withBatt && pts.some(p => p.toB > 1e-9 || p.fromB > 1e-9)) {
    const dir = pts.map(p => p.direct);
    svg += `<path d="${area(use, pts.map(p => p.use + p.toB))}" fill="${BT_BATT_COLOUR}" fill-opacity=".35"/>` +
           `<path d="${area(dir, pts.map(p => p.direct + p.fromB))}" fill="${BT_BATT_COLOUR}" fill-opacity=".85"/>`;
  }
  svg += `<polyline class="net" points="${line(pts.map(p => p.imp - p.exp))}" fill="none"/>`;
  // the time axis
  const lab = (i, t) => `<text x="${x(i).toFixed(1)}" y="${H - 5}" class="ax" text-anchor="middle">${t}</text>`;
  if (daily) BT_MONTH_NAME.forEach((m, k) => { svg += lab(btMonthStart(k) + BT_MONTH_LEN[k] / 2 - 0.5, m); });
  else if (b - a === 1) [0, 6, 12, 18].forEach(h => { svg += lab(h, h === 0 ? "midnight" : h === 12 ? "noon" : h < 12 ? h + "am" : (h - 12) + "pm"); });
  else {
    const every = b - a > 10 ? (b - a > 20 ? 5 : 2) : 1;
    for (let d = a; d < b; d++) {
      const m = btMonthOf(d), dm = d - btMonthStart(m) + 1;
      if (b - a > 10 ? (dm === 1 || dm % every === 0) : true) svg += lab((d - a) * 24 + 11.5, `${dm} ${BT_MONTH_NAME[m]}`);
      if (d > a) svg += `<line x1="${(L + (d - a) * 24 * (W - L - R) / n).toFixed(1)}" x2="${(L + (d - a) * 24 * (W - L - R) / n).toFixed(1)}" y1="${T}" y2="${H - B}" class="gl"/>`;
    }
  }
  svg += `<line class="cur" x1="0" x2="0" y1="${T}" y2="${H - B}" style="display:none"/>` +
         `<rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent"/>`;
  return { svg: `<svg viewBox="0 0 ${W} ${H}" class="sx-chart sx-ychart" role="img" aria-label="The year: use and solar, ${daily ? "each day" : "each hour"}">${svg}</svg>`,
           pts, daily, a, b, L, R, W };
}

function btYearCsv(year) {
  const head = "date,hour,season,use_kwh,solar_kwh,from_grid_kwh,exported_kwh,into_battery_kwh,from_battery_kwh,battery_to_grid_kwh,price_per_kwh,export_price_per_kwh,bill_today_dollars,bill_with_solar_dollars";
  const f = (v, dp) => (Math.round(v * 10 ** dp) / 10 ** dp).toString();
  return head + "\n" + year.rows.map(r =>
    [`${String(r.day).padStart(2, "0")} ${BT_MONTH_NAME[r.month]}`, `${String(r.h).padStart(2, "0")}:00`, r.season,
     f(r.use, 4), f(r.solar, 4), f(r.imp, 4), f(r.exp, 4), f(r.toB || 0, 4), f(r.fromB || 0, 4), f(r.bExp || 0, 4), f(r.buy, 4), f(r.sell, 4), f(r.costNow, 4), f(r.cost, 4)].join(",")).join("\n") + "\n";
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { btBillMonths, btExtremeMonth, BT_PICK, btBattStep, BT_SEASONS, BT_DEFAULTS, BT_SHAPES, btHdd, btBuildLoad, btSeasonLoad,
                     btDailyKwh, btCalibration, btRun, btReturns, btEvaluate, btModelledDay, btPanelEcon,
                     btYearHours, btBlendYear, btYearCsv, btYearSvg, btYearPoints, btYearSpan, btRoundMoney, btMoney, btMoneyStep, btAnnuity, btNetShown, btTariffFromEcon };
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
  --sx-ink: #f5f5f7; --sx-ink-2: rgba(235,235,245,.66); --sx-ink-3: rgba(235,235,245,.56);
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
/* Today | After install: one grid for the head and both bills, so the labels
   sit over their columns */
.sx-billrow, .sx-billhead { display: grid; grid-template-columns: minmax(0, 1fr) 76px 12px 84px; align-items: center; }
.sx-billrow .sx-field { justify-self: start; }
.sx-billrow .after { justify-self: end; }
.sx-billhead { min-height: 0; padding-top: 6px; padding-bottom: 0; font-size: 11px; font-weight: 600; color: var(--sx-ink-3);
  text-transform: uppercase; letter-spacing: .04em; }
.sx-billhead > span { white-space: nowrap; }
.sx-billhead > span:last-child { text-align: right; justify-self: end; }
.sx-billhead + .sx-row::before { display: none; }
.sx-costdrop { margin: 0 0 10px; }
.sx-costdrop > summary .t { font-size: 15px; }
.sx-costdrop > summary .sum { color: var(--sx-ink-2); font-size: 13px; }
.sx-costdrop > summary .sum b { color: var(--sx-ink); font-size: 16px; font-weight: 700; }
.sx-moredrop { margin-top: 12px; }
.sx-moredrop > .sx-more { padding-top: 4px; }
.sx-prices { display: grid; grid-template-columns: repeat(auto-fit, minmax(68px, 1fr)); gap: 4px; margin: 6px 0 2px; }
.sx-pc { display: flex; flex-direction: column; gap: 1px; background: var(--sx-fill); border-radius: 8px; padding: 5px 6px 4px; cursor: text; min-width: 0; }
.sx-pc .v { display: flex; align-items: baseline; gap: 1px; font-weight: 600; font-size: 14px; }
.sx-pc .v i { font-style: normal; font-weight: 500; color: var(--sx-ink-2); font-size: 12px; }
.sx-pc input { width: 4.2ch; field-sizing: content; min-width: 1.6ch; max-width: 100%; background: none; border: 0; padding: 0; color: var(--sx-ink); font: inherit; -moz-appearance: textfield; }
.sx-pc input::-webkit-outer-spin-button, .sx-pc input::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
.sx-pc input:focus { outline: none; }
.sx-pc:focus-within { box-shadow: 0 0 0 2px var(--sx-tint); }
.sx-pc small { font-size: 11px; color: var(--sx-ink-2); white-space: nowrap; }
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
/* the lightbox's tabs and its year and year-by-year charts */
.sx-bigtabs { flex: 1; display: flex; max-width: 620px; }
.sx-bigtabs button { flex: 1; font-size: 14px; padding: 8px 10px; }
.sx-chartbtns .sx-explore { margin-top: 8px; }
.sx-yzoom { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.sx-ynav { display: inline-flex; align-items: center; gap: 8px; }
.sx-ynav b { min-width: 9em; text-align: center; font-size: 14px; }
.sx-ynav .sx-btn { min-width: 34px; padding: 4px 10px; font-size: 16px; }
.sx-ynav .sx-btn:disabled { opacity: .35; cursor: default; }
.sx-ybox { margin-top: 6px; }
.sx-ychart { cursor: zoom-in; }
.sx-ybox .sx-tip { width: 230px; font-size: 12.5px; }
.sx-ysum { font-size: 14px; color: var(--sx-ink); margin-top: 10px; }
.sx-ysum .ok { color: #34c759; } .sx-ysum .warn { color: #ff9f0a; }
.sx-bigrow { margin-top: 10px; }
.sx-loanret { margin-top: 4px; color: var(--sx-ink-2); }
.sx-rsum { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: 12px 0 10px; }
.sx-rsum > div { background: var(--sx-fill); border-radius: 12px; padding: 10px 14px; }
.sx-rsum b { display: block; font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
.sx-rsum > div:first-child b { color: #34c759; }
.sx-rsum small { font-size: 12.5px; color: var(--sx-ink-2); }
@media (max-width: 760px) { .sx-rsum { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.sx-rkey { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12.5px; color: var(--sx-ink-2); margin: 0 4px 6px; }
.sx-rkey i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }
.sx-rkey i.sv { background: #30d158; } .sx-rkey i.rp { background: #8e8e93; } .sx-rkey i.rl { background: #ff9f0a; } .sx-rkey i.nt { background: #fff; border-radius: 50%; }
.sx-rkey .u { margin-left: auto; color: var(--sx-ink-3); }
.sx-returns { width: 100%; height: auto; display: block; }
.sx-returns .rg { stroke: rgba(255,255,255,.09); } .sx-returns .rz { stroke: rgba(255,255,255,.4); }
.sx-returns .ra { font-size: 11px; fill: rgba(235,235,245,.66); } .sx-returns .rt { font-size: 12px; font-weight: 600; fill: #f5f5f7; }
.sx-returns .rs-sv { fill: #30d158; } .sx-returns .rs-rp { fill: #8e8e93; } .sx-returns .rs-rl { fill: #ff9f0a; } .sx-returns .rs-nt { fill: #fff; }
.sx-returns .rs-cp { fill: rgba(48,209,88,.55); } .sx-returns .rs-cn { fill: rgba(255,105,97,.6); }
.sx-returns .rs-hit { fill: transparent; } .sx-returns .rs-hit:hover { fill: rgba(255,255,255,.07); }
html.light .sx-returns .rg { stroke: rgba(0,0,0,.08); } html.light .sx-returns .rz { stroke: rgba(0,0,0,.4); }
html.light .sx-returns .ra { fill: rgba(60,60,67,.75); } html.light .sx-returns .rt { fill: #1d1d1f; }
html.light .sx-returns .rs-nt { fill: #1d1d1f; } html.light .sx-rkey i.nt { background: #1d1d1f; }
html.light .sx-returns .rs-sv { fill: #1f9d48; } html.light .sx-returns .rs-hit:hover { fill: rgba(0,0,0,.05); }
html.light .sx-chart .pk { fill: rgba(0,0,0,.05); } html.light .sx-chart .gl { stroke: rgba(0,0,0,.09); }
html.light .sx-chart .ax { fill: rgba(60,60,67,.72); } html.light .sx-chart .cur { stroke: rgba(0,0,0,.45); }
html.light .sx-chart .net { stroke: #1d1d1f; } html.light .sx-chart .zl { stroke: rgba(0,0,0,.35); }
html.light .sx-key i.nl { border-top-color: #1d1d1f; }
html.light .sx-rsum > div:first-child b { color: #15803d; } html.light .sx-ysum .ok { color: #15803d; } html.light .sx-ysum .warn { color: #b25000; }
.sx-seasons-tabbed { grid-template-columns: minmax(0, 1fr); }
.sx-seasons-tabbed .sx-season:not(.on) { display: none; }
.sx-season-tabs { display: flex; margin: 0 0 8px; }
.sx-season-tabs button { flex: 1; padding: 6px 4px; }
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
.sx-chart .net { stroke: rgba(255,255,255,.85); stroke-width: 1.3; stroke-dasharray: 2.5 2; stroke-linejoin: round; }
.sx-chart .zl { stroke: rgba(255,255,255,.3); stroke-width: .7; }
.sx-key i.nl { background: none; border-top: 2px dotted rgba(255,255,255,.85); height: 0; border-radius: 0; }
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
  home: { label: "Electricity use", colour: "#8fb3ff" },
  ev: { label: "Electric cars", colour: "#c79bff" },
};

// Money on the card is an estimate, so it is shown to the precision it
// deserves: to the nearest $10 under $1,000, $100 under $10,000, and $1,000
// above. "$5,742 a year" claimed a precision the inputs do not have; "$5,700"
// reads as the rough figure it is. Display only: every workings breakdown
// (Wk) shows and sums the full figures.
function btMoneyStep(v) {
  const a = Math.abs(v);
  return a < 1000 ? 10 : a < 10000 ? 100 : 1000;
}
function btRoundMoney(v, step) {
  const st = step || btMoneyStep(v);
  return Math.sign(v) * Math.round(Math.abs(v) / st) * st;
}
// The net a year as shown: savings and repayments rounded to ONE step, then
// subtracted, so the green box's parts add up on screen. The step is the
// one the NET deserves, not the larger part's: with the larger part's step a
// true $142 net showed as $200 and a true $933 as $0 ($10,453 - $9,520 at
// the nearest $1,000). The shown net is then within half a step of the true
// one in each part, at most one step in all. Money mode's pill for the open
// roof uses this too, so the two agree.
function btNetShown(save, repay, repl) {
  const st = btMoneyStep(save - repay - (repl || 0));
  return { step: st, net: btRoundMoney(save, st) - btRoundMoney(repay, st) - btRoundMoney(repl || 0, st) };
}
// `step` forces one rounding step on figures shown as a sum, so the parts
// on screen add up to the total on screen.
function btMoney(v, step) {
  const r = btRoundMoney(v, step);
  const s = "$" + Math.abs(r).toLocaleString();
  return r < 0 ? "-" + s : s;
}

// A DAY IN EACH SEASON: the bills' own hour rows drawn as an area chart.
// Use is stacked by what uses it; the roof's solar is drawn over it, so the
// overlap is power the roof covers, use above the solar line comes from the
// grid (or the battery) and solar above use is exported. All four seasons
// share one scale so they compare at a glance. Peak hours are shaded.
const BT_SOLAR_COLOUR = "#ffd60a";
const BT_BATT_COLOUR = "#0d9488";
function btSeasonChart(s, sd, ymax, withBatt, size, ymin) {
  // size {W, H} in viewBox units: the lightbox draws a wider, taller box
  // so the chart fills the screen while its labels stay their normal size.
  const W = (size && size.W) || 300, H = (size && size.H) || 132, L = 26, R = 4, T = 6, B = 18;
  const x = h => L + (h + 0.5) * (W - L - R) / 24;
  // below zero: room for the net load when the home exports (ymin < 0)
  const lo0 = Math.min(0, ymin || 0);
  const y = v => T + (H - T - B) * (ymax - v) / (ymax - lo0);
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
  for (let v = Math.ceil(lo0 / step) * step; v < ymax; v += step) {
    if (Math.abs(v) < 1e-9) continue;
    svg += `<line x1="${L}" x2="${W - R}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="gl"/>` +
           `<text x="${L - 4}" y="${(y(v) + 3).toFixed(1)}" class="ax" text-anchor="end">${v < 0 ? "−" + (-v) : v}</text>`;
  }
  if (lo0 < 0) svg += `<line x1="${L}" x2="${W - R}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}" class="zl"/>` +
                      `<text x="${L - 4}" y="${(y(0) + 3).toFixed(1)}" class="ax" text-anchor="end">0</text>`;
  for (const k of parts) {
    const hi = base.map((b, h) => b + sd.partsH[k][h]);
    svg += `<path d="${pathFor(base, hi)}" fill="${BT_PART_STYLE[k].colour}" fill-opacity=".55"/>`;
    hi.forEach((v, h) => { base[h] = v; });
  }
  const sol = rows.map(r => r.solar);
  svg += `<path d="${pathFor(new Array(24).fill(0), sol)}" fill="${BT_SOLAR_COLOUR}" fill-opacity=".28"/>` +
         `<polyline points="${sol.map((v, h) => `${x(h).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}" fill="none" stroke="${BT_SOLAR_COLOUR}" stroke-width="1.8"/>`;
  // THE BATTERY'S FLOWS. Charging: the part of the spare yellow above the
  // use that goes into the battery (light teal, between the use and the
  // solar line). Powering the home: the part of the use the battery covers,
  // above what solar covers directly (solid teal).
  if (withBatt && rows.some(r => (r.toB || 0) > 1e-9 || (r.fromB || 0) > 1e-9)) {
    const use = rows.map(r => r.use), direct = rows.map(r => r.direct);
    svg += `<path d="${pathFor(use, use.map((u, h) => u + (rows[h].toB || 0)))}" fill="${BT_BATT_COLOUR}" fill-opacity=".35"/>` +
           `<path d="${pathFor(direct, direct.map((d, h) => d + (rows[h].fromB || 0)))}" fill="${BT_BATT_COLOUR}" fill-opacity=".85"/>`;
  }
  // NET LOAD at the meter: from the grid less exported, each hour; below
  // zero while the home exports
  svg += `<polyline class="net" points="${rows.map((r, h) => `${x(h).toFixed(1)},${y(r.imp - r.exp).toFixed(1)}`).join(" ")}" fill="none"/>`;
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
  const reg = r.load.region;
  const useParts = isBiz
    ? `${({ business: "A business", industrial: "A workshop or store", school: "A school", hospital: "A medical centre or rest home", community: "A hall, church or club" })[r.load.bizKind || "business"]} uses ${BT_BIZ_KWH_M2[r.load.bizKind || "business"].kwh} kWh per m² of floor a year before heating (at least ${D.biz_min_kwh.toLocaleString()} kWh), spread across the working day. Its floor is its roof area × ${r.load.storeys} storey${r.load.storeys === 1 ? "" : "s"}${r.load.storeys > 1 || r.load.heightKnown ? ", counted from its height in the laser scan" : ""}.`
    : `A home's use is the Electricity Authority's figure for the average home in ${btCouncilKwh(reg).label} (${Math.round(btCouncilKwh(reg).kwh).toLocaleString()} kWh a year, 2025), scaled for ${prof.people} ${prof.people === 1 ? "person" : "people"} against an average of ${BT_AVG_PEOPLE}, and for the size of the house: a roof under 100 m² uses 30% less than a typical one, 100–140 m² 15% less, 220–300 m² 15% more, 300–400 m² 30% more, over 400 m² 50% more. ` +
      `Each season's day, and how it is spread over the hours, follows the metered half-hourly load of ${reg.label} over the last year (EMI): hot water and heating run when homes actually run them, overnight on ripple control and at the morning and evening peaks.`;
  const heatText = !isBiz ? ""
    : prof.heating === "gas" || prof.heating === "none"
    ? "Rooms are not heated with electricity."
    : `Heating follows how cold the town is: ${go("use", Math.round(r.load.hdd).toLocaleString() + " heating degree-days")} a year × ${D.heat_kwh_per_hdd} kWh each for a heat pump${prof.heating === "electric" ? `, × ${D.resistive_vs_heatpump} for plug-in heaters` : ""}, nearly all of it in winter.`;
  const evText = prof.evs > 0
    ? `${prof.evs} electric car${prof.evs > 1 ? "s" : ""}: ${D.ev_km_per_year.toLocaleString()} km a year each at ${D.ev_kwh_per_km} kWh/km, charged ${prof.evOnSun ? "while the sun is out" : "overnight"}.`
    : "No electric car.";
  const battText = prof.battery > 0
    ? `A ${prof.battery} kWh battery takes spare solar and gives it back when the ${isBiz ? "building" : "home"} needs it: ${BD.round_trip_pct}% of what goes in comes out, it keeps ${BD.reserve_pct}% in reserve and moves at most ${BD.kw} kW.`
    : "No battery: spare solar is exported.";
  return `<div class="sx-method">` +
    `<p><b>Electricity use.</b> ${useParts.trim()} ${heatText} ${evText} Together: ${go("use", Math.round(N.useYear.value).toLocaleString() + " kWh a year")}. ${calText}</p>` +
    `<p><b>Solar.</b> The roof's own average day in each season, from its tilt, direction and shading (the curve on the building card): ${go("gen", Math.round(N.genYear.value).toLocaleString() + " kWh a year")}.</p>` +
    `<p><b>Hour by hour.</b> For an average day in each season (${BT_SEASONS.map((s, i) => `${s} ${days[i]} days`).join(", ")}), each hour the solar powers the ${isBiz ? "building" : "home"} first. ${battText} The rest comes from the grid.</p>` +
    `<p><b>Bills.</b> Power from the grid at ${c(P.peak.value)} a kWh in peak hours (${btPeakText()}) and ${c(P.off.value)} off-peak; exports earn ${c(P.peakSell.value)} at peak and ${c(P.offSell.value)} off-peak; plus $${P.daily.value.toFixed(2)} a day. A month is ${P.month.value.toFixed(1)} days. A year: ${go("nowbill", btMoney(N.nowBill.value) + " today")}, ${go("solarbill", btMoney((prof.battery > 0 ? N.battBill : N.solarBill).value) + (prof.battery > 0 ? " with solar and the battery" : " with solar"))}.</p>` +
    `<p><b>Over the years.</b> Power prices rise ${E.elec_inflation_pct}% a year, export rates fall ${E.export_decline_pct}% a year and panels lose ${E.degradation_pct}% of their output a year, over a ${E.life_years}-year life. The inverter is replaced in year ${(E.inverter_replace_year || 0) + 1} at ${E.inverter_cost_pct}% of the solar cost${prof.battery > 0 ? `, the battery after ${BD.life_years || 15} years` : ""}. Money is shown ${E.real_prices ? `in today's dollars (general inflation ${E.cpi_pct}% a year taken out)` : "in each year's own dollars"}.</p>` +
    `<p class="sx-foot">Every figure in this panel comes from these steps. Tap any number to see its working. The full method is listed under Docs.</p>` +
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
//
// A HOME IS PRICED FOR ITS OWN AREA. `area` is the building's map region (its
// `sr`); economics.js homePricesFor gives the Electricity Authority's prices
// for that area's network region (SITE.priceRegionByArea). They apply while
// the reader has not set home prices of their own in Assumptions & pricing;
// once they have, theirs apply everywhere. Businesses keep econ's prices.
function btTariffFromEcon(isBiz, area) {
  const E = typeof econ !== "undefined" ? econ : {};
  const p = isBiz ? "biz" : "home";
  const t = { peak: E[p + "_peak_c"], offpeak: E[p + "_offpeak_c"], peakSell: E[p + "_peak_sell_c"],
              offpeakSell: E[p + "_offpeak_sell_c"], daily: E[p + "_daily"], pfx: p };
  const r = !isBiz && typeof homePricesFor === "function" ? homePricesFor(area) : null;
  if (r) Object.assign(t, { peak: r.peak_c, offpeak: r.offpeak_c, daily: r.daily, source: r.source });
  // A FLAT PLAN: one price all day -- the area's EA price to the cent for a
  // home, else the map's flat price (home_buy_c / biz_buy_c)
  if (E.plan === "flat") {
    const flat = r ? r.flat_c : E[p + "_buy_c"];
    Object.assign(t, { peak: flat, offpeak: flat, flat: true });
  }
  // peak export is never paid below daytime export
  t.peakSell = Math.max(t.peakSell, t.offpeakSell);
  return t;
}

// The bill model for a roof that isn't open: the household its reader saved
// on its card, if any, else the defaults for its type -- the same inputs the
// card itself would start from (renderBillTool).
function btEvaluateFor(bid, isBizDefault, ctx) {
  const saved = btLoadState(bid) || {};
  const type = saved.type || (isBizDefault ? "business" : "home");
  const prof = Object.assign({ type }, BT_DEFAULTS[type], saved[type] || {});
  const sys = Object.assign({ solarPerKw: null, battPerKwh: null }, saved.sys || {});
  return btEvaluate(ctx, prof, saved.calib || null, btTariffFromEcon(type === "business", ctx.area), sys);
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
const BT_YEAR_VIEW = { zoom: "year", start: 0 };
// The season the panel shows (kept across redraws); today's season at first,
// southern hemisphere: Dec-Feb summer, Mar-May autumn, Jun-Aug winter.
const BT_SEASON_TAB = { on: ["summer", "autumn", "winter", "spring"][Math.floor(((new Date().getMonth() + 1) % 12) / 3)] };   // which measure the year view shows (kept across redraws)
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
    const tariff = btTariffFromEcon(isBiz, ctx.area);
    const haveGen = !!(ctx.gen && ctx.gen.length === 4 && ctx.kwp > 0);
    const r = btEvaluate({ lat: ctx.lat, lon: ctx.lon, roofM2: ctx.roofM2, btype: ctx.btype, heightM: ctx.heightM, kwp: ctx.kwp, kwpNode: ctx.kwpNode,
                           gen: haveGen ? ctx.gen : null, seasonDays: ctx.seasonDays, genNode: ctx.genNode },
                         prof, state.calib, tariff, state.sys);
    last = r;
    const N = r.nodes, S = N.seasons;
    const withBatt = prof.battery > 0;
    const ret = withBatt ? r.battRet : r.solarRet;
    const save1 = withBatt ? N.battSave : N.solarSave;
    const after = withBatt ? "battMonth" : "solarMonth";
    // the card's two bills: the highest winter and lowest summer month
    const BM = haveGen ? btBillMonths(r.run, withBatt) : null;
    const E = typeof econ !== "undefined" ? econ : {};
    const pfx = isBiz ? "biz" : "home";
    const calibBy = r.cal.by;
    const money = n => btMoney(n.value);
    const entered = state.calib && !state.calib.total
      ? [["summer", BM ? BM.summer.now : S.summer.nowMonth], ["winter", BM ? BM.winter.now : S.winter.nowMonth]].filter(([k]) => state.calib[k] > 0) : [];
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
      : "";
    const P = isBiz ? state.business : state.home;
    const field = (id, value, attrs, pre, post, est) =>
      `<label class="sx-field${est ? " est" : ""}">${pre ? `<span>${pre}</span>` : ""}<input id="bt-${id}" type="number" inputmode="decimal" ${attrs} value="${value}">${post ? `<span>${post}</span>` : ""}</label>`;
    const priceStrip = () => {
      const pc = (key, label, value, pre, post, step) =>
        `<label class="sx-pc"><span class="v">${pre ? `<i>${pre}</i>` : ""}<input type="number" inputmode="decimal" min="0" ` +
        `step="${step}" data-econ="${key}" value="${+(+value || 0).toFixed(2)}" aria-label="${label}">${post ? `<i>${post}</i>` : ""}</span>` +
        `<small>${label}</small></label>`;
      return `<div class="sx-prices">` +
        (tariff.flat ? pc(`${pfx}_buy_c`, "Price", tariff.peak, "", "c", 0.5)
                     : pc(`${pfx}_peak_c`, "Peak", tariff.peak, "", "c", 0.5) + pc(`${pfx}_offpeak_c`, "Off-peak", tariff.offpeak, "", "c", 0.5)) +
        pc(`${pfx}_daily`, "Daily charge", tariff.daily, "$", "", 0.05) +
        pc(`${pfx}_offpeak_sell_c`, "Export", tariff.offpeakSell, "", "c", 0.5) +
        pc(`${pfx}_peak_sell_c`, "Peak export", tariff.peakSell, "", "c", 0.5) +
        `</div>`;
    };
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
    // TODAY | AFTER INSTALL: today's bill is typed in right there (it is
    // that month's, BT_PICK); the bill after opens how it is worked out
    const billHead = `<div class="sx-row sx-billhead"><span class="lbl"></span><span>Today</span><span class="sx-arrow"></span><span>After install</span></div>`;
    const billPair = (s, label) => {
      const mine = state.calib && !state.calib.total && state.calib[s] > 0;
      return `<div class="sx-row sx-billrow"><span class="lbl">${label}</span>` +
        field(`billq-${s}`, Math.round(BM[s].now.value), `min="0" step="10" data-bill="${s}" aria-label="${label}, today, dollars a month"`, "$", "", !mine) +
        `<span class="sx-arrow" aria-hidden="true">→</span>` + afterBill(s) + `</div>`;
    };
    const afterBill = s => {
      const n = BM[s].after;
      return `<span class="val after" data-wk="after-${s}" tabindex="0" role="button">` +
        (n.value < -0.5 ? `<b>${btMoney(-n.value)}</b> <small>credit</small>` : `<b>${btMoney(n.value)}</b>`) + `</span>`;
    };
    // the prices THIS building is priced at: its area's EA prices for a home
    // (btTariffFromEcon), else the map's set
    const shown = key => key.endsWith("_peak_c") ? tariff.peak : key.endsWith("_offpeak_c") ? tariff.offpeak
      : key.endsWith("_buy_c") ? tariff.peak : key.endsWith("_daily") ? tariff.daily : E[key];
    const price = (label, key) => row(label, field(key, +(shown(key) || 0).toFixed(2), `min="0" step="0.5" data-econ="${key}"`, "", "c/kWh"));
    const totalUse = Object.values(r.yearParts).reduce((a, b) => a + b, 0) || 1;
    const parts = Object.entries(r.yearParts).filter(([, v]) => v > 1);
    const priceSum = tariff.flat
      ? `${(tariff.peak || 0)}c flat · ${(E[pfx + "_offpeak_sell_c"] || 0)}c export`
      : `${(shown(pfx + "_peak_c") || 0)}c peak · ${(shown(pfx + "_offpeak_c") || 0)}c off-peak · ${(E[pfx + "_offpeak_sell_c"] || 0)}c export`;

    const SX_SPLIT = "<!--sx-split-->";
    // A day in each season (the next layer down from the card)
    const SD = r.run.seasons;
    const peakOf = sd => Math.max(...(withBatt ? sd.rowsB : sd.rows).map(x => Math.max(x.use, x.solar)));
    const ymaxRaw = Math.max(0.5, ...BT_SEASONS.map(s => peakOf(SD[s]))) * 1.08;
    const ymax = ymaxRaw > 6 ? Math.ceil(ymaxRaw) : Math.ceil(ymaxRaw * 2) / 2;
    // the most any season exports in an hour sets how far below zero the
    // charts go (the net-load line), the same for all four
    const yminRaw = Math.min(0, ...BT_SEASONS.map(s => Math.min(...(withBatt ? SD[s].rowsB : SD[s].rows).map(x => x.imp - x.exp)))) * 1.08;
    const ymin = yminRaw < -6 ? Math.floor(yminRaw) : Math.floor(yminRaw * 2) / 2;
    const usedParts = Object.keys(r.yearParts).filter(k => r.yearParts[k] > 1);
    const fig = (key, label, n) => `<span data-wk="${key}" tabindex="0" role="button"><small>${label}</small><b>${Wk.fmt(n.value, "kWh", 1)}</b></span>`;
    const seasonCard = (s, i, size) => {
      const nowM = S[s].nowMonth, aft = S[s][after];
      return `<div class="sx-season${s === BT_SEASON_TAB.on ? " on" : ""}" data-season="${s}">` +
        `<div class="sx-season-h"><b>${BT_CAP(s)}</b><span>${r.run.days[i]} days</span></div>` +
        `<div class="sx-chartbox">${btSeasonChart(s, SD[s], ymax, withBatt, size, ymin)}<div class="sx-tip" hidden></div></div>` +
        `<div class="sx-figs">` +
          fig(`s-use-${s}`, "Use", S[s].use) + fig(`s-made-${s}`, "Solar", S[s].made) +
          fig(`s-imp-${s}`, "From the grid", withBatt ? S[s].impB : S[s].imp) + fig(`s-exp-${s}`, "Exported", withBatt ? S[s].expB : S[s].exp) +
          (withBatt ? fig(`s-tob-${s}`, "Into battery", S[s].toB) + fig(`s-fromb-${s}`, "From battery", S[s].fromB) : "") +
        `</div>` +
        `<div class="sx-sbill"><span>A month</span>` +
          `<span data-wk="s-now-${s}" tabindex="0" role="button">${btMoney(nowM.value)}</span><span class="sx-arrow">→</span>` +
          `<span class="after" data-wk="s-after-${s}" tabindex="0" role="button">${aft.value < -0.5 ? btMoney(-aft.value) + " <small>credit</small>" : btMoney(aft.value)}</span>` +
          `<span class="sx-hours" data-wk="s-hours-${s}" tabindex="0" role="button">the hours ›</span>` +
        `</div></div>`;
    };
    const keyHtml = `<div class="sx-key">` + usedParts.map(k => `<span><i style="background:${BT_PART_STYLE[k].colour}"></i>${BT_PART_STYLE[k].label}</span>`).join("") +
      `<span><i style="background:${BT_SOLAR_COLOUR}"></i>Solar</span>` +
      (withBatt ? `<span><i style="background:${BT_BATT_COLOUR}"></i>Battery: charging (light), powering the ${isBiz ? "building" : "home"} (solid)</span>` : "") +
      `<span><i class="nl"></i>Net load: from the grid less exported (below zero when exporting)</span>` +
      `<span><i class="pk"></i>Peak price</span><span class="u">kWh each hour</span></div>`;
    const methodHtml = haveGen
      ? `<div class="sx-h">How it's worked out</div>` +
        `<details class="sx-dis" id="bt-adv-method"><summary><span class="t">The method, with the numbers used now</span><span class="sum">updates with every change</span></summary>` +
          `<div class="sx-group">${btMethodHtml(r, prof, isBiz)}</div></details>`
      : "";
    const html =
      `<div class="sx">` +
      // the corner card leads with what it costs (Josh, 18 Oct)
      // COSTS ABOUT opens to the two prices it is built from, each editable
      `<details class="sx-dis sx-costdrop" id="bt-costs"><summary><span class="t">Costs about</span>` +
        `<span class="sum"><b>${money(withBatt ? N.allCost : N.solarCost)}</b> <small>${N.kwp.value.toFixed(1)} kW${withBatt ? ` + ${+prof.battery} kWh` : ""}</small></span></summary>` +
        `<div class="sx-group">` +
          row("Solar price", field("solarrate", Math.round(N.solarRate.value), `min="0" step="50" data-sys="solarPerKw"`, "$", "/kW", !(state.sys.solarPerKw > 0)),
            { small: state.sys.solarPerKw > 0 ? "yours" : "typical for this size" }) +
          row("Solar", val(money(N.solarCost)), { go: "solarcost", small: N.kwp.value.toFixed(1) + " kW" }) +
          row("Battery price", field("battrate", Math.round(N.battRate.value), `min="0" step="50" data-sys="battPerKwh"`, "$", "/kWh", !(state.sys.battPerKwh > 0)),
            { small: state.sys.battPerKwh > 0 ? "yours" : "typical" }) +
          (withBatt ? row("Battery", val(money(N.battCost)), { go: "battcost", small: `${+prof.battery} kWh` }) : "") +
          row("Total", val(money(withBatt ? N.allCost : N.solarCost)), { go: "allcost", cls: "total" }) +
        `</div></details>` +
      `<div class="sx-summary">` +
        // ANNUAL FIGURES ARE THE AVERAGE YEAR over the loan, in today's
        // dollars (btEvaluate avgOver): the same as the green box
        `<div class="sx-cell hero" data-wk="saveavg" tabindex="0" role="button"><div class="l">Bill savings</div>` +
          `<div class="v">${haveGen ? money(N.saveAvg) : "–"}</div><div class="s">a year on average${withBatt ? ", with the battery" : ""}</div></div>` +
        `<div class="sx-cell" data-wk="lifetime" tabindex="0" role="button"><div class="l">Total bill savings</div>` +
          `<div class="v">${haveGen ? money(ret.nodes.lifetime) : "–"}</div><div class="s">over ${ret.years} years</div></div>` +
        `<div class="sx-cell" data-wk="net" tabindex="0" role="button"><div class="l">Net savings</div>` +
          `<div class="v">${haveGen ? money(ret.nodes.net) : "–"}</div><div class="s">over ${ret.years} years</div></div>` +
        // THE RETURN TWO WAYS. Paid upfront: the yearly rate the cost earns
        // (btReturns irr). With a loan nothing is put in, so there is no rate
        // to earn on it; what the loan gives is the net saving a year.
        `<div class="sx-cell" data-wk="irr" tabindex="0" role="button"><div class="l">Return on investment</div>` +
          `<div class="v">${haveGen && ret.irr != null ? Wk.fmt(ret.irr, "share", 1) : "–"}</div><div class="s">a year</div></div>` +
      `</div>` +
      (haveGen ? "" : `<div class="sx-foot">Loading this roof's sun&hellip;</div>`) +
      // the corner card's few key facts (shown only while compact)
      `<div class="sx-group sx-glance">` +
        (haveGen ? billHead + billPair("winter", "Your highest winter bill") + billPair("summer", "Your lowest summer bill") : "") +
        // THE PRICES THIS BUILDING IS PRICED AT, editable in place: five
        // numbers, no prose. A home shows its area's home prices, a business
        // business prices; an edit applies across the map for that type.
        (haveGen ? priceStrip() : "") +
        (haveGen ? row("Plan", seg("plan", [["tou", "Time of use"], ["flat", "Flat"]], tariff.flat ? "flat" : "tou", "Electricity plan")) : "") +
      `</div>` +
      (haveGen && matchNote ? `<div class="sx-foot sx-glance-foot">${matchNote}</div>` : "") +
      (haveGen ? `<button type="button" class="sx-explore" data-big="seasons">Load Profile Charts <span aria-hidden="true">⤢</span></button>` : "") +
      `</div>` + SX_SPLIT + `<details class="sx-dis sx-moredrop" id="bt-more"><summary><span class="t">Details and assumptions</span><span class="sum">life, prices, use</span></summary><div class="sx sx-more">` +

      `<div class="sx-h">Over the panels' life</div>` +
      `<div class="sx-group">` +
        row("Total bill savings", val(money(ret.nodes.lifetime)), { go: "lifetime", small: `over ${ret.years} years, ` + (ret.real ? "in today's dollars" : "nominal") }) +
        row("Replacements", val(btMoney(-ret.nodes.replacements.value)), { go: "repl", small: withBatt ? "inverter and battery" : "inverter" }) +
        row("Cost", val(money(withBatt ? N.allCost : N.solarCost)), { go: "allcost" }) +
        row("Net savings", val(money(ret.nodes.net)), { go: "net", cls: "total" }) +
        row("Pays back in", val(ret.payback == null ? "not within 60 years" : ret.payback > ret.years ? `${ret.payback.toFixed(1)} years, after its ${ret.years}-year life` : ret.payback.toFixed(1) + " years"), { go: "payback" }) +
        row("Return on investment", val(ret.irr == null ? "–" : Wk.fmt(ret.irr, "share", 1)), { go: "irr", small: "a year, " + (ret.real ? "after inflation" : "before inflation") + ", paid upfront" }) +
        row("With a loan", val(btMoney(r.netShown) + " a year"), { go: "net-year", small: `nothing up front, ${N.loanYears.value} years at ${Wk.fmt(N.loanRate.value, "share", 1)}` }) +
      `</div>` +
      `<div class="sx-h">Assumptions</div>` +
      `<div class="sx-sub">Electricity prices</div>` +
      `<div class="sx-group">` +
        (tariff.flat ? price("Price", pfx + "_buy_c")
                     : price("Peak", pfx + "_peak_c") + price("Off-peak", pfx + "_offpeak_c")) +
        price("Export, off-peak", pfx + "_offpeak_sell_c") +
        price("Export, peak", pfx + "_peak_sell_c") +
        row("Daily charge", field("daily", +(shown(pfx + "_daily") || 0).toFixed(2), `min="0" step="0.1" data-econ="${pfx}_daily"`, "$", "/day")) +
        (tariff.flat ? [] : btPeakHours()).map(([a, b], i) => {
          const lo = i === 0 ? 4 : 12, hi = i === 0 ? 13 : 24;
          return `<div class="sx-row sx-peak"><span class="lbl">${i === 0 ? "Morning peak" : "Evening peak"}<small data-peak-label="${i}">${hours([a, b])}</small></span>` +
            `<div class="sx-range" style="--lo:${(a - lo) / (hi - lo) * 100}%;--hi:${(b - lo) / (hi - lo) * 100}%">` +
            `<input type="range" min="${lo}" max="${hi}" step="1" value="${a}" data-peak="${i}" data-end="0" aria-label="${i === 0 ? "Morning" : "Evening"} peak starts">` +
            `<input type="range" min="${lo}" max="${hi}" step="1" value="${b}" data-peak="${i}" data-end="1" aria-label="${i === 0 ? "Morning" : "Evening"} peak ends">` +
            `</div></div>`;
        }).join("") +
      `</div>` +
      `<div class="sx-foot">Including GST. ${tariff.flat ? "One price all day." : `Peak prices apply ${btPeakHours().map(hours).join(" and ")}.`} ` +
        `${isBiz ? "Business energy prices, used across the map. Network and connection charges come on a separate bill that solar does not change, so they are left out."
                 : "Home prices, used across the map."}</div>` +
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
        `<span class="sum">${isBiz ? `Business · ${({ heatpump: "heat pumps", electric: "electric heaters", gas: "gas heating", none: "no heating" })[P.heating]}` : `${P.people} ${P.people === 1 ? "person" : "people"}`}${P.evs ? ` · ${P.evs} EV${P.evs > 1 ? "s" : ""}` : ""}</span></summary>` +
        `<div class="sx-group">` +
          row("Building", seg("type", [["home", "Home"], ["business", "Business"]], state.type, "Building")) +
          (isBiz ? "" : row("People", `<div class="sx-step" data-step="people"><button type="button" data-d="-1" aria-label="Fewer people">−</button><b>${P.people}</b><button type="button" data-d="1" aria-label="More people">+</button></div>`)) +

          (isBiz ? row("Heating", seg("heating", [["heatpump", "Heat pump"], ["electric", "Electric"], ["gas", "Gas"], ["none", "None"]], P.heating, "Heating")) : "") +
          row(isBiz ? "Electric vehicles" : "Electric cars", seg("evs", (isBiz ? [0, 1, 2, 4, 8] : [0, 1, 2, 3]).map(n => [n, String(n)]), P.evs, "Electric cars")) +
          (P.evs > 0 ? row(isBiz ? "Charge in the working day" : "Charge when it's sunny", `<label class="sx-switch"><input type="checkbox" data-tick="evOnSun" ${P.evOnSun ? "checked" : ""} aria-label="Charge when it's sunny"><i></i></label>`) : "") +
        `</div>` +
        `<details class="sx-why sx-foot"><summary>How this is used</summary>These answers set how much power the home uses, and when. If you enter your bills or yearly use, we scale the estimate to match.</details>` +
      `</details>` +

      `<div class="sx-foot">An estimate, not a quote. <details class="sx-why sx-inline"><summary>More</summary>Based on the roof, the local climate and the assumptions above. Tap any result to see the working.</details></div>` +
      methodHtml +
      `</div></details>`;
    // ONE CARD: the details sit in it, collapsed, not in a second column
    const [top, more] = html.split(SX_SPLIT);
    const moreBox = null;
    box.innerHTML = top + more;
    // THE HEADLINE SAVING under the card's yellow box (ctx.saveBox): the same
    // node as the "Bill savings" cell, so the two always agree and both open
    // the same workings. Hidden until the roof's sun has loaded, and when the
    // roof would not lower the bills.
    const saveBox = ctx.saveBox && ctx.saveBox.isConnected ? ctx.saveBox : null;
    // The headline is the NET a year while a loan for the system is repaid:
    // bill savings less repayments, with both shown beneath it. Negative when
    // the repayments are larger than the savings, and then it says so.
    if (saveBox) {
      const netN = withBatt ? N.battNet : N.solarNet, repN = N.repAvg, saveN = N.saveAvg;
      const loanN = withBatt ? N.allRepay : N.solarRepay;
      const show = haveGen && netN && Number.isFinite(netN.value);
      saveBox.hidden = !show;
      const ahead = show && netN.value >= 0;
      const yrs = N.loanYears.value, pct = Wk.fmt(N.loanRate.value, "share", 1);
      // one step for all three, set by the largest, so savings - repayments
      // = net on screen as well as in the sums
      const shownNet = btNetShown(saveN.value, repN.value), st = shownNet.step;
      const m = v => btMoney(v, st);
      // LOOK: one figure, two short rows, the detail behind an (i). Each
      // figure still opens its workings when tapped.
      const info = `An average year over a ${yrs}-year loan at ${pct}, in today's dollars, ` +
        `replacements included. Tap a figure to see how it is worked out.`;
      saveBox.innerHTML = show
        ? `<div class="roof-saving${ahead ? "" : " behind"}">` +
          // the figure on the left, how it is made up in a light box on the right
          `<div class="rs-top"><div class="rs-left">` +
          `<div class="rs-head"><span class="rs-l">${ahead ? "This roof could save about" : "With the loan, this roof costs"}</span>` +
          `<button type="button" class="rs-i" aria-label="About this figure" aria-expanded="false">i</button></div>` +
          `<div class="rs-v" data-wk="net-year">${m(Math.abs(shownNet.net))}<span>/yr</span></div></div>` +
          `<div class="rs-parts">` +
          `<div class="rs-row" data-wk="saveavg"><span>Bill savings</span><b>${m(saveN.value)}</b></div>` +
          `<div class="rs-row" data-wk="repay"><span>Loan repayments</span><b>&minus;${m(repN.value)}</b></div>` +
          `</div></div>` +
          `<div class="rs-info" hidden>${info}</div>` +
          // A BATTERY, one tap: 10 kWh to start (BT_BATT_ADD_KWH), any size
          // typed in; the figures above then include it and its loan
          (withBatt
            ? `<div class="rs-batt"><span>Battery</span><span class="rs-bk">` +
              `<button type="button" data-battstep="-1" aria-label="Smaller battery"${+prof.battery <= BT_BATT_MIN_KWH ? " disabled" : ""}>&minus;</button>` +
              `<label><input type="text" inputmode="decimal" data-battkwh="1" value="${+prof.battery}" aria-label="Battery size, kWh"><i>kWh</i></label>` +
              `<button type="button" data-battstep="1" aria-label="Larger battery"${+prof.battery >= BT_BATT_MAX_KWH ? " disabled" : ""}>+</button></span>` +
              `<b data-wk="battcost">${btMoney(N.battCost.value)}</b>` +
              `<button type="button" class="rs-bx" data-battadd="0" aria-label="Remove the battery">&times;</button></div>`
            : `<button type="button" class="rs-add" data-battadd="${BT_BATT_ADD_KWH}">+ Add a battery</button>`) +
          `<button type="button" class="rs-yby" data-big="returns">Year by year <span aria-hidden="true">›</span></button>` +
          `</div>`
        : "";
      const ib = saveBox.querySelector(".rs-i");
      if (ib) ib.addEventListener("click", e => {
        e.stopPropagation();
        const p = saveBox.querySelector(".rs-info"), open = p.hidden;
        p.hidden = !open; ib.setAttribute("aria-expanded", String(open));
      });
    }
    const roots = [box, moreBox, saveBox].filter(Boolean);
    const qa = sel => roots.flatMap(rt => [...rt.querySelectorAll(sel)]);
    const q1 = sel => qa(sel)[0] || null;
    qa("details.sx-dis[id]").forEach(d => {
      if (BT_OPEN.has(d.id)) d.open = true;
      d.addEventListener("toggle", () => { if (d.open) BT_OPEN.add(d.id); else BT_OPEN.delete(d.id); });
    });

    // every result opens its workings
    const nodeFor = {
      save: () => save1, lifetime: () => ret.nodes.lifetime, net: () => ret.nodes.net, irr: () => ret.nodes.irr,
      "after-summer": () => BM ? BM.summer.after : S.summer[after], "after-winter": () => BM ? BM.winter.after : S.winter[after],
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
      nodeFor[`s-tob-${s}`] = () => S[s].toB;
      nodeFor[`s-fromb-${s}`] = () => S[s].fromB;
      nodeFor[`s-now-${s}`] = () => S[s].nowMonth;
      nodeFor[`s-after-${s}`] = () => S[s][after];
      nodeFor[`s-hours-${s}`] = () => withBatt ? S[s].battDay : S[s].solarDay;
    });
    nodeFor.gen = () => N.genYear;
    nodeFor["net-year"] = () => withBatt ? N.battNet : N.solarNet;
    nodeFor.repay = () => N.repAvg;
    nodeFor.saveavg = () => N.saveAvg;
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
      if (k === "real") { econ.real_prices = v === "1"; if (ctx.onEcon) ctx.onEcon(); else redraw(); return; }
      if (k === "plan") { econ.plan = v; if (ctx.onEcon) ctx.onEcon(); else redraw(); return; }
      if (k === "type") state.type = v;
      else (state.type === "business" ? state.business : state.home)[k] = (k === "evs" || k === "battery") ? +v : v;
      save(); redraw(`.sx-seg[data-key="${k}"] button[data-v="${v}"]`);
    }));
    // the green box's battery: add (10 kWh), resize, remove
    const setBatt = (kwh, focusSel) => {
      (state.type === "business" ? state.business : state.home).battery = Math.max(0, Math.min(200, kwh || 0));
      save(); redraw(focusSel);
    };
    qa("[data-battadd]").forEach(b => b.addEventListener("click", e => {
      e.stopPropagation(); setBatt(+b.dataset.battadd, +b.dataset.battadd > 0 ? "input[data-battkwh]" : null);
    }));
    qa("[data-battstep]").forEach(b => b.addEventListener("click", e => {
      e.stopPropagation();
      const d = +b.dataset.battstep;
      setBatt(btBattStep((state.type === "business" ? state.business : state.home).battery, d), `[data-battstep="${d}"]`);
    }));
    qa("input[data-battkwh]").forEach(inp => {
      inp.addEventListener("click", e => { e.stopPropagation(); inp.select(); });
      inp.addEventListener("keydown", e => { if (e.key === "Enter") inp.blur(); });
      inp.addEventListener("change", () => {
        const v = parseFloat(inp.value);
        if (v > 0) setBatt(Math.max(BT_BATT_MIN_KWH, v), "input[data-battkwh]"); else redraw("input[data-battkwh]");
      });
    });
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
      // The first edit of a home price while this area's EA prices are in
      // use takes all of them as the reader's own first, so editing one
      // price on a Wanaka home keeps Wanaka's others (not Queenstown's).
      const area = typeof homePricesFor === "function" && /^home_(peak_c|offpeak_c|daily|buy_c)$/.test(inp.dataset.econ)
        ? homePricesFor(ctx.area) : null;
      if (area) Object.assign(econ, { home_peak_c: area.peak_c, home_offpeak_c: area.offpeak_c,
                                      home_daily: area.daily, home_buy_c: area.flat_c });
      econ[inp.dataset.econ] = v;
      if (ctx.onEcon) ctx.onEcon(); else redraw();
    }));


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
          (withBatt ? `<div>From the battery <b>${k(row.fromB || 0)}</b>${row.bExp > 0.0005 ? ` · to the grid <b>${k(row.bExp)}</b>` : ""}</div>` : "") +
          `<div>From the grid <b>${k(row.imp)}</b> · exported <b>${k(row.exp)}</b></div>` +
          `<div>Net load <b>${row.imp - row.exp < -0.0005 ? "−" + k(row.exp - row.imp) : k(row.imp - row.exp)}</b></div>`;
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

    // THE LIGHTBOX: the card's charts, big enough to read, one tab each --
    // a day in each season, the whole year hour by hour, and year by year
    // (savings against repayments). Every figure is from this draw's own
    // calculation (r), the same nodes the card and the green box show.
    // the year's weather loads once, when the year is first shown; the year
    // is drawn without it until then
    let yearData = null, yearWeather = null, weatherAsked = false;
    const getYear = () => yearData || (yearData = btYearHours(r.run, withBatt, yearWeather));
    const askWeather = redraw => {
      if (weatherAsked || typeof window === "undefined" || !window.__yearWeather) return;
      weatherAsked = true;
      window.__yearWeather().then(w => { if (w) { yearWeather = w; yearData = null; redraw(); } }).catch(() => {});
    };
    // YEAR BY YEAR, in today's dollars: each year's bill savings (btReturns'
    // rows) and that year's fixed repayment taken back to today's dollars.
    // Their average over the loan is the green box's figure (checked below).
    // with a loan: each year's repayments, replacements added to the loan
    // when due (btEvaluate repaySched); one falling after the term is paid
    // in its year
    const returnsRows = () => {
      const L = N.loanYears.value;
      let cum = 0;
      return ret.rows.slice(0, Math.max(ret.years, L)).map((row, y) => {
        const repay = y < L ? r.repaySched[y] : 0, repl = y < L ? 0 : -row.out;
        cum += row.saving - repay - repl;
        return { year: row.year, save: row.saving, repay, repl, net: row.saving - repay - repl, cum };
      });
    };
    const niceStep = span => {
      const raw = Math.max(span, 1) / 4, p = Math.pow(10, Math.floor(Math.log10(raw)));
      return [1, 2, 2.5, 5, 10].map(k => k * p).find(k => k >= raw);
    };
    const kMoney = v => Math.abs(v) >= 1000 ? `${v < 0 ? "−" : ""}$${(Math.abs(v) / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 })}k` : `${v < 0 ? "−" : ""}$${Math.abs(Math.round(v))}`;
    // two panels sharing the years: each year's savings (up) and repayment
    // (down) with the net as a dot; under it, the running total
    const returnsSvg = (rows, W, H) => {
      const L = 58, R = 14, gap = 44, B = 26, T = 22;
      const h1 = Math.round((H - T - B - gap) * 0.58), h2 = H - T - B - gap - h1;
      const n = rows.length, bw = (W - L - R) / n;
      const x = i => L + i * bw;
      const panel = (y0, h, lo, hi) => {
        const st = niceStep(hi - lo), a = Math.floor(lo / st) * st, b = Math.ceil(hi / st) * st;
        const y = v => y0 + h * (1 - (v - a) / Math.max(b - a, 1e-9));
        let g = "";
        for (let v = a; v <= b + 1e-9; v += st) {
          g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="${Math.abs(v) < 1e-9 ? "rz" : "rg"}"/>` +
               `<text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" class="ra">${kMoney(v)}</text>`;
        }
        return { y, g };
      };
      const p1 = panel(T, h1, Math.min(0, ...rows.map(q => -q.repay - q.repl)), Math.max(0, ...rows.map(q => q.save)));
      const y2 = T + h1 + gap;
      const p2 = panel(y2, h2, Math.min(0, ...rows.map(q => q.cum)), Math.max(0, ...rows.map(q => q.cum)));
      let svg = p1.g + p2.g;
      rows.forEach((q, i) => {
        const cx = x(i) + bw * 0.15, w = bw * 0.7;
        svg += `<rect x="${cx}" y="${p1.y(q.save)}" width="${w}" height="${Math.max(0, p1.y(0) - p1.y(q.save))}" class="rs-sv"/>`;
        if (q.repay > 0) svg += `<rect x="${cx}" y="${p1.y(0)}" width="${w}" height="${Math.max(0, p1.y(-q.repay) - p1.y(0))}" class="rs-rp"/>`;
        if (q.repl > 0) svg += `<rect x="${cx}" y="${p1.y(-q.repay)}" width="${w}" height="${Math.max(0, p1.y(-q.repay - q.repl) - p1.y(-q.repay))}" class="rs-rl"/>`;
        svg += `<circle cx="${x(i) + bw / 2}" cy="${p1.y(q.net)}" r="${Math.min(4, bw * 0.22)}" class="rs-nt"/>`;
        const top = Math.min(p2.y(q.cum), p2.y(0)), hh = Math.abs(p2.y(q.cum) - p2.y(0));
        svg += `<rect x="${cx}" y="${top}" width="${w}" height="${hh}" class="${q.cum < 0 ? "rs-cn" : "rs-cp"}"/>`;
        if (q.year === 1 || q.year % 5 === 0) svg += `<text x="${x(i) + bw / 2}" y="${H - 8}" text-anchor="middle" class="ra">${q.year === 1 ? "Year 1" : q.year}</text>`;
        svg += `<rect x="${x(i)}" y="${T}" width="${bw}" height="${H - T - B}" class="rs-hit"><title>Year ${q.year}: bill savings ${btMoney(q.save)}, ` +
               `repayment ${btMoney(q.repay)}${q.repl > 0 ? `, replacements ${btMoney(q.repl)}` : ""}, net ${btMoney(q.net)}; running total ${btMoney(q.cum)} (today's dollars)</title></rect>`;
      });
      svg += `<text x="${L}" y="${T - 10}" class="rt">Each year</text><text x="${L}" y="${y2 - 12}" class="rt">Running total</text>`;
      return `<svg class="sx-returns" viewBox="0 0 ${W} ${H}" role="img" aria-label="Savings and repayments, year by year, in today's dollars">${svg}</svg>`;
    };
    const yearSum = sumEl => {
      const Y = getYear();
      const T = Y.totals;
      const ok = Y.check.ok
        ? `<span class="ok">✓ The same use and solar as the bills.</span>`
        : `<span class="warn">Does not add up to the bills (${Y.check.off.join(", ")}${Y.check.daysOff.length ? "; season lengths differ from the calendar" : ""}).</span>`;
      const d = Y.check.diff, kw = v => Math.round(Math.abs(v)).toLocaleString();
      const vs = Math.abs(d.imp) < 0.5 ? "the same as" : `${kw(d.imp)} kWh ${d.imp > 0 ? "more" : "less"} than`;
      sumEl.innerHTML = `Use ${Math.round(T.use).toLocaleString()} kWh, solar ${Math.round(T.solar).toLocaleString()} kWh. ${ok} ` +
        `Day by day the ${isBiz ? "building" : "home"} takes ${Math.round(T.imp).toLocaleString()} kWh from the grid, ${vs} the bills' average days, and exports ${Math.round(T.exp).toLocaleString()} kWh. ` +
        `Bills ${btMoney(T.billNow)} today, ${btMoney(T.bill)} ${withBatt ? "with solar and the battery" : "with solar"}.`;
    };
    const BIG_TABS = [["seasons", "A day in each season"], ["year", "The whole year"], ["returns", "Year by year"]];
    const openBig = tab0 => {
      if (!haveGen) return;
      let tab = BIG_TABS.some(t => t[0] === tab0) ? tab0 : "seasons";
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
      const render = () => {
        // FILL THE SCREEN: the card is the window less a margin; charts are
        // sized to it so axis text reads at about 11 px (1.35 px a unit)
        const twoCol = innerWidth > 760;
        const cardW = Math.min(1600, innerWidth - 48);
        const chartPx = twoCol ? (cardW - 44 - 14) / 2 - 28 - 164 : cardW - 44 - 20;
        const hPx = twoCol ? Math.max(220, (innerHeight - 48 - 215 - 2 * 50) / 2) : 220;
        const size = { W: Math.round(chartPx / 1.35), H: Math.round(hPx / 1.35) };
        const tabs = `<div class="sx-seg sx-bigtabs" role="tablist" aria-label="Chart">${BIG_TABS.map(([k, l]) =>
          `<button type="button" role="tab" data-btab="${k}" aria-selected="${k === tab}"${k === tab ? ' class="on"' : ""}>${l}</button>`).join("")}</div>`;
        let body = "";
        if (tab === "seasons") {
          body = `<div class="sx-foot sx-lead">An average day, hour by hour. Colours: what the ${isBiz ? "business" : "home"} uses. Yellow: what the roof makes. Where the yellow covers the colour, the roof is powering it; colour above the yellow comes from the grid${withBatt ? " or the battery" : ""}; yellow above the colour is exported. Shaded: peak hours. Tap a figure to see how it is worked out.</div>` +
            keyHtml + `<div class="sx-seasons sx-seasons-big">${BT_SEASONS.map((s, i) => seasonCard(s, i, size)).join("")}</div>`;
        } else if (tab === "year") {
          body = `<div class="sx-row sx-bigrow sx-yzoom">${seg("yzoom", [["year", "Year"], ["month", "Month"], ["week", "Week"], ["day", "Day"]], BT_YEAR_VIEW.zoom, "Zoom")}` +
              `<span class="sx-ynav"><button type="button" class="sx-btn" data-ymove="-1" aria-label="Earlier">‹</button><b class="sx-ylab"></b>` +
              `<button type="button" class="sx-btn" data-ymove="1" aria-label="Later">›</button></span></div>` +
            `<div class="sx-key"><span><i style="background:#8fb3ff"></i>Use</span><span><i style="background:${BT_SOLAR_COLOUR}"></i>Solar</span>` +
              (withBatt ? `<span><i style="background:${BT_BATT_COLOUR}"></i>Battery: charging (light), powering the ${isBiz ? "building" : "home"} (solid)</span>` : "") +
              `<span><i class="nl"></i>Net load (below zero when exporting)</span><span class="u sx-yunit"></span></div>` +
            `<div class="sx-chartbox sx-ybox"><div class="sx-yplot"></div><div class="sx-tip" hidden></div></div>` +
            `<div class="sx-foot sx-ysum"></div>` +
            `<div class="sx-foot sx-yhow"></div>` +
            `<div class="sx-bigfoot"><button type="button" class="sx-btn sx-ycsv">Download the 8,760 hours (CSV)</button></div>`;
        } else {
          const rows = returnsRows(), L = N.loanYears.value;
          const loanRows = rows.slice(0, L);
          const avgNet = loanRows.reduce((t, q) => t + q.net, 0) / L;
          const agrees = Math.abs(avgNet - (N.saveAvg.value - N.repAvg.value)) < 0.01;
          const sh = btNetShown(N.saveAvg.value, N.repAvg.value), m = v => btMoney(v, sh.step);
          const W = Math.round(Math.min(1500, innerWidth - 100) / 1.35), H = Math.round(Math.max(300, innerHeight - 330) / 1.35);
          const be = rows.find(q => q.cum >= 0 && rows.slice(0, rows.indexOf(q)).some(p => p.cum < 0));
          body = `<div class="sx-rsum">` +
              `<div><b>${m(sh.net)}</b><small>a year on average${withBatt ? ", with the battery" : ""}</small></div>` +
              `<div><b>${m(N.saveAvg.value)}</b><small>bill savings, average</small></div>` +
              `<div><b>−${m(N.repAvg.value)}</b><small>repayments, average</small></div>` +
              `<div><b>${btMoney(rows[rows.length - 1].cum)}</b><small>ahead after ${rows.length} years</small></div>` +
            `</div>` +
            (agrees ? "" : `<div class="sx-foot warn">The years here do not average to the green box's figure. Please report this.</div>`) +
            `<div class="sx-rkey"><span><i class="sv"></i>Bill savings</span><span><i class="rp"></i>Loan repayment</span>${rows.some(q => q.repl > 0) ? `<span><i class="rl"></i>Replacements, paid in cash after the loan</span>` : ""}<span><i class="nt"></i>Net</span>` +
              `<span class="u">today's dollars</span></div>` +
            returnsSvg(rows, W, H) +
            `<div class="sx-foot">All in today's dollars. Savings rise with power prices (${E.elec_inflation_pct}% a year) less inflation (${E.cpi_pct}%), ` +
            `export rates fall ${E.export_decline_pct}% a year and the panels lose ${E.degradation_pct}% a year. The repayment is fixed at ` +
            `${btMoney((withBatt ? N.allRepay : N.solarRepay).value)} a year for ${L} years, so inflation shrinks it in today's dollars. ` +
            `The replacement inverter (year ${(E.inverter_replace_year || 0) + 1})${withBatt ? ` and battery (year ${(E.battery_replace_year != null ? E.battery_replace_year : 14) + 1}, at ${E.battery_replace_cost_pct != null ? E.battery_replace_cost_pct : 70}% of today's price) are` : " is"} ` +
            `added to the loan when due and repaid with interest by year ${L}, so the repayment steps up then.` +
            (be ? ` The running total turns positive in year ${be.year}.` : "") + `</div>`;
        }
        lb.innerHTML =
          `<div class="clb-card sx sx-bigcard" role="dialog" aria-modal="true" aria-label="${BIG_TABS.find(t => t[0] === tab)[1]}">` +
            `<div class="clb-head">${tabs}<button type="button" class="clb-x" aria-label="Close">&times;</button></div>` +
            body +
            `<div class="sx-bigfoot"><button type="button" class="sx-btn" data-tomethod="method">How it's worked out ›</button></div>` +
          `</div>`;
        lb.querySelectorAll("[data-btab]").forEach(b => b.addEventListener("click", () => {
          tab = b.dataset.btab; render();
          const t = lb.querySelector(`[data-btab="${tab}"]`); if (t) t.focus();
        }));
        lb.querySelector(".clb-x").addEventListener("click", close);
        // the method lives in the panel: close, open it there
        lb.querySelectorAll("[data-tomethod]").forEach(b => b.addEventListener("click", () => {
          close();
          const sp = document.getElementById("side-panel");
          const ex = document.getElementById("side-panel-expand");
          if (sp && sp.classList.contains("compact") && ex) ex.click();
          setTimeout(() => {
            const t = q1("#bt-adv-method");
            if (!t) return;
            if (t.tagName === "DETAILS") t.open = true;
            t.scrollIntoView({ behavior: "smooth", block: "start" });
          }, 80);
        }));
        if (tab === "seasons") {
          const big = [...lb.querySelectorAll(".sx-chart")];
          bindCharts(big, false);
          big.forEach(svg => { svg.style.cursor = "crosshair"; });
        }
        if (tab === "year") {
          const plot = lb.querySelector(".sx-yplot"), tip = lb.querySelector(".sx-ybox .sx-tip");
          const W = Math.round(Math.min(1500, innerWidth - 100) / 1.35), H = Math.round(Math.max(260, innerHeight - 380) / 1.35);
          const how = () => {
            const w = getYear().weather;
            lb.querySelector(".sx-yhow").textContent = "Tap the chart to zoom in there. Use and solar change day by day from one season's average day to the next. " +
              (w ? `Each day's sunlight follows the weather of ${w.year} at this roof (ERA5), scaled so each season makes what the bills say. ` : "Day-to-day weather is not in it yet: no sunlight record was found for this place. ") +
              `The battery carries its charge from one day to the next. Use does not follow the weather: heating is the season's average.`;
          };
          yearSum(lb.querySelector(".sx-ysum")); how();
          askWeather(() => { if (!lb.hidden && tab === "year") { yearSum(lb.querySelector(".sx-ysum")); how(); draw(); } });
          let cur = null;
          const k = v => v.toFixed(v < 10 ? 2 : 1) + " kWh";
          const dayName = d => { const m = btMonthOf(d); return `${d - btMonthStart(m) + 1} ${BT_MONTH_NAME[m]}`; };
          const draw = () => {
            cur = btYearSvg(getYear(), BT_YEAR_VIEW, W, H, withBatt);
            plot.innerHTML = cur.svg;
            const z = BT_YEAR_VIEW.zoom;
            lb.querySelector(".sx-ylab").textContent = z === "year" ? "The year" : z === "month" ? ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"][btMonthOf(cur.a)]
              : z === "week" ? `${dayName(cur.a)} – ${dayName(cur.b - 1)}` : dayName(cur.a);
            lb.querySelector(".sx-yunit").textContent = cur.daily ? "kWh each day" : "kWh each hour";
            lb.querySelectorAll("[data-ymove]").forEach(bt => { bt.disabled = z === "year" || (+bt.dataset.ymove < 0 ? cur.a <= 0 : cur.b >= 365); });
            lb.querySelectorAll('.sx-seg[data-key="yzoom"] button').forEach(x => { x.classList.toggle("on", x.dataset.v === z); x.setAttribute("aria-pressed", String(x.dataset.v === z)); });
            const svg = plot.querySelector("svg"), line = svg.querySelector(".cur");
            const at = ev => {
              const rc = svg.getBoundingClientRect(), u = (ev.clientX - rc.left) / rc.width * cur.W;
              return Math.max(0, Math.min(cur.pts.length - 1, Math.floor((u - cur.L) / (cur.W - cur.L - cur.R) * cur.pts.length)));
            };
            svg.addEventListener("mousemove", ev => {
              const i = at(ev), p = cur.pts[i], rc = svg.getBoundingClientRect();
              const xu = cur.L + (i + 0.5) * (cur.W - cur.L - cur.R) / cur.pts.length;
              line.setAttribute("x1", xu); line.setAttribute("x2", xu); line.style.display = "";
              const when = cur.daily ? dayName(p.doy) : `${dayName(p.doy)}, ${String(p.h).padStart(2, "0")}:00`;
              const net = p.imp - p.exp;
              tip.innerHTML = `<b>${when}</b> (${btMonthOf(p.doy) >= 0 ? BT_MONTH_SEASON[btMonthOf(p.doy)] : ""})` +
                `<div>Use <b>${k(p.use)}</b> · solar <b>${k(p.solar)}</b></div>` +
                (withBatt ? `<div>From the battery <b>${k(p.fromB)}</b>${p.bExp > 0.0005 ? ` · to the grid <b>${k(p.bExp)}</b>` : ""}</div>` : "") +
                `<div>From the grid <b>${k(p.imp)}</b> · exported <b>${k(p.exp)}</b></div>` +
                `<div>Net load <b>${net < -0.0005 ? "−" + k(-net) : k(net)}</b></div>`;
              tip.hidden = false;
              tip.style.left = Math.min(Math.max(0, ev.clientX - rc.left - 110), rc.width - 230) + "px";
              tip.style.top = Math.max(0, ev.clientY - rc.top - 96) + "px";
            });
            svg.addEventListener("mouseleave", () => { tip.hidden = true; line.style.display = "none"; });
            // tap: one level in, centred on the day tapped
            svg.addEventListener("click", ev => {
              const p = cur.pts[at(ev)], next = { year: "month", month: "week", week: "day" }[BT_YEAR_VIEW.zoom];
              if (!next) return;
              BT_YEAR_VIEW.zoom = next;
              BT_YEAR_VIEW.start = next === "week" ? Math.max(0, Math.min(358, p.doy - 3)) : p.doy;
              draw();
            });
          };
          lb.querySelectorAll('.sx-seg[data-key="yzoom"] button').forEach(bt => bt.addEventListener("click", ev => {
            ev.stopPropagation();
            const z = bt.dataset.v, [a0, b0] = btYearSpan(BT_YEAR_VIEW), mid = Math.floor((a0 + b0 - 1) / 2);
            // from the whole year, start at January; otherwise stay on the
            // middle of what is shown
            const at0 = b0 - a0 === 365 ? 0 : mid;
            BT_YEAR_VIEW.zoom = z;
            BT_YEAR_VIEW.start = z === "year" ? 0 : z === "week" ? Math.max(0, Math.min(358, at0 - (at0 ? 3 : 0))) : at0;
            draw();
          }));
          lb.querySelectorAll("[data-ymove]").forEach(bt => bt.addEventListener("click", () => {
            const d = +bt.dataset.ymove, z = BT_YEAR_VIEW.zoom, [a0, b0] = btYearSpan(BT_YEAR_VIEW);
            if (z === "month") { const m = Math.max(0, Math.min(11, btMonthOf(a0) + d)); BT_YEAR_VIEW.start = btMonthStart(m); }
            else BT_YEAR_VIEW.start = Math.max(0, Math.min(365 - (b0 - a0), a0 + d * (b0 - a0)));
            draw();
          }));
          draw();
          lb.querySelector(".sx-ycsv").addEventListener("click", () => {
            const blob = new Blob([btYearCsv(getYear())], { type: "text/csv" });
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = `solar-hours-${ctx.buildingId}.csv`;
            document.body.appendChild(a); a.click(); a.remove();
            setTimeout(() => URL.revokeObjectURL(a.href), 1000);
          });
        }
        lb.querySelectorAll("[data-wk]").forEach(elx => {
          const k = elx.dataset.wk, f = nodeFor[k];
          if (f && typeof WkView !== "undefined") WkView.bind(elx, () => (BT_LATEST.nodeFor[k] || f)());
        });
      };
      render();
      lb.hidden = false;
      document.body.classList.add("clb-open");
      lb.onclick = e => { if (e.target === lb) close(); };
      lb.onkeydown = e => { if (e.key === "Escape") close(); };
      requestAnimationFrame(() => { const t = lb.querySelector(".sx-bigtabs .on"); if (t) t.focus(); });
    };
    qa("[data-big]").forEach(b => b.addEventListener("click", e => { e.stopPropagation(); openBig(b.dataset.big); }));
    // read-only, for browser checks: open a chart tab on the open card
    window.__openBigChart = openBig;

    if (ctx.onResult) ctx.onResult(r);
  };
  draw();
  return { redraw: draw, state, result: () => last };
}
