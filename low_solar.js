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
// BEAM AND DIFFUSE SCALE APART (audit, 24 Oct). A tilted roof multiplies the
// sun's direct beam, not the grey sky's diffuse light. Scaling a day's whole
// output by its total sunlight made an overcast winter day on a north-facing
// 35-degree roof about twice as bright as it is (the roof's winter average
// is mostly boosted beam; an overcast day has none), so low days were
// undercounted on sunny roofs and overcounted on south-facing ones. Each day
// is now split into beam and diffuse by how clear it was (its clearness
// index against the sun above the atmosphere; Erbs et al. 1982's daily
// diffuse fraction), and the roof's own beam and diffuse shares of that
// month (the chart's diffuse-only curve, with the same shading) scale each
// part by its own ratio to the month's average.
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
    // the nearest VALLEY-LEVEL cell: a 25 km cell averaging over 800 m is
    // mountains, not the town in it (src/solar_model.py ERA5_MAX_CELL_ELEV_M)
    const valley = ix.cells.filter(c => c.elev == null || c.elev <= 800);
    const pool = valley.length ? valley : ix.cells;
    const c = pool.reduce((a, b) =>
      Math.hypot(b.lat - lat, b.lon - lng) < Math.hypot(a.lat - lat, a.lon - lng) ? b : a);
    if (Math.hypot(c.lat - lat, c.lon - lng) > 0.4) return null;     // no cell near this roof
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

  // Daily sunlight above the atmosphere on a level surface (MJ/m2), and the
  // diffuse share of a day's sunlight from its clearness (Erbs et al. 1982,
  // daily form, as in Duffie & Beckman 2013 eq. 2.11.2).
  function h0(latDeg, doy) {
    const phi = latDeg * Math.PI / 180;
    const dec = 23.45 * Math.PI / 180 * Math.sin(2 * Math.PI * (284 + doy) / 365);
    const ws = Math.acos(Math.max(-1, Math.min(1, -Math.tan(phi) * Math.tan(dec))));
    const e0 = 1 + 0.033 * Math.cos(2 * Math.PI * doy / 365);
    return { h: 24 * 3600 / Math.PI * 1367e-6 * e0 *
      (Math.cos(phi) * Math.cos(dec) * Math.sin(ws) + ws * Math.sin(phi) * Math.sin(dec)), ws: ws * 180 / Math.PI };
  }
  function diffuseFraction(kt, wsDeg) {
    if (wsDeg <= 81.4) return kt < 0.715 ? 1 - 0.2727 * kt + 2.4495 * kt * kt - 11.9514 * kt ** 3 + 9.3879 * kt ** 4 : 0.143;
    return kt < 0.722 ? 1 + 0.2832 * kt - 2.5557 * kt * kt + 0.8448 * kt ** 3 : 0.175;
  }

  // The roof's average day in each month, from its four seasonal days,
  // interpolated around the year between the seasons' middle days.
  // `share` (per season, optional) takes that share of each seasonal day.
  function roofMonthly(curve, share) {
    const pts = curve.seasons.map((s, i) => [SEASON_DOY[s],
      curve.gen[i].reduce((a, b) => a + b, 0) * (share ? share[i] : 1)])
      .filter(p => p[0] != null && !Number.isNaN(p[1])).sort((a, b) => a[0] - b[0]);
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

  // Every day's output on this roof. difShare: per season (curve.seasons
  // order), the diffuse share of the roof's average day; without it the day
  // scales as a whole (the old, cruder rule).
  function dailyGen(cell, curve, difShare) {
    const n = cell.mj.length;
    const lat = cell.lat;
    const beam = new Float64Array(n), dif = new Float64Array(n);
    const sb = new Float64Array(12), sd = new Float64Array(12), sum = new Float64Array(12), cnt = new Float64Array(12);
    const fill = new Float64Array(12), fillN = new Float64Array(12);
    for (let i = 0; i < n; i++) if (!Number.isNaN(cell.mj[i])) { fill[cell.month[i]] += cell.mj[i]; fillN[cell.month[i]]++; }
    for (let i = 0; i < n; i++) {
      const m = cell.month[i];
      const v = Number.isNaN(cell.mj[i]) ? fill[m] / Math.max(fillN[m], 1) : cell.mj[i];
      const doy = Math.floor((cell.t0 + i * 86400000 - Date.UTC(cell.year[i], 0, 1)) / 86400000) + 1;
      const x = h0(lat, doy);
      const kt = x.h > 0 ? Math.min(v / x.h, 0.9) : 0;
      const kd = Math.max(0, Math.min(1, diffuseFraction(kt, x.ws)));
      dif[i] = v * kd; beam[i] = v - dif[i];
      if (cell.year[i] >= 1991 && cell.year[i] <= 2020) { sb[m] += beam[i]; sd[m] += dif[i]; sum[m] += v; cnt[m]++; }
    }
    const sunMonth = Array.from(sum, (s, m) => s / Math.max(cnt[m], 1));
    const beamMonth = Array.from(sb, (s, m) => s / Math.max(cnt[m], 1));
    const difMonth = Array.from(sd, (s, m) => s / Math.max(cnt[m], 1));
    const roof = roofMonthly(curve);
    // a steep south face in winter is lit by the sky alone: its diffuse-only
    // curve can come out a little above the average one (sky-model rounding,
    // 1.09 at 30 degrees south), which is a share of 1
    const split = Array.isArray(difShare) && difShare.length === curve.seasons.length
      && difShare.every(x => x != null && Number.isFinite(x) && x >= 0);
    const roofDif = split ? roofMonthly(curve, difShare.map(x => Math.min(1, x))) : null;
    const gen = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      const m = cell.month[i];
      if (split) {
        const d = Math.min(roofDif[m], roof[m]);
        gen[i] = (roof[m] - d) * (beamMonth[m] > 0 ? beam[i] / beamMonth[m] : 0) + d * dif[i] / difMonth[m];
      } else {
        gen[i] = roof[m] * (beam[i] + dif[i]) / sunMonth[m];
      }
    }
    return { gen, roof, roofDif, sunMonth, split };
  }

  function analyse(cell, curve, threshold, difShare) {
    const n = cell.mj.length;
    const { gen, roof, roofDif, sunMonth, split } = dailyGen(cell, curve, difShare);
    let tot = 0;
    for (let i = 0; i < n; i++) tot += gen[i];
    const mean = tot / n, cut = threshold * mean;
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
    return { mean, cut, roof, roofDif, split, gen, sunMonth, runs, lowShare: lowDays / n, years, y0, y1, amax,
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
    const r = analyse(cell, o.curve, threshold, o.difShare);
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

  // THE RESILIENCE LIGHTBOX (Josh, 24 Oct): the same record, in full -- each
  // year's longest run, when in the year low days fall, the worst runs with
  // their dates, and the roof's average and darkest days month by month.
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  // darker blue bars; the highlighted year and the once-a-decade line in a
  // light red-orange -- not yellow, which on this map means sun (Josh, 24 Oct)
  const BAR = "#2f5ea8", HI = "#ff8a65";
  function bars(values, o) {
    const W = o.w || 720, H = o.h || 150, pad = { l: 34, r: 8, t: 10, b: 22 };
    const max = Math.max(1, ...values, o.line || 0);
    const bw = (W - pad.l - pad.r) / values.length;
    const y = v => pad.t + (H - pad.t - pad.b) * (1 - v / max);
    let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${o.label || ""}">`;
    const step = max <= 6 ? 1 : max <= 15 ? 3 : max <= 40 ? 10 : max <= 100 ? 25 : 50;
    for (let v = 0; v <= max; v += step) {
      svg += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}" stroke="rgba(255,255,255,.08)"/>` +
             `<text x="${pad.l - 6}" y="${y(v) + 3.5}" text-anchor="end" font-size="10" fill="rgba(235,235,245,.5)">${v}${o.unit || ""}</text>`;
    }
    values.forEach((v, i) => {
      const hi = o.highlight && o.highlight(i);
      svg += `<rect x="${pad.l + i * bw + bw * 0.12}" y="${y(v)}" width="${Math.max(1, bw * 0.76)}" height="${H - pad.b - y(v)}" fill="${hi ? HI : o.colour || BAR}"><title>${o.tip ? o.tip(i, v) : v}</title></rect>`;
    });
    if (o.line) svg += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(o.line)}" y2="${y(o.line)}" stroke="${HI}" stroke-width="1.5" stroke-dasharray="5 4"/>` +
      `<text x="${W - pad.r}" y="${y(o.line) - 4}" text-anchor="end" font-size="11" fill="${HI}">${o.lineLabel || ""}</text>`;
    (o.ticks || []).forEach(([i, t]) => {
      svg += `<text x="${pad.l + i * bw + bw / 2}" y="${H - 6}" text-anchor="middle" font-size="10" fill="rgba(235,235,245,.5)">${t}</text>`;
    });
    return svg + `</svg>`;
  }

  async function renderFull(box, o) {
    if (!box) return;
    box.innerHTML = `<div class="ls-note">Loading 86 years of daily sunlight&hellip;</div>`;
    const cell = o.curve ? await getCell(o.lat, o.lng) : null;
    if (!cell) { box.innerHTML = `<div class="ls-note">No long-term daily sunlight record for this roof.</div>`; return; }
    const r = analyse(cell, o.curve, threshold, o.difShare);
    const pct = Math.round(threshold * 100);
    // low days by month, as a share of that month's days
    const lowM = new Array(12).fill(0), allM = new Array(12).fill(0), darkM = new Array(12).fill(Infinity);
    const n = cell.mj.length;
    for (let i = 0; i < n; i++) {
      const m = cell.month[i], g = r.gen[i];
      allM[m]++; if (g < r.cut) lowM[m]++;
      if (g < darkM[m]) darkM[m] = g;
    }
    const worst = r.runs.slice().sort((a, b) => b.len - a.len || a.start - b.start).slice(0, 8);
    const years = Array.from({ length: r.years }, (_, k) => r.y0 + k);
    const structural = r.lowShare > 0.15;
    const hiYear = r.longest.len ? cell.year[r.longest.start] : null;
    box.innerHTML =
      `<div class="rs-figs">` +
        `<div class="rs-fig"><div class="ls-v">${days(r.longest.len)}</div><div class="ls-l">Max low-solar days in a row</div><div class="ls-s">${r.longest.len ? "from " + fmtDate(cell, r.longest.start) : "none on record"}</div></div>` +
        `<div class="rs-fig"><div class="ls-v">${days(r.decade)}</div><div class="ls-l">Once a decade</div><div class="ls-s">${r.decade ? "runs this long: " + times(r.decadePerDecade, "decade") : "rare"}</div></div>` +
        `<div class="rs-fig"><div class="ls-v">${days(r.typical)}</div><div class="ls-l">Common low-solar days in a row</div><div class="ls-s">${r.typical ? "in a normal year; " + times(r.typicalPerYear, "year") : "most years have none"}</div></div>` +
      `</div>` +
      `<label class="ls-thr rs-thr">A low-solar day makes under <input id="rs-thr" type="number" min="5" max="90" step="5" value="${pct}">% ` +
        `of this roof's average day (${r.mean.toFixed(1)} kWh, i.e. under ${r.cut.toFixed(1)} kWh) &middot; ${(r.lowShare * 100).toFixed(1)}% of all days, ${r.y0}&ndash;${r.y1}</label>` +
      (structural ? `<div class="ls-note"><b>Most of winter is "low" on this roof:</b> it faces away from the winter sun or is shaded, so its longest runs are the season itself, not a run of bad weather. A battery sized on these runs would be sized for winter.</div>` : "") +
      `<div class="rs-grid"><div class="rs-col">` +
        `<h4>Longest run of low-solar days, each year${hiYear ? ` <span class="rs-key"><i></i>${hiYear}, the longest</span>` : ""}</h4>` +
        bars(years.map((_, k) => r.amax[k]), {
          h: 118, label: "Each year's longest run", line: r.decade, lineLabel: `once a decade: ${days(r.decade)}`,
          highlight: k => hiYear != null && r.y0 + k === hiYear,
          tip: (k, v) => `${r.y0 + k}: ${days(v)}`,
          ticks: years.map((y, k) => [k, y]).filter(([, y]) => y % 10 === 0) }) +
        `<h4>When low-solar days happen <span class="rs-sub">share of each month's days, ${r.y0}&ndash;${r.y1}</span></h4>` +
        bars(lowM.map((v, m) => 100 * v / allM[m]), { label: "Share of low-solar days by month", h: 96, unit: "%",
          tip: (m, v) => `${MON[m]}: ${v.toFixed(1)}% of days`, ticks: MON.map((t, m) => [m, t]) }) +
        `<div class="rs-hz-slot"></div>` +
      `</div><div class="rs-col">` +
        `<div class="rs-two">` +
          `<div><h4>Longest runs on record</h4><table class="rs-tab"><tr><th>Days</th><th>Starting</th></tr>` +
            worst.map(w => `<tr><td>${w.len}</td><td>${fmtDate(cell, w.start)}</td></tr>`).join("") + `</table></div>` +
          `<div><h4>This roof, by month</h4><table class="rs-tab"><tr><th></th><th>Average</th><th>Darkest</th></tr>` +
            MON.map((t, m) => `<tr><td>${t}</td><td>${r.roof[m].toFixed(1)}</td><td>${darkM[m].toFixed(1)}</td></tr>`).join("") +
            `<tr><td></td><td colspan="2" class="rs-unit">kWh a day</td></tr></table></div>` +
        `</div>` +
        `<details class="clb-info"><summary>How this is worked out</summary>` +
          `<p>Daily sunlight for every day from ${r.y0} to ${r.y1} comes from the ERA5 reanalysis (Copernicus Climate Change Service, via Open-Meteo, CC BY 4.0) for the ~25 km grid cell at ${cell.lat}, ${cell.lon}. Each day is split into direct sun and grey-sky light by how clear it was, and each part is scaled to this roof: its own average day in that month (from its tilt, direction, panels and the shading of hills, trees and buildings), split the same way. A tilted roof gains from direct sun, not from an overcast sky, so a dull day is dull on every roof.</p>` +
          `<p>A low-solar day makes under ${pct}% of the roof's average day across the whole year. Winter's average day is well under the year's, so in practice these are dark winter days. A run is consecutive low-solar days. The once-a-decade run is the length only one year in ten exceeds; the common run is the middle year's longest.</p>` +
          `<p>Checked against NASA's satellite record (POWER, 1984&ndash;2024): the same average sunlight and a day-by-day correlation of 0.94, but the satellite has about twice as many very dark winter days (1% against 0.5%). Reanalysis smooths the darkest days, so real runs can be somewhat longer. A check against NIWA station records is next.</p></details>` +
      `</div></div>`;
    if (o.afterRender) o.afterRender(box);
    const inp = box.querySelector("#rs-thr");
    if (inp) inp.addEventListener("change", () => {
      threshold = Math.max(5, Math.min(90, +inp.value || 20)) / 100;
      renderFull(box, o);
      if (o.onThreshold) o.onThreshold();
    });
  }

  return { render, renderFull, analyse, roofMonthly, dailyGen, h0, diffuseFraction };
})();
if (typeof module !== "undefined") module.exports = LowSolar;
