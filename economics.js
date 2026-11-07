// Solar economics: cost, self-consumption, savings, payback.
//
// Pulled out of preview.html on 1 Sep so the money maths is ONE implementation
// that both the map and a test runner can call, so the economics can be
// checked -- which was impossible while every
// function was a closure inside a 4,000-line page, and is how a 2.4x error in
// the yearly figure survived long enough to be spotted on the map.
//
// Loaded as a plain script by preview.html (so it shares the page's scope, no
// build step) and required by tests/test_economics.mjs under Node.
//
// Everything here is pure: numbers in, numbers out, no DOM and no map. That is
// deliberate -- the moment this reaches for the page state it stops being
// testable, which is the condition it was just rescued from.

const ECON_DEFAULTS = {
  cost_tiers: [
    { max_kw: 3,     rate: 3000, label: "under 3 kW" },
    // My Solar Quotes: $2.20/W for 5-6 kW (2025), a $2.54/W median for 3-5 kW (2026 quotes)
    { max_kw: 8,     rate: 2300, label: "3 – 8 kW" },
    { max_kw: 25,    rate: 1800, label: "8 – 25 kW" },
    { max_kw: 50,    rate: 1500, label: "25 – 50 kW" },
    { max_kw: Infinity, rate: 1200, label: "over 50 kW" },
  ],
  // Homes and businesses buy electricity at very different prices, so a
  // single retail rate would flatter one and penalise the other. Export is
  // the same for both today.
  home_buy_c: 30, home_sell_c: 12,
  // a business's flat plan: its time-of-use prices weighted by a typical
  // business's use in peak hours (26%): 0.26 x 17c + 0.74 x 10c = 12c
  biz_buy_c: 12,  biz_sell_c: 12,
  // TIME-OF-USE PRICES (the building panel, 17 Oct). Peak hours cost more;
  // export can be paid differently at peak. Cents per kWh incl. GST; daily
  // in $. The home prices here are the fallback and set the peak : off-peak
  // ratio; where the site names a network region (SITE.priceRegion) the
  // Electricity Authority's prices for it replace them (below).
  // Export: 12c a kWh in the first year (Powerswitch's retailer buy-back
  // rates, powerswitch.org.nz/solar-rates), falling by export_decline_pct.
  // home peak is twice off-peak: a Queenstown time-of-use plan in Nov 2026
  // was 55c : 25c (2.2); 34c : 26c (1.31) was far flatter than real plans.
  // THE PLAN: "tou" (peak and off-peak, the default) or "flat" (one price all
  // day: home_buy_c / biz_buy_c). Switched on a building's card, map-wide.
  plan: "tou",
  // EXPORT: 12c in the day for homes and businesses; at peak 23c for a home
  // and 15c for a business (2026 retailer peak buy-back:
  // Electric Kiwi 20c/10c, Mercury 23c/11.5c, Octopus 23c/10c; lines
  // companies pay peak export rebates from April 2026). Peak export is
  // never below daytime export (bill_tool.js btTariffFromEcon).
  home_peak_c: 50, home_offpeak_c: 25, home_peak_sell_c: 23, home_offpeak_sell_c: 12, home_daily: 2.5,
  // A BUSINESS PAYS ENERGY PRICES: its network and connection charges come
  // on a separate bill that solar does not change, so they are left out
  // (daily 0) and the energy rate is far below a home's: 17c peak, 10c
  // off-peak.
  biz_peak_c: 17,  biz_offpeak_c: 10,  biz_peak_sell_c: 15,  biz_offpeak_sell_c: 12,  biz_daily: 0,
  // [from, to) hours of the day that are peak: 7-10am and 6-9pm (Josh,
  // 18 Oct); adjustable on the building panel's sliders
  peak_hours: [[7, 10], [18, 21]],
  // Real or nominal money over the system's life. Real divides each future
  // year by general inflation (CPI), so every figure is in today's dollars.
  real_prices: true,
  cpi_pct: 2.0,
  // Which rate set a building gets.
  //
  // ROOF AREA is the real signal and does the work. System size is not an
  // independent second signal at all -- it is roof area times the coverage
  // setting -- so using it as a threshold meant a house FLIPPED to business
  // pricing when the coverage slider went up, which is nonsense: a building
  // does not change what it is because we modelled more panels on it. That
  // is the bug: a home treated as a business.
  //
  // The kW test is kept only as a backstop for the case roof area would miss
  // (a genuinely industrial system on a modest footprint), at 100kW.
  // NZ households are 3-12kW, and even a 300 m2 house fully covered lands
  // near 48kW, so nothing residential reaches it.
  //
  // Better signals exist if this proves too blunt: address_count is already
  // on every building, and a footprint carrying many addresses is a complex
  // rather than a house. LINZ outlines carry no building-use attribute.
  biz_min_roof_m2: 400,
  biz_min_kw: 100,
  // Export earns less over time: it falls at a steady rate from 14c rather
  // than via dated anchors.
  //
  // 2%/yr from 14c: 12.9c in 2030, 11.7c in 2035, 10.5c in 2040, 8.6c in
  // 2050. Above the 12c/9c first sketched, and deliberately so -- at 3%
  // a compound decline reaches 5.6c by the end of a 30-year system, and
  // unlike the old anchored schedule it never levels off. 2% keeps the tail
  // from doing more work than a buyback forecast can honestly carry.
  export_decline_pct: 2.0,
  // Self-consumption is a LOAD, not a share of output. A
  // house drawing 1.2kW through the day soaks up the same ~8kWh whether the
  // array is 5kW or 40kW, so the kWh self-consumed is flat and the SHARE
  // falls as the system grows. The old fixed-percentage model did the
  // opposite -- every extra panel earned the retail price -- which is why
  // big roofs on small houses looked implausibly good.
  home_daytime_kw: 1.5,   // average daytime draw of a house
  biz_daytime_kw: 6.0,    // weekday-daytime business, ~49% of biz_use_kwh
  profile: "home_typ",
  // Hours a day the daytime load actually meets useful sun. 1.5kW x 6.7h is
  // ~10kWh/day. Not 12: the load only offsets generation while there is sun
  // on the roof, so the hours are daylight hours minus the shoulders.
  daytime_hours: 6.7,
  // Annual electricity use on site. This is not decoration: self-consumption
  // is capped by it. A house cannot use 30% of a modelled 48kW system's
  // output if that exceeds everything it burns in a year -- without the cap,
  // oversized systems on small buildings show savings nobody could realise.
  home_use_kwh: 7000,     // typical NZ household
  biz_use_kwh: 30000,     // placeholder, varies enormously -- editable
  life_years: 30,
  degradation_pct: 0.5, // per year
  // Retail electricity rises; the export rate does NOT. Buyback is
  // already on a declining schedule, and inflating it as well would have the
  // two assumptions fighting each other.
  elec_inflation_pct: 4.0,
  // An inverter does not last the life of the panels. Standard practice
  // includes replacing it; leaving it out flatters every system equally.
  inverter_replace_year: 15,
  inverter_cost_pct: 18,
  // the battery is replaced once, in year 15, at this share of today's
  // battery price (batteries keep getting cheaper)
  battery_replace_year: 14,      // 0-based, as inverter_replace_year: year 15
  battery_replace_cost_pct: 70,
  // Money later is worth less than money now. Without this the model sums 30
  // years of nominal dollars and calls it savings, which overstates the
  // result badly -- and adding 5% inflation without it would overstate it
  // further still. Editable; set to 0 for an undiscounted view.
  // 3%. Lower than the ~5% a private householder would use, which is a
  // defensible public-good framing -- and it is on screen and adjustable, so
  // the choice is visible rather than buried.
  discount_rate_pct: 3.0,
  // A loan for the system and its one replacement inverter, over the
  // system's life, for the card's net saving a year (bill savings less
  // repayments, bill_tool.js btAnnuity): 5.5% over 30 years. Editable in
  // Assumptions & pricing.
  loan_rate_pct: 5.5,
  loan_years: 30,
};
// Named daytime-load profiles. The kW figure drives the maths; the names
// exist so the choice is a recognisable situation rather than a number
// pulled out of the air. A battery does not raise daytime demand, it moves
// evening demand into the daylight -- which is the same thing to this model,
// so it is carried as a larger effective daytime load.
const SELF_PROFILES = [
  { id: "home_out",   label: "Home — out on weekdays",       kw: 0.8 },
  { id: "home_typ",   label: "Home — typical mix",           kw: 1.5 },
  { id: "home_in",    label: "Home — someone there daytime", kw: 2.0 },
  { id: "home_batt",  label: "Home — with a battery",        kw: 3.5 },
  { id: "business",   label: "Business — weekday daytime",   kw: 6.0 },
  { id: "custom",     label: "Custom",                       kw: null },
];
const THIS_YEAR = new Date().getFullYear();
// REGIONAL HOME PRICES. regional_prices.js (tools/fetch_regional_prices.mjs,
// refreshed monthly) holds the Electricity Authority's latest prices for a
// standard-use home in each network region, already split into peak and
// off-peak so the default home pays the EA's average. The site's region
// (SITE.priceRegion) sets the home defaults; without it the figures above
// stand. Businesses keep the figures above: the EA set is residential.
function applyRegionalPrices(defaults, prices, code) {
  const r = prices && code != null ? prices.regions[code] : null;
  if (!r) return null;
  defaults.home_peak_c = r.peak_c;
  defaults.home_offpeak_c = r.offpeak_c;
  defaults.home_daily = r.daily;
  defaults.home_buy_c = Math.round(r.variable_c);   // the flat plan: the EA price to the cent
  defaults.price_source = priceSourceText(r);
  return r;
}
function priceSourceText(r) {
  return `Electricity Authority, ${r.name}, ${r.month.slice(0, 7)}: ` +
    `${r.variable_c}c a kWh and $${r.daily.toFixed(2)} a day for a standard-use home, incl. GST, ` +
    `split into peak and off-peak so a typical home pays that average`;
}
if (typeof REGIONAL_PRICES !== "undefined" && typeof window !== "undefined" && window.SITE) {
  applyRegionalPrices(ECON_DEFAULTS, REGIONAL_PRICES, window.SITE.priceRegion);
}
// EACH AREA ITS OWN NETWORK REGION. A building's map area (its `sr`) names
// its EA region through SITE.priceRegionByArea (else SITE.priceRegion):
// Wanaka and Cromwell are on Aurora's Central Otago network, Kingston on The
// Power Company's. Returns that region's home prices, or null when the
// reader has set home prices of their own (then theirs apply everywhere).
function homePricesFor(area, opts) {
  const o = opts || {};
  const prices = o.prices || (typeof REGIONAL_PRICES !== "undefined" ? REGIONAL_PRICES : null);
  const site = o.site || (typeof window !== "undefined" ? window.SITE : null);
  const e = o.econ || (typeof econ !== "undefined" ? econ : null);
  if (!prices || !site) return null;
  if (e && (e.home_peak_c !== ECON_DEFAULTS.home_peak_c || e.home_offpeak_c !== ECON_DEFAULTS.home_offpeak_c
            || e.home_daily !== ECON_DEFAULTS.home_daily || e.home_buy_c !== ECON_DEFAULTS.home_buy_c)) return null;
  const byArea = site.priceRegionByArea || {};
  const code = area != null && byArea[area] != null ? byArea[area] : site.priceRegion;
  const r = code != null ? prices.regions[code] : null;
  return r ? { code: +code, name: r.name, peak_c: r.peak_c, offpeak_c: r.offpeak_c, daily: r.daily,
               flat_c: Math.round(r.variable_c), source: priceSourceText(r) } : null;
}
let econ = JSON.parse(JSON.stringify(ECON_DEFAULTS));
econ.cost_tiers[4].max_kw = Infinity;   // JSON round-trip turns Infinity into null

// THE PRICE PER kW IS A SMOOTH CURVE THROUGH THE TIERS, not a step per tier.
// Applied to the whole system, a step made the total cost FALL as panels
// were added (25 kW at $1,800/kW = $45,000; 25.5 kW at $1,500/kW = $38,250),
// and a panel's cost jumped with the slider. Each tier's rate now sits at the
// middle of its band (geometric middle; the open-ended ends at two-thirds of
// the first band's top and 1.4x the last band's start), joined by straight
// lines and flat beyond the ends. With the default tiers the total cost rises
// with every kW (tests/test_economics.mjs checks 0-200 kW).
function costAnchors() {
  const t = econ.cost_tiers;
  return t.map((x, i) => {
    const lo = i ? t[i - 1].max_kw : 0, hi = x.max_kw;
    const kw = lo === 0 ? hi * 2 / 3 : !Number.isFinite(hi) ? lo * 1.4 : Math.sqrt(lo * hi);
    return [kw, x.rate];
  });
}
function costPerKw(kwp) {
  const a = costAnchors();
  if (kwp <= a[0][0]) return a[0][1];
  for (let i = 1; i < a.length; i++) {
    if (kwp <= a[i][0]) {
      const [k0, r0] = a[i - 1], [k1, r1] = a[i];
      return r0 + (r1 - r0) * (kwp - k0) / (k1 - k0);
    }
  }
  return a[a.length - 1][1];
}
function systemCost(kwp) { return kwp * costPerKw(kwp); }

// First-year value of the energy: what is used on site displaces electricity
// at the retail price, what is exported earns the buyback rate. Lifetime
// applies linear panel degradation over the system life. economicsFor (this
// function's caller) applies elec_inflation_pct and discount_rate_pct; it is
// kept for the tests only. The map's figures come from bill_tool.js, which
// shows today's dollars (general inflation, cpi_pct, taken out).
// Business or home?
//
// THE COUNCIL ALREADY ANSWERED THIS. Some homes were misclassified as
// businesses; the district plan is the building data for it. A zone is a statement about what may legally be built
// on the land, from the authority that decides it, and tools/fetch_zoning.py
// joins it onto every building as `zone_class`.
//
// Roof area was never a use signal and could not become one -- a 450 m2
// house in a suburb and a 450 m2 small business look identical from above,
// so the old rule mislabelled large houses by construction. Measured on the
// pilot: 205 of 1,066 buildings disagree with the zoning, in both
// directions. 66 homes were being billed as businesses (including six
// apartment blocks in High Density Residential), and 139 small buildings in
// the Queenstown Town Centre were being billed as homes.
//
// Zoning leads; where a zone genuinely holds both uses (Business Mixed Use,
// Settlements) or no zoning is available, the old geometry test still
// decides, because a blunt answer beats no answer.
function isBusiness(kwp, roofM2, zoneClass) {
  if (zoneClass === "business") return true;
  if (zoneClass === "home") return false;
  return (roofM2 || 0) >= econ.biz_min_roof_m2 || (kwp || 0) >= econ.biz_min_kw;
}
// Export rate in a calendar year: today's rate declining at a steady
// percentage. Takes the starting rate as an argument rather than reading a
// schedule, so the home and business figures each decline from their own
// starting point without needing the rescaling the anchored version did.
function sellRate(year, startCents) {
  const yrs = Math.max(0, year - THIS_YEAR);
  return startCents * Math.pow(1 - econ.export_decline_pct / 100, yrs);
}

// NOT WHAT THE MAP SHOWS since 28 Oct: every roof's panel colours and the
// building card are priced by bill_tool.js (btEvaluate, btPanelEcon). This
// flat-price, discounted model is kept for tests/test_economics.mjs.
function economicsFor(kwp, kwhYear, roofM2, override) {
  if (!(kwp > 0) || !(kwhYear > 0)) return null;
  const biz = (override && override.biz != null)
    ? override.biz
    : isBusiness(kwp, roofM2, override && override.zoneClass);
  const buy = biz ? econ.biz_buy_c : econ.home_buy_c;
  const sellNow = biz ? econ.biz_sell_c : econ.home_sell_c;
  // The decline schedule is anchored on today's rate, so editing the
  // home/business export price moves the whole curve with it instead of
  // snapping back to the schedule's own first value.
  // The old anchored schedule needed rescaling to today's edited rate; a
  // decline from the rate itself does not.
  const daytimeKw = (override && override.daytimeKw != null)
    ? override.daytimeKw : (biz ? econ.biz_daytime_kw : econ.home_daytime_kw);
  const useKwh = (override && override.useKwh != null)
    ? override.useKwh : (biz ? econ.biz_use_kwh : econ.home_use_kwh);
  const cost = systemCost(kwp);
  const d = econ.degradation_pct / 100;
  // The site can absorb at most daytime_kw for daytime_hours a day, so that
  // is the self-consumption ceiling however large the array gets. Annual use
  // is a second ceiling, for the case where someone enters a very low yearly
  // figure -- a site cannot self-consume more than it uses. Whichever binds
  // first, binds; generation past it is exported.
  // Raising a home's consumption from 7,000 to 10,000 kWh must raise its
  // self-consumption, and the old code could not do that: the ceiling
  // was min(daytime kW load, annual use), and for a typical home the kW side
  // always bound -- 1.5 kW x 6.7 h x 365 = 3,668 kWh against 7,000 used --
  // so editing annual use moved nothing at all until it dropped below 3,668.
  //
  // A house that uses more electricity uses more of it in daylight too, so
  // the daytime load scales with annual consumption rather than sitting
  // beside it as an independent number. The profile's kW is now read as
  // "this load AT THE DEFAULT annual use", and moves proportionally from
  // there. Both ceilings then respond together instead of one silently
  // dominating.
  const defaultUse = biz ? ECON_DEFAULTS.biz_use_kwh : ECON_DEFAULTS.home_use_kwh;
  const scaledDaytimeKw = daytimeKw * (useKwh / defaultUse);
  const loadCeiling = scaledDaytimeKw * econ.daytime_hours * 365;
  const ceiling = Math.min(loadCeiling, useKwh);
  const selfKwhFor = gen => Math.min(gen, ceiling);
  // Retail inflates, export does not, and everything is discounted back to
  // today. One function for every year so the annual figure, the lifetime
  // sum and the payback walk below all use the same numbers; memoised,
  // because those three ask for the same years over again.
  const infl = 1 + econ.elec_inflation_pct / 100;
  const disc = 1 + econ.discount_rate_pct / 100;
  const realValues = [];
  const realValueInYear = y => {
    if (realValues[y] === undefined) {
      const gen = kwhYear * Math.pow(1 - d, y);
      const selfKwh = selfKwhFor(gen);
      const retail = selfKwh * buy * Math.pow(infl, y);
      const exported = (gen - selfKwh) * sellRate(THIS_YEAR + y, sellNow);
      realValues[y] = (retail + exported) / 100 / Math.pow(disc, y);
    }
    return realValues[y];
  };
  const annual = realValueInYear(0);
  let lifetime = 0;
  for (let y = 0; y < econ.life_years; y++) lifetime += realValueInYear(y);
  // The inverter replacement is a cost in a future year, so it is discounted
  // the same way and subtracted from the lifetime figure.
  const inverterCost = econ.inverter_replace_year < econ.life_years
    ? cost * (econ.inverter_cost_pct / 100) / Math.pow(disc, econ.inverter_replace_year)
    : 0;
  lifetime -= inverterCost;
  let cum = 0, payback = null;
  for (let y = 0; y < 60; y++) {
    const v = realValueInYear(y);
    cum += v;
    if (cum >= cost) { payback = y + 1 - (cum - cost) / v; break; }
  }
  // Lifetime cents earned by ONE kWh/yr of extra generation, if all of it is
  // exported. This is the marginal panel's world: self-consumption is capped
  // by the daytime load and already met by the panels that came before it,
  // so an added panel earns the buyback rate, not the retail one.
  let exportLifetimePerKwhYr = 0, retailLifetimePerKwhYr = 0;
  for (let y = 0; y < econ.life_years; y++) {
    const deg = Math.pow(1 - d, y), dsc = Math.pow(disc, y);
    exportLifetimePerKwhYr += deg * sellRate(THIS_YEAR + y, sellNow) / 100 / dsc;
    retailLifetimePerKwhYr += deg * buy * Math.pow(infl, y) / 100 / dsc;
  }
  return { cost, annual, lifetime, payback, rate: costPerKw(kwp), biz, buy, sellNow,
           exportLifetimePerKwhYr, retailLifetimePerKwhYr,
           sellLater: sellRate(THIS_YEAR + econ.life_years, sellNow),
           inverterCost,
           daytimeKw: scaledDaytimeKw, ceiling, useKwh, selfKwh: selfKwhFor(kwhYear),
           // Share is now an OUTPUT of the model, not an input to it.
           selfPct: 100 * selfKwhFor(kwhYear) / kwhYear,
           capped: kwhYear > ceiling, capByUse: loadCeiling > useKwh };
}
// Node (tests) and browser (preview.html) both, without a build step.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { costAnchors, applyRegionalPrices, homePricesFor, ECON_DEFAULTS, SELF_PROFILES, costPerKw, systemCost,
                     isBusiness, sellRate, economicsFor, setEcon: e => { econ = e; },
                     getEcon: () => econ };
}

// ============================================================ HOURLY ENGINE
//
// WHY AN HOUR IS THE UNIT NOW. Everything above works on annual kWh with
// self-consumption capped by an average daytime load. That is enough to
// answer "is solar worth it", and it cannot answer either of two
// questions:
//
//   * a RETAIL PLAN only differs from another if it prices electricity
//     differently at different times -- day/night, peak/off-peak, a free
//     hour. Annually they are all just "a number of cents".
//   * a BATTERY is nothing but a time-shifting device. It moves midday
//     surplus into the evening. At annual resolution it does not exist;
//     the old model faked one as a bigger daytime load, which credits it
//     for energy it never stored.
//
// The resolution is a REPRESENTATIVE DAY PER SEASON -- four days of 24
// hours, weighted by the days in each season -- not 8,760 hours. Two
// reasons. The generation side is exactly what data/seasonal_curves.json
// already holds, so the money and the chart on screen can never disagree
// about what the roof makes. And an 8,760-hour run would need a genuine
// half-hourly load trace, which we do not have; pretending to that
// resolution with a repeated daily shape would be precision theatre.
//
// Everything here stays pure: numbers in, numbers out.

// Household and business demand through the day, as a share of the day's
// total. Shapes, not magnitudes: the annual use figure sets the scale.
// The home shape is the familiar double peak (breakfast, then a larger
// evening one after work) with a midday trough -- which is exactly why
// self-consumption is limited and why a battery has something to do.
const LOAD_SHAPES = {
  home: [
    0.020, 0.018, 0.017, 0.017, 0.018, 0.024, 0.038, 0.055,   // 0-7
    0.052, 0.042, 0.036, 0.034, 0.034, 0.033, 0.033, 0.036,   // 8-15
    0.045, 0.062, 0.085, 0.082, 0.068, 0.054, 0.040, 0.027,   // 16-23
  ],
  business: [
    0.012, 0.011, 0.011, 0.011, 0.012, 0.016, 0.026, 0.042,   // 0-7
    0.062, 0.073, 0.077, 0.078, 0.074, 0.076, 0.075, 0.070,   // 8-15
    0.058, 0.044, 0.030, 0.022, 0.018, 0.015, 0.014, 0.013,   // 16-23
  ],
};

// A plan prices each hour. `periods` maps an hour (0-23) to a rate in
// cents; `sell_c` is that plan's buyback. These are ILLUSTRATIVE SHAPES OF
// REAL PLAN TYPES sold in New Zealand, not offers from any retailer, and
// they are all editable -- which is why none of them carries a company
// name. The point is to let someone see how much the plan structure
// matters, which for a solar household is a lot: a plan that pays little
// for export but charges little at night suits a battery, and the reverse
// suits a bare array.
function flatRates(c) { return new Array(24).fill(c); }
function touRates(spec, base) {
  const r = new Array(24).fill(base);
  for (const [from, to, c] of spec) {
    for (let h = from; h !== to; h = (h + 1) % 24) r[h] = c;
  }
  return r;
}
const RETAIL_PLANS = [
  {
    id: "flat", label: "Flat rate", biz: false,
    note: "One price all day. The default here.",
    buy: () => flatRates(30), sell_c: 14, daily_c: 250,
  },
  {
    id: "day_night", label: "Day / night", biz: false,
    note: "Cheaper overnight, dearer in the day. Suits a battery charged " +
          "on cheap night power more than solar alone.",
    buy: () => touRates([[23, 7, 18]], 33), sell_c: 12, daily_c: 250,
  },
  {
    id: "peak_offpeak", label: "Peak / off-peak / night", biz: false,
    note: "Morning and evening peaks cost most. Solar alone rarely covers " +
          "them, so a battery earns most here.",
    buy: () => touRates([[23, 7, 17], [7, 9, 45], [17, 21, 45]], 28),
    sell_c: 12, daily_c: 250,
  },
  {
    id: "high_buyback", label: "High buyback, dearer power", biz: false,
    note: "Pays well for export, charges more for power you buy. Suits " +
          "a big system on a house that is empty in the day.",
    buy: () => flatRates(35), sell_c: 20, daily_c: 250,
  },
  {
    id: "free_evening", label: "One free hour a day", biz: false,
    note: "A free evening hour, paid for by a higher rate the rest " +
          "of the time. Check it against your own use.",
    buy: () => touRates([[21, 22, 0]], 33), sell_c: 12, daily_c: 250,
  },
  {
    id: "biz_flat", label: "Business — flat rate", biz: true,
    note: "Lower commercial rates per kWh. Use is mostly in the day, " +
          "when the roof generates.",
    buy: () => flatRates(17), sell_c: 14, daily_c: 800,
  },
  {
    id: "biz_tou", label: "Business — time of use", biz: true,
    note: "Daytime commercial rate with a cheaper overnight block.",
    buy: () => touRates([[22, 7, 12]], 19), sell_c: 14, daily_c: 800,
  },
];

// A battery is defined by what it can hold, how fast it moves energy, and
// what it loses doing so. `reserve_pct` is the floor a real installation
// keeps for backup and cycle life -- ignoring it would credit the battery
// with capacity no installer lets you use.
const BATTERY_DEFAULTS = {
  enabled: false,
  kwh: 10,            // usable-before-reserve capacity
  kw: 5,              // charge/discharge power limit
  round_trip_pct: 90, // in-and-out efficiency
  reserve_pct: 10,    // never discharged below this
  cost_per_kwh: 900,  // installed, NZ, incl. GST: the map's set price
  life_years: 15,     // replaced once inside a 30-year system life
  fade_pct: 2,        // capacity lost a year, as a share of new (bill_tool.js btEvaluate)
  export_kw: 5,       // the most sent to the grid in an hour, solar and battery together:
                      // NZ networks commonly limit a home's export to 5 kW
};

// One representative day. Generation and load are 24 kW values; the battery
// charges only from surplus (never from the grid, which is a separate
// product decision and a different plan calculation) and discharges only
// into a deficit.
//
// Returns the day's kWh split three ways, and the hour each one happened in,
// because a time-of-use plan prices them differently.
function simulateDay(genKw, loadKw, batt, socStart, opts) {
  const cap = batt && batt.enabled ? Math.max(0, batt.kwh) : 0;
  const floor = cap * (batt ? batt.reserve_pct : 0) / 100;
  const pmax = batt ? Math.max(0, batt.kw) : 0;
  // ROUND-TRIP LOSS, half each way: a kWh of spare solar adds sqrt(0.9) kWh
  // to the charge, and a kWh to the building takes 1/sqrt(0.9) from it, so a
  // kWh in returns round_trip_pct of a kWh (90%: tests/test_reference_cases).
  // chargeH is solar-side kWh, dischargeH and battExpH building/grid-side.
  const eff = Math.sqrt(Math.max(0.01, (batt ? batt.round_trip_pct : 100) / 100));
  // ON A TIME-OF-USE PLAN THE BATTERY SAVES ITS CHARGE FOR THE PEAK
  // (opts.isPeak(h) true in peak hours). In a peak hour it covers any
  // shortfall. Off-peak it gives only what the coming peak hours will not
  // need before the sun next refills it, so stored solar displaces the
  // dearest power first and is not spent at 2am leaving the 7am peak to the
  // grid. Without isPeak (a flat plan) it covers any shortfall as it comes.
  const isPeak = opts && typeof opts.isPeak === "function" ? opts.isPeak : null;
  // PEAK EXPORT (opts.exportPeak, where export pays more at peak than in the
  // day): in a peak hour, charge the building will not need before the sun
  // next refills the battery is sold to the grid, up to the battery's power.
  // Kept, it would sit full and the next day's spare sun would be exported
  // at the day's lower price instead.
  const exportPeak = !!(isPeak && opts.exportPeak);
  const gap = new Array(24), spare = new Array(24);
  for (let h = 0; h < 24; h++) {
    gap[h] = Math.max(0, loadKw[h] - genKw[h]);
    spare[h] = Math.max(0, genKw[h] - loadKw[h]);
  }
  // house-side kWh the peak hours ahead need before solar is next spare
  const peakNeedAfter = h => {
    let need = 0;
    for (let k = 1; k < 24; k++) {
      const j = (h + k) % 24;
      if (spare[j] > 1e-9) break;
      if (isPeak(j)) need += Math.min(gap[j], pmax);
    }
    return need;
  };
  // house-side kWh every hour ahead needs before solar is next spare
  const needAfter = h => {
    let need = 0;
    for (let k = 1; k < 24; k++) {
      const j = (h + k) % 24;
      if (spare[j] > 1e-9) break;
      need += Math.min(gap[j], pmax);
    }
    return need;
  };
  let soc = Math.min(Math.max(socStart, floor), cap);
  const selfH = new Array(24).fill(0);
  const expH = new Array(24).fill(0);
  const impH = new Array(24).fill(0);
  const chargeH = new Array(24).fill(0);      // spare solar into the battery (kWh in)
  const dischargeH = new Array(24).fill(0);   // battery to the building (kWh out)
  const battExpH = new Array(24).fill(0);     // battery to the grid at peak (kWh out)
  const socH = new Array(24).fill(0);         // charge at the end of each hour
  for (let h = 0; h < 24; h++) {
    const g = genKw[h], l = loadKw[h];
    const direct = Math.min(g, l);
    selfH[h] += direct;
    let surplus = g - direct;
    let deficit = l - direct;
    if (cap > 0 && surplus > 0) {
      const room = (cap - soc) / eff;              // grid-side kWh to fill
      const take = Math.min(surplus, pmax, room);
      soc += take * eff;
      surplus -= take;
      chargeH[h] = take;
    }
    if (cap > 0 && deficit > 0) {
      let avail = Math.max(0, (soc - floor) * eff); // house-side kWh available
      if (isPeak && !isPeak(h)) avail = Math.max(0, avail - peakNeedAfter(h));
      const give = Math.min(deficit, pmax, avail);
      soc -= give / eff;
      selfH[h] += give;                            // stored sun, used later
      deficit -= give;
      dischargeH[h] = give;
    }
    if (exportPeak && cap > 0 && isPeak(h) && deficit <= 1e-12) {
      const avail = Math.max(0, (soc - floor) * eff - needAfter(h));
      const lim = batt && batt.export_kw > 0 ? Math.max(0, batt.export_kw - surplus) : Infinity;
      const sell = Math.min(avail, pmax - dischargeH[h], lim);
      if (sell > 1e-12) { soc -= sell / eff; surplus += sell; battExpH[h] = sell; }
    }
    expH[h] = surplus;
    impH[h] = deficit;
    socH[h] = soc;
  }
  return { selfH, expH, impH, chargeH, dischargeH, battExpH, socH, socEnd: soc };
}

// A year of representative days. `genByHour[s][h]` is kW for season s, and
// `seasonDays[s]` how many days that season stands for.
//
// The battery's state of charge is carried across two passes of each season
// so a day starts where the previous one ended rather than empty -- an
// empty start every morning would understate a battery that habitually
// holds charge overnight.
function simulateYear(genByHour, seasonDays, dailyKwh, shape, batt) {
  const out = { selfKwh: 0, exportKwh: 0, importKwh: 0,
                selfByHour: new Array(24).fill(0),
                exportByHour: new Array(24).fill(0),
                importByHour: new Array(24).fill(0) };
  const loadKw = shape.map(f => f * dailyKwh);     // kWh in an hour == kW
  let soc = 0;
  for (let pass = 0; pass < 2; pass++) {
    for (let s = 0; s < genByHour.length; s++) {
      const d = simulateDay(genByHour[s], loadKw, batt, soc);
      soc = d.socEnd;
      if (pass === 0) continue;                    // first pass only warms soc
      const days = seasonDays[s];
      for (let h = 0; h < 24; h++) {
        out.selfByHour[h] += d.selfH[h] * days;
        out.exportByHour[h] += d.expH[h] * days;
        out.importByHour[h] += d.impH[h] * days;
      }
    }
  }
  for (let h = 0; h < 24; h++) {
    out.selfKwh += out.selfByHour[h];
    out.exportKwh += out.exportByHour[h];
    out.importKwh += out.importByHour[h];
  }
  return out;
}

// The money, hour by hour. Same shape of answer as economicsFor above --
// cost, annual, lifetime, payback -- so the page can swap one for the other,
// but every kWh is now priced at the hour it happened in and a battery is
// simulated rather than approximated.
//
// genByHour[s][h] is kW per hour for a representative day in season s, at
// TODAY's output. Degradation, retail inflation and the export decline are
// applied per year exactly as in the annual model, and the year is
// re-simulated each time: as generation fades the battery fills less, which
// a single scaling factor could not express.
function economicsHourlyFor(kwp, genByHour, seasonDays, roofM2, override) {
  if (!(kwp > 0) || !genByHour || !genByHour.length) return null;
  const o = override || {};
  const biz = (o.biz != null) ? o.biz : isBusiness(kwp, roofM2, o.zoneClass);
  const plan = o.plan || RETAIL_PLANS.find(p => !!p.biz === !!biz) || RETAIL_PLANS[0];
  const buyRates = o.buyRates || plan.buy();
  const sellStart = (o.sell_c != null) ? o.sell_c : plan.sell_c;
  const batt = Object.assign({}, BATTERY_DEFAULTS, o.battery || {});
  const useKwh = (o.useKwh != null) ? o.useKwh
    : (biz ? econ.biz_use_kwh : econ.home_use_kwh);
  const shape = o.shape || (biz ? LOAD_SHAPES.business : LOAD_SHAPES.home);
  const days = seasonDays || new Array(genByHour.length)
    .fill(365 / genByHour.length);
  const dailyKwh = useKwh / 365;

  const battCost = batt.enabled ? batt.kwh * batt.cost_per_kwh : 0;
  const cost = systemCost(kwp) + battCost;
  const d = econ.degradation_pct / 100;
  const infl = 1 + econ.elec_inflation_pct / 100;
  const disc = 1 + econ.discount_rate_pct / 100;

  // Re-simulating a year is the expensive step, and the annual figure, the
  // lifetime sum and the payback walk ask for the same years up to three
  // times over (~90 simulations per call, on every keystroke in the panel).
  // simulateYear is pure, so each year is simulated once.
  const years = new Map();
  const yearOf = y => {
    if (!years.has(y)) years.set(y, simulateYearValue(y));
    return years.get(y);
  };
  const simulateYearValue = y => {
    const deg = Math.pow(1 - d, y);
    const gen = genByHour.map(row => row.map(v => v * deg));
    const sim = simulateYear(gen, days, dailyKwh, shape, batt);
    let retail = 0;
    for (let h = 0; h < 24; h++) retail += sim.selfByHour[h] * buyRates[h];
    const exported = sim.exportKwh * sellRate(THIS_YEAR + y, sellStart);
    return { sim, value: (retail * Math.pow(infl, y) + exported) / 100 };
  };

  const y0 = yearOf(0);
  const annual = y0.value / Math.pow(disc, 0);
  let lifetime = 0;
  for (let y = 0; y < econ.life_years; y++) {
    lifetime += yearOf(y).value / Math.pow(disc, y);
  }
  const inverterCost = econ.inverter_replace_year < econ.life_years
    ? systemCost(kwp) * (econ.inverter_cost_pct / 100)
      / Math.pow(disc, econ.inverter_replace_year)
    : 0;
  // A battery does not last a 30-year system either, and leaving its
  // replacement out is the single easiest way to make one look good.
  const battReplace = (batt.enabled && batt.life_years < econ.life_years)
    ? battCost / Math.pow(disc, batt.life_years) : 0;
  lifetime -= inverterCost + battReplace;

  let cum = 0, payback = null;
  for (let y = 0; y < 60; y++) {
    const v = yearOf(Math.min(y, econ.life_years - 1)).value / Math.pow(disc, y);
    cum += v;
    if (cum >= cost) { payback = y + 1 - (cum - cost) / v; break; }
  }
  const genKwh = genByHour.reduce(
    (t, row, s) => t + row.reduce((a, b) => a + b, 0) * days[s], 0);
  return {
    cost, annual, lifetime, payback, biz, plan, batteryCost: battCost,
    inverterCost, batteryReplaceCost: battReplace,
    rate: costPerKw(kwp), sellNow: sellStart,
    genKwh, useKwh,
    selfKwh: y0.sim.selfKwh, exportKwh: y0.sim.exportKwh,
    importKwh: y0.sim.importKwh,
    selfPct: genKwh > 0 ? 100 * y0.sim.selfKwh / genKwh : 0,
    selfByHour: y0.sim.selfByHour, exportByHour: y0.sim.exportByHour,
    importByHour: y0.sim.importByHour,
    buyRates, hourly: true,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = Object.assign(module.exports || {}, {
    LOAD_SHAPES, RETAIL_PLANS, BATTERY_DEFAULTS, isBusiness,
    simulateDay, simulateYear, economicsHourlyFor,
  });
}
