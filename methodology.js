// "How this map is made": the methodology explorer behind the bottom-left
// button.
//
// Everything it shows comes from methodology.json, which src/methodology.py
// writes from the code itself: config values, the build's stage plan
// (src/build_plan.py), LINZ's own metadata for every layer the build reads
// (data/source_metadata.json), and the open roofs in data/roof_cases.json.
// tests/test_methodology.py fails when the file differs from what the code
// would write now, when a build stage is missing, when a named function,
// file, switch, test or label no longer exists. This file only draws; it
// holds no facts. The one thing it fetches live is the count of open GitHub
// issues for each label, which changes without a build.
//
// Three views behind one button, each read from a source that cannot drift
// from the code:
//   How it's made  methodology.json, written from the code (tests/test_methodology.py)
//   Status         the served build itself, read at open: build_summary.json and
//                  assumptions.json at SITE.dataBase, site-config.js, and
//                  site_version.json (the commit, written by tools/build_static_site.py);
//                  measured accuracy from roadmap.json
//   Roadmap        roadmap.json; tests/test_roadmap.py fails when a file, switch,
//                  methodology part or evidence quote it names is gone
// How it's made is one surface: steps along the top, the step's parts down
// the left, and for the chosen part five short tabs (how, why, try instead,
// where it fails, change it). The place is kept in the address
// (#how=<step>.<item>.<tab>, #status, #roadmap) so a link opens the same view.
(function () {
  "use strict";
  let data = null, box = null;
  let at = { s: 0, i: 0, t: "how" };
  let view = "how";
  let extra = null;         // Status and Roadmap sources, loaded on first use
  const issueCounts = {};   // label -> number | null (in flight) | undefined
  const VIEWS = [["how", "How it's made"], ["status", "Status"], ["roadmap", "Roadmap"]];

  const TABS = [
    ["how", "How it works"], ["why", "Why this way"], ["try", "Try instead"],
    ["fails", "Where it fails"], ["change", "Change it"],
  ];
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const repoUrl = p => `https://github.com/${data.repo}/${p}`;
  const fileUrl = path => repoUrl(`blob/main/${path}`);
  const place = r => { const t = r.replace(/_/g, " "); return t.charAt(0).toUpperCase() + t.slice(1); };
  const STATUS = { open: "open", needs_verdict: "waiting for a check", regressed: "broke again" };

  function codeLink(ref) {
    const [path, sym] = ref.split(/::|~~/);
    return `<a href="${fileUrl(esc(path))}" target="_blank" rel="noopener"><code>${esc(path)}</code></a>` +
      (sym ? ` <code class="mth-sym">${esc(sym)}</code>` : "");
  }
  const runCmd = t => (t.endsWith(".mjs") || t.endsWith(".js") ? "node " : "python ") + t;

  // ---- pictures -------------------------------------------------------------
  function thresholdHtml(sh, k) {
    const span = sh.max - sh.min;
    const pct = v => ((v - sh.min) / span * 100).toFixed(2);
    const edges = [sh.min, ...sh.cuts.map(c => c.at), sh.max];
    const tone = i => i === 0 ? "bad" : (i === sh.outcomes.length - 1 ? "good" : "mid");
    const bands = sh.outcomes.map((o, i) =>
      `<span class="mth-band ${tone(i)}" style="left:${pct(edges[i])}%;width:${(pct(edges[i + 1]) - pct(edges[i])).toFixed(2)}%" title="${esc(o)}"></span>`).join("");
    const marks = sh.cuts.map(c =>
      `<span class="mth-cut" style="left:${pct(c.at)}%" title="${esc(c.source)}"><b>${esc(fmt(c.at, sh.step))}</b></span>`).join("");
    const start = (sh.cuts[0].at + (sh.cuts[1] ? sh.cuts[1].at : sh.max)) / 2;
    const v0 = Math.round(start / sh.step) * sh.step;
    return `<figure class="mth-fig mth-thr" data-k="${k}">` +
      `<figcaption>Try it: move the slider</figcaption>` +
      `<div class="mth-track">${bands}${marks}</div>` +
      `<input type="range" min="${sh.min}" max="${sh.max}" step="${sh.step}" value="${v0}" aria-label="${esc(sh.ask)}">` +
      `<p class="mth-out" aria-live="polite"></p></figure>`;
  }
  function fmt(v, step) {
    const dp = step < 1 ? Math.max(0, -Math.floor(Math.log10(step))) : 0;
    return Number(v).toFixed(dp);
  }
  function wireThreshold(fig, sh) {
    const inp = fig.querySelector("input"), out = fig.querySelector(".mth-out");
    const upd = () => {
      const v = +inp.value;
      let i = 0;
      while (i < sh.cuts.length && v >= sh.cuts[i].at) i++;
      const tone = i === 0 ? "bad" : (i === sh.outcomes.length - 1 ? "good" : "mid");
      out.innerHTML = `${esc(sh.ask)} <b>${esc(fmt(v, sh.step))}${sh.unit ? (sh.unit[0] === "%" ? "" : " ") + esc(sh.unit) : ""}</b> ` +
        `<span class="mth-verdict ${tone}">${esc(sh.outcomes[i])}</span>`;
    };
    inp.addEventListener("input", upd);
    upd();
  }
  function lossesHtml(sh) {
    const lost = 100 - sh.kept.pct;
    const scale = lost / sh.parts.reduce((a, p) => a + p.pct, 0);   // losses multiply; share the gap by size
    const segs = [{ cls: "kept", pct: sh.kept.pct, label: "reaches the building", src: sh.kept.source },
      ...sh.parts.map((p, n) => ({ cls: "lost l" + n, pct: +(p.pct * scale).toFixed(1), label: "lost: " + p.label.toLowerCase(), src: p.source }))];
    return `<figure class="mth-fig"><figcaption>Of every 100 kWh the panel makes in sunlight</figcaption>` +
      `<div class="mth-bar">${segs.map(s => `<span class="mth-seg ${s.cls}" style="flex-basis:${s.pct}%" title="${esc(s.src)}"></span>`).join("")}</div>` +
      `<ul class="mth-legend">${segs.map(s => `<li><i class="mth-seg ${s.cls}"></i><b>${esc(String(s.pct))}</b> ${esc(s.label)}</li>`).join("")}</ul>` +
      `</figure>`;
  }

  // ---- tabs -----------------------------------------------------------------
  function tabHow(it, k) {
    const facts = (it.facts || []).map(f =>
      `<li title="From ${esc(f.source)}"><span>${esc(f.label)}</span><b>${esc(f.value)}</b></li>`).join("");
    const show = it.show ? (it.show.kind === "threshold" ? thresholdHtml(it.show, k) : lossesHtml(it.show)) : "";
    return `<p class="mth-lead">${esc(it.text)}</p>` +
      (facts ? `<ul class="mth-chips" aria-label="Figures from the code">${facts}</ul>` : "") + show;
  }
  function tabWhy(it) {
    return `<p class="mth-lead">${esc(it.why)}</p>`;
  }
  function tabTry(it) {
    const title = encodeURIComponent(`Idea: ${it.title}`);
    return `<ul class="mth-list">${it.try.map(t => `<li>${esc(t)}</li>`).join("")}</ul>` +
      `<p class="mth-hint">Want to try one, or have another? ` +
      `<a href="${repoUrl(`issues/new?title=${title}`)}" target="_blank" rel="noopener">Open an issue</a> ` +
      `and say how you would judge it (see <a href="#" data-tab="change">Change it</a>).</p>`;
  }
  function labelChip(l) {
    const n = issueCounts[l];
    const count = n === undefined || n === null ? "" : ` <b>${n}</b>`;
    return `<a class="mth-label" data-label="${esc(l)}" href="${repoUrl(`issues?q=is%3Aopen+label%3A${encodeURIComponent(l)}`)}" ` +
      `target="_blank" rel="noopener">${esc(l)}${count}</a>`;
  }
  function tabFails(it) {
    const f = it.fails;
    const cases = f.cases.map(c =>
      `<li class="mth-case"><div><span class="mth-defect">${esc(c.defect)}</span>` +
      `<span class="mth-meta">${esc(place(c.region))} · <span class="mth-st ${esc(c.status)}">${esc(STATUS[c.status] || c.status)}</span></span></div>` +
      (window.solarOpenBuilding ? `<button type="button" class="mth-go" data-b="${c.id}">Show on map</button>` : "") + `</li>`).join("");
    const more = f.cases_total > f.cases.length
      ? `<p class="mth-hint">${f.cases_total - f.cases.length} more in <a href="${fileUrl("data/roof_cases.json")}" target="_blank" rel="noopener"><code>data/roof_cases.json</code></a>, the register of every reported roof.</p>` : "";
    const known = (f.known || []).map(k => `<li>${esc(k)}</li>`).join("");
    const labels = (f.labels || []).map(labelChip).join("");
    return (cases ? `<h5>Reported roofs it gets wrong <span class="mth-dim">(${f.cases_total})</span></h5><ul class="mth-cases">${cases}</ul>${more}` : "") +
      (known ? `<h5>Known limits</h5><ul class="mth-list">${known}</ul>` : "") +
      (labels ? `<h5>Open issues on GitHub</h5><p class="mth-labels">${labels}</p>` : "") +
      (!cases && !known && !labels ? `<p class="mth-dim">Nothing reported yet.</p>` : "");
  }
  function tabChange(it) {
    const c = it.change, j = data.judges[c.judge];
    const files = it.code.map(r => `<li>${codeLink(r)}</li>`).join("");
    const sw = c.switches.map(s =>
      `<li><code>${esc(s.name)}</code> <span class="mth-dim">read in</span> <a href="${fileUrl(esc(s.file))}" target="_blank" rel="noopener"><code>${esc(s.file)}</code></a></li>`).join("");
    const tests = c.tests.map(t => `<li><a href="${fileUrl(esc(t))}" target="_blank" rel="noopener"><code>${esc(runCmd(t))}</code></a></li>`).join("");
    const stages = it.stages.length ? `<p class="mth-dim">Build step${it.stages.length > 1 ? "s" : ""}: ${it.stages.map(s => `<code>${esc(s)}</code>`).join(", ")}</p>` : "";
    const judge = j ? `<div class="mth-judge"><h5>How a change is judged: ${esc(j.title)}</h5>` +
      `<ol>${j.steps.map(s => `<li>${esc(s)}</li>`).join("")}</ol>` +
      `<pre>${j.run.map(esc).join("\n")}</pre></div>` : "";
    return `<div class="mth-cols">` +
      `<div><h5>1. Edit</h5><ul class="mth-code">${files}</ul>${stages}</div>` +
      (sw ? `<div><h5>2. Switch it off or tune it</h5><ul class="mth-code">${sw}</ul></div>` : "") +
      `<div><h5>${sw ? "3" : "2"}. Prove it still works</h5><ul class="mth-code">${tests}</ul></div>` +
      `</div>` + judge;
  }
  const TAB_HTML = { how: tabHow, why: tabWhy, try: tabTry, fails: tabFails, change: tabChange };

  // ---- the three views -------------------------------------------------------
  function render(focusSel) {
    box.querySelectorAll(".mth-view").forEach(b => {
      const on = b.dataset.view === view;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", on);
      b.tabIndex = on ? 0 : -1;
    });
    if (view === "how") return renderHow(focusSel);
    const body = box.querySelector(".mth-body");
    if (!extra) {
      body.innerHTML = `<p class="mth-dim">Loading&hellip;</p>`;
      loadExtra().then(() => render(focusSel));
      return;
    }
    body.innerHTML = view === "status" ? statusHtml() : roadmapHtml();
    saveHash();
    if (view === "status") loadRepoCount();
    if (focusSel) { const el = box.querySelector(focusSel); if (el) el.focus(); }
  }

  // Every source is fetched as served: nothing here is typed in by hand.
  function loadExtra() {
    const SITE = window.SITE || {};
    const base = new URL(SITE.dataBase || "./", location.href).href;
    const v = encodeURIComponent(SITE.dataVersion || "");
    const get = u => fetch(u, { cache: "no-cache" }).then(r => r.ok ? r.json() : Promise.reject(r.status));
    return Promise.allSettled([
      get("roadmap.json"),
      get(`${base}data/build_summary.json?v=${v}`),
      get(`${base}data/assumptions.json?v=${v}`),
      get("site_version.json"),
    ]).then(([rm, sum, asm, ver]) => {
      extra = { roadmap: rm.value || null, summary: sum.value || null,
                assumptions: asm.value || null, version: ver.value || null,
                site: SITE, base };
    });
  }

  const num = (x, dp = 0) => Number(x).toLocaleString("en-NZ", { maximumFractionDigits: dp, minimumFractionDigits: dp });
  const day = iso => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleDateString("en-NZ", { day: "numeric", month: "short", year: "numeric" }); };
  const daysAgo = iso => Math.floor((Date.now() - new Date(iso).getTime()) / 864e5);
  const srcLink = (file, label) => `<a href="${fileUrl(esc(file))}" target="_blank" rel="noopener"><code>${esc(label || file)}</code></a>`;
  let repoOpen;   // open issues and pull requests, from GitHub's public API

  function tile(label, value, sub, src) {
    return `<div class="mth-tile" title="${esc(src)}"><span class="mth-tl">${esc(label)}</span>` +
      `<b class="mth-tv">${value}</b><span class="mth-ts">${sub}</span></div>`;
  }

  function statusHtml() {
    const { summary: sm, assumptions: a, version: ver, site, roadmap: rm, base } = extra;
    const t = sm && sm.totals;
    const commit = ver && ver.commit
      ? `<a href="${repoUrl(`commit/${esc(ver.commit)}`)}" target="_blank" rel="noopener"><code>${esc(ver.commit.slice(0, 7))}</code></a>`
      : `<span class="mth-dim">this copy</span>`;
    const tiles = [
      tile("Map data", `v${esc(site.dataVersion || "?")}`, sm ? `built ${esc(day(sm.built_at))}` : "build summary not found",
           `site-config.js dataVersion; built_at in ${base}data/build_summary.json`),
      t ? tile("Buildings", num(t.n), `${num(t.n_est)} with an estimate`, "build_summary.json totals.n, totals.n_est") : "",
      t ? tile("Panels", num(t.panel_count), `${num(t.kwp / 1000, 1)} MW`, "build_summary.json totals.panel_count, totals.kwp") : "",
      t ? tile("A year", `${num(t.kwh / 1e6, 1)} GWh`, `${sm.regions.length} areas`, "build_summary.json totals.kwh, regions") : "",
      tile("Code", commit, ver && ver.committed ? `committed ${esc(day(ver.committed))}` : "not built by the site build",
           "site_version.json, written by tools/build_static_site.py"),
    ].join("");
    const types = sm && sm.by_type ? Object.entries(sm.by_type).map(([k, x]) =>
      `<tr><td>${esc(k[0].toUpperCase() + k.slice(1))}</td><td class="mth-num">${num(x.n)}</td><td class="mth-num">${num(x.panel_count)}</td><td class="mth-num">${num(x.kwh / 1e6, 1)}</td></tr>`).join("") : "";
    const asm = a ? [["Panel", `${a.panel_rated_power_w} W, ${a.panel_efficiency_pct}% efficient`],
                     ["Inverter", `${a.inverter_efficiency_pct}% efficient`],
                     ["Other losses", `${a.system_derate_pct}%`]]
      .map(([k, v]) => `<li><span>${esc(k)}</span><b>${esc(v)}</b></li>`).join("") : "";
    const measures = rm ? rm.measures.map(m =>
      `<tr><td>${esc(m.label)}<div class="mth-dim">${esc(m.detail || "")}</div></td><td class="mth-v">${esc(m.value)}</td>` +
      `<td class="mth-dim">${esc(m.measured)}</td><td>${srcLink(m.file)}</td></tr>`).join("") : "";
    const cases = data.cases_total;
    const open = repoOpen === undefined || repoOpen === null ? "" : `<b>${num(repoOpen)}</b> `;
    return `<p class="mth-intro">What is live now, read from the published build and the code as you open this.</p>` +
      `<div class="mth-tiles">${tiles}</div>` +
      `<div class="mth-cols2">` +
      (types ? `<div><h5>By building type</h5><table class="mth-facts"><thead><tr><th>Type</th><th>Buildings</th><th>Panels</th><th>GWh a year</th></tr></thead><tbody>${types}</tbody></table></div>` : "") +
      (asm ? `<div><h5>Assumptions in this build</h5><ul class="mth-chips">${asm}</ul>` +
        `<p class="mth-hint">From <code>assumptions.json</code> in the build. Change them under Settings; the map recalculates.</p></div>` : "") +
      `</div>` +
      (measures ? `<h5>How accurate it is</h5><div class="mth-scroll"><table class="mth-facts mth-measures"><thead><tr><th>Measure</th><th>Result</th><th>Measured</th><th>Source</th></tr></thead><tbody>${measures}</tbody></table></div>` +
        `<p class="mth-hint">Each result is quoted from the file shown; <code>tests/test_roadmap.py</code> fails if the file no longer says it.</p>` : "") +
      `<h5>Known problems</h5><p class="mth-labels">` +
      `<a class="mth-label" href="${fileUrl("data/roof_cases.json")}" target="_blank" rel="noopener">reported roofs <b>${num(cases)}</b></a> ` +
      `<a class="mth-label mth-repo" href="${repoUrl("issues")}" target="_blank" rel="noopener">${open}open issues and pull requests</a> ` +
      `<a class="mth-label" href="#roadmap" data-view="roadmap">what is planned</a></p>`;
  }

  function loadRepoCount() {
    if (repoOpen !== undefined) return;
    repoOpen = null;
    fetch(`https://api.github.com/repos/${data.repo}`, { headers: { Accept: "application/vnd.github+json" } })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(j => {
        repoOpen = j.open_issues_count;
        if (view === "status" && box && !box.hidden) render();
      })
      .catch(() => { /* the link still opens the list */ });
  }

  const EFFORT = { S: "takes days", M: "takes weeks", L: "takes months" };
  const GROUPS = [["underway", "Underway"], ["next", "Next"], ["later", "Later"]];
  let rmArea = "";

  function howPart(title) {
    for (let si = 0; si < data.steps.length; si++) {
      const ii = data.steps[si].items.findIndex(x => x.title === title);
      if (ii >= 0) return { s: si, i: ii };
    }
    return null;
  }

  function roadmapHtml() {
    const rm = extra.roadmap;
    if (!rm) return `<p>Could not load roadmap.json.</p>`;
    const areas = [...new Set(rm.items.map(x => x.area))];
    const age = daysAgo(rm.reviewed);
    const filt = `<div class="mth-filter" role="group" aria-label="Show one area">` +
      [["", "All"], ...areas.map(a => [a, a])].map(([k, l]) =>
        `<button type="button" class="mth-fl${k === rmArea ? " on" : ""}" data-area="${esc(k)}" aria-pressed="${k === rmArea}">${esc(l)}</button>`).join("") + `</div>`;
    let n = 0;
    const groups = GROUPS.map(([st, label]) => {
      const items = rm.items.filter(x => x.status === st && (!rmArea || x.area === rmArea));
      if (!items.length) return "";
      return `<section class="mth-rg"><h4>${label} <span class="mth-dim">${items.length}</span></h4><ol class="mth-rl">` +
        items.map(x => {
          n++;
          const ev = x.evidence.map(e => `<li>&ldquo;${esc(e.quote)}&rdquo; ${srcLink(e.file)}</li>`).join("");
          const files = x.refs.files.map(f => srcLink(f)).join(" ");
          const sw = x.refs.switches.map(w => `<code>${esc(w)}</code>`).join(" ");
          const hp = x.refs.how && howPart(x.refs.how);
          return `<li class="mth-ri" id="rm-${esc(x.id)}"><div class="mth-rh"><h5>${esc(x.title)}</h5>` +
            `<span class="mth-chip">${esc(x.area)}</span><span class="mth-chip">${esc(EFFORT[x.effort] || x.effort)}</span></div>` +
            `<p>${esc(x.why)}</p><ul class="mth-list">${x.fix.map(f => `<li>${esc(f)}</li>`).join("")}</ul>` +
            `<details class="mth-rd"><summary>Evidence and code</summary><ul class="mth-list mth-ev">${ev}</ul>` +
            `<p class="mth-code">${files}${sw ? ` <span class="mth-dim">switches</span> ${sw}` : ""}</p></details>` +
            (hp ? `<button type="button" class="mth-pg mth-howlink" data-hs="${hp.s}" data-hi="${hp.i}">How it works now: ${esc(x.refs.how)} &rarr;</button>` : "") +
            `</li>`;
        }).join("") + `</ol></section>`;
    }).join("");
    return `<p class="mth-intro">Known next steps and fixes, most useful first within each group. ` +
      `Reviewed ${esc(day(rm.reviewed))}${age > 30 ? ` <span class="mth-stale">(${age} days ago)</span>` : ""}. ` +
      `Each entry names the code and the measurement behind it; <code>tests/test_roadmap.py</code> fails when those change.</p>` +
      filt + (n ? groups : `<p class="mth-dim">Nothing planned in this area.</p>`) +
      `<p class="mth-foot">Have an idea or found a problem? <a href="${repoUrl("issues/new")}" target="_blank" rel="noopener">Open an issue</a>. ` +
      `<a href="${fileUrl("roadmap.json")}" target="_blank" rel="noopener">Edit the roadmap</a>.</p>`;
  }

  // ---- How it's made: the surface ---------------------------------------------
  function renderHow(focusSel) {
    const steps = data.steps;
    at.s = Math.min(Math.max(at.s, 0), steps.length - 1);
    const s = steps[at.s];
    at.i = Math.min(Math.max(at.i, 0), s.items.length - 1);
    const it = s.items[at.i];
    const k = `${at.s}.${at.i}`;
    const flow = steps.map((st, i) =>
      `<li><button type="button" class="mth-step${i === at.s ? " on" : ""}" data-s="${i}" aria-current="${i === at.s ? "step" : "false"}">` +
      `<span class="mth-n">${i + 1}</span><span class="mth-ic" aria-hidden="true">${st.icon || ""}</span>` +
      `<span class="mth-t">${esc(st.title)}</span></button></li>`).join("");
    const list = s.items.map((x, i) => {
      const n = x.fails.cases_total;
      return `<li><button type="button" class="mth-it${i === at.i ? " on" : ""}" data-i="${i}" aria-current="${i === at.i}">` +
        `${esc(x.title)}${n ? `<span class="mth-badge" title="${n} reported roofs">${n}</span>` : ""}</button></li>`;
    }).join("");
    const tabs = TABS.map(([id, label]) => {
      const n = id === "fails" && it.fails.cases_total ? ` <span class="mth-badge">${it.fails.cases_total}</span>` : "";
      return `<button type="button" role="tab" class="mth-tab${id === at.t ? " on" : ""}" data-tab="${id}" aria-selected="${id === at.t}">${label}${n}</button>`;
    }).join("");
    const prev = at.s > 0 ? steps[at.s - 1] : null, next = at.s < steps.length - 1 ? steps[at.s + 1] : null;
    box.querySelector(".mth-body").innerHTML =
      `<p class="mth-intro">${esc(data.intro)}</p>` +
      `<ol class="mth-flow" aria-label="Steps">${flow}</ol>` +
      `<p class="mth-sum"><b>${at.s + 1}. ${esc(s.title)}</b> ${esc(s.summary)}</p>` +
      `<div class="mth-main">` +
      `<ul class="mth-items" aria-label="Parts of this step">${list}</ul>` +
      `<section class="mth-pane" aria-live="polite"><h3>${esc(it.title)}</h3>` +
      `<div class="mth-tabs" role="tablist">${tabs}</div>` +
      `<div class="mth-tabbody" role="tabpanel">${TAB_HTML[at.t](it, k)}</div></section></div>` +
      `<nav class="mth-pager">` +
      (prev ? `<button type="button" class="mth-pg" data-s="${at.s - 1}">&larr; ${esc(prev.title)}</button>` : "<span></span>") +
      (next ? `<button type="button" class="mth-pg" data-s="${at.s + 1}">${esc(next.title)} &rarr;</button>` : "<span></span>") +
      `</nav>` + sourcesHtml() + `<p class="mth-foot">${esc(data.footer)} ` +
      `<a href="${fileUrl("src/methodology.py")}" target="_blank" rel="noopener">Edit the words</a>.</p>`;
    const fig = box.querySelector(".mth-thr");
    if (fig) wireThreshold(fig, it.show);
    if (at.t === "fails") loadIssueCounts(it.fails.labels || []);
    saveHash();
    if (focusSel) { const el = box.querySelector(focusSel); if (el) el.focus(); }
  }

  function sourcesHtml() {
    const rows = (data.sources || []).map(s =>
      `<tr><td><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a></td>` +
      `<td>${esc(s.used_for)}</td><td>${esc(s.resolution || "")}</td>` +
      `<td>${s.accuracy ? esc(s.accuracy) : '<span class="mth-dim">not stated by LINZ</span>'}</td></tr>`).join("");
    return `<details class="mth-sources"><summary>Every national data layer the build reads (${(data.sources || []).length})</summary>` +
      `<div class="mth-scroll"><table class="mth-facts"><thead><tr><th>Layer</th><th>Used for</th><th>Resolution</th><th>LINZ's stated accuracy</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
  }

  // Open-issue counts by label, fetched once per label per page load from
  // GitHub's public API (no key; 60 requests an hour per address). The chip
  // links to the list either way.
  function loadIssueCounts(labels) {
    labels.filter(l => !(l in issueCounts)).forEach(l => {
      issueCounts[l] = null;
      fetch(`https://api.github.com/repos/${data.repo}/issues?state=open&per_page=100&labels=${encodeURIComponent(l)}`,
        { headers: { Accept: "application/vnd.github+json" } })
        .then(r => r.ok ? r.json() : Promise.reject(r.status))
        .then(list => {
          issueCounts[l] = list.filter(x => !x.pull_request).length;
          const chip = box && box.querySelector(`.mth-label[data-label="${CSS.escape(l)}"]`);
          if (chip) chip.outerHTML = labelChip(l);
        })
        .catch(() => { /* the chip still links to the list */ });
    });
  }

  function saveHash() {
    const h = view === "how" ? `#how=${at.s + 1}.${at.i + 1}.${at.t}` : `#${view}`;
    try { history.replaceState(history.state, "", location.pathname + location.search + h); }
    catch (e) { /* sandboxed */ }
  }
  function readHash() {
    if (location.hash === "#status" || location.hash === "#roadmap") { view = location.hash.slice(1); return true; }
    const m = /^#how(?:=(\d+)(?:\.(\d+))?(?:\.([a-z]+))?)?$/.exec(location.hash);
    if (!m) return false;
    view = "how";
    at = { s: (+m[1] || 1) - 1, i: (+m[2] || 1) - 1, t: TABS.some(t => t[0] === m[3]) ? m[3] : "how" };
    return true;
  }

  function onClick(e) {
    const b = e.target.closest("button, a[data-tab], a[data-view]");
    if (!b || !box.contains(b)) return;
    if (b.dataset.view) { e.preventDefault(); view = b.dataset.view; render(`.mth-view[data-view="${view}"]`); }
    else if (b.dataset.area !== undefined) { rmArea = b.dataset.area; render(`.mth-fl[data-area="${CSS.escape(rmArea)}"]`); }
    else if (b.dataset.hs !== undefined) { view = "how"; at = { s: +b.dataset.hs, i: +b.dataset.hi, t: "how" }; render(".mth-it.on"); }
    else if (b.dataset.s !== undefined) { at = { s: +b.dataset.s, i: 0, t: at.t }; render(".mth-step.on"); }
    else if (b.dataset.i !== undefined) { at.i = +b.dataset.i; render(".mth-it.on"); }
    else if (b.dataset.tab) { e.preventDefault(); at.t = b.dataset.tab; render(`.mth-tab[data-tab="${at.t}"]`); }
    else if (b.dataset.b) {
      const id = +b.dataset.b;
      b.disabled = true; b.textContent = "Finding it…";
      Promise.resolve(window.solarOpenBuilding(id)).then(ok => {
        if (ok) close();
        else { b.textContent = "Not on this map"; }
      });
    }
  }
  function onKey(e) {
    if (box.hidden) return;
    if (e.key === "Escape") { close(); return; }
    if (e.target.matches("input, textarea")) return;
    if (e.target.closest(".mth-views") && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
      const ids = VIEWS.map(v => v[0]);
      view = ids[(ids.indexOf(view) + (e.key === "ArrowRight" ? 1 : ids.length - 1)) % ids.length];
      render(`.mth-view[data-view="${view}"]`); e.preventDefault(); return;
    }
    if (view !== "how") return;
    const tabs = TABS.map(t => t[0]);
    if (e.target.closest(".mth-tabs") && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
      at.t = tabs[(tabs.indexOf(at.t) + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      render(`.mth-tab[data-tab="${at.t}"]`); e.preventDefault();
    } else if (e.target.closest(".mth-items") && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      at.i += e.key === "ArrowDown" ? 1 : -1; render(".mth-it.on"); e.preventDefault();
    } else if (e.target.closest(".mth-flow") && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
      at = { s: at.s + (e.key === "ArrowRight" ? 1 : -1), i: 0, t: at.t }; render(".mth-step.on"); e.preventDefault();
    }
  }

  async function open() {
    if (!box) {
      box = document.createElement("div");
      box.id = "methodology-lightbox";
      box.setAttribute("role", "dialog");
      box.setAttribute("aria-modal", "true");
      box.setAttribute("aria-label", "How this map is made");
      box.innerHTML = `<div class="mth-card"><div class="mth-head"><h2>How this map is made</h2>` +
        `<button type="button" class="mth-close" aria-label="Close">&times;</button></div>` +
        `<div class="mth-views" role="tablist" aria-label="Views">` +
        VIEWS.map(([id, label]) => `<button type="button" role="tab" class="mth-view" data-view="${id}">${label}</button>`).join("") +
        `</div><div class="mth-body" role="tabpanel"><p>Loading&hellip;</p></div></div>`;
      document.body.appendChild(box);
      box.addEventListener("click", e => { if (e.target === box) close(); else onClick(e); });
      box.querySelector(".mth-close").addEventListener("click", close);
      document.addEventListener("keydown", onKey);
    }
    box.hidden = false;
    if (!data) {
      try {
        data = await (await fetch("methodology.json", { cache: "no-cache" })).json();
      } catch (e) {
        box.querySelector(".mth-body").innerHTML = `<p>Could not load methodology.json.</p>`;
        return;
      }
    }
    render();
    box.querySelector(".mth-close").focus();
  }

  function close() {
    if (!box) return;
    box.hidden = true;
    if (location.hash.startsWith("#how")) {
      try { history.replaceState(history.state, "", location.pathname + location.search); } catch (e) { /* sandboxed */ }
    }
  }

  window.openMethodology = open;
  document.addEventListener("DOMContentLoaded", () => {
    const b = document.getElementById("open-methodology");
    if (b) b.addEventListener("click", open);
    if (readHash()) open();
  });
  // a #how link followed while the page is open (our own replaceState
  // writes fire no hashchange)
  window.addEventListener("hashchange", () => { if (readHash()) { if (box && !box.hidden && data) render(); else open(); } });
})();
