// Low-solar runs: how many days in a row a roof makes little power, from 86
// years of daily sunlight (Josh, 22 Oct: "how many days of battery storage
// might be required").
//
// DATA. ERA5 reanalysis daily solar radiation, 1940-2025, on its ~25 km grid
// (Copernicus Climate Change Service, via Open-Meteo, CC BY 4.0), packed one
// byte a day per cell by tools/pack_era5_daily.py into climate/era5/. It is
// used for the DAY-TO-DAY pattern only: each roof's own seasonal output (the
// building card's generation curve, which carries its tilt, direction,
// shading and hills) sets the level, and each day of the record scales it by
// how that day's sunlight compared with the same month's average.
//
// A LOW DAY is one under `threshold` of the roof's own average day. A run is
// consecutive low days. Reported:
//   longest run in the record (and when),
//   the once-a-decade run (90th percentile of each year's longest run) and
//     how often runs that long happen,
//   a typical year's longest run (median of each year's longest) and how
//     often runs that long happen in a year.
// Reanalysis smooths the darkest days a little, so real runs may be somewhat
// longer; checking against NIWA station records is in BACKLOG.

const LowSolar = (() => {
  const BASE = "climate/era5/";
  const SEASON_DOY = { summer: 15, autumn: 105, winter: 196, spring: 288 };   // mid Jan / Apr / Jul / Oct
  const MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  let index = null;
  const cells = {};

  async function getIndex() {
    if (!index) index = fetch(BASE + "index.json").then(r => r.ok ? r.json() : null).catch(() => null);
    return index;
  }
  async function getCell(lat, lng) {
    const ix = await getIndex();
    if (!ix || !ix.cells || !ix.cells.length) return null;
    const c = ix.cells.reduce((a, b) =>
      Math.hypot(b.lat - lat, b.lon - lng) < Math.hypot(a.lat - lat, a.lon - lng) ? b : a);
    if (Math.hypot(c.lat - lat, c.lon - lng) > 0.3) return null;     // no cell near this roof
    if (!cells[c.name]) {
      cells[c.name] = fetch(BASE + c.name + ".json").then(r => r.ok ? r.json() : null).then(d => {
        if (!d) return null;
        const bin = atob(d.v), mj = new Float32Array(bin.length);
        for (let i = 0; i < bin.length; i++) { const b = bin.charCodeAt(i); mj[i] = b === 255 ? NaN : b / d.q; }
        const t0 = Date.UTC(+d.start.slice(0, 4), +d.start.slice(5, 7) - 1, +d.start.slice(8, 10));
        const month = new Uint8Array(mj.length), year = new Uint16Array(mj.length);
        for (let i = 0; i < mj.length; i++) {
          const dt = new Date(t0 + i * 86400000);
          month[i] = dt.getUTCMonth(); year[i] = dt.getUTCFullYear();
        }
        return { name: c.name, lat: c.lat, lon: c.lon, t0, mj, month, year };
      }).catch(() => null);
    }
    return cells[c.name];
  }

  // The roof's average day in each month, from its four seasonal days,
  // interpolated around the year between the seasons' middle days.
  function roofMonthly(curve) {
    const pts = curve.seasons.map((s, i) => [SEASON_DOY[s], curve.gen[i].reduce((a, b) => a + b, 0)])
      .filter(p => p[0] != null).sort((a, b) => a[0] - b[0]);
    const at = doy => {
      for (let k = 0; k < pts.length; k++) {
        const a = pts[k], b = pts[(k + 1) % pts.length];
        const span = ((b[0] - a[0]) + 365) % 365 || 365, off = ((doy - a[0]) + 365) % 365;
        if (off <= span) return a[1] + (b[1] - a[1]) * off / span;
      }
      return pts[0][1];
    };
    const out = []; let d = 0;
    for (let m = 0; m < 12; m++) {
      let s = 0;
      for (let k = 0; k < MONTH_DAYS[m]; k++) s += at(d + k + 1);
      out.push(s / MONTH_DAYS[m]); d += MONTH_DAYS[m];
    }
    return out;
  }

  function analyse(cell, curve, threshold) {
    const n = cell.mj.length;
    const sum = new Float64Array(12), cnt = new Float64Array(12);
    for (let i = 0; i < n; i++) if (!Number.isNaN(cell.mj[i]) && cell.year[i] >= 1991 && cell.year[i] <= 2020) {
      sum[cell.month[i]] += cell.mj[i]; cnt[cell.month[i]]++;
    }
    const sunMonth = Array.from(sum, (s, m) => s / Math.max(cnt[m], 1));
    const roof = roofMonthly(curve);
    const gen = new Float64Array(n);
    let tot = 0, k = 0;
    for (let i = 0; i < n; i++) {
      const m = cell.month[i], v = Number.isNaN(cell.mj[i]) ? sunMonth[m] : cell.mj[i];
      gen[i] = roof[m] * v / sunMonth[m]; tot += gen[i]; k++;
    }
    const mean = tot / k, cut = threshold * mean;
    const runs = []; let lowDays = 0;
    for (let i = 0; i < n;) {
      if (gen[i] < cut) {
        let j = i; while (j < n && gen[j] < cut) j++;
        runs.push({ len: j - i, start: i }); lowDays += j - i; i = j;
      } else i++;
    }
    const y0 = cell.year[0], y1 = cell.year[n - 1], years = y1 - y0 + 1;
    const amax = new Array(years).fill(0);
    for (const r of runs) { const y = cell.year[r.start] - y0; amax[y] = Math.max(amax[y], r.len); }
    const sorted = amax.slice().sort((a, b) => a - b);
    const q = p => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
    const typical = q(0.5), decade = q(0.9);
    const longest = runs.reduce((a, b) => (b.len > a.len ? b : a), { len: 0, start: 0 });
    const count = L => runs.filter(r => r.len >= L).length;
    return { mean, cut, roof, sunMonth, runs, lowShare: lowDays / n, years, y0, y1, amax,
             longest, typical, decade, typicalPerYear: count(typical) / years,
             decadePerDecade: count(decade) / years * 10 };
  }

  const fmtDate = (cell, i) => new Date(cell.t0 + i * 86400000)
    .toLocaleDateString("en-NZ", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  const days = n => n === 1 ? "1 day" : `${n} days`;
  const times = (x, per) => x < 0.7
    ? `about once every ${Math.round(per === "year" ? 1 / x : 10 / x)} ${per === "year" ? "years" : "decades"}`
    : `about ${x < 1.5 ? "once" : x < 2.5 ? "twice" : Math.round(x) + " times"} a ${per}`;

  let threshold = 0.2;
  async function render(box, o) {
    if (!box) return;
    if (!o.curve || !o.curve.gen) { box.innerHTML = ""; return; }
    box.innerHTML = `<div class="ls-note">Loading 86 years of daily sunlight&hellip;</div>`;
    const cell = await getCell(o.lat, o.lng);
    if (!cell) { box.innerHTML = `<div class="ls-note">No long-term daily sunlight record for this area yet.</div>`; return; }
    const r = analyse(cell, o.curve, threshold);
    const node = (label, value, unit, formula) => (typeof Wk !== "undefined")
      ? Wk.given(label, value, unit, [
          Wk.input("A low day: under this share of the roof's average day", threshold, "share", { dp: 0 }),
          Wk.input("The roof's average day", r.mean, "kWh", { dp: 1, source: "This roof's generation curve, spread over 86 years of daily sunlight" }),
          Wk.input("Years of daily sunlight", r.years, "yrs", { source: `ERA5 reanalysis ${r.y0}-${r.y1}, ~25 km cell at ${cell.lat}, ${cell.lon} (Copernicus via Open-Meteo, CC BY 4.0)` })],
          { formula, check: () => value }) : null;
    const structural = r.lowShare > 0.15;
    const longestTxt = r.longest.len ? `${days(r.longest.len)} <small>from ${fmtDate(cell, r.longest.start)}</small>` : "none";
    const fig = (key, label, value, sub) =>
      `<div class="ls-fig" data-ls="${key}" tabindex="0" role="button">` +
        `<div class="ls-v">${value}</div><div class="ls-l">${label}</div><div class="ls-s">${sub}</div></div>`;
    box.innerHTML =
      `<div class="ls">` +
        fig("longest", "Max low-solar days in a row", days(r.longest.len),
            r.longest.len ? `from ${fmtDate(cell, r.longest.start)} (${r.y0}&ndash;${r.y1} record)` : `none in ${r.y0}&ndash;${r.y1}`) +
        fig("decade", "Once a decade", days(r.decade),
            r.decade ? `runs this long: ${times(r.decadePerDecade, "decade")}` : "rare") +
        fig("typical", "Common low-solar days in a row", days(r.typical),
            r.typical ? `in a normal year; ${times(r.typicalPerYear, "year")}` : "most years have none") +
        `<label class="ls-thr">A low day is under <input id="ls-thr" type="number" min="5" max="90" step="5" value="${Math.round(threshold * 100)}">% ` +
          `of this roof's average day (${r.mean.toFixed(1)} kWh) &middot; ${(r.lowShare * 100).toFixed(1)}% of days</label>` +
      `</div>` +
      (structural ? `<div class="ls-note"><b>Most of winter is "low" on this roof:</b> it faces away from the winter sun or is shaded, so its longest runs are the season itself, not a run of bad weather.</div>` : "") +
      `<div class="ls-note">From ${r.years} years of daily sunlight (ERA5 reanalysis, ~25 km grid), scaled to this roof's own seasons. ` +
      `Reanalysis smooths the very darkest days, so real runs can be a little longer.</div>`;
    const nodes = {
      longest: () => node("Max low-solar days in a row", r.longest.len, "days", `the longest run of consecutive days under ${Math.round(threshold * 100)}% of the roof's average day, ${r.y0}-${r.y1}${r.longest.len ? `, starting ${fmtDate(cell, r.longest.start)}` : ""}`),
      decade: () => node("Once-a-decade run", r.decade, "days", `each year's longest run, ranked: the length only one year in ten exceeds; runs at least this long happened ${r.runs.filter(x => x.len >= r.decade).length} times in ${r.years} years`),
      typical: () => node("Common low-solar days in a row", r.typical, "days", `each year's longest run, ranked: the middle year; runs at least this long happened ${r.runs.filter(x => x.len >= r.typical).length} times in ${r.years} years`),
    };
    box.querySelectorAll("[data-ls]").forEach(el => {
      if (typeof WkView !== "undefined") WkView.bind(el, nodes[el.dataset.ls]);
    });
    const inp = box.querySelector("#ls-thr");
    if (inp) inp.addEventListener("change", () => {
      const v = Math.max(5, Math.min(90, +inp.value || 20));
      threshold = v / 100; render(box, o);
    });
  }

  return { render, analyse, roofMonthly };
})();
if (typeof module !== "undefined") module.exports = LowSolar;
