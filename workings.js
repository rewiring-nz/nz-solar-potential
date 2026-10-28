// Workings: every number on the map, with the sum that made it.
//
// WHY. Josh, 17 Oct: each major number must be traceable and checkable by
// the person reading it -- click it, see how it was reached, spot a problem
// if there is one -- and the breakdown must ALWAYS match the code.
//
// HOW IT CANNOT DRIFT. A breakdown written beside a calculation is a second
// copy of it, and second copies drift. So the calculation is written IN
// these nodes: a node is built from its inputs by an operation and computes
// its own value. The number on screen is node.value; the breakdown is the
// same node opened up. There is nothing else to keep in step.
//
//   const g = Wk.input("Sunlight on flat ground", 1312, "kWh/m²/yr", {source: "..."});
//   const k = Wk.input("Kept after losses", 0.86, "share");
//   const e = Wk.product("Generation", "kWh/yr", [g, area, k]);
//   e.value   // what the page shows
//
// CHECKS. Wk.verify(node) recomputes every operation in the tree from its
// children and reports any step that does not add up; the page runs it on
// every breakdown it opens and says so on screen, and the tests run it on
// every model output. Steps that are not plain arithmetic (an hour-by-hour
// battery run, a payback year) are tables or `given` nodes that carry their
// own check, and a `given` node without one is reported as unchecked rather
// than trusted.
//
// Pure model first (Node and browser); the panel that shows a breakdown is
// at the bottom and only runs in a page.

const Wk = (() => {
  let nextId = 1;
  const TOL = 1e-9;

  function node(kind, label, unit, value, extra) {
    const n = Object.assign({ id: nextId++, kind, label, unit, value,
                              children: [], flags: [] }, extra || {});
    if (!Number.isFinite(n.value) && n.value !== null) {
      n.flags.push("This number could not be worked out (" + String(value) + ").");
    }
    return n;
  }
  const opt = o => o || {};
  // edit: {key, scale, unit, step} -- an assumption the reader may change in
  // place (WkView shows it as a field; the page decides what the key means)
  const meta = o => ({ source: opt(o).source, note: opt(o).note, dp: opt(o).dp,
                       flags: (opt(o).flags || []).slice(),
                       ...(opt(o).edit ? { edit: opt(o).edit } : {}) });

  // ---- leaves -------------------------------------------------------------
  // A number from outside the calculation: an assumption, a measurement, a
  // figure from the build. `source` says where it comes from.
  function input(label, value, unit, o) {
    return node("input", label, unit, value, meta(o));
  }

  // ---- operations ---------------------------------------------------------
  const OPS = {
    product: { sym: "×", f: v => v.reduce((a, b) => a * b, 1) },
    sum:     { sym: "+", f: v => v.reduce((a, b) => a + b, 0) },
    diff:    { sym: "−", f: v => v[0] - v[1] },
    quotient:{ sym: "÷", f: v => v[0] / v[1] },
    min:     { sym: "smaller of", f: v => Math.min(...v) },
    max:     { sym: "larger of", f: v => Math.max(...v) },
    // 1 − share (a loss becomes what is kept)
    complement: { sym: "1 −", f: v => 1 - v[0] },
  };
  function op(name, label, unit, kids, o) {
    const n = node("op", label, unit, OPS[name].f(kids.map(k => k.value)), meta(o));
    n.op = name;
    n.children = kids;
    return n;
  }
  const product = (l, u, k, o) => op("product", l, u, k, o);
  const sum = (l, u, k, o) => op("sum", l, u, k, o);
  const diff = (l, u, a, b, o) => op("diff", l, u, [a, b], o);
  const quotient = (l, u, a, b, o) => op("quotient", l, u, [a, b], o);
  const minOf = (l, u, k, o) => op("min", l, u, k, o);
  const maxOf = (l, u, k, o) => op("max", l, u, k, o);
  const complement = (l, share, o) => op("complement", l, "share", [share], o);

  // Held between lo and hi; flagged when it had to be.
  function clamp(label, unit, child, lo, hi, o) {
    const v = Math.min(hi, Math.max(lo, child.value));
    const n = node("op", label, unit, v, meta(o));
    n.op = "clamp"; n.lo = lo; n.hi = hi; n.children = [child];
    if (v !== child.value) {
      n.flags.push(`Held at ${fmt(v, unit)}. The sum gave ${fmt(child.value, unit)}, ` +
                   `outside the allowed range of ${fmt(lo, unit)} to ${fmt(hi, unit)}.`);
    }
    return n;
  }

  // A table whose total is the node's value: column `totalCol` summed over
  // rows (optionally x a weight column). Hour-by-hour runs, year-by-year
  // money, panel groups.
  //   columns: [{key, label, unit}]   rows: [{...}]
  function table(label, unit, columns, rows, totalCol, o) {
    const w = opt(o).weightCol;
    const total = rows.reduce((t, r) => t + (r[totalCol] || 0) * (w ? r[w] : 1), 0);
    const n = node("table", label, unit, total, meta(o));
    n.columns = columns; n.rows = rows; n.totalCol = totalCol; n.weightCol = w || null;
    // rowCheck(row) -> true when the row's own derived columns (e.g. kWh x
    // that hour's price) are right; verify() runs it on every row
    n.rowCheck = opt(o).rowCheck || null;
    n.children = opt(o).children || [];
    return n;
  }

  // A value the caller worked out by a step that is not plain arithmetic.
  // `formula` says in words what was done, `check` re-derives the value from
  // the children so verify() can test it. No check = reported as unchecked.
  function given(label, value, unit, kids, o) {
    const n = node("given", label, unit, value, meta(o));
    n.children = kids || [];
    n.formula = opt(o).formula || "";
    n.check = opt(o).check || null;
    return n;
  }

  // ---- checking -----------------------------------------------------------
  const close = (a, b) => (a === b) ||
    (Number.isFinite(a) && Number.isFinite(b) &&
     Math.abs(a - b) <= TOL * Math.max(1, Math.abs(a), Math.abs(b)));

  // Every step that does not add up, anywhere in the tree.
  function verify(root) {
    const problems = [];
    const unchecked = [];
    const seen = new Set();
    const walk = n => {
      if (!n || seen.has(n)) return;
      seen.add(n);
      n.children.forEach(walk);
      let again = null;
      if (n.kind === "op") {
        again = n.op === "clamp"
          ? Math.min(n.hi, Math.max(n.lo, n.children[0].value))
          : OPS[n.op].f(n.children.map(c => c.value));
      } else if (n.kind === "table") {
        again = n.rows.reduce((t, r) => t + (r[n.totalCol] || 0) * (n.weightCol ? r[n.weightCol] : 1), 0);
        if (n.rowCheck) {
          n.rows.forEach((r, i) => {
            if (!n.rowCheck(r)) problems.push({ node: n, expected: `row ${i + 1} to add up`, got: JSON.stringify(r) });
          });
        }
      } else if (n.kind === "given") {
        if (n.check) again = n.check(n.children.map(c => c.value), n);
        else unchecked.push(n);
      }
      if (again !== null && !close(again, n.value)) {
        problems.push({ node: n, expected: again, got: n.value });
      }
      if (!Number.isFinite(n.value) && n.value !== null) problems.push({ node: n, expected: NaN, got: n.value });
    };
    walk(root);
    return { ok: problems.length === 0, problems, unchecked };
  }

  // Two numbers the page shows that must be the same figure (the annual kWh
  // and the year under its curve; the bill tool's generation and the
  // panel's). Returns a flag string, or null when they agree.
  function same(a, b, what, relTol) {
    const t = relTol == null ? 1e-6 : relTol;
    const va = typeof a === "number" ? a : a.value, vb = typeof b === "number" ? b : b.value;
    if (Math.abs(va - vb) <= t * Math.max(1, Math.abs(va), Math.abs(vb))) return null;
    return `${what}: ${va} and ${vb} should match.`;
  }

  // ---- formatting ---------------------------------------------------------
  const UNIT_DP = { "kWh/yr": 0, "kWh/m²/yr": 0, "kWh": 0, "m²": 1, "kW": 2, "kWp": 2,
                    "$": 0, "$/yr": 0, "$/month": 0, "$/day": 2, "c/kWh": 1, "kWh/day": 1,
                    "yrs": 1, "panels": 0, "people": 0, "W": 0, "km/yr": 0, "kWh/km": 3,
                    "days": 0, "°C·days": 0, "kWh/°C·day": 4, "kWh/m²": 2, "h": 0 };
  function fmt(v, unit, dp) {
    if (v === null || v === undefined) return "–";
    if (!Number.isFinite(v)) return String(v);
    if (unit === "share") return (v * 100).toLocaleString(undefined, { maximumFractionDigits: dp == null ? 1 : dp }) + "%";
    if (unit === "×") return "× " + v.toLocaleString(undefined, { maximumFractionDigits: dp == null ? 3 : dp });
    const d = dp != null ? dp : (UNIT_DP[unit] != null ? UNIT_DP[unit]
      : Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 10 ? 1 : 2);
    const s = Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
    const sign = v < 0 ? "−" : "";
    if (unit && unit[0] === "$") return sign + "$" + s + (unit.length > 1 ? unit.slice(1) : "");
    return sign + s + (unit ? " " + unit : "");
  }
  const show = n => fmt(n.value, n.unit, n.dp);

  // The step written out with its own numbers, e.g. "1,312 × 1.08 = 1,417".
  function formula(n) {
    if (n.kind === "input") return "";
    if (n.kind === "table") {
      return `the ${n.columns.find(c => c.key === n.totalCol).label.toLowerCase()} column` +
             (n.weightCol ? ` × ${n.columns.find(c => c.key === n.weightCol).label.toLowerCase()}` : "") +
             ` added up over ${n.rows.length} rows = ${show(n)}`;
    }
    if (n.kind === "given") return n.formula ? `${n.formula} = ${show(n)}` : show(n);
    // a multiplier inside a product reads "× 0.58", not "× × 0.58"
    const k = n.children.map(c => (c.unit === "×" && (n.op === "product" || n.op === "quotient"))
      ? fmt(c.value, "", c.dp != null ? c.dp : 3) : show(c));
    switch (n.op) {
      case "product": return `${k.join(" × ")} = ${show(n)}`;
      case "sum": return `${k.join(" + ")} = ${show(n)}`.replace(/\+ −/g, "− ");
      case "diff": return `${k[0]} − ${k[1]} = ${show(n)}`;
      case "quotient": return `${k[0]} ÷ ${k[1]} = ${show(n)}`;
      case "min": return `the smaller of ${k.join(" and ")} = ${show(n)}`;
      case "max": return `the larger of ${k.join(" and ")} = ${show(n)}`;
      case "complement": return `100% − ${k[0]} = ${show(n)}`;
      case "clamp": return `${k[0]}, kept between ${fmt(n.lo, n.unit)} and ${fmt(n.hi, n.unit)} = ${show(n)}`;
    }
    return show(n);
  }

  // Plain-text breakdown, for tests, the console and bug reports.
  function text(root, depth) {
    const lines = [];
    const walk = (n, d) => {
      if (depth != null && d > depth) return;
      const f = formula(n);
      lines.push(`${"  ".repeat(d)}${n.label}: ${show(n)}${f ? "   [" + f + "]" : ""}` +
                 (n.source ? `   (source: ${n.source})` : ""));
      n.flags.forEach(fl => lines.push(`${"  ".repeat(d)}  ! ${fl}`));
      n.children.forEach(c => walk(c, d + 1));
    };
    walk(root, 0);
    return lines.join("\n");
  }

  return { input, product, sum, diff, quotient, minOf, maxOf, complement, clamp, table, given,
           verify, same, fmt, show, formula, text, UNIT_DP };
})();

if (typeof module !== "undefined" && module.exports) module.exports = { Wk };

// ---------------------------------------------------------------------------
// The panel that shows a breakdown. Everything below touches the page.
// ---------------------------------------------------------------------------

const WK_CSS = `
.wk-editwrap { display: inline-flex; align-items: baseline; gap: 4px; }
.wk-edit { width: 6.5em; background: rgba(255,255,255,.08); border: 1px solid rgba(255,210,74,.55); color: #fff;
  border-radius: 6px; padding: 3px 6px; font: inherit; text-align: right; font-variant-numeric: tabular-nums; }
.wk-edit:focus { outline: 2px solid #ffd24a; outline-offset: 1px; }
.wk-editwrap.big .wk-edit { font-size: 22px; width: 5.5em; padding: 4px 8px; }
.wk-editwrap .u { color: #aaa; font-size: .9em; }
.wk-hint { font-size: 12px; color: #aab; margin: 4px 0 8px; }

.wk-num { cursor: pointer; text-decoration: underline dotted rgba(255,210,74,.65); text-underline-offset: 3px; }
.wk-num:hover, .wk-num:focus-visible { text-decoration-color: var(--accent, #ffd24a); outline: none; }
.wk-num:focus-visible { box-shadow: 0 0 0 2px rgba(255,210,74,.6); border-radius: 3px; }
.wk-num.wk-bad { text-decoration-color: #ff6b6b; }
.wk-sheet { position: fixed; z-index: 4000; right: 12px; top: 12px; bottom: 12px; width: min(440px, calc(100vw - 24px));
  background: #1c2028; color: #eef1f5; border: 1px solid rgba(255,255,255,.12); border-radius: 12px;
  box-shadow: 0 18px 50px rgba(0,0,0,.5); display: flex; flex-direction: column; font: 13px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
.wk-top { display:flex; align-items:center; gap: 8px; padding: 10px 12px 8px; border-bottom: 1px solid rgba(255,255,255,.08); }
.wk-top button { background: rgba(255,255,255,.07); color: inherit; border: 1px solid rgba(255,255,255,.12); border-radius: 7px; padding: 4px 9px; cursor: pointer; font: inherit; }
.wk-top button:focus-visible { outline: 2px solid #ffd24a; }
.wk-crumbs { flex: 1; font-size: 11.5px; color: #9aa3b1; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.wk-body { overflow: auto; padding: 12px 14px 16px; }
.wk-title { font-size: 12px; color: #9aa3b1; text-transform: uppercase; letter-spacing: .06em; }
.wk-value { font-size: 26px; font-weight: 750; color: #ffd24a; font-variant-numeric: tabular-nums; margin: 2px 0 4px; }
.wk-formula { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; background: rgba(255,255,255,.05);
  border-radius: 7px; padding: 7px 9px; margin: 6px 0 10px; overflow-x: auto; white-space: nowrap; }
.wk-check { font-size: 12px; margin: 0 0 10px; }
.wk-check.ok { color: #7fd18e; } .wk-check.bad { color: #ff8a8a; } .wk-check.part { color: #e8c76a; }
.wk-flag { background: rgba(255,120,80,.12); border: 1px solid rgba(255,120,80,.35); color: #ffc4b0; border-radius: 7px; padding: 6px 8px; margin: 6px 0; font-size: 12px; }
.wk-src, .wk-note { font-size: 12px; color: #aab2bf; margin: 4px 0 10px; }
.wk-src b { color: #d6dbe3; font-weight: 600; }
.wk-steps { list-style: none; margin: 4px 0 0; padding: 0; display: grid; gap: 6px; }
.wk-step { display: grid; grid-template-columns: 1fr auto; gap: 2px 10px; align-items: baseline; padding: 8px 10px; border-radius: 8px; background: rgba(255,255,255,.04); }
.wk-step.go { cursor: pointer; } .wk-step.go:hover, .wk-step.go:focus-visible { background: rgba(255,210,74,.10); outline: none; }
.wk-step .l { font-size: 12.5px; } .wk-step .v { font-variant-numeric: tabular-nums; font-weight: 650; text-align: right; }
.wk-step .s { grid-column: 1 / -1; font-size: 11px; color: #8e97a5; }
.wk-step.go .v::after { content: " ›"; color: #ffd24a; }
.wk-tab { overflow-x: auto; margin: 6px 0 10px; }
.wk-tab table { border-collapse: collapse; font-size: 11.5px; font-variant-numeric: tabular-nums; min-width: 100%; }
.wk-tab th, .wk-tab td { padding: 3px 7px; text-align: right; border-bottom: 1px solid rgba(255,255,255,.06); white-space: nowrap; }
.wk-tab th:first-child, .wk-tab td:first-child { text-align: left; }
.wk-tab th { color: #9aa3b1; font-weight: 600; position: sticky; top: 0; background: #1c2028; }
.wk-tab tr.mark td { color: #ffd24a; }
.wk-copy { margin-top: 14px; font-size: 11.5px; color: #8e97a5; }
@media (max-width: 520px) { .wk-sheet { left: 8px; right: 8px; top: auto; height: 78vh; width: auto; } }
@media (prefers-reduced-motion: no-preference) { .wk-sheet { animation: wk-in .14s ease-out; } }
@keyframes wk-in { from { transform: translateY(6px); opacity: .6; } to { transform: none; opacity: 1; } }
`;

const WkView = (() => {
  let sheet = null, stack = [], lastFocus = null;
  // CHANGE AN ASSUMPTION WHERE YOU SEE IT (Josh, 20 Oct): a number tagged
  // `edit` shows as a field; a change goes to the page's handler, which
  // updates the user's assumptions everywhere, and the sheet is re-worked
  // from the fresh calculation along the same path.
  let onEdit = null, rootGet = null, path = [];
  function setEditHandler(fn) { onEdit = fn; }
  const canEdit = c => !!(onEdit && c && c.edit && c.edit.key);
  const shown = c => {
    const sc = c.edit.scale || 1, v = c.value * sc;
    const dp = c.edit.dp != null ? c.edit.dp : (Math.abs(v) >= 100 ? 0 : 2);
    return +v.toFixed(dp);
  };

  function css() {
    if (typeof document === "undefined" || document.getElementById("wk-css")) return;
    const st = document.createElement("style");
    st.id = "wk-css"; st.textContent = WK_CSS;
    document.head.appendChild(st);
  }
  const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function close() {
    if (sheet) sheet.remove();
    sheet = null; stack = [];
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  function checkLine(n) {
    const v = Wk.verify(n);
    if (!v.ok) {
      const p = v.problems[0];
      console.error("[workings] does not add up:", p.node.label, "expected", p.expected, "got", p.got, p.node);
      return `<div class="wk-check bad">⚠ Doesn't add up: "${esc(p.node.label)}" should be ${esc(Wk.fmt(p.expected, p.node.unit))} but shows ${esc(Wk.show(p.node))}. Please report this.</div>`;
    }
    // Nothing is said when the sums add up: that they agree with each other
    // does not make the inputs or the model right, and a green tick read as
    // if it did (Josh, 18 Oct). Only a sum that does not add up is shown.
    return "";
  }

  function tableHtml(n) {
    const cols = n.columns;
    const head = "<tr>" + cols.map(c => `<th>${esc(c.label)}${c.unit && c.unit !== "share" ? `<br><span style="font-weight:400">${esc(c.unit)}</span>` : ""}</th>`).join("") + "</tr>";
    const body = n.rows.map(r => `<tr class="${r._mark ? "mark" : ""}">` + cols.map(c => {
      const v = r[c.key];
      // the header carries the unit; cells are numbers (money keeps its $, shares their %)
      const bare = c.unit && c.unit[0] !== "$" && c.unit !== "share" && c.unit !== "×" ? "" : c.unit;
      return `<td>${typeof v === "number" ? esc(Wk.fmt(v, bare, c.dp != null ? c.dp : (Wk.UNIT_DP[c.unit] != null ? Wk.UNIT_DP[c.unit] : undefined))) : esc(v)}</td>`;
    }).join("") + "</tr>").join("");
    return `<div class="wk-tab"><table>${head}${body}</table></div>`;
  }

  function field(c, big) {
    return `<span class="wk-editwrap${big ? " big" : ""}"><input class="wk-edit" type="number" inputmode="decimal" ` +
      `step="${esc(c.edit.step || "any")}" data-k="${esc(c.edit.key)}" value="${shown(c)}" aria-label="${esc(c.label)}">` +
      `<span class="u">${esc(c.edit.unit || c.unit || "")}</span></span>`;
  }

  function refresh(focusKey) {
    if (!rootGet || !sheet) return;
    let n = rootGet();
    if (!n) return;
    const st = [n];
    for (const i of path) { n = n.children && n.children[i]; if (!n) break; st.push(n); }
    stack = st.length === path.length + 1 ? st : [st[0]];
    if (stack.length === 1) path = [];
    draw();
    const f = focusKey && sheet.querySelector(`.wk-edit[data-k="${focusKey}"]`);
    if (f) f.focus();
  }

  function draw() {
    const n = stack[stack.length - 1];
    const crumbs = stack.map(s => esc(s.label)).join(" › ");
    const f = Wk.formula(n);
    sheet.innerHTML =
      `<div class="wk-top">` +
        (stack.length > 1 ? `<button type="button" data-act="back" aria-label="Back">‹ Back</button>` : "") +
        `<div class="wk-crumbs">${crumbs}</div>` +
        `<button type="button" data-act="close" aria-label="Close workings">✕</button>` +
      `</div>` +
      `<div class="wk-body">` +
        `<div class="wk-title">${esc(n.label)}</div>` +
        `<div class="wk-value">${canEdit(n) ? field(n, true) : esc(Wk.show(n))}</div>` +
        (canEdit(n) || n.children.some(canEdit) ? `<div class="wk-hint">Change a number in a box and every figure updates. Your change is saved for next time.</div>` : "") +
        (f ? `<div class="wk-formula">${esc(f)}</div>` : "") +
        (stack.length === 1 ? checkLine(n) : "") +
        n.flags.map(fl => `<div class="wk-flag">${esc(fl)}</div>`).join("") +
        (n.source ? `<div class="wk-src"><b>Source:</b> ${esc(n.source)}</div>` : "") +
        (n.note ? `<div class="wk-note">${esc(n.note)}</div>` : "") +
        (n.kind === "table" ? tableHtml(n) : "") +
        (n.children.length ? `<ul class="wk-steps">` + n.children.map((c, i) => {
          const go = c.children.length || c.kind === "table" || c.source || c.note || c.flags.length;
          return `<li class="wk-step${go ? " go" : ""}" ${go ? `tabindex="0" role="button" data-i="${i}"` : ""}>` +
                 `<span class="l">${esc(c.label)}</span><span class="v">${canEdit(c) ? field(c) : esc(Wk.show(c))}</span>` +
                 (c.source ? `<span class="s">Source: ${esc(c.source)}</span>` : "") +
                 (c.flags.length ? `<span class="s" style="color:#ffb38f">⚠ ${esc(c.flags[0])}</span>` : "") +
                 `</li>`;
        }).join("") + `</ul>` : "") +
        `<div class="wk-copy">Numbers are rounded for display. The sums use full figures.</div>` +
      `</div>`;
    sheet.querySelectorAll("[data-act=close]").forEach(b => b.addEventListener("click", close));
    sheet.querySelectorAll("[data-act=back]").forEach(b => b.addEventListener("click", () => { stack.pop(); path.pop(); draw(); }));
    sheet.querySelectorAll(".wk-edit").forEach(inp => {
      ["click", "keydown"].forEach(ev => inp.addEventListener(ev, e => e.stopPropagation()));
      inp.addEventListener("change", () => {
        const v = parseFloat(inp.value);
        if (!Number.isFinite(v) || !onEdit) return;
        onEdit(inp.dataset.k, v);
        setTimeout(() => refresh(inp.dataset.k), 30);
      });
    });
    sheet.querySelectorAll(".wk-step.go").forEach(li => {
      const go = () => { stack.push(n.children[+li.dataset.i]); path.push(+li.dataset.i); draw(); const b = sheet.querySelector("[data-act=back]"); if (b) b.focus(); };
      li.addEventListener("click", go);
      li.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    });
  }

  function open(root, getter) {
    if (!root) return;
    rootGet = getter || null;
    path = [];
    css();
    lastFocus = document.activeElement;
    if (!sheet) {
      sheet = document.createElement("div");
      sheet.className = "wk-sheet";
      sheet.setAttribute("role", "dialog");
      sheet.setAttribute("aria-label", "How this number was worked out");
      sheet.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
      document.body.appendChild(sheet);
    }
    stack = [root];
    draw();
    const c = sheet.querySelector("[data-act=close]");
    if (c) c.focus();
  }

  // Make a shown number open its workings. getNode is called on click so the
  // breakdown is always the current calculation, never a stale copy.
  function bind(el, getNode) {
    if (!el) return;
    css();
    el.classList.add("wk-num");
    el.setAttribute("role", "button");
    el.setAttribute("tabindex", "0");
    el.setAttribute("title", "Show how this was worked out");
    el.__wkGet = getNode;
    if (el.__wkBound) return;
    el.__wkBound = true;
    const go = e => { e.preventDefault(); e.stopPropagation(); open(el.__wkGet(), el.__wkGet); };
    el.addEventListener("click", go);
    el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") go(e); });
    // a number whose workings do not add up is marked where it is shown
    try { const n = getNode(); if (n && !Wk.verify(n).ok) el.classList.add("wk-bad"); } catch (e) { /* drawn later */ }
  }

  return { open, close, bind, setEditHandler };
})();

if (typeof window !== "undefined") { window.Wk = Wk; window.WkView = WkView; }
