// Historic wholesale electricity prices: averages for any period, by time of
// day, in real or nominal dollars. Draws wholesale_prices.json, which
// tools/fetch_wholesale_prices.py writes from the Electricity Authority's
// half-hourly final energy prices and Stats NZ's CPI. This file holds no
// figures; the arithmetic is wsAverage (tests/test_wholesale.mjs).
(function (root) {
  "use strict";
  const GROUPS = ["sun", "peak", "night", "all"];
  const LABEL = { sun: "Sunlight hours", peak: "Peak", night: "Overnight", all: "All hours" };

  // Average price per group over [from, to] (ISO dates, inclusive), in c/kWh:
  // the sum of each day's sums over the sum of its counts, so every
  // half-hour counts once. $/MWh / 10 = c/kWh.
  function wsAverage(node, cols, from, to, real) {
    const at = {};
    cols.forEach((c, i) => { at[c] = i; });
    const out = {}, n = {};
    for (const g of GROUPS) { out[g] = 0; n[g] = 0; }
    let days = 0;
    for (const row of node.days) {
      if (row[0] < from || row[0] > to) continue;
      days++;
      for (const g of GROUPS) {
        out[g] += row[at[`${g}_sum_${real ? "real" : "nominal"}`]];
        n[g] += row[at[`${g}_count`]];
      }
    }
    const res = { days };
    for (const g of GROUPS) res[g] = n[g] ? out[g] / n[g] / 10 : null;
    res.halfHours = n.all;
    return res;
  }

  // the first and last day held, and N years back from the last
  function wsSpan(node) { return [node.days[0][0], node.days[node.days.length - 1][0]]; }
  function wsYearsBack(lastIso, years) {
    // the day after the last, N years earlier: the range then holds exactly
    // N calendar years of days (366 for a year ending 29 Feb)
    const d = new Date(lastIso + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() + 1);
    d.setUTCFullYear(d.getUTCFullYear() - years);
    return d.toISOString().slice(0, 10);
  }

  // monthly averages for the chart: { month: "YYYY-MM", g: c/kWh }
  function wsMonthly(node, cols, real, from = "", to = "9999") {
    const months = new Map();
    for (const row of node.days) {
      if (row[0] < from || row[0] > to) continue;
      const m = row[0].slice(0, 7);
      if (!months.has(m)) months.set(m, { from: row[0], to: row[0] });
      months.get(m).to = row[0];
    }
    return [...months.entries()].map(([m, r]) => Object.assign({ month: m }, wsAverage(node, cols, r.from, r.to, real)));
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { wsAverage, wsSpan, wsYearsBack, wsMonthly, GROUPS };
    return;
  }

  // ---- the page -------------------------------------------------------------
  let DATA = null, nodeKey = "FKN0331", real = true;
  const $ = s => document.querySelector(s);
  const fmt = v => v == null ? "–" : v.toFixed(1);
  const longDate = iso => new Date(iso + "T00:00:00Z").toLocaleDateString("en-NZ", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

  function card(title, sub, r) {
    return `<section class="ws-card"><h3>${title}</h3><p class="ws-sub">${sub}</p>` +
      `<dl>${GROUPS.map(g => `<div class="ws-g ws-${g}"><dt>${LABEL[g]}</dt><dd>${fmt(r[g])}<small>c/kWh</small></dd></div>`).join("")}</dl></section>`;
  }

  function render() {
    const node = DATA.nodes[nodeKey], cols = DATA.columns;
    const [first, last] = wsSpan(node);
    const boxes = [1, 2, 5, 10].map(y => {
      const from = wsYearsBack(last, y);
      if (from < first) return "";
      return card(`Last ${y} year${y > 1 ? "s" : ""}`, `${longDate(from)} – ${longDate(last)}`, wsAverage(node, cols, from, last, real));
    }).join("");
    $("#ws-boxes").innerHTML = boxes;
    const f = $("#ws-from").value || wsYearsBack(last, 3), t = $("#ws-to").value || last;
    $("#ws-from").min = $("#ws-to").min = first; $("#ws-from").max = $("#ws-to").max = last;
    if (!$("#ws-from").value) { $("#ws-from").value = f; $("#ws-to").value = t; }
    const r = wsAverage(node, cols, $("#ws-from").value, $("#ws-to").value, real);
    $("#ws-custom").innerHTML = card("Your period", `${longDate($("#ws-from").value)} – ${longDate($("#ws-to").value)} · ${r.days.toLocaleString()} days`, r);
    $("#ws-unit").textContent = real ? `${DATA.cpi_label} dollars` : "dollars of the day";
    drawChart(wsMonthly(node, cols, real, $("#ws-from").value, $("#ws-to").value));
  }

  function drawChart(rows) {
    if (!rows.length) { $("#ws-chart").textContent = "No days in this period."; return; }
    const W = 900, H = 260, L = 40, R = 10, T = 10, B = 26;
    const ymax = Math.max(...rows.map(r => Math.max(r.peak || 0, r.all || 0, r.sun || 0, r.night || 0)));
    const top = Math.ceil(ymax / 5) * 5;
    const x = i => L + i * (W - L - R) / Math.max(1, rows.length - 1);
    const y = v => T + (H - T - B) * (1 - v / top);
    const years = rows.length / 12, step = years > 15 ? 5 : years > 6 ? 2 : 1;
    let svg = "";
    for (let v = 0; v <= top; v += top > 40 ? 10 : 5) {
      svg += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="ws-gl"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" class="ws-ax">${v}</text>`;
    }
    rows.forEach((r, i) => {
      if (r.month.endsWith("-01") && (+r.month.slice(0, 4)) % step === 0) {
        svg += `<text x="${x(i)}" y="${H - 6}" text-anchor="middle" class="ws-ax">${r.month.slice(0, 4)}</text>`;
      }
    });
    for (const g of GROUPS) {
      const pts = rows.map((r, i) => r[g] == null ? null : `${x(i).toFixed(1)},${y(r[g]).toFixed(1)}`).filter(Boolean).join(" ");
      svg += `<polyline points="${pts}" class="ws-line ws-${g}" fill="none"/>`;
    }
    $("#ws-chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Monthly average wholesale price, c/kWh">${svg}</svg>`;
  }

  async function start() {
    try {
      DATA = await (await fetch("wholesale_prices.json", { cache: "no-cache" })).json();
    } catch (e) {
      $("#ws-boxes").textContent = "Could not load wholesale_prices.json.";
      return;
    }
    DATA.cpi_label = DATA.cpi_quarter ? new Date(DATA.cpi_quarter + "-01T00:00:00Z").toLocaleDateString("en-NZ", { month: "long", year: "numeric", timeZone: "UTC" }) : "today's";
    const sel = $("#ws-node");
    sel.innerHTML = Object.entries(DATA.nodes).map(([k, v]) => `<option value="${k}">${v.name}</option>`).join("");
    sel.value = nodeKey;
    sel.addEventListener("change", () => { nodeKey = sel.value; render(); });
    document.querySelectorAll("[data-real]").forEach(b => b.addEventListener("click", () => {
      real = b.dataset.real === "1";
      document.querySelectorAll("[data-real]").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
      render();
    }));
    $("#ws-from").addEventListener("change", render);
    $("#ws-to").addEventListener("change", render);
    $("#ws-real-btn").textContent = $("#ws-real-name").textContent = `${DATA.cpi_label} dollars`;
    $("#ws-source").textContent = `${DATA.source}. Inflation: ${DATA.cpi}.`;
    render();
  }
  document.addEventListener("DOMContentLoaded", start);
})(typeof window !== "undefined" ? window : globalThis);
