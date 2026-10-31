// Pipeline web panel (pipeline.html). Talks to tools/pipeline_web.py on
// loopback. The per-launch token arrives in the URL fragment, is kept in
// sessionStorage and removed from the address bar.
(function () {
  "use strict";

  const LOCALITY_MIN_ZOOM = 10;
  const LOCALITY_DEBOUNCE_MS = 400;
  const LOG_MAX_LINES = 5000;
  const ESTIMATE_POLL_MS = 1000;

  const tokenMatch = location.hash.match(/token=([A-Za-z0-9_-]+)/);
  if (tokenMatch) {
    sessionStorage.setItem("pipelineToken", tokenMatch[1]);
    history.replaceState(null, "", location.pathname + location.search);
  }
  const token = sessionStorage.getItem("pipelineToken") || "";
  // Opening a new link in the same tab changes only the fragment, which does not reload the page.
  window.addEventListener("hashchange", () => {
    if (/token=[A-Za-z0-9_-]+/.test(location.hash)) location.reload();
  });

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtInt = (n) => (n == null ? "?" : Number(n).toLocaleString());
  const fmtDur = (s) => {
    if (s == null) return "?";
    s = Math.round(s);
    if (s < 90) return s + " s";
    if (s < 5400) return Math.round(s / 60) + " min";
    return (s / 3600).toFixed(1) + " h";
  };
  const fmtGb = (g) => (g == null ? "?" : g < 1 ? Math.round(g * 1000) + " MB" : g.toFixed(1) + " GB");

  let writable = false;
  const selected = new Map();       // region_id -> label
  const localityFeatures = new Map(); // linz_id -> feature
  let currentEstimate = null;
  let stream = null;
  let shownJob = null;

  async function api(path, opts = {}) {
    const headers = { "X-Pipeline-Token": token };
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    const res = await fetch("/api/" + path, {
      method: opts.method || "GET", headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) {
      const err = new Error((data && data.error) || res.status + " " + res.statusText);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function banner(html) {
    const b = $("banner");
    b.innerHTML = html || "";
    b.classList.toggle("show", !!html);
  }

  // ---- tabs ------------------------------------------------------------
  document.querySelectorAll("nav button").forEach((btn) => btn.addEventListener("click", () => showTab(btn.dataset.tab)));
  function showTab(name) {
    document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
    if (name === "jobs") loadJobs();
    updateHint();
  }

  // ---- map -------------------------------------------------------------
  const empty = { type: "FeatureCollection", features: [] };
  const map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      sources: {
        satellite: {
          type: "raster",
          tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
          tileSize: 256, attribution: "Esri World Imagery",
        },
        // Same LINZ Basemaps source and public key as preview.html.
        "linz-aerial": {
          type: "raster",
          tiles: ["https://basemaps.linz.govt.nz/v1/tiles/aerial/WebMercatorQuad/{z}/{x}/{y}.webp?api=c01m0keqqjx0anrxjrt7grvtqxh"],
          tileSize: 256, minzoom: 14, maxzoom: 21, attribution: "LINZ Basemaps",
        },
      },
      layers: [
        { id: "satellite", type: "raster", source: "satellite" },
        { id: "linz-aerial", type: "raster", source: "linz-aerial" },
      ],
    },
    center: [172.5, -41.5], zoom: 5,
  });
  map.addControl(new maplibregl.NavigationControl(), "top-right");

  const stateColour = ["match", ["get", "state"], "built", "#2e7d32", "failed", "#c62828", "#ef6c00"];

  map.on("load", () => {
    map.addSource("localities", { type: "geojson", data: empty, promoteId: "linz_id" });
    map.addSource("regions", { type: "geojson", data: empty });
    map.addLayer({ id: "localities-fill", type: "fill", source: "localities",
      paint: { "fill-color": "#1565c0",
               "fill-opacity": ["case", ["boolean", ["feature-state", "selected"], false], 0.4, 0.05] } });
    map.addLayer({ id: "localities-line", type: "line", source: "localities",
      paint: { "line-color": "#90caf9", "line-width": ["case", ["boolean", ["feature-state", "selected"], false], 3, 1] } });
    map.addLayer({ id: "localities-label", type: "symbol", source: "localities", minzoom: 12,
      layout: { "text-field": ["get", "name"], "text-size": 12 },
      paint: { "text-color": "#fff", "text-halo-color": "#000", "text-halo-width": 1 } });
    map.addLayer({ id: "regions-fill", type: "fill", source: "regions", filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "fill-color": stateColour, "fill-opacity": 0.25 } });
    map.addLayer({ id: "regions-line", type: "line", source: "regions", filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "line-color": stateColour, "line-width": 2 } });
    map.addLayer({ id: "regions-point", type: "circle", source: "regions", filter: ["==", ["geometry-type"], "Point"],
      paint: { "circle-color": stateColour, "circle-radius": 7, "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });

    map.on("click", "localities-fill", (e) => {
      if (!e.features.length) return;
      const p = e.features[0].properties;
      toggleSelection(p.region_id, p.name + (p.town && p.town !== p.name ? ", " + p.town : ""));
    });
    map.on("click", "regions-fill", (e) => showRegionPopup(e.features[0], e.lngLat));
    map.on("click", "regions-point", (e) => showRegionPopup(e.features[0], e.lngLat));
    ["localities-fill", "regions-fill", "regions-point"].forEach((id) => {
      map.on("mouseenter", id, () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", id, () => { map.getCanvas().style.cursor = ""; });
    });
    map.on("moveend", scheduleLocalities);
    loadRegions(true);
  });

  function showRegionPopup(feature, lngLat) {
    const p = feature.properties;
    const html = `<b>${esc(p.name || p.region)}</b><br><span class="pill ${esc(p.state)}">${esc(p.state)}</span>
      ${p.n != null ? fmtInt(p.n) + " buildings" : ""}<br>
      <a href="${esc(p.preview_url)}" target="_blank" rel="noopener">Open preview</a>
      ${p.report_url ? ` · <a href="${esc(p.report_url)}" target="_blank" rel="noopener">Report</a>` : ""}`;
    new maplibregl.Popup().setLngLat(lngLat).setHTML(html).addTo(map);
  }

  function updateHint() {
    const onBuild = $("tab-build").classList.contains("active");
    const z = map.getZoom();
    $("hint").textContent = !writable ? "" : onBuild && z < LOCALITY_MIN_ZOOM
      ? "Zoom in further to choose areas on the map" : "";
    $("hint").style.display = $("hint").textContent ? "block" : "none";
  }

  let localityTimer = null;
  function scheduleLocalities() {
    updateHint();
    clearTimeout(localityTimer);
    if (!writable || map.getZoom() < LOCALITY_MIN_ZOOM) return;
    localityTimer = setTimeout(loadLocalities, LOCALITY_DEBOUNCE_MS);
  }

  async function loadLocalities() {
    const b = map.getBounds();
    const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(3)).join(",");
    try {
      const fc = await api("localities?bbox=" + bbox);
      fc.features.forEach((f) => localityFeatures.set(f.properties.linz_id, f));
      map.getSource("localities").setData({ type: "FeatureCollection", features: [...localityFeatures.values()] });
      syncSelectionState();
    } catch (e) {
      $("hint").textContent = e.status === 413 ? "Zoom in to choose areas" : "Localities: " + e.message;
      $("hint").style.display = "block";
    }
  }

  function syncSelectionState() {
    if (!map.getSource("localities")) return;
    localityFeatures.forEach((f, id) =>
      map.setFeatureState({ source: "localities", id }, { selected: selected.has(f.properties.region_id) }));
  }

  // ---- regions tab -----------------------------------------------------
  async function loadRegions(fit) {
    let fc;
    try { fc = await api("regions"); } catch (e) {
      $("region-list").innerHTML = `<p class="block">${esc(e.message)}</p>`;
      return;
    }
    if (map.getSource("regions")) map.getSource("regions").setData(fc);
    const items = fc.features.map((f) => f.properties)
      .sort((a, b) => String(b.built_at || "").localeCompare(String(a.built_at || "")));
    $("region-list").innerHTML = items.length ? items.map((p) => `
      <div class="item" data-region="${esc(p.region)}">
        <h3 class="fly">${esc(p.name || p.region)} <span class="pill ${esc(p.state)}">${esc(p.state)}</span></h3>
        <div class="muted">${esc(p.region)}${p.council ? " · " + esc(p.council) : ""}</div>
        <div class="muted">${p.n != null ? fmtInt(p.n) + " buildings · " : ""}${p.kwh ? fmtInt(Math.round(p.kwh / 1000)) + " MWh/yr · " : ""}${p.built_at ? "built " + esc(p.built_at.slice(0, 10)) : ""}</div>
        <div class="links">
          ${p.state === "built" ? `<a href="${esc(p.preview_url)}" target="_blank" rel="noopener">Open preview</a>` : ""}
          ${p.report_url ? `<a href="${esc(p.report_url)}" target="_blank" rel="noopener">Last report (${esc(p.last_run_status || "?")})</a>` : ""}
          ${writable ? `<button class="small rebuild">Rebuild</button>` : ""}
        </div>
      </div>`).join("") : "<p class='muted'>Nothing built yet. Use New build.</p>";
    document.querySelectorAll("#region-list .item").forEach((el) => {
      const feature = fc.features.find((f) => f.properties.region === el.dataset.region);
      el.querySelector(".fly").addEventListener("click", () => flyTo(feature));
      const rb = el.querySelector(".rebuild");
      if (rb) rb.addEventListener("click", () => {
        toggleSelection(feature.properties.region, feature.properties.name || feature.properties.region, true);
        showTab("build");
      });
    });
    if (fit && fc.features.length) {
      const bounds = new maplibregl.LngLatBounds();
      fc.features.forEach((f) => extend(bounds, f.geometry));
      if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 60, maxZoom: 14, duration: 0 });
    }
  }

  function extend(bounds, geom) {
    if (!geom) return;
    const walk = (c) => (typeof c[0] === "number" ? bounds.extend(c) : c.forEach(walk));
    walk(geom.coordinates);
  }

  function flyTo(feature) {
    if (!feature || !feature.geometry) return;
    const bounds = new maplibregl.LngLatBounds();
    extend(bounds, feature.geometry);
    map.fitBounds(bounds, { padding: 60, maxZoom: 16 });
  }

  // ---- new build tab ---------------------------------------------------
  function toggleSelection(region, label, forceOn) {
    if (!writable || !region) return;
    if (selected.has(region) && !forceOn) selected.delete(region);
    else selected.set(region, label || region);
    currentEstimate = null;
    $("estimate").innerHTML = "";
    renderSelection();
    syncSelectionState();
  }

  function renderSelection() {
    const chips = [...selected.entries()].map(([id, label]) =>
      `<span class="chip" title="${esc(id)}">${esc(label)}<button data-id="${esc(id)}" aria-label="Remove">×</button></span>`);
    $("selection").innerHTML = chips.length ? "<b>Selected:</b><br>" + chips.join("") : "<p class='muted'>Nothing selected.</p>";
    $("selection").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => toggleSelection(b.dataset.id)));
    $("estimate-btn").disabled = !writable || !selected.size;
  }

  let searchTimer = null;
  $("search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    const q = $("search").value.trim();
    if (q.length < 2) { $("search-results").innerHTML = ""; return; }
    searchTimer = setTimeout(async () => {
      try {
        const res = await api("localities/search?q=" + encodeURIComponent(q));
        $("search-results").innerHTML = res.results.map((r, i) =>
          `<div data-i="${i}"><b>${esc(r.name)}</b> <span class="muted">${esc(r.type || "")}${r.town && r.town !== r.name ? ", " + esc(r.town) : ""} · ${esc(r.council || "")}</span></div>`).join("")
          || "<div class='muted'>No matches</div>";
        $("search-results").querySelectorAll("[data-i]").forEach((el) => el.addEventListener("click", () => {
          const r = res.results[+el.dataset.i];
          toggleSelection(r.region_id, r.name + (r.town && r.town !== r.name ? ", " + r.town : ""), true);
          $("search-results").innerHTML = "";
          $("search").value = "";
          const cached = localityFeatures.get(r.linz_id);
          if (cached) flyTo(cached);
          else if (r.bbox) map.fitBounds([[r.bbox[0], r.bbox[1]], [r.bbox[2], r.bbox[3]]], { padding: 60, maxZoom: 16 });
        }));
      } catch (e) {
        $("search-results").innerHTML = `<div class="block">${esc(e.message)}</div>`;
      }
    }, 300);
  });

  $("estimate-btn").addEventListener("click", async () => {
    $("estimate-btn").disabled = true;
    $("estimate").innerHTML = "<p class='muted'>Estimating… (counting buildings with LINZ can take a minute for new areas)</p>";
    try {
      const { estimate_id } = await api("estimate", { method: "POST", body: { regions: [...selected.keys()] } });
      let est;
      for (;;) {
        est = await api("estimate/" + estimate_id);
        if (est.status !== "pending") break;
        await new Promise((r) => setTimeout(r, ESTIMATE_POLL_MS));
      }
      if (est.status === "error") throw new Error(est.error);
      currentEstimate = est;
      renderEstimate(est);
    } catch (e) {
      $("estimate").innerHTML = `<p class="block">${esc(e.message)}</p>`;
    } finally {
      $("estimate-btn").disabled = !selected.size;
    }
  });

  function renderEstimate(est) {
    const t = est.totals;
    const rows = est.regions.map((r) => `<tr>
        <td>${esc(selected.get(r.region) || r.region)}${r.elevation === "synthetic" ? " <span class='warn'>(no LiDAR)</span>" : ""}
          ${(r.warnings || []).map((w) => `<div class="warn">${esc(w)}</div>`).join("")}
          ${(r.blockers || []).map((w) => `<div class="block">${esc(w)}</div>`).join("")}</td>
        <td class="num">${fmtInt(r.buildings)}</td><td class="num">${fmtGb(r.download_gb)}</td>
        <td class="num">${fmtDur(r.total_s)}</td></tr>`).join("");
    const ref = est.paid_referee || {};
    const blocked = (est.blockers || []).length > 0;
    $("estimate").innerHTML = `
      <table><tr><th>Area</th><th class="num">Buildings</th><th class="num">Download</th><th class="num">Time</th></tr>
      ${rows}
      <tr><th>Total</th><th class="num">${fmtInt(t.buildings)}</th><th class="num">${fmtGb(t.download_gb)}</th><th class="num">${fmtDur(t.total_s)}</th></tr></table>
      <div class="muted">Disk about ${fmtGb(t.disk_gb)} (${esc(est.machine.disk_free_gb)} GB free). Cloud cost: $0 (runs on this computer).
        Rate ${esc(est.calibration.seconds_per_building)} s/building from ${est.calibration.source === "history" ? esc(est.calibration.runs_used) + " recent runs" : "defaults"}.</div>
      ${(est.warnings || []).map((w) => `<div class="warn">${esc(w)}</div>`).join("")}
      ${(est.blockers || []).map((w) => `<div class="block">${esc(w)}</div>`).join("")}
      <p class="muted">${esc(est.disclaimer)}</p>
      ${blocked ? "" : `<div class="confirm">
        <label><input type="checkbox" id="referee"> Use the paid AI referee (up to US$${esc(ref.usd_upper)}; ${esc(ref.text || "")})</label>
        <p>Type <b>yes</b> to start this build:</p>
        <input type="text" id="confirm" autocomplete="off" placeholder="yes">
        <p><button class="primary" id="start-btn" disabled>Start build</button></p>
      </div>`}`;
    if (blocked) return;
    $("confirm").addEventListener("input", () => {
      $("start-btn").disabled = $("confirm").value.trim().toLowerCase() !== "yes";
    });
    $("start-btn").addEventListener("click", startJob);
  }

  async function startJob() {
    $("start-btn").disabled = true;
    try {
      const res = await api("jobs", { method: "POST", body: {
        estimate_id: currentEstimate.estimate_id, confirm: $("confirm").value.trim(),
        paid_referee: $("referee").checked } });
      selected.clear();
      renderSelection();
      syncSelectionState();
      $("estimate").innerHTML = "";
      showTab("jobs");
      showJob(res.job_id);
    } catch (e) {
      $("estimate").insertAdjacentHTML("beforeend", `<p class="block">${esc(e.message)}</p>`);
      $("start-btn").disabled = false;
    }
  }

  // ---- jobs tab --------------------------------------------------------
  async function loadJobs() {
    let res;
    try { res = await api("jobs"); } catch (e) {
      $("job-list").innerHTML = `<p class="block">${esc(e.message)}</p>`;
      return;
    }
    $("job-list").innerHTML = res.jobs.map((j) => `
      <div class="item"><h3 data-job="${esc(j.job_id)}">${esc(j.created)} <span class="pill ${esc(j.status)}">${esc(j.status)}</span></h3>
        <div class="muted">${esc(j.regions.join(", "))}</div></div>`).join("") || "<p class='muted'>No jobs yet.</p>";
    $("job-list").querySelectorAll("[data-job]").forEach((el) => el.addEventListener("click", () => showJob(el.dataset.job)));
    if (!shownJob && res.active_job) showJob(res.active_job);
  }

  async function showJob(jobId) {
    shownJob = jobId;
    if (stream) { stream.close(); stream = null; }
    $("job-detail").innerHTML = `<div id="job-summary"></div><div id="log"></div>`;
    let job;
    try { job = await api("jobs/" + encodeURIComponent(jobId)); } catch (e) {
      $("job-summary").innerHTML = `<p class="block">${esc(e.message)}</p>`;
      return;
    }
    renderJob(job);
    const log = $("log");
    let lines = [];
    const append = (text) => {
      const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
      lines = lines.concat(text.split("\n"));
      if (lines.length > LOG_MAX_LINES) lines = lines.slice(-LOG_MAX_LINES);
      log.textContent = lines.join("\n");
      if (atBottom) log.scrollTop = log.scrollHeight;
    };
    stream = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/stream?token=${encodeURIComponent(token)}`);
    stream.addEventListener("log", (e) => append(e.data));
    stream.addEventListener("status", (e) => renderJob(Object.assign(job, JSON.parse(e.data))));
    stream.addEventListener("end", () => {
      stream.close(); stream = null;
      api("jobs/" + encodeURIComponent(jobId)).then(renderJob).catch(() => {});
      loadRegions(false);
      loadJobs();
    });
  }

  function renderJob(job) {
    const el = $("job-summary");
    if (!el) return;
    const running = job.status === "running" || job.status === "queued";
    const runs = (job.runs || []).map((r) => {
      const st = r.status_json || {};
      const step = (st.steps || []).find((s) => s.status === "RUNNING");
      const report = r.run_dir ? "/" + r.run_dir.replace(/^\/+/, "") + "/report.md" : null;
      return `<tr><td>${esc(r.region)}${step ? `<div class="muted">${esc(step.label || step.key)}</div>` : ""}</td>
        <td><span class="pill ${esc(r.status)}">${esc(r.status)}</span></td>
        <td>${report ? `<a href="${esc(report)}" target="_blank" rel="noopener">report</a>` : ""}</td></tr>`;
    }).join("");
    const fc = job.final_combine || {};
    el.innerHTML = `
      <h3>Job ${esc(job.job_id)} <span class="pill ${esc(job.status)}">${esc(job.status)}</span></h3>
      <progress max="1" value="${Number(job.progress || 0)}"></progress>
      <div class="muted">${Math.round((job.progress || 0) * 100)}%${running && job.eta_s != null ? " · about " + fmtDur(job.eta_s) + " left (estimate)" : ""}
        ${job.paid_referee ? " · paid referee on" : ""}</div>
      <table>${runs}</table>
      ${fc.status && fc.status !== "not needed" ? `<div class="muted">Map combine: ${esc(fc.status)}</div>` : ""}
      ${job.error ? `<div class="block">${esc(job.error)}</div>` : ""}
      ${running && job.active !== false && writable ? `<button class="small" id="cancel-btn">Cancel</button>` : ""}
      ${job.status === "succeeded" ? `<p><a href="/preview.html" target="_blank" rel="noopener">Open the map preview</a></p>` : ""}`;
    const cancel = $("cancel-btn");
    if (cancel) cancel.addEventListener("click", async () => {
      if (!confirm("Cancel this build? The region being built stops; finished regions are kept.")) return;
      cancel.disabled = true;
      try { await api(`jobs/${encodeURIComponent(job.job_id)}/cancel`, { method: "POST", body: {} }); }
      catch (e) { alert(e.message); cancel.disabled = false; }
    });
  }

  // ---- start-up --------------------------------------------------------
  async function init() {
    try {
      const health = await fetch("/api/health").then((r) => r.json());
      if (!token) {
        banner("Read-only: open the link printed by <code>python3 tools/pipeline_web.py</code> to start builds.");
      } else {
        await api("jobs");  // verifies the token
        writable = true;
        if (!health.linz_key) banner("No LINZ API key found (environment, .env or Secret Manager): area search and new areas will fail.");
        else if (health.workspace_lock) banner("A command-line build is running in this checkout; new builds wait until it ends.");
      }
      $("server-info").textContent = `Local server · code ${health.git || "?"}${health.active_job ? " · job running" : ""}`;
    } catch (e) {
      banner(e.status === 403
        ? "This link's token is out of date (the server was restarted). Open the new link it printed."
        : "Read-only: the panel server is not running. Start it with <code>python3 tools/pipeline_web.py</code>.");
      $("server-info").textContent = "Not connected";
    }
    renderSelection();
    updateHint();
    if (writable) loadJobs();
    if (map.getSource("regions")) loadRegions(false);  // re-render with Rebuild buttons
  }
  init();
})();
