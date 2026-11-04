// Pipeline web panel (pipeline.html). Talks to tools/pipeline_web.py on
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
    local: { label: "Local", regions: "Areas built or prepared in this checkout.",
             start: "Start build" },
    developer: { label: "Developer", regions: "Every developer's preview on the shared VM's preview bucket.",
                 start: "Start developer build on the VM" },
    published: { label: "Published", regions: "Areas on the live map (production).",
                 start: "Build, gate and publish" },
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
  const meshblockFeatures = new Map(); // MB2023 -> feature
  const selectedMeshblocks = new Map(); // MB2023 -> label
  let selectedParent = null;
  let meshblockMode = false;
  let currentEstimate = null;
  let selectionRevision = 0;
  let stream = null;
  let shownJob = null;
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

  // ---- tabs ------------------------------------------------------------
  document.querySelectorAll("nav button").forEach((btn) => btn.addEventListener("click", () => showTab(btn.dataset.tab)));
  function showTab(name) {
    document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
    if (name === "jobs") loadJobs();
    if (name === "queue") loadQueue();
    updateHint();
  }

  // ---- target toggle ---------------------------------------------------
  document.querySelectorAll("#targets button").forEach((b) => b.addEventListener("click", () => setTarget(b.dataset.target)));
  function setTarget(name) {
    if (meshblockMode && name !== "local") {
      banner("Meshblock selection builds currently run locally only. Return to locality selection before changing target.");
      return;
    }
    const changed = name !== target;
    target = name;
    localStorage.setItem("pipelineTarget", name);
    document.querySelectorAll("#targets button").forEach((b) => b.classList.toggle("active", b.dataset.target === name));
    document.body.className = "target-" + name;
    $("regions-desc").textContent = TARGETS[name].regions;
    if (changed) {
      selectionRevision++;
      currentEstimate = null;
      $("estimate").innerHTML = "";
      renderSelection();
      if (map.getSource("regions")) loadRegions(true);
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
    map.addSource("meshblocks", { type: "geojson", data: empty, promoteId: "meshblock_id" });
    map.addSource("regions", { type: "geojson", data: empty });
    map.addLayer({ id: "localities-fill", type: "fill", source: "localities",
      paint: { "fill-color": "#1565c0",
               "fill-opacity": ["case", ["boolean", ["feature-state", "selected"], false], 0.4, 0.05] } });
    map.addLayer({ id: "localities-line", type: "line", source: "localities",
      paint: { "line-color": "#90caf9", "line-width": ["case", ["boolean", ["feature-state", "selected"], false], 3, 1] } });
    map.addLayer({ id: "localities-label", type: "symbol", source: "localities", minzoom: 12,
      layout: { "text-field": ["get", "name"], "text-size": 12 },
      paint: { "text-color": "#fff", "text-halo-color": "#000", "text-halo-width": 1 } });
    map.addLayer({ id: "meshblocks-fill", type: "fill", source: "meshblocks",
      paint: { "fill-color": "#00acc1",
               "fill-opacity": ["case", ["boolean", ["feature-state", "selected"], false], 0.48, 0.12] } });
    map.addLayer({ id: "meshblocks-line", type: "line", source: "meshblocks",
      paint: { "line-color": ["case", ["boolean", ["feature-state", "selected"], false], "#006064", "#80deea"],
               "line-width": ["case", ["boolean", ["feature-state", "selected"], false], 3, 1] } });
    map.addLayer({ id: "meshblocks-label", type: "symbol", source: "meshblocks", minzoom: 12,
      layout: { "text-field": ["get", "meshblock_id"], "text-size": 10 },
      paint: { "text-color": "#fff", "text-halo-color": "#000", "text-halo-width": 1 } });
    const regionColour = ["case", ["==", ["get", "kind"], "meshblock-selection"], "#7e57c2", stateColour];
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
        { id: "meshblocks", label: "Stats NZ meshblocks (2023)", ids: ["meshblocks-fill", "meshblocks-line", "meshblocks-label"], colour: "#00acc1" },
        { id: "regions", label: "Prepared / built areas", ids: ["regions-fill", "regions-line", "regions-point"], colour: "#2e7d32",
          description: "Green: complete locality; purple: partial meshblock selection; amber: prepared; red: last run failed. These boundaries are not cached dataset coverage." },
      ].map(entry => Object.assign(entry, {
        group: "operations",
        getVisible: () => map.getLayoutProperty(entry.ids[0], "visibility") !== "none",
        queryLayers: entry.ids.filter(id => !id.endsWith("-label")),
        setVisible: on => entry.ids.forEach(id => map.setLayoutProperty(id, "visibility", on ? "visible" : "none")),
      })),
    ]);

    map.on("click", "localities-fill", (e) => {
      if (layerLegend.queryId || meshblockMode) return;
      if (!e.features.length) return;
      const p = e.features[0].properties;
      toggleSelection(p.region_id, p.name + (p.town && p.town !== p.name ? ", " + p.town : ""),
                      false, { region_id: p.region_id, linz_id: p.linz_id, name: p.name });
    });
    map.on("click", "meshblocks-fill", (e) => {
      if (layerLegend.queryId || !meshblockMode || !e.features.length) return;
      toggleMeshblock(e.features[0].properties.meshblock_id);
    });
    map.on("click", "regions-fill", (e) => { if (!layerLegend.queryId) showRegionPopup(e.features[0], e.lngLat); });
    map.on("click", "regions-point", (e) => { if (!layerLegend.queryId) showRegionPopup(e.features[0], e.lngLat); });
    ["localities-fill", "meshblocks-fill", "regions-fill", "regions-point"].forEach((id) => {
      map.on("mouseenter", id, () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", id, () => { map.getCanvas().style.cursor = ""; });
    });
    map.on("moveend", scheduleLocalities);
    loadRegions(true);
  });

  function showRegionPopup(feature, lngLat) {
    const p = feature.properties;
    const html = `<b>${esc(p.name || p.region)}</b><br><span class="pill ${esc(p.state)}">${esc(p.state)}</span>
      ${p.kind === "meshblock-selection" ? `<br><span class="warn">Partial selection: ${fmtInt(p.meshblock_count)} meshblocks within ${esc(p.parent_region)}; not complete parent coverage.</span>` : ""}
      ${p.n != null ? fmtInt(p.n) + " buildings" : ""}
      ${p.alias ? "<br>Developer: " + esc(p.alias) + (p.run ? " · run " + esc(p.run) : "") : ""}
      ${p.data_version ? "<br>Live data v" + esc(p.data_version) : ""}<br>
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
  $("regions-refresh").addEventListener("click", () => { loadRegions(false, true); loadVm(true); });

  async function loadRegions(fit, refresh) {
    loadLayers();
    const source = target;
    let fc;
    $("regions-meta").innerHTML = source === "local" ? "" : "<p class='muted'>Reading the bucket…</p>";
    try {
      fc = await api("regions" + (source === "local" ? "" : "?source=" + source + (refresh ? "&refresh=1" : "")));
    } catch (e) {
      $("regions-meta").innerHTML = "";
      $("region-list").innerHTML = `<p class="block">${esc(e.message)}</p>`;
      return;
    }
    if (source !== target) return;  // the toggle moved on while this loaded
    $("regions-meta").innerHTML = regionsMeta(fc);
    if (map.getSource("regions")) map.getSource("regions").setData(fc);
    const items = fc.features.map((f) => f.properties)
      .sort((a, b) => String(b.built_at || "").localeCompare(String(a.built_at || "")));
    $("region-list").innerHTML = items.length ? items.map((p) => `
      <div class="item" data-region="${esc(p.region)}" data-alias="${esc(p.alias || "")}">
        <h3 class="fly">${esc(p.name || p.region)} <span class="pill ${esc(p.state)}">${esc(p.state)}</span>
          ${p.kind === "meshblock-selection" ? '<span class="pill" style="background:#7e57c2">partial selection</span>' : ""}</h3>
        <div class="muted">${esc(p.region)}${p.council ? " · " + esc(p.council) : ""}${p.alias ? " · developer " + esc(p.alias) : ""}</div>
        <div class="muted">${p.n != null ? fmtInt(p.n) + " buildings · " : ""}${p.kwh ? fmtInt(Math.round(p.kwh / 1000)) + " MWh/yr · " : ""}${p.built_at ? "built " + esc(p.built_at.slice(0, 10)) : ""}</div>
        <div class="links">
          ${p.state === "built" ? `<a href="${esc(p.preview_url)}" target="_blank" rel="noopener">${p.source === "published" ? "Open live map" : "Open preview"}</a>` : ""}
          ${p.report_url ? `<a href="${esc(p.report_url)}" target="_blank" rel="noopener">Last report (${esc(p.last_run_status || "?")})</a>` : ""}
          ${writable ? `<button class="small rebuild">Rebuild</button>` : ""}
        </div>
      </div>`).join("") : `<p class='muted'>${source === "local" ? "Nothing built yet. Use New build." : "Nothing found."}</p>`;
    document.querySelectorAll("#region-list .item").forEach((el) => {
      const feature = fc.features.find((f) => f.properties.region === el.dataset.region &&
        (!f.properties.alias || f.properties.alias === el.dataset.alias));
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

  function regionsMeta(fc) {
    const warn = (fc.warnings || []);
    const warnHtml = warn.length ? `<details><summary class="warn">${warn.length} warning(s)</summary>
      ${warn.map((w) => `<div class="warn">${esc(w)}</div>`).join("")}</details>` : "";
    if (fc.source === "published") {
      return `<div class="muted">Live data v${esc(fc.data_version || "?")} · versions in the bucket: ${esc((fc.versions || []).join(", ") || "?")}
        · <a href="${esc(fc.site)}" target="_blank" rel="noopener">live map</a></div>${warnHtml}`;
    }
    if (fc.source === "developer") {
      return `<div class="muted">${(fc.aliases || []).map((a) =>
        `<a href="${esc(a.preview_url)}" target="_blank" rel="noopener">${esc(a.alias)}</a> (${fmtInt(a.regions)})`).join(" · ") || "No developer previews."}</div>${warnHtml}`;
    }
    return "";
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
  function toggleSelection(region, label, forceOn, parent) {
    if (!writable || !region) return;
    if (meshblockMode && parent && selectedParent &&
        parent.region_id !== selectedParent.region_id) {
      meshblockMode = false;
      selectedMeshblocks.clear();
      meshblockFeatures.clear();
      if (map.getSource("meshblocks")) map.getSource("meshblocks").setData(empty);
    }
    if (selected.has(region) && !forceOn) selected.delete(region);
    else selected.set(region, label || region);
    if (parent) selectedParent = parent;
    if (selectedParent && !selected.has(selectedParent.region_id)) {
      selectedParent = null;
      meshblockMode = false;
      selectedMeshblocks.clear();
      meshblockFeatures.clear();
      if (map.getSource("meshblocks")) map.getSource("meshblocks").setData(empty);
    }
    currentEstimate = null;
    selectionRevision++;
    $("estimate").innerHTML = "";
    renderSelection();
    syncSelectionState();
  }

  function renderSelection() {
    $("meshblock-btn").disabled = !writable || !selectedParent || selected.size !== 1;
    $("meshblock-btn").textContent = meshblockMode ? "Back to locality selection" : "Choose meshblocks for this locality";
    $("meshblock-controls").hidden = !meshblockMode;
    const chips = [...selected.entries()].map(([id, label]) =>
      `<span class="chip" title="${esc(id)}">${esc(label)}<button data-id="${esc(id)}" aria-label="Remove">×</button></span>`);
    $("selection").innerHTML = meshblockMode
      ? `<b>${selectedMeshblocks.size} meshblock(s) selected</b> within ${esc(selectedParent.name)}<br>` +
        [...selectedMeshblocks.keys()].map((id) => `<span class="chip">${esc(id)}</span>`).join("")
      : chips.length ? `<b>Selected for a ${esc(TARGETS[target].label.toLowerCase())} build:</b><br>` +
        chips.join("") : "<p class='muted'>Nothing selected.</p>";
    $("selection").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => toggleSelection(b.dataset.id)));
    $("meshblock-count").textContent = meshblockFeatures.size
      ? `${selectedMeshblocks.size} of ${meshblockFeatures.size} meshblocks` : "";
    $("estimate-btn").disabled = !writable || (meshblockMode ? !selectedMeshblocks.size : !selected.size) ||
      (meshblockMode && target !== "local");
  }

  function syncMeshblockState() {
    if (!map.getSource("meshblocks")) return;
    meshblockFeatures.forEach((f, id) =>
      map.setFeatureState({ source: "meshblocks", id }, { selected: selectedMeshblocks.has(id) }));
    $("meshblock-count").textContent = `${selectedMeshblocks.size} of ${meshblockFeatures.size} meshblocks`;
  }

  function toggleMeshblock(id) {
    if (!meshblockMode || !id) return;
    if (selectedMeshblocks.has(id)) selectedMeshblocks.delete(id);
    else selectedMeshblocks.set(id, id);
    currentEstimate = null;
    selectionRevision++;
    $("estimate").innerHTML = "";
    renderSelection();
    syncMeshblockState();
  }

  async function loadMeshblocks(parent) {
    const fc = await api(`meshblocks?region=${encodeURIComponent(parent.region_id)}&linz_id=${encodeURIComponent(parent.linz_id)}`);
    if (!fc.features.length) throw new Error("no Stats NZ meshblocks intersect this locality");
    meshblockFeatures.clear();
    fc.features.forEach((f) => meshblockFeatures.set(f.properties.meshblock_id, f));
    map.getSource("meshblocks").setData(fc);
    selectedMeshblocks.clear();
    meshblockMode = true;
    currentEstimate = null;
    selectionRevision++;
    $("estimate").innerHTML = "";
    renderSelection();
    syncMeshblockState();
    const feature = localityFeatures.get(parent.linz_id);
    if (feature) flyTo(feature);
  }

  $("meshblock-btn").addEventListener("click", async () => {
    if (meshblockMode) {
      meshblockMode = false;
      selectedMeshblocks.clear();
      currentEstimate = null;
      selectionRevision++;
      $("estimate").innerHTML = "";
      renderSelection();
      syncMeshblockState();
      return;
    }
    if (!selectedParent || selected.size !== 1) return;
    if (target !== "local") {
      banner("Meshblock selection builds currently run locally only. Choose Local to continue.");
      return;
    }
    try {
      $("meshblock-count").textContent = "Loading locality meshblocks…";
      await loadMeshblocks(selectedParent);
    } catch (e) {
      $("meshblock-count").textContent = "";
      banner("Could not load meshblocks: " + esc(e.message));
    }
  });
  $("meshblock-all").addEventListener("click", () => {
    meshblockFeatures.forEach((_, id) => selectedMeshblocks.set(id, id));
    currentEstimate = null;
    selectionRevision++;
    $("estimate").innerHTML = "";
    renderSelection();
    syncMeshblockState();
  });
  $("meshblock-none").addEventListener("click", () => {
    selectedMeshblocks.clear();
    currentEstimate = null;
    selectionRevision++;
    $("estimate").innerHTML = "";
    renderSelection();
    syncMeshblockState();
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

  $("estimate-btn").addEventListener("click", async () => {
    const requestRevision = selectionRevision;
    $("estimate-btn").disabled = true;
    $("estimate").innerHTML = `<p class='muted'>Estimating a ${esc(TARGETS[target].label.toLowerCase())} build… (counting buildings with LINZ can take a minute for new areas)</p>`;
    try {
      const body = meshblockMode
        ? { selection: { parent_region: selectedParent.region_id, parent_linz_id: selectedParent.linz_id,
                         meshblock_ids: [...selectedMeshblocks.keys()] }, target }
        : { regions: [...selected.keys()], target };
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
      renderEstimate(est);
    } catch (e) {
      if (requestRevision === selectionRevision) {
        $("estimate").innerHTML = `<p class="block">${esc(e.message)}</p>`;
      }
    } finally {
      $("estimate-btn").disabled = !writable || (meshblockMode ? !selectedMeshblocks.size : !selected.size) ||
        (meshblockMode && target !== "local");
    }
  });

  function renderEstimate(est) {
    const t = est.totals;
    const rows = est.regions.map((r) => `<tr>
        <td>${esc(r.meshblock_count ? `${selectedParent.name} · ${r.meshblock_count} meshblocks`
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
      <table><tr><th>Area / selection</th><th class="num">Buildings</th><th class="num">Footprint</th><th class="num">Download</th><th class="num">Time</th></tr>
      ${rows}
      <tr><th>Total</th><th class="num">${fmtInt(t.buildings)}</th><th class="num">—</th><th class="num">${fmtGb(t.download_gb)}</th><th class="num">${fmtDur(t.total_s)}</th></tr></table>
      ${costHtml(est)}
      <div class="muted">Rate ${esc(est.calibration.seconds_per_building)} s/building from ${est.calibration.source === "history" ? esc(est.calibration.runs_used) + " recent runs" : "defaults"}.</div>
      ${(est.warnings || []).map((w) => `<div class="warn">${esc(w)}</div>`).join("")}
      ${(est.blockers || []).map((w) => `<div class="block">${esc(w)}</div>`).join("")}
      <p class="muted">${esc(est.disclaimer)}</p>
      ${blocked ? "" : `<div class="confirm">
        <label><input type="checkbox" id="referee"> Use the paid AI referee (up to US$${esc(ref.usd_upper)}; ${esc(ref.text || "")})</label>
        <p>${est.target === "local" ? "Type <b>yes</b> to start this build:"
          : `Type <b>yes</b> to accept a cloud cost of up to <b>NZD ${esc(est.cloud.cost_nzd)}</b> (not a spending cap)${est.target === "published" ? " and publish to the live map if the gate passes" : ""}:`}</p>
        <input type="text" id="confirm" autocomplete="off" placeholder="yes">
        <p><button class="primary" id="start-btn" disabled>${esc(TARGETS[est.target].start)}</button></p>
      </div>`}`;
    if (blocked) return;
    $("confirm").addEventListener("input", () => {
      $("start-btn").disabled = $("confirm").value.trim().toLowerCase() !== "yes";
    });
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
        estimate_id: currentEstimate.estimate_id, confirm: $("confirm").value.trim(),
        paid_referee: $("referee").checked, target: currentEstimate.target,
        cost_nzd: currentEstimate.cloud ? currentEstimate.cloud.cost_nzd : undefined } });
      selected.clear();
      selectionRevision++;
      selectedParent = null;
      meshblockMode = false;
      selectedMeshblocks.clear();
      meshblockFeatures.clear();
      if (map.getSource("meshblocks")) map.getSource("meshblocks").setData(empty);
      renderSelection();
      syncSelectionState();
      $("estimate").innerHTML = "";
      showTab("queue");
    } catch (e) {
      $("estimate").insertAdjacentHTML("beforeend", `<p class="block">${esc(e.message)}</p>`);
      $("start-btn").disabled = false;
    }
  }

  // ---- queue tab -------------------------------------------------------
  async function loadQueue() {
    let res;
    try { res = await api("queue"); } catch (e) {
      $("queue-list").innerHTML = `<p class="block">${esc(e.message)}</p>`;
      return;
    }
    const lanes = res.lanes || {};
    const total = Object.values(lanes).reduce((n, l) => n + l.jobs.length, 0);
    $("queue-count").textContent = total ? "(" + total + ")" : "";
    const sel = selected.size ? `<div class="item"><b>Selected, not started (${esc(TARGETS[target].label)}):</b>
      ${[...selected.values()].map((l) => `<span class="chip">${esc(l)}</span>`).join("")}
      <div class="muted">${currentEstimate ? "Estimated; confirm on New build." : "Estimate on New build to start."}</div></div>` : "";
    $("queue-list").innerHTML = sel + Object.entries(lanes).map(([name, lane]) => `
      <div class="lane"><h3>${name === "local" ? "Local lane (this computer)" : "Cloud lane (shared VM)"}</h3>
        ${lane.jobs.map((j) => {
          const running = j.job_id === lane.active;
          const cost = j.approved && j.approved.cloud_cost_nzd && j.approved.cloud_cost_nzd !== "0" ? " · up to NZD " + esc(j.approved.cloud_cost_nzd) : "";
          return `<div class="item"><span class="row-actions">${writable ? `<button class="small" data-q="${esc(j.job_id)}" data-running="${running}">${running ? "Cancel" : "Remove"}</button>` : ""}</span>
            <h3 data-job="${esc(j.job_id)}"><span class="pill ${esc(j.target)}">${esc(j.target)}</span> <span class="pill ${esc(j.status)}">${running ? "running" : "queued"}</span> ${esc(j.job_id)}</h3>
            <div class="muted">${esc((j.regions || []).join(", "))}</div>
            <div class="muted">${j.estimated_total_s != null ? "about " + fmtDur(j.estimated_total_s) : ""}${cost}${j.waiting ? " · " + esc(j.waiting) : ""}</div></div>`;
        }).join("") || "<p class='muted'>Idle.</p>"}</div>`).join("");
    $("queue-list").querySelectorAll("[data-job]").forEach((el) => el.addEventListener("click", () => { showTab("jobs"); showJob(el.dataset.job); }));
    $("queue-list").querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () =>
      cancelJob(b.dataset.q, b.dataset.running === "true", b).then(loadQueue)));
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

  // ---- jobs tab --------------------------------------------------------
  async function loadJobs() {
    let res;
    try { res = await api("jobs"); } catch (e) {
      $("job-list").innerHTML = `<p class="block">${esc(e.message)}</p>`;
      return;
    }
    $("job-list").innerHTML = res.jobs.map((j) => `
      <div class="item"><h3 data-job="${esc(j.job_id)}">${esc(j.created)} <span class="pill ${esc(j.target || "local")}">${esc(j.target || "local")}</span> <span class="pill ${esc(j.status)}">${esc(j.status)}</span></h3>
        <div class="muted">${esc(j.regions.join(", "))}</div></div>`).join("") || "<p class='muted'>No jobs yet.</p>";
    $("job-list").querySelectorAll("[data-job]").forEach((el) => el.addEventListener("click", () => showJob(el.dataset.job)));
    const active = Object.values(res.active_jobs || {}).find(Boolean) || res.active_job;
    if (!shownJob && active) showJob(active);
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
      <h3>Job ${esc(job.job_id)} <span class="pill ${esc(job.target || "local")}">${esc(job.target || "local")}</span> <span class="pill ${esc(job.status)}">${esc(job.status)}</span></h3>
      ${job.queue_position ? `<div class="muted">Queued: position ${esc(job.queue_position)} in its lane.</div>` : ""}
      ${job.waiting ? `<div class="warn">${esc(job.waiting)}</div>` : ""}
      ${job.approved && job.approved.cloud_cost_text ? `<div class="muted">Approved: ${esc(job.approved.cloud_cost_text)}</div>` : ""}
      ${job.gate_result ? `<div>Deploy gate: <span class="pill ${job.gate_result === "pass" ? "PASS" : "FAIL"}">${esc(job.gate_result)}</span></div>` : ""}
      ${job.published_version ? `<div><b>Published data v${esc(job.published_version)}</b></div>` : ""}
      <progress max="1" value="${Number(job.progress || 0)}"></progress>
      <div class="muted">${Math.round((job.progress || 0) * 100)}%${running && job.eta_s != null ? " · about " + fmtDur(job.eta_s) + " left (estimate)" : ""}
        ${job.paid_referee ? " · paid referee on" : ""}</div>
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
    if (map.getSource("regions")) loadRegions(false);  // re-render with Rebuild buttons
  }
  init();
})();
