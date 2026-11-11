// Build data panel (pipeline.html). Talks to tools/pipeline_web.py on
// loopback. The per-launch token arrives in the URL fragment, is kept in
// sessionStorage and removed from the address bar.
(function () {
  "use strict";

  const LOCALITY_MIN_ZOOM = 10;
  const LOCALITY_DEBOUNCE_MS = 400;
  const LOG_MAX_LINES = 5000;
  const ESTIMATE_POLL_MS = 1000;
  const QUEUE_POLL_MS = 5000;
  const VM_POLL_MS = 60000;
  const TARGETS = {
    local: { label: "Local", start: "Start build" },
    developer: { label: "Developer", start: "Start developer build on the VM" },
    published: { label: "Published", start: "Build, gate and publish" },
  };

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
  const fmtArea = (km2) => (km2 == null ? "?" : Number(km2).toFixed(2) + " km²");

  let writable = false;
  const selected = new Map();       // region_id -> label
  const localityFeatures = new Map(); // linz_id -> feature
  const regionRecords = new Map(); // region_id -> area status and links
  let regionStatusMeta = "";
  const gridFeatures = new Map(); // grid-cell id -> feature
  const selectedGridCells = new Map(); // selected grid-cell id -> label
  let selectedParent = null;
  let gridMode = false;
  let currentEstimate = null;
  let selectionRevision = 0;
  let includeImagery = true;
  let paidReferee = false;
  let sourceImageryAvailable = true;
  let refereeUsdUpper = 0;
  let stream = null;
  let shownJob = null;
  let queueLoading = false;
  let queueInitialized = false;
  let queueUpdatedAt = null;
  let jobsSignature = null;
  let target = TARGETS[localStorage.getItem("pipelineTarget")] ? localStorage.getItem("pipelineTarget") : "local";

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

  function showSection(name) {
    const section = $("stage-" + name);
    section.open = true;
    section.scrollIntoView({ behavior: "smooth", block: "nearest" });
    if (name === "jobs") loadJobs();
    if (name === "queue") loadQueue();
    updateHint();
  }

  // ---- target toggle ---------------------------------------------------
  document.querySelectorAll("#targets button").forEach((b) => b.addEventListener("click", () => setTarget(b.dataset.target)));
  function setTarget(name) {
    if (gridMode && name !== "local") {
      banner("Grid selection builds currently run locally only. Return to locality selection before changing target.");
      return;
    }
    const changed = name !== target;
    target = name;
    localStorage.setItem("pipelineTarget", name);
    document.querySelectorAll("#targets button").forEach((b) => b.classList.toggle("active", b.dataset.target === name));
    document.body.className = "target-" + name;
    if (changed) {
      selectionRevision++;
      currentEstimate = null;
      $("estimate").innerHTML = "";
      regionRecords.clear();
      regionStatusMeta = `Checking ${esc(TARGETS[name].label.toLowerCase())} area records…`;
      renderSelection();
      if (map.getSource("regions")) loadRegions(false);
    }
    loadVm(false);
  }

  async function loadVm(refresh) {
    if (target === "local" || !writable) { $("vm-info").textContent = ""; return; }
    try {
      const vm = await api("cloud/status" + (refresh ? "?refresh=1" : ""));
      $("vm-info").innerHTML = `VM ${esc(vm.vm)} <span class="pill ${esc(vm.status)}">${esc(vm.status || "unknown")}</span>
        ${vm.error ? `<span class="block">${esc(vm.error)}</span>` : ""}
        ${vm.busy_with ? " · panel job " + esc(vm.busy_with) + " using it" : ""} · builds ${esc(vm.ref)}`;
    } catch (e) {
      $("vm-info").innerHTML = `<span class="block">VM status: ${esc(e.message)}</span>`;
    }
  }

  // ---- map -------------------------------------------------------------
  const empty = { type: "FeatureCollection", features: [] };
  const tileProtocol = new pmtiles.Protocol();
  maplibregl.addProtocol("pmtiles", tileProtocol.tile);
  const map = new maplibregl.Map({
    container: "map", style: MapLayers.aerialStyle(),
    center: [172.5, -41.5], zoom: 5,
  });
  map.addControl(new maplibregl.NavigationControl(), "top-right");
  const layerLegend = new MapLayers.Legend({ beforeId: "localities-fill" });
  map.addControl(layerLegend, "top-right");
  let layerRequest = 0;
  let layersTarget = null;
  async function loadLayers() {
    const source = target;
    const request = ++layerRequest;
    if (layersTarget !== source) {
      layerLegend.clearDatasets();
      layerLegend.setMessages([]);
      layersTarget = source;
    }
    try {
      const catalogue = await api("layers?source=" + source);
      if (request !== layerRequest || source !== target) return;
      layerLegend.setCatalogue(catalogue, location.href);
    } catch (error) {
      if (request !== layerRequest || source !== target) return;
      layerLegend.clearDatasets();
      layerLegend.setMessages(["Dataset catalogue: " + error.message]);
    }
  }

  const stateColour = ["match", ["get", "state"], "built", "#2e7d32", "failed", "#c62828", "#ef6c00"];

  map.on("load", () => {
    map.addSource("localities", { type: "geojson", data: empty, promoteId: "linz_id" });
    map.addSource("grid-cells", { type: "geojson", data: empty, promoteId: "grid_id" });
    map.addSource("regions", { type: "geojson", data: empty });
    map.addLayer({ id: "localities-fill", type: "fill", source: "localities",
      paint: { "fill-color": "#1565c0",
               "fill-opacity": ["case", ["boolean", ["feature-state", "selected"], false], 0.4, 0.05] } });
    map.addLayer({ id: "localities-line", type: "line", source: "localities",
      paint: { "line-color": "#90caf9", "line-width": ["case", ["boolean", ["feature-state", "selected"], false], 3, 1] } });
    map.addLayer({ id: "localities-label", type: "symbol", source: "localities", minzoom: 12,
      layout: { "text-field": ["get", "name"], "text-size": 12 },
      paint: { "text-color": "#fff", "text-halo-color": "#000", "text-halo-width": 1 } });
    map.addLayer({ id: "grid-cells-fill", type: "fill", source: "grid-cells",
      paint: { "fill-color": "#00acc1",
               "fill-opacity": ["case", ["boolean", ["feature-state", "selected"], false], 0.48, 0.12] } });
    map.addLayer({ id: "grid-cells-line", type: "line", source: "grid-cells",
      paint: { "line-color": ["case", ["boolean", ["feature-state", "selected"], false], "#006064", "#80deea"],
               "line-width": ["case", ["boolean", ["feature-state", "selected"], false], 3, 1] } });
    map.addLayer({ id: "grid-cells-label", type: "symbol", source: "grid-cells", minzoom: 12,
      layout: { "text-field": ["get", "grid_id"], "text-size": 10 },
      paint: { "text-color": "#fff", "text-halo-color": "#000", "text-halo-width": 1 } });
    const regionColour = ["case", ["==", ["get", "kind"], "grid-selection"], "#00897b", stateColour];
    map.addLayer({ id: "regions-fill", type: "fill", source: "regions", filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "fill-color": regionColour, "fill-opacity": 0.25 } });
    map.addLayer({ id: "regions-line", type: "line", source: "regions", filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "line-color": regionColour, "line-width": 2 } });
    map.addLayer({ id: "regions-point", type: "circle", source: "regions", filter: ["==", ["geometry-type"], "Point"],
      paint: { "circle-color": stateColour, "circle-radius": 7, "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
    layerLegend.setEntries([
      ...MapLayers.basemaps(map),
      ...[
        { id: "localities", label: "Locality selection boundaries", ids: ["localities-fill", "localities-line", "localities-label"], colour: "#90caf9" },
        { id: "grid-cells", label: "Grid cells", ids: ["grid-cells-fill", "grid-cells-line", "grid-cells-label"], colour: "#00acc1" },
        { id: "regions", label: "Prepared / built areas", ids: ["regions-fill", "regions-line", "regions-point"], colour: "#2e7d32",
          description: "Green: complete locality; teal: partial grid selection; amber: prepared; red: last run failed." },
      ].map(entry => Object.assign(entry, {
        group: "operations",
        getVisible: () => map.getLayoutProperty(entry.ids[0], "visibility") !== "none",
        queryLayers: entry.ids.filter(id => !id.endsWith("-label")),
        setVisible: on => entry.ids.forEach(id => map.setLayoutProperty(id, "visibility", on ? "visible" : "none")),
      })),
    ]);

    map.on("click", "localities-fill", (e) => {
      if (layerLegend.queryId || gridMode) return;
      if (!e.features.length) return;
      const p = e.features[0].properties;
      toggleSelection(p.region_id, p.name + (p.town && p.town !== p.name ? ", " + p.town : ""),
                      false, { region_id: p.region_id, linz_id: p.linz_id, name: p.name });
    });
    map.on("click", "grid-cells-fill", (e) => {
      if (layerLegend.queryId || !gridMode || !e.features.length) return;
      toggleGridCell(e.features[0].properties.grid_id);
    });
    map.on("click", "regions-fill", (e) => { if (!layerLegend.queryId) showRegionPopup(e.features[0], e.lngLat); });
    map.on("click", "regions-point", (e) => { if (!layerLegend.queryId) showRegionPopup(e.features[0], e.lngLat); });
    ["localities-fill", "grid-cells-fill", "regions-fill", "regions-point"].forEach((id) => {
      map.on("mouseenter", id, () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", id, () => { map.getCanvas().style.cursor = ""; });
    });
    map.on("moveend", scheduleLocalities);
    loadRegions(false);
  });

  function showRegionPopup(feature, lngLat) {
    const p = feature.properties;
    const html = `<b>${esc(p.name || p.region)}</b><br><span class="pill ${esc(p.state)}">${esc(p.state)}</span>
      ${p.kind === "grid-selection"
        ? `<br><span class="warn">Partial selection: ${fmtInt(p.selection_count)} grid cells within ${esc(p.parent_region)}; not complete parent coverage.</span>` : ""}
      ${p.n != null ? fmtInt(p.n) + " buildings" : ""}
      ${p.alias ? "<br>Developer: " + esc(p.alias) + (p.run ? " · run " + esc(p.run) : "") : ""}
      ${p.data_version ? "<br>Live data v" + esc(p.data_version) : ""}<br>
      <a href="${esc(p.preview_url)}" target="_blank" rel="noopener">Open preview</a>
      ${p.report_url ? ` · <a href="${esc(p.report_url)}" target="_blank" rel="noopener">Report</a>` : ""}`;
    new maplibregl.Popup().setLngLat(lngLat).setHTML(html).addTo(map);
  }

  function updateHint() {
    const z = map.getZoom();
    $("hint").textContent = !writable ? "" : z < LOCALITY_MIN_ZOOM
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

  $("area-refresh").addEventListener("click", () => {
    loadRegions(false, true);
    loadVm(true);
  });

  async function loadRegions(fit, refresh) {
    loadLayers();
    const source = target;
    let fc;
    $("area-status-meta").textContent = source === "local" ? "Checking this checkout…" : "Checking the selected target…";
    try {
      fc = await api("regions" + (source === "local" ? "" : "?source=" + source + (refresh ? "&refresh=1" : "")));
    } catch (e) {
      if (source !== target) return;
      regionRecords.clear();
      regionStatusMeta = `<span class="block">Area status unavailable: ${esc(e.message)}</span>`;
      if (map.getSource("regions")) map.getSource("regions").setData(empty);
      renderAreaStatus();
      return;
    }
    if (source !== target) return;  // the toggle moved on while this loaded
    regionRecords.clear();
    fc.features.forEach((feature) => regionRecords.set(feature.properties.region, feature.properties));
    regionStatusMeta = regionsMeta(fc);
    if (map.getSource("regions")) map.getSource("regions").setData(fc);
    renderAreaStatus();
    if (fit && fc.features.length) {
      const bounds = new maplibregl.LngLatBounds();
      fc.features.forEach((f) => extend(bounds, f.geometry));
      if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 60, maxZoom: 14, duration: 0 });
    }
  }

  function regionsMeta(fc) {
    const warn = (fc.warnings || []);
    const warnHtml = warn.length ? `<details><summary class="warn">${warn.length} warning(s)</summary>
      ${warn.map((w) => `<div class="warn">${esc(w)}</div>`).join("")}</details>` : "";
    if (fc.source === "published") {
      return `<div class="muted">Live data v${esc(fc.data_version || "?")} · <a href="${esc(fc.site)}" target="_blank" rel="noopener">live map</a></div>${warnHtml}`;
    }
    if (fc.source === "developer") {
      return `<div class="muted">Developer preview area records from the shared VM.</div>${warnHtml}`;
    }
    return `<div class="muted">Local area records in this checkout. Cached datasets are listed in Layers &amp; legend.</div>${warnHtml}`;
  }

  function renderAreaStatus() {
    const labels = gridMode && selectedParent
      ? [[selectedParent.region_id, selectedParent.name]]
      : [...selected.entries()];
    const entries = labels.map(([id, label]) => {
      const record = regionRecords.get(id);
      if (!record) {
        const missing = target === "local"
          ? "No prepared or built area record in this checkout."
          : `No built area record found for ${esc(TARGETS[target].label)}.`;
        return `<div class="area-record"><b>${esc(label)}</b><div class="muted">${missing}</div></div>`;
      }
      const state = record.state === "built" ? "Built"
        : record.state === "failed" ? "Last run failed"
          : record.state === "downloaded" ? "Inputs prepared; no built summary found"
            : "Area record available";
      const parts = [];
      if (record.n != null) parts.push(`${fmtInt(record.n)} buildings`);
      if (record.built_at) parts.push(`built ${esc(String(record.built_at).slice(0, 10))}`);
      if (record.last_run_status) parts.push(`last run ${esc(record.last_run_status)}`);
      if (record.alias) parts.push(`developer ${esc(record.alias)}`);
      return `<div class="area-record"><b>${esc(label)}</b> <span class="pill ${esc(record.state || "")}">${state}</span>
        ${parts.length ? `<div class="muted">${parts.join(" · ")}</div>` : ""}
        <div class="links">${record.preview_url
          ? `<a href="${esc(record.preview_url)}" target="_blank" rel="noopener">${target === "published" ? "Open live map" : "Open preview"}</a>` : ""}
          ${record.report_url ? `<a href="${esc(record.report_url)}" target="_blank" rel="noopener">Last report</a>` : ""}</div></div>`;
    });
    $("area-status-records").innerHTML = labels.length
      ? entries.join("")
      : `<p class="muted">Select an area to see what is prepared or built for this target.</p>`;
    $("area-status-meta").innerHTML = regionStatusMeta;
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

  // ---- new build ------------------------------------------------------
  function toggleSelection(region, label, forceOn, parent) {
    if (!writable || !region) return;
    if (gridMode && parent && selectedParent &&
        parent.region_id !== selectedParent.region_id) {
      gridMode = false;
      selectedGridCells.clear();
      gridFeatures.clear();
      if (map.getSource("grid-cells")) map.getSource("grid-cells").setData(empty);
    }
    if (selected.has(region) && !forceOn) selected.delete(region);
    else selected.set(region, label || region);
    if (parent) selectedParent = parent;
    if (selectedParent && !selected.has(selectedParent.region_id)) {
      selectedParent = null;
      gridMode = false;
      selectedGridCells.clear();
      gridFeatures.clear();
      if (map.getSource("grid-cells")) map.getSource("grid-cells").setData(empty);
    }
    currentEstimate = null;
    selectionRevision++;
    $("estimate").innerHTML = "";
    renderSelection();
    syncSelectionState();
  }

  function renderSelection() {
    $("grid-btn").disabled = !writable || !selectedParent || selected.size !== 1;
    $("grid-btn").textContent = gridMode ? "Back to locality selection" : "Choose grid cells";
    $("grid-controls").hidden = !gridMode;
    $("grid-size-row").hidden = !gridMode;
    $("grid-size").value = String($("grid-size").dataset.value || "4");
    const chips = [...selected.entries()].map(([id, label]) =>
      `<span class="chip" title="${esc(id)}">${esc(label)}<button data-id="${esc(id)}" aria-label="Remove">×</button></span>`);
    $("selection").innerHTML = gridMode
      ? `<b>${selectedGridCells.size} grid cell(s) selected</b> within ${esc(selectedParent.name)}<br>` +
        [...selectedGridCells.keys()].map((id) => `<span class="chip">${esc(id)}</span>`).join("")
      : chips.length ? `<b>Selected for a ${esc(TARGETS[target].label.toLowerCase())} build:</b><br>` +
        chips.join("") : "<p class='muted'>Nothing selected.</p>";
    $("selection").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => toggleSelection(b.dataset.id)));
    renderAreaStatus();
    $("grid-count").textContent = gridFeatures.size
      ? `${selectedGridCells.size} of ${gridFeatures.size} grid cells` : "";
    $("estimate-btn").disabled = !writable || (gridMode ? !selectedGridCells.size : !selected.size) ||
      (gridMode && target !== "local");
  }

  function syncGridState() {
    if (!map.getSource("grid-cells")) return;
    gridFeatures.forEach((f, id) =>
      map.setFeatureState({ source: "grid-cells", id }, { selected: selectedGridCells.has(id) }));
    $("grid-count").textContent = `${selectedGridCells.size} of ${gridFeatures.size} grid cells`;
  }

  function toggleGridCell(id) {
    if (!gridMode || !id) return;
    if (selectedGridCells.has(id)) selectedGridCells.delete(id);
    else selectedGridCells.set(id, id);
    currentEstimate = null;
    selectionRevision++;
    $("estimate").innerHTML = "";
    renderSelection();
    syncGridState();
  }

  async function loadGridCells(parent, gridSize = 4) {
    const fc = await api(`grid-cells?region=${encodeURIComponent(parent.region_id)}&linz_id=${encodeURIComponent(parent.linz_id)}&grid_size=${gridSize}`);
    if (!fc.features.length) throw new Error("no grid cells intersect this locality");
    $("grid-size").dataset.value = String(gridSize);
    gridFeatures.clear();
    fc.features.forEach((f) => gridFeatures.set(f.properties.grid_id, f));
    map.getSource("grid-cells").setData(fc);
    selectedGridCells.clear();
    gridMode = true;
    currentEstimate = null;
    selectionRevision++;
    $("estimate").innerHTML = "";
    renderSelection();
    syncGridState();
    const feature = localityFeatures.get(parent.linz_id);
    if (feature) flyTo(feature);
  }

  $("grid-btn").addEventListener("click", async () => {
    if (gridMode) {
      gridMode = false;
      selectedGridCells.clear();
      currentEstimate = null;
      selectionRevision++;
      $("estimate").innerHTML = "";
      renderSelection();
      syncGridState();
      return;
    }
    if (!selectedParent || selected.size !== 1) return;
    if (target !== "local") {
      banner("Grid selection builds currently run locally only. Choose Local to continue.");
      return;
    }
    try {
      $("grid-count").textContent = "Loading grid…";
      await loadGridCells(selectedParent);
    } catch (e) {
      $("grid-count").textContent = "";
      banner("Could not load grid: " + esc(e.message));
    }
  });
  $("grid-size").addEventListener("change", async () => {
    if (!selectedParent || !gridMode) return;
    try {
      await loadGridCells(selectedParent, Number($("grid-size").value));
    } catch (e) {
      banner("Could not load grid cells: " + esc(e.message));
    }
  });
  $("grid-all").addEventListener("click", () => {
    gridFeatures.forEach((_, id) => selectedGridCells.set(id, id));
    currentEstimate = null;
    selectionRevision++;
    $("estimate").innerHTML = "";
    renderSelection();
    syncGridState();
  });
  $("grid-none").addEventListener("click", () => {
    selectedGridCells.clear();
    currentEstimate = null;
    selectionRevision++;
    $("estimate").innerHTML = "";
    renderSelection();
    syncGridState();
  });

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
          toggleSelection(r.region_id, r.name + (r.town && r.town !== r.name ? ", " + r.town : ""),
                          true, { region_id: r.region_id, linz_id: r.linz_id, name: r.name });
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

  async function estimateCurrent() {
    const requestRevision = selectionRevision;
    $("estimate-btn").disabled = true;
    currentEstimate = null;
    $("estimate").innerHTML = `${sourceChoicesHtml(true)}
      <p class='muted'>Estimating a ${esc(TARGETS[target].label.toLowerCase())} build… (counting buildings with LINZ can take a minute for new areas)</p>`;
    bindSourceChoices();
    try {
      const body = gridMode
        ? { selection: {
              parent_region: selectedParent.region_id, parent_linz_id: selectedParent.linz_id,
              grid_size: Number($("grid-size").value),
              grid_ids: [...selectedGridCells.keys()],
            }, target, include_imagery: includeImagery, paid_referee: paidReferee }
        : { regions: [...selected.keys()], target,
            include_imagery: includeImagery, paid_referee: paidReferee };
      const { estimate_id } = await api("estimate", { method: "POST", body });
      let est;
      for (;;) {
        est = await api("estimate/" + estimate_id);
        if (est.status !== "pending") break;
        await new Promise((r) => setTimeout(r, ESTIMATE_POLL_MS));
      }
      if (est.status === "error") throw new Error(est.error);
      if (est.target !== target || requestRevision !== selectionRevision) return;
      currentEstimate = est;
      sourceImageryAvailable = est.regions.some((region) => region.imagery_available !== false);
      refereeUsdUpper = est.paid_referee?.usd_upper || 0;
      renderEstimate(est);
    } catch (e) {
      if (requestRevision === selectionRevision) {
        $("estimate").innerHTML = `${sourceChoicesHtml(false)}
          <p class="block">${esc(e.message)}</p>`;
        bindSourceChoices();
      }
    } finally {
      if (requestRevision === selectionRevision) {
        $("estimate-btn").disabled = !writable || (gridMode ? !selectedGridCells.size : !selected.size) ||
          (gridMode && target !== "local");
      }
    }
  }

  $("estimate-btn").addEventListener("click", estimateCurrent);

  function sourceChoicesHtml(disabled) {
    const imageryDisabled = !sourceImageryAvailable || disabled;
    const refereeDisabled = !sourceImageryAvailable || !includeImagery || disabled;
    return `<div class="source-options">
      <b>Inputs</b>
      <label><input type="checkbox" id="include-imagery"${includeImagery ? " checked" : ""}
        ${imageryDisabled ? " disabled" : ""}> Include aerial imagery (free; improves alignment and image-based checks)</label>
      ${!sourceImageryAvailable ? `<div class="muted">No aerial imagery source is available for this selection.</div>` : ""}
      <label><input type="checkbox" id="referee"${paidReferee ? " checked" : ""}
        ${refereeDisabled ? " disabled" : ""}> Use the paid AI referee (up to US$${esc(
          refereeUsdUpper)}; only requested when imagery is included)</label>
      ${disabled ? `<div class="muted">Recalculating estimate…</div>` : ""}
    </div>`;
  }

  function bindSourceChoices() {
    const imagery = $("include-imagery");
    const referee = $("referee");
    if (imagery) imagery.addEventListener("change", () => {
      includeImagery = imagery.checked;
      if (!includeImagery) paidReferee = false;
      selectionRevision++;
      estimateCurrent();
    });
    if (referee) referee.addEventListener("change", () => {
      paidReferee = referee.checked;
      selectionRevision++;
      estimateCurrent();
    });
  }

  function renderEstimate(est) {
    const t = est.totals;
    const rows = est.regions.map((r) => `<tr>
        <td>${esc(r.grid_size ? `${selectedParent.name} · ${r.selection_count} grid cells (${r.grid_size}×${r.grid_size})`
                                      : selected.get(r.region) || r.region)}${r.elevation === "synthetic" ? " <span class='warn'>(no LiDAR)</span>" : ""}
          ${(r.warnings || []).map((w) => `<div class="warn">${esc(w)}</div>`).join("")}
          ${(r.blockers || []).map((w) => `<div class="block">${esc(w)}</div>`).join("")}</td>
        <td class="num">${fmtInt(r.buildings)}</td>
        <td class="num">${fmtArea(r.bbox_km2)}${r.acquisition_footprints
          ? `<div class="muted">${r.acquisition_footprints.length} buffered footprint(s)</div>` : ""}</td>
        <td class="num">${fmtGb(r.download_gb)}</td>
        <td class="num">${fmtDur(r.total_s)}</td></tr>`).join("");
    const ref = est.paid_referee || {};
    const blocked = (est.blockers || []).length > 0;
    $("estimate").innerHTML = `
      ${sourceChoicesHtml(false)}
      <table><tr><th>Area / selection</th><th class="num">Buildings</th><th class="num">Footprint</th><th class="num">Download</th><th class="num">Time</th></tr>
      ${rows}
      <tr><th>Total</th><th class="num">${fmtInt(t.buildings)}</th><th class="num">—</th><th class="num">${fmtGb(t.download_gb)}</th><th class="num">${fmtDur(t.total_s)}</th></tr></table>
      ${costHtml(est)}
      <div class="muted">Rate ${esc(est.calibration.seconds_per_building)} s/building from ${est.calibration.source === "history" ? esc(est.calibration.runs_used) + " recent runs" : "defaults"}.</div>
      ${(est.warnings || []).map((w) => `<div class="warn">${esc(w)}</div>`).join("")}
      ${(est.blockers || []).map((w) => `<div class="block">${esc(w)}</div>`).join("")}
      <p class="muted">${esc(est.disclaimer)}</p>
      ${blocked ? "" : `<div class="confirm">
        ${est.target === "local" ? "<p>Start this build on this computer?</p>"
          : `<label><input type="checkbox" id="cloud-cost-ack"> I understand this build may cost up to NZD ${esc(est.cloud.cost_nzd)}; this is an estimate, not a spending cap.${est.target === "published" ? " It will publish to the live map if the gate passes." : ""}</label>`}
        <p><button class="primary" id="start-btn" disabled>${esc(TARGETS[est.target].start)}</button></p>
      </div>`}`;
    bindSourceChoices();
    if (blocked) return;
    const cloudAck = $("cloud-cost-ack");
    const startButton = $("start-btn");
    const updateStartButton = () => {
      startButton.disabled = est.target !== "local" && !cloudAck.checked;
    };
    if (cloudAck) cloudAck.addEventListener("change", updateStartButton);
    updateStartButton();
    $("start-btn").addEventListener("click", startJob);
  }

  function costHtml(est) {
    const t = est.totals;
    if (est.target === "local") {
      return `<div class="muted">Disk about ${fmtGb(t.disk_gb)} (${esc(est.machine.disk_free_gb)} GB free). Cloud cost: $0 (runs on this computer).</div>`;
    }
    const c = est.cloud || {};
    return `<div class="cost"><b>Cloud cost estimate: up to NZD ${esc(c.cost_nzd)}</b> (${esc(c.vm_hours)} VM hours)
      <div>${esc(c.cost_text)}</div>
      <div class="muted">${esc(c.cost_basis)}</div>
      <div class="muted">VM ${esc(c.project)}/${esc(c.zone)}/${esc(c.vm)} is <b>${esc(c.vm_status || "unknown")}</b>; it builds <b>${esc(c.ref)}</b> as pushed.
        ${c.preview_url ? `Preview: <a href="${esc(c.preview_url)}" target="_blank" rel="noopener">${esc(c.preview_user)}</a>.` : ""}
        ${est.target === "published" ? "<b>Publishes to the live map if the deploy gate passes.</b>" : ""}</div></div>`;
  }

  async function startJob() {
    $("start-btn").disabled = true;
    try {
      const res = await api("jobs", { method: "POST", body: {
        estimate_id: currentEstimate.estimate_id,
        include_imagery: currentEstimate.options.include_imagery,
        paid_referee: currentEstimate.options.paid_referee,
        cloud_cost_acknowledged: currentEstimate.target === "local"
          || $("cloud-cost-ack").checked,
        target: currentEstimate.target,
        cost_nzd: currentEstimate.cloud ? currentEstimate.cloud.cost_nzd : undefined } });
      selected.clear();
      selectionRevision++;
      selectedParent = null;
      gridMode = false;
      selectedGridCells.clear();
      gridFeatures.clear();
      if (map.getSource("grid-cells")) map.getSource("grid-cells").setData(empty);
      renderSelection();
      syncSelectionState();
      $("estimate").innerHTML = "";
      showSection("queue");
    } catch (e) {
      $("estimate").insertAdjacentHTML("beforeend", `<p class="block">${esc(e.message)}</p>`);
      $("start-btn").disabled = false;
    }
  }

  // ---- queue ----------------------------------------------------------
  async function loadQueue() {
    if (queueLoading) return;
    queueLoading = true;
    let res;
    try { res = await api("queue"); } catch (e) {
      $("queue-updates").textContent = "Updates disconnected. " +
        (queueUpdatedAt ? "Showing last known state from " + queueUpdatedAt + ". " : "") + e.message;
      $("queue-list").dataset.stale = "true";
      return;
    } finally {
      queueLoading = false;
    }
    queueUpdatedAt = new Date().toLocaleTimeString();
    $("queue-updates").textContent = "";
    $("queue-list").dataset.stale = "false";
    const lanes = res.lanes || {};
    const total = Object.values(lanes).reduce((n, l) => n + l.jobs.length, 0);
    $("queue-count").textContent = total ? "(" + total + ")" : "";
    if (!queueInitialized) {
      $("stage-queue").open = total > 0;
      queueInitialized = true;
    }
    const focused = document.activeElement;
    const focusKey = focused && $("queue-list").contains(focused) && (focused.dataset.q ? ["q", focused.dataset.q] :
      focused.dataset.job ? ["job", focused.dataset.job] : null);
    const focusLabel = focused && focused.textContent;
    $("queue-list").innerHTML = Object.entries(lanes).map(([name, lane]) => `
      <div class="lane"><h3>${name === "local" ? "Local lane (this computer)" : "Cloud lane (shared VM)"}</h3>
        ${lane.jobs.map(j => PipelineProgress.renderQueueCard(j, j.job_id === lane.active, writable)).join("") || "<p class='muted'>No active or queued builds.</p>"}
      </div>`).join("");
    $("queue-list").querySelectorAll("[data-job]").forEach((el) => el.addEventListener("click", () => showJob(el.dataset.job)));
    $("queue-list").querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () =>
      cancelJob(b.dataset.q, b.dataset.running === "true", b).then(loadQueue)));
    if ($("stage-jobs").open) loadJobs();
    if (focusKey) {
      const replacement = [...$("queue-list").querySelectorAll("[data-q], [data-job]")]
        .find(el => el.dataset[focusKey[0]] === focusKey[1] && el.textContent === focusLabel);
      if (replacement) replacement.focus({ preventScroll: true });
    }
  }

  async function cancelJob(jobId, running, btn) {
    const msg = running
      ? "Cancel this running build? The region being built stops; finished regions are kept. For a cloud build the VM stops only if this run started it."
      : "Remove this job from the queue?";
    if (!confirm(msg)) return;
    if (btn) btn.disabled = true;
    try { await api(`jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST", body: {} }); }
    catch (e) { alert(e.message); if (btn) btn.disabled = false; }
  }

  // ---- job history and detail ----------------------------------------
  async function loadJobs() {
    let res;
    try { res = await api("jobs"); } catch (e) {
      jobsSignature = null;
      $("job-list").innerHTML = `<p class="block">${esc(e.message)}</p>`;
      return;
    }
    const history = res.jobs
      .filter((j) => ["succeeded", "failed", "cancelled", "interrupted"].includes(j.status))
      .sort((a, b) => String(b.finished || b.created || "").localeCompare(String(a.finished || a.created || "")));
    const signature = history.map((j) => `${j.job_id}:${j.status}:${j.finished || ""}`).join("|");
    if (signature === jobsSignature) return;
    jobsSignature = signature;
    $("job-count").textContent = history.length ? `(${history.length})` : "";
    $("job-list").innerHTML = history.map((j) => `
      <div class="item"><h3><button class="job-title" data-job="${esc(j.job_id)}">${esc(PipelineProgress.jobTitle(j))}</button>
        <span class="pill ${esc(j.target || "local")}">${esc(j.target || "local")}</span> <span class="pill ${esc(j.status)}">${esc(j.status)}</span></h3>
        <div class="muted">${esc(j.finished || j.created)} · ${esc(j.job_id)}</div></div>`).join("") || "<p class='muted'>No completed jobs yet.</p>";
    $("job-list").querySelectorAll("[data-job]").forEach((el) => el.addEventListener("click", () => showJob(el.dataset.job)));
  }
  $("stage-jobs").addEventListener("toggle", () => {
    if ($("stage-jobs").open) loadJobs();
  });

  async function showJob(jobId) {
    $("stage-jobs").open = true;
    $("stage-jobs").scrollIntoView({ behavior: "smooth", block: "nearest" });
    shownJob = jobId;
    if (stream) { stream.close(); stream = null; }
    $("job-detail").innerHTML = `<div id="job-updates" class="warn" role="status"></div><div id="job-summary"></div><div id="log"></div>`;
    let job;
    try { job = await api("jobs/" + encodeURIComponent(jobId)); } catch (e) {
      if (shownJob === jobId) $("job-summary").innerHTML = `<p class="block">${esc(e.message)}</p>`;
      return;
    }
    if (shownJob !== jobId) return;
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
    const source = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/stream?token=${encodeURIComponent(token)}`);
    stream = source;
    source.addEventListener("open", () => {
      if (stream !== source) return;
      $("job-updates").textContent = "";
      $("job-summary").dataset.stale = "false";
    });
    source.addEventListener("error", () => {
      if (stream !== source) return;
      $("job-updates").textContent = "Updates disconnected. Showing last known state; reconnecting…";
      $("job-summary").dataset.stale = "true";
    });
    source.addEventListener("log", (e) => { if (stream === source) append(e.data); });
    source.addEventListener("status", (e) => {
      if (stream === source) renderJob(Object.assign(job, JSON.parse(e.data)));
    });
    source.addEventListener("end", async () => {
      source.close();
      if (stream !== source) return;
      stream = null;
      try {
        const final = await api("jobs/" + encodeURIComponent(jobId));
        if (shownJob === jobId) renderJob(final);
      } catch (e) {
        if (shownJob === jobId) {
          $("job-updates").textContent = "Could not refresh final status: " + e.message;
          $("job-summary").dataset.stale = "true";
        }
      }
      loadRegions(false);
      loadJobs();
      loadQueue();
    });
  }

  function renderJob(job) {
    const el = $("job-summary");
    if (!el) return;
    const running = job.status === "running" || job.status === "queued";
    const runs = (job.runs || []).map((r) => {
      const st = r.status_json || {};
      const step = (st.steps || []).find((s) => s.status === "RUNNING");
      const report = r.report_url;
      return `<tr><td>${esc(r.region)}${step ? `<div class="muted">${esc(step.label || step.key)}</div>` : ""}</td>
        <td><span class="pill ${esc(r.status)}">${esc(r.status)}</span></td>
        <td>${report ? `<a href="${esc(report)}" target="_blank" rel="noopener">report</a>` : ""}</td></tr>`;
    }).join("");
    const fc = job.final_combine || {};
    el.innerHTML = `
      <h3>${esc(PipelineProgress.jobTitle(job))} <span class="pill ${esc(job.target || "local")}">${esc(job.target || "local")}</span> <span class="pill ${esc(job.status)}">${esc(job.status)}</span></h3>
      <div class="muted job-id">Job ${esc(job.job_id)}</div>
      ${job.approved && job.approved.cloud_cost_text ? `<div class="muted">Approved: ${esc(job.approved.cloud_cost_text)}</div>` : ""}
      ${job.gate_result ? `<div>Deploy gate: <span class="pill ${job.gate_result === "pass" ? "PASS" : "FAIL"}">${esc(job.gate_result)}</span></div>` : ""}
      ${job.published_version ? `<div><b>Published data v${esc(job.published_version)}</b></div>` : ""}
      ${PipelineProgress.renderProgress(job)}
      ${job.paid_referee ? '<div class="muted">Paid referee on</div>' : ""}
      <table>${runs}</table>
      ${fc.status && fc.status !== "not needed" ? `<div class="muted">Map combine: ${esc(fc.status)}</div>` : ""}
      ${job.error ? `<div class="block">${esc(job.error)}</div>` : ""}
      ${running && writable ? `<button class="small" id="cancel-btn">${job.status === "queued" ? "Remove from queue" : "Cancel"}</button>` : ""}
      ${job.preview_url ? `<p><a href="${esc(job.preview_url)}" target="_blank" rel="noopener">Open the developer preview</a></p>` : ""}
      ${job.status === "succeeded" && (job.target || "local") === "local" ? `<p><a href="/preview.html" target="_blank" rel="noopener">Open the map preview</a></p>` : ""}`;
    const cancel = $("cancel-btn");
    if (cancel) cancel.addEventListener("click", () => cancelJob(job.job_id, job.status === "running", cancel));
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
      const lanes = health.active_jobs || {};
      $("server-info").textContent = `Local server · code ${health.git || "?"}` +
        (lanes.local ? " · local job running" : "") + (lanes.cloud ? " · cloud job running" : "");
    } catch (e) {
      banner(e.status === 403
        ? "This link's token is out of date (the server was restarted). Open the new link it printed."
        : "Read-only: the panel server is not running. Start it with <code>python3 tools/pipeline_web.py</code>.");
      $("server-info").textContent = "Not connected";
    }
    setTarget(target);
    renderSelection();
    updateHint();
    if (writable) {
      loadJobs();
      loadQueue();
      setInterval(loadQueue, QUEUE_POLL_MS);
      setInterval(() => loadVm(false), VM_POLL_MS);
    }
    if (map.getSource("regions")) loadRegions(false);
  }
  init();
})();
