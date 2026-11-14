// Build data panel (pipeline.html). Talks to tools/pipeline_web.py on
// loopback. The per-launch token arrives in the URL fragment, is kept in
// sessionStorage and removed from the address bar.
//
// One area tree (council > town > locality, plus grid cells while a gridded
// build is open) whose rows update in place; a selection bar acts on the
// ticked rows; the map colours each locality by its state for the target.
(function () {
  "use strict";

  const T = PipelineTree;
  const LOG_MAX_LINES = 5000;
  const ESTIMATE_POLL_MS = 1000;
  const QUEUE_POLL_MS = 5000;
  const VM_POLL_MS = 60000;
  const FLAT_ROW_LIMIT = 500;
  const AUTO_EXPAND_LIMIT = 300;
  const TARGETS = {
    local: { label: "Local", start: "Add to queue" },
    developer: { label: "Developer", start: "Add developer builds to queue" },
    published: { label: "Published", start: "Queue builds, gate and publish" },
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
    else showView();
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
  const fmtDate = (s) => (s ? String(s).replace("T", " ").slice(0, 16) : "");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  let writable = false;
  let maxRegions = 25;
  let target = TARGETS[localStorage.getItem("pipelineTarget")] ? localStorage.getItem("pipelineTarget") : "local";
  let catalog = null;           // national catalogue, or null when not built yet
  let status = { regions: {}, grids: {}, calibration: null };
  let statusSignature = "";
  const remote = new Map();     // region id -> developer/published area record
  let remoteMeta = "";
  let tree = T.buildTree([], [], {});
  const byLinz = new Map();     // LINZ locality id -> locality leaf
  const views = new Map();      // leaf id -> {view, state}
  const selected = new Set();
  const expanded = new Set();
  const openDetail = new Set();
  const activeFilters = new Set();
  const hiddenStates = new Set();
  let query = "";
  let highlighted = null;
  let queueJobs = [];
  let recentQueueJobs = [];
  let stream = null;
  let shownJob = null;
  let queueLoading = false;
  let queueInitialized = false;
  let queueUpdatedAt = null;
  let jobsSignature = null;

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

  function showView() {
    const config = location.hash === "#config";
    document.body.classList.toggle("config", config);
    if (config) { loadCatalogInfo(); loadSnapshots(); }
    else setTimeout(() => map.resize(), 0);
  }

  // Snapshots always operate on local emitted outputs, independently of target.
  let snapshotBusy = false;
  const releaseChoices = new Set();
  let releaseState = null;
  let releaseRecords = [];
  async function loadSnapshots() {
    if (!writable) return;
    try {
      const [data, staging] = await Promise.all([api("snapshots"), api("staging")]);
      const select = $("snapshot-region");
      const previous = select.value;
      // Grid cells are local build pieces; only whole regions are snapshotted and staged.
      data.regions = data.regions.filter((region) => !/\/grid-[0-9a-f]{16}$/.test(region));
      select.innerHTML = data.regions.map((region) =>
        `<option value="${esc(region)}">${esc(region)}</option>`).join("");
      if (data.regions.includes(previous)) select.value = previous;
      select.disabled = snapshotBusy || !data.regions.length;
      $("snapshot-capture").disabled = snapshotBusy || !data.regions.length;
      $("snapshot-list").innerHTML = data.snapshots.length ? data.snapshots.map((item) =>
        `<div class="item"><b>${esc(item.region_id || "Unknown region")}</b>
          <div class="muted job-id">${esc(item.snapshot_id)}</div>
          <div class="muted">Captured: ${esc(item.created_at || "unknown")}<br>
          Built: ${esc(item.built_at || "unknown")}. Integrity not checked by listing.</div>
          ${item.error ? `<p class="block">${esc(item.error)}</p>` : ""}
          <button class="small" data-verify-snapshot="${esc(item.snapshot_id)}"
            ${snapshotBusy ? "disabled" : ""}>Verify snapshot</button>
          <button class="small" data-stage-snapshot="${esc(item.snapshot_id)}"
            ${snapshotBusy || item.error ? "disabled" : ""}>Stage locally</button></div>`).join("")
        : '<p class="muted">No completed snapshots. Build a region, then capture it here.</p>';
      $("snapshot-list").querySelectorAll("[data-verify-snapshot]").forEach((button) =>
        button.addEventListener("click", () => runSnapshot(
          `snapshots/${encodeURIComponent(button.dataset.verifySnapshot)}/verify`, {})));
      $("snapshot-list").querySelectorAll("[data-stage-snapshot]").forEach((button) =>
        button.addEventListener("click", () => runSnapshot("staging", {
          snapshot_id: button.dataset.stageSnapshot,
        })));
      $("staging-list").innerHTML = staging.staged.length ? staging.staged.map((item) =>
        `<div class="item"><b>${esc(item.region_id || "Unknown region")}</b>
          <div class="muted job-id">Staging / source snapshot: ${esc(item.staging_id)}</div>
          <div class="muted">Staged: ${esc(item.staged_at || "unknown")}<br>
          Built: ${esc(item.built_at || "unknown")}. Not approved or deployed. Listing is not verification.</div>
          <label><input type="checkbox" data-release-choice="${esc(item.staging_id)}"
            ${releaseChoices.has(item.staging_id) ? "checked" : ""} ${item.error ? "disabled" : ""}>
            Include this version in a local candidate</label><br>
          ${item.error ? `<p class="block">${esc(item.error)}</p>` : ""}
          <button class="small" data-verify-staged="${esc(item.staging_id)}"
            ${snapshotBusy ? "disabled" : ""}>Verify staged copy</button></div>`).join("")
        : '<p class="muted">No completed staged copies.</p>';
      $("staging-list").querySelectorAll("[data-verify-staged]").forEach((button) =>
        button.addEventListener("click", () => runSnapshot(
          `staging/${encodeURIComponent(button.dataset.verifyStaged)}/verify`, {})));
      const available = new Set(staging.staged.filter((item) => !item.error).map((item) => item.staging_id));
      for (const id of releaseChoices) if (!available.has(id)) releaseChoices.delete(id);
      $("staging-list").querySelectorAll("[data-release-choice]").forEach((input) =>
        input.addEventListener("change", () => {
          if (input.checked) releaseChoices.add(input.dataset.releaseChoice);
          else releaseChoices.delete(input.dataset.releaseChoice);
          releaseButtons();
        }));
      await loadReleases();
    } catch (error) {
      $("snapshot-status").textContent = `Cannot load snapshots: ${error.message}`;
      $("snapshot-capture").disabled = true;
    }
  }

  async function followSnapshot(identifier) {
    snapshotBusy = true;
    releaseButtons();
    $("snapshot-capture").disabled = true;
    $("snapshot-region").disabled = true;
    $("snapshot-list").querySelectorAll("button").forEach((button) => { button.disabled = true; });
    $("staging-list").querySelectorAll("button").forEach((button) => { button.disabled = true; });
    try {
      let operation;
      do {
        operation = await api(`snapshots/operations/${encodeURIComponent(identifier)}`);
        const labels = {
          capture: ["Copying and verifying local snapshot", "Captured and verified"],
          verify: ["Verifying local snapshot", "Verified snapshot"],
          stage: ["Copying and verifying local staging", "Staged and verified"],
          verify_staged: ["Verifying staged copy", "Verified staged copy"],
          release_create: ["Assembling isolated local candidate", "Candidate ready"],
          release_compare: ["Comparing with active local release", "Comparison ready"],
          release_verify: ["Verifying candidate files", "Candidate verified"],
          release_approve: ["Recording local review", "Review recorded"],
          release_activate: ["Switching local deployment", "Local deployment switched"],
          release_publish: ["Reviewing and switching local deployment", "Local deployment switched"],
          release_rollback: ["Rolling back local deployment", "Local rollback completed"],
          release_policy: ["Saving retention policy", "Policy saved"],
          release_cleanup: ["Checking retention and cleanup", "Cleanup completed"],
        };
        const label = labels[operation.action] || ["Running operation", "Completed"];
        const statusElement = operation.action.startsWith("release_") ? $("release-status") : $("snapshot-status");
        statusElement.textContent = operation.status === "running"
          ? `${label[0]}...`
          : operation.status === "succeeded"
            ? operation.action.startsWith("release_")
              ? `${operation.action.replace("release_", "")}: completed locally.`
              : `${label[1]} ${operation.staging_id || operation.snapshot_id}: ${fmtInt(operation.artifact_count)} artifacts, ${fmtInt(operation.bytes)} bytes.`
            : `${operation.status}: ${operation.error || "Operation did not complete."}`;
        if (operation.result) renderReleaseResult(operation);
        if (operation.failed_release_id) {
          $("release-result").innerHTML = '<button class="small" id="release-failure-log">Read assembly log</button><pre id="release-log" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>';
          $("release-failure-log").addEventListener("click", async () => {
            try {
              const log = await api(`releases/incomplete/${encodeURIComponent(operation.failed_release_id)}/log`);
              $("release-log").textContent = (log.truncated ? "Last 64 KiB:\n" : "") + log.text;
            } catch (error) {
              $("release-log").textContent = `Log unavailable: ${error.message}`;
            }
          });
        }
        if (operation.status === "succeeded" && operation.action === "release_create") {
          await loadReleases();
          $("release-select").value = operation.result.release_id;
          $("release-reason").value = "";
          releasePreview();
        }
        if (operation.status === "running") await new Promise((resolve) => setTimeout(resolve, 750));
      } while (operation.status === "running");
    } catch (error) {
      $("snapshot-status").textContent = `Status unavailable: ${error.message}. Refresh snapshots to reconnect; do not capture again blindly.`;
    } finally {
      snapshotBusy = false;
      await loadSnapshots();
    }
  }

  async function runSnapshot(path, body) {
    if (!writable || snapshotBusy) return;
    snapshotBusy = true;
    releaseButtons();
    $("snapshot-capture").disabled = true;
    const status = path.startsWith("releases/") ? $("release-status") : $("snapshot-status");
    status.textContent = "Starting local operation...";
    try {
      const operation = await api(path, { method: "POST", body });
      sessionStorage.setItem("snapshotOperation", operation.operation_id);
      await followSnapshot(operation.operation_id);
    } catch (error) {
      const status = path.startsWith("releases/") ? $("release-status") : $("snapshot-status");
      status.textContent = `Operation refused: ${error.message}`;
    } finally {
      snapshotBusy = false;
      await loadSnapshots();
    }
  }

  $("snapshot-capture").addEventListener("click", () =>
    runSnapshot("snapshots", { region: $("snapshot-region").value }));
  $("snapshot-refresh").addEventListener("click", async () => {
    if (snapshotBusy) return;
    const operation = sessionStorage.getItem("snapshotOperation");
    if (operation) await followSnapshot(operation);
    else await loadSnapshots();
  });

  function releaseButtons() {
    const disabled = !writable || snapshotBusy || !releaseState;
    $("release-create").disabled = disabled || !releaseChoices.size;
    $("release-select").disabled = disabled || !releaseRecords.length;
    document.querySelectorAll("[data-release-action]").forEach((button) => {
      button.disabled = disabled || !$("release-select").value;
    });
    $("release-policy").disabled = disabled;
    $("release-cleanup-preview").disabled = disabled || !releaseState.policy;
    $("release-cleanup-apply").disabled = disabled || !releaseState.policy || !$("release-cleanup-confirm").checked;
    $("release-rollback").disabled = disabled || !releaseState.previous ||
      !releaseState.rollback_until || Date.parse(releaseState.rollback_until) <= Date.now();
  }

  function releasePreview() {
    const id = $("release-select").value;
    $("release-preview").innerHTML = id
      ? `<a href="/local-releases/${encodeURIComponent(id)}/rooftop-solar.html" target="_blank" rel="noopener">Preview exact release</a>` : "";
  }

  async function loadReleases() {
    if (!writable) return;
    try {
      const result = await api("releases");
      releaseState = result.state;
      releaseRecords = result.releases;
      const previous = $("release-select").value;
      $("release-select").innerHTML = releaseRecords.map((item) =>
        `<option value="${esc(item.release_id)}">${esc(item.created_at)} · ${Object.keys(item.region_builds).length} regions · ${esc(item.release_id.slice(0, 8))}</option>`).join("");
      if (releaseRecords.some((r) => r.release_id === previous)) $("release-select").value = previous;
      else if (releaseRecords.length) $("release-select").value = releaseRecords[releaseRecords.length - 1].release_id;
      $("release-active").innerHTML = releaseState.active
        ? `Active: ${esc(releaseState.active)} · generation ${releaseState.generation}<br>
           <a href="/local-deploy/" target="_blank" rel="noopener">Open local deployed map</a><br>
           Rollback expires: ${esc(releaseState.rollback_until || "none")}`
        : "No local deployment. The first candidate contains only the selected complete regions.";
      document.querySelectorAll("[data-retention]").forEach((input) => {
        if (!input.value && releaseState.policy) input.value = releaseState.policy[input.dataset.retention];
      });
      releasePreview();
    } catch (error) {
      releaseState = null;
      $("release-status").textContent = `Releases unavailable: ${error.message}`;
    }
    releaseButtons();
  }

  function renderReleaseResult(operation) {
    const data = operation.result;
    if (operation.action === "release_compare") {
      $("release-result").innerHTML = `<p>${data.stale ? "STALE: create a new candidate against the current deployment." :
        data.automatic_eligible ? "Eligible for automatic approval." : "Manual review reason required; unknown values are not passes."}</p>
        ${data.warnings.map((w) => `<p class="warn">${esc(w)}</p>`).join("")}
        <table><thead><tr><th>Region / metric</th><th>Active</th><th>Candidate</th><th>Status / reason</th></tr></thead><tbody>
        ${data.metrics.map((m) => `<tr><td>${esc(m.region)}<br>${esc(m.field)}</td>
          <td>${esc(m.previous == null ? "unknown / new" : JSON.stringify(m.previous))}</td>
          <td>${esc(m.proposed == null ? "unknown" : JSON.stringify(m.proposed))}</td>
          <td><span class="${m.status === "fail" ? "block" : m.status === "warning" ? "warn" : m.status === "pass" ? "pill PASS" : "muted"}">${esc(m.status)}</span> ${esc(m.reason)}</td></tr>`).join("")}
        </tbody></table>`;
    } else {
      $("release-result").innerHTML = `<pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(JSON.stringify(data, null, 2))}</pre>`;
    }
  }

  $("release-select").addEventListener("change", () => {
    $("release-result").textContent = "";
    $("release-reason").value = "";
    releasePreview();
  });
  $("release-refresh").addEventListener("click", async () => {
    if (snapshotBusy) return;
    const operation = sessionStorage.getItem("snapshotOperation");
    if (operation) await followSnapshot(operation);
    else await loadReleases();
  });
  $("release-create").addEventListener("click", () => {
    if (releaseState) runSnapshot("releases/create", {staging_ids: [...releaseChoices], generation: releaseState.generation});
  });
  document.querySelectorAll("[data-release-action]").forEach((button) =>
    button.addEventListener("click", () => {
      if (releaseState) runSnapshot(`releases/${button.dataset.releaseAction}`, {
        release_id: $("release-select").value, generation: releaseState.generation,
        reason: $("release-reason").value,
      });
    }));
  $("release-rollback").addEventListener("click", () => {
    if (releaseState) runSnapshot("releases/rollback", {generation: releaseState.generation});
  });
  $("release-policy").addEventListener("click", () => {
    const policy = {};
    document.querySelectorAll("[data-retention]").forEach((input) => { policy[input.dataset.retention] = Number(input.value); });
    runSnapshot("releases/policy", {policy});
  });
  $("release-cleanup-confirm").addEventListener("change", releaseButtons);
  $("release-cleanup-preview").addEventListener("click", () => runSnapshot("releases/cleanup", {apply:false}));
  $("release-cleanup-apply").addEventListener("click", () => {
    if ($("release-cleanup-confirm").checked) runSnapshot("releases/cleanup", {apply:true});
  });

  // ---- national catalogue (Config) -------------------------------------
  let catalogPoll = null;
  async function loadCatalogInfo() {
    if (!writable) return;
    try {
      const refresh = await api("catalog/refresh");
      renderCatalogRefresh(refresh);
    } catch (e) { /* the refresh status is optional */ }
    $("catalog-info").innerHTML = catalog
      ? `<p>${fmtInt(catalog.regions.length)} localities with buildings · built ${esc(fmtDate(catalog.built_at))} UTC
          in ${esc(Math.round(catalog.build_seconds || 0))} s.</p>`
      : `<p class="warn">No catalogue yet: the area list shows only areas already built in this checkout.</p>`;
  }

  function renderCatalogRefresh(state) {
    const el = $("catalog-progress");
    $("catalog-refresh").disabled = !writable || state.status === "running";
    if (state.status === "running") {
      el.innerHTML = `Refreshing: ${esc(state.stage || "starting")}${state.total
        ? ` (${fmtInt(state.done)} of ${fmtInt(state.total)})` : ""}…
        <progress ${state.total ? `value="${state.done}" max="${state.total}"` : ""}></progress>`;
    } else if (state.status === "failed") {
      el.innerHTML = `<span class="block">Refresh failed: ${esc(state.error)}</span>`;
    } else if (state.status === "succeeded") {
      el.textContent = `Refreshed: ${fmtInt(state.regions)} localities.`;
    } else {
      el.textContent = "";
    }
    return state.status;
  }

  async function followCatalogRefresh() {
    clearTimeout(catalogPoll);
    let state;
    try { state = await api("catalog/refresh"); } catch (e) {
      $("catalog-progress").innerHTML = `<span class="block">${esc(e.message)}</span>`;
      return;
    }
    if (renderCatalogRefresh(state) === "running") {
      catalogPoll = setTimeout(followCatalogRefresh, 1500);
      return;
    }
    if (state.status === "succeeded") {
      await loadCatalog();
      loadGeometry();
      loadCatalogInfo();
    }
  }

  $("catalog-refresh").addEventListener("click", async () => {
    $("catalog-refresh").disabled = true;
    try {
      renderCatalogRefresh(await api("catalog/refresh", { method: "POST", body: {} }));
      followCatalogRefresh();
    } catch (e) {
      $("catalog-progress").innerHTML = `<span class="block">${esc(e.message)}</span>`;
      $("catalog-refresh").disabled = false;
    }
  });

  // ---- target toggle ---------------------------------------------------
  document.querySelectorAll("#targets button").forEach((b) => b.addEventListener("click", () => setTarget(b.dataset.target)));
  function setTarget(name) {
    const changed = name !== target;
    target = name;
    localStorage.setItem("pipelineTarget", name);
    document.querySelectorAll("#targets button").forEach((b) => b.classList.toggle("active", b.dataset.target === name));
    Object.keys(TARGETS).forEach((t) => document.body.classList.toggle("target-" + t, t === name));
    if (changed) {
      remote.clear();
      remoteMeta = "";
      if (name !== "local") {
        for (const id of [...selected]) if ((tree.leaves.get(id) || {}).kind === "cell") selected.delete(id);
      }
      rebuildTree();
    }
    loadRemote(false);
    loadLayers();
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

  // Developer and published states come from the VM or live site.
  async function loadRemote(refresh) {
    if (target === "local" || !writable) return;
    const source = target;
    try {
      const fc = await api("regions?source=" + source + (refresh ? "&refresh=1" : ""));
      if (source !== target) return;
      remote.clear();
      fc.features.forEach((f) => remote.set(f.properties.region, f.properties));
      const warnings = fc.warnings || [];
      remoteMeta = (fc.source === "published"
        ? `Live data v${esc(fc.data_version || "?")}`
        : "Developer previews on the shared VM") +
        (warnings.length ? ` · <span class="warn" title="${esc(warnings.join("\n"))}">${warnings.length} warning(s)</span>` : "");
    } catch (e) {
      if (source !== target) return;
      remote.clear();
      remoteMeta = `<span class="block">${esc(TARGETS[source].label)} area states unavailable: ${esc(e.message)}</span>`;
    }
    rebuildTree();
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
    if (!writable) return;
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

  const stateColour = ["match", ["coalesce", ["feature-state", "state"], "none"],
    ...T.STATES.flatMap((s) => [s, T.STATE_COLOURS[s]]), T.STATE_COLOURS.none];
  const isOn = (key) => ["boolean", ["feature-state", key], false];
  let mapReady = false;

  map.on("load", () => {
    map.addSource("localities", { type: "geojson", data: empty, promoteId: "linz_id" });
    map.addSource("grid-cells", { type: "geojson", data: empty, promoteId: "grid_id" });
    map.addSource("job-focus", { type: "geojson", data: empty });
    map.addLayer({ id: "localities-fill", type: "fill", source: "localities",
      paint: { "fill-color": stateColour,
               "fill-opacity": ["case", isOn("hidden"), 0,
                 isOn("selected"), 0.6,
                 ["==", ["coalesce", ["feature-state", "state"], "none"], "none"], 0.08, 0.45] } });
    map.addLayer({ id: "localities-line", type: "line", source: "localities",
      paint: { "line-color": ["case", isOn("selected"), "#111", stateColour],
               "line-opacity": ["case", isOn("hidden"), 0, 1],
               "line-width": ["case", isOn("selected"), 2.5, 0.6] } });
    map.addLayer({ id: "grid-cells-fill", type: "fill", source: "grid-cells",
      paint: { "fill-color": stateColour,
               "fill-opacity": ["case", isOn("selected"), 0.6, 0.3] } });
    map.addLayer({ id: "grid-cells-line", type: "line", source: "grid-cells",
      paint: { "line-color": ["case", isOn("selected"), "#111", "#ffffff"],
               "line-width": ["case", isOn("selected"), 2.5, 1] } });
    map.addLayer({ id: "grid-cells-label", type: "symbol", source: "grid-cells", minzoom: 11,
      layout: { "text-field": ["get", "grid_id"], "text-size": 10 },
      paint: { "text-color": "#fff", "text-halo-color": "#000", "text-halo-width": 1 } });
    map.addLayer({ id: "job-focus-fill", type: "fill", source: "job-focus",
      paint: { "fill-color": "#ffeb3b", "fill-opacity": 0.12 } });
    map.addLayer({ id: "job-focus-line", type: "line", source: "job-focus",
      paint: { "line-color": "#ffeb3b", "line-width": 5 } });
    map.addLayer({ id: "job-focus-point", type: "circle", source: "job-focus",
      filter: ["==", ["geometry-type"], "Point"],
      paint: { "circle-color": "#ffeb3b", "circle-radius": 11, "circle-stroke-color": "#212121", "circle-stroke-width": 2 } });
    layerLegend.setEntries([
      ...MapLayers.basemaps(map),
      ...[
        { id: "localities", label: "Area status", ids: ["localities-fill", "localities-line"], colour: T.STATE_COLOURS.built,
          description: "Each locality coloured by its state for the selected target; see the status legend." },
        { id: "grid-cells", label: "Grid cells", ids: ["grid-cells-fill", "grid-cells-line", "grid-cells-label"], colour: "#ffffff" },
      ].map(entry => Object.assign(entry, {
        group: "operations",
        getVisible: () => map.getLayoutProperty(entry.ids[0], "visibility") !== "none",
        queryLayers: entry.ids.filter(id => !id.endsWith("-label")),
        setVisible: on => entry.ids.forEach(id => map.setLayoutProperty(id, "visibility", on ? "visible" : "none")),
      })),
    ]);

    map.on("click", "localities-fill", (e) => {
      if (layerLegend.queryId || !e.features.length) return;
      if (map.queryRenderedFeatures(e.point, { layers: ["grid-cells-fill"] }).length) return;
      const leaf = byLinz.get(e.features[0].id);
      if (!leaf || e.features[0].state.hidden) return;
      toggleLeaves(T.leavesOf(leaf));
      revealRow(leaf.id);
    });
    map.on("click", "grid-cells-fill", (e) => {
      if (layerLegend.queryId || !e.features.length) return;
      const leaf = gridShown && tree.leaves.get(gridShown.cells.get(e.features[0].properties.grid_id));
      if (!leaf) return;
      toggleLeaves([leaf]);
      revealRow(leaf.id);
    });
    ["localities-fill", "grid-cells-fill"].forEach((id) => {
      map.on("mouseenter", id, () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", id, () => { map.getCanvas().style.cursor = ""; });
    });
    mapReady = true;
    loadGeometry();
  });

  async function loadGeometry() {
    if (!mapReady || !writable) return;
    try {
      const res = await fetch("/api/catalog/geometry", { headers: { "X-Pipeline-Token": token } });
      if (!res.ok) return;
      map.getSource("localities").setData(await res.json());
      mapStates.clear();
      syncMap();
    } catch (e) { /* the tree still works without boundaries */ }
  }

  // Feature-state changes only where a locality's colour or selection moved.
  const mapStates = new Map();
  function syncMap() {
    if (!mapReady) return;
    for (const [linz, leaf] of byLinz) {
      const s = mapState(leaf);
      const key = s.state + (s.selected ? "+" : "") + (s.hidden ? "-" : "");
      if (mapStates.get(linz) === key) continue;
      mapStates.set(linz, key);
      map.setFeatureState({ source: "localities", id: linz }, s);
    }
    if (gridShown) {
      for (const [gridId, id] of gridShown.cells) {
        const v = views.get(id);
        map.setFeatureState({ source: "grid-cells", id: gridId },
          { state: v ? v.state : "none", selected: selected.has(id) });
      }
    }
  }

  // A gridded locality shows its cells' progress until they are combined.
  function mapState(leaf) {
    const cells = leaf.children && leaf.children.length ? leaf.children : null;
    let state = (views.get(leaf.id) || {}).state || "none";
    if (cells) {
      const states = cells.map((c) => (views.get(c.id) || {}).state);
      state = states.includes("running") ? "running" : states.includes("queued") ? "queued"
        : states.includes("failed") ? "failed" : state === "none" && states.some((s) => s === "built") ? "built" : state;
    }
    const sel = T.leavesOf(leaf).some((l) => selected.has(l.id));
    return { state, selected: sel, hidden: hiddenStates.has(state) };
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

  function zoomToNode(node) {
    const boxes = (node.bbox ? [node] : T.leavesOf(node.kind === "cell" ? node : node))
      .map((l) => l.bbox || (l.kind === "cell" && tree.leaves.get(l.parent_region) || {}).bbox).filter(Boolean);
    if (node.kind === "locality" && node.grid) showGrid(node);
    if (!boxes.length || node.kind === "cell" || node.legacy) {
      if (node.kind === "cell" || node.kind === "locality") showAreaOnMap(node.id);
      return;
    }
    const b = boxes.reduce((a, x) => [Math.min(a[0], x[0]), Math.min(a[1], x[1]), Math.max(a[2], x[2]), Math.max(a[3], x[3])]);
    map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 50, maxZoom: 15 });
  }

  // Grid cells of one locality, drawn while its row is in use.
  let gridShown = null;
  async function showGrid(locality) {
    const grid = locality.grid;
    if (!grid || !mapReady || (gridShown && gridShown.id === locality.id)) return;
    try {
      const fc = await api(`grid-cells?region=${encodeURIComponent(locality.id)}&linz_id=${encodeURIComponent(
        grid.parent_linz_id || locality.linz_id)}&grid_size=${grid.grid_size}`);
      const cells = new Map();
      for (const [gridId, cell] of Object.entries(grid.cells)) cells.set(gridId, cell.region);
      fc.features = fc.features.filter((f) => cells.has(f.properties.grid_id));
      gridShown = { id: locality.id, cells };
      map.getSource("grid-cells").setData(fc);
      syncMap();
    } catch (e) {
      $("map-action-status").textContent = `Grid cells unavailable: ${e.message}`;
    }
  }

  let jobFocusTimer = null;
  async function showAreaOnMap(region) {
    const parentId = region.replace(/\/grid-[0-9a-f]{16}$/, "");
    const leaf = tree.leaves.get(region) || tree.leaves.get(parentId);
    if (leaf && leaf.kind === "locality" && leaf.bbox && region === parentId) {
      const b = leaf.bbox;
      map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 50, maxZoom: 15 });
      revealRow(leaf.id);
      return;
    }
    let feature;
    try {
      feature = await api("localities/feature?region=" + encodeURIComponent(region));
    } catch (error) {
      $("map-action-status").textContent = `Area boundary unavailable: ${error.message}`;
      return;
    }
    if (!feature || !feature.geometry) {
      $("map-action-status").textContent = "No map boundary is available for this build area.";
      return;
    }
    $("map-action-status").textContent = "";
    const source = map.getSource("job-focus");
    if (!source) return;
    source.setData({ type: "FeatureCollection", features: [feature] });
    clearTimeout(jobFocusTimer);
    jobFocusTimer = setTimeout(() => source.setData(empty), 8000);
    flyTo(feature);
    if (tree.leaves.has(region)) revealRow(region);
  }

  // ---- tree data -------------------------------------------------------
  async function loadCatalog() {
    try {
      catalog = await api("catalog");
      $("catalog-meta").textContent = `${fmtInt(catalog.regions.length)} localities`;
      $("catalog-meta").title = "National catalogue built " + fmtDate(catalog.built_at) + " UTC";
      if ($("banner").dataset.catalog) { banner(""); delete $("banner").dataset.catalog; }
    } catch (e) {
      catalog = null;
      $("catalog-meta").textContent = "";
      if (e.status === 404) {
        banner(`No national area catalogue yet, so only areas already built here are listed.
          <a href="#config">Build it in Config</a> (about a minute and a half).`);
        $("banner").dataset.catalog = "1";
      }
    }
    rebuildTree();
  }

  async function loadStatus() {
    if (!writable) return;
    let next;
    try { next = await api("catalog/status"); } catch (e) { return; }
    const signature = JSON.stringify(next);
    if (signature === statusSignature) return;
    statusSignature = signature;
    status = next;
    const gridsChanged = Object.keys(next.grids || {}).sort().join() !==
      [...tree.leaves.values()].filter((l) => l.grid).map((l) => l.id).sort().join();
    if (gridsChanged) rebuildTree();
    else { computeViews(); render(); }
  }

  function rebuildTree() {
    const ids = new Set(Object.keys(status.regions || {}));
    remote.forEach((_, id) => ids.add(id));
    tree = T.buildTree(catalog ? catalog.regions : [], [...ids], status.grids || {});
    byLinz.clear();
    for (const leaf of tree.leaves.values()) {
      if (leaf.kind === "locality" && leaf.linz_id != null) byLinz.set(leaf.linz_id, leaf);
    }
    for (const id of [...selected]) if (!tree.leaves.has(id)) selected.delete(id);
    if (gridShown && !(tree.leaves.get(gridShown.id) || {}).grid) {
      gridShown = null;
      if (mapReady) map.getSource("grid-cells").setData(empty);
    }
    computeViews();
    render();
  }

  function computeViews() {
    views.clear();
    for (const leaf of tree.leaves.values()) {
      const view = T.rowView(status.regions[leaf.id], remote.get(leaf.id), target);
      views.set(leaf.id, { view, state: T.lifecycle(view) });
    }
  }

  // ---- tree rendering --------------------------------------------------
  const filtering = () => !!query || activeFilters.size > 0 || hiddenStates.size > 0;
  function visible(leaf) {
    const v = views.get(leaf.id);
    if (!v || hiddenStates.has(v.state)) return false;
    return T.matchesQuery(tree, leaf, query) && T.passesFilters(v.view, v.state, activeFilters);
  }

  function rollup(leaves) {
    const counts = {};
    leaves.forEach((l) => { const s = views.get(l.id).state; counts[s] = (counts[s] || 0) + 1; });
    const bar = T.STATES.filter((s) => counts[s]).map((s) =>
      `<span style="flex:${counts[s]};background:${T.STATE_COLOURS[s]}"></span>`).join("");
    const title = T.STATES.filter((s) => counts[s]).map((s) => `${T.STATE_LABELS[s]}: ${counts[s]}`).join(", ");
    const built = (counts.built || 0) + (counts.staged || 0) + (counts.published || 0);
    return `<span class="meta"><span class="rollup" title="${esc(title)}">${bar}</span> ${fmtInt(leaves.length)} · ${fmtInt(built)} built</span>`;
  }

  function stateHtml(state) {
    return `<span class="state"><span class="dot ${state}" style="background:${T.STATE_COLOURS[state]}"></span>${esc(T.STATE_LABELS[state])}</span>`;
  }

  function tagsHtml(leaf, v) {
    const tags = [];
    if (v.view.stale) tags.push('<span class="tag stale" title="Built with an older solar model">stale</span>');
    if (v.view.options && v.view.options.include_imagery === false) tags.push('<span class="tag" title="Built without aerial imagery">no imagery</span>');
    if (leaf.grid) tags.push(`<span class="tag" title="Building as grid cells">grid ${leaf.grid.built_cells}/${Object.keys(leaf.grid.cells).length}</span>`);
    if (leaf.legacy) tags.push('<span class="tag" title="Not in the national catalogue">local</span>');
    return tags.join("");
  }

  function groupRow(node, depth, leaves, open) {
    const all = T.leavesOf(node).filter(visible);
    const check = T.checkState(node, selected, visible);
    const total = T.leavesOf(node).reduce((n, l) => n + (l.buildings || 0), 0);
    return `<div class="row lvl-${Math.min(depth, 2)}" style="--depth:${depth}" data-id="${esc(node.id)}" role="treeitem" aria-expanded="${open}">
      <span class="twisty" data-twisty="${esc(node.id)}" aria-label="${open ? "Collapse" : "Expand"}">${open ? "▾" : "▸"}</span>
      <input type="checkbox" data-group="${esc(node.id)}" ${check === "all" ? "checked" : ""} ${check === "some" ? "data-indet" : ""}
        ${writable && all.length ? "" : "disabled"} aria-label="Select ${esc(node.name)}">
      <span class="name" data-zoom="${esc(node.id)}" title="Zoom to ${esc(node.name)}">${esc(node.name)}${node.grid ? tagsHtml(node, views.get(node.id)) : ""}</span>
      ${rollup(leaves)}
      <span class="meta">${total ? fmtInt(total) : ""}</span></div>`;
  }

  function leafRow(leaf, depth, crumb) {
    const v = views.get(leaf.id);
    const open = openDetail.has(leaf.id);
    return `<div class="row lvl-3${highlighted === leaf.id ? " hl" : ""}" style="--depth:${depth}" data-id="${esc(leaf.id)}" role="treeitem">
      <span class="twisty" data-twisty="${esc(leaf.id)}" aria-label="Details">${open ? "▾" : "▸"}</span>
      <input type="checkbox" data-leaf="${esc(leaf.id)}" ${selected.has(leaf.id) ? "checked" : ""} ${writable ? "" : "disabled"}
        aria-label="Select ${esc(leaf.name)}">
      <span class="name" data-zoom="${esc(leaf.id)}" title="${esc(leaf.id)}">${esc(leaf.name)}${tagsHtml(leaf, v)}
        ${crumb ? `<div class="crumb">${esc(T.breadcrumb(tree, leaf.id))}</div>` : ""}</span>
      ${stateHtml(v.state)}
      <span class="meta">${leaf.buildings != null ? fmtInt(leaf.buildings) : ""}</span></div>
      ${open ? detailHtml(leaf, depth) : ""}`;
  }

  function optionsText(o) {
    if (!o) return "";
    return [o.include_imagery === false ? "no imagery" : o.include_imagery ? "imagery" : null,
            o.paid_referee ? "paid referee" : null].filter(Boolean).join(", ");
  }

  function detailHtml(leaf, depth) {
    const { view, state } = views.get(leaf.id);
    const rough = T.roughSeconds(leaf.buildings, status.calibration);
    const facts = [];
    if (leaf.buildings != null) facts.push(["Buildings", fmtInt(leaf.buildings) + (leaf.area_km2 != null ? ` · ${fmtArea(leaf.area_km2)}` : "")]);
    if (rough != null && target === "local") facts.push(["Rough build time", "≈ " + fmtDur(rough) + " (estimate gives the real figure)"]);
    facts.push(["Status", stateHtml(state)]);
    if (view.built_at) facts.push(["Last built", esc(fmtDate(view.built_at)) + (view.options ? ` · ${esc(optionsText(view.options))}` : "")]);
    if (view.n != null) facts.push(["Result", `${fmtInt(view.n)} buildings${view.kwp != null ? ` · ${fmtInt(Math.round(view.kwp))} kWp` : ""}`]);
    if (view.stale) facts.push(["Model", '<span class="warn">Built with an older solar model; rebuild with Yield only to refresh.</span>']);
    if (view.staged) facts.push(["Staged", esc(fmtDate(view.staged.staged_at)) + (state === "built" ? ' <span class="warn">(older than the latest build)</span>' : "")]);
    if (view.live) facts.push(["Live", "In the active local release " + esc(view.live)]);
    if (leaf.grid) {
      const g = leaf.grid;
      const n = Object.keys(g.cells).length;
      const ready = g.built_cells === n && !g.active_cells;
      facts.push(["Grid", `${g.grid_size} × ${g.grid_size}: ${fmtInt(g.built_cells)} of ${fmtInt(n)} estimated cells built
        ${g.active_cells ? ` · ${g.active_cells} active` : ""}
        <button class="small" data-combine="${esc(leaf.id)}" ${writable && ready && target === "local" ? "" : "disabled"}
          title="${ready ? "Build the locality whole; it reuses the cells' downloads" : "Available when every estimated cell has built"}">Combine</button>`]);
    }
    const links = [];
    if (view.preview_url) links.push(`<a href="${esc(view.preview_url)}" target="_blank" rel="noopener">${target === "published" ? "Open live map" : "Open preview"}</a>`);
    const history = view.history.slice(0, 8).map((h) => `<tr>
        <td>${esc(fmtDate(h.at))}</td><td><span class="pill ${esc(h.status)}">${esc(h.status)}</span></td>
        <td>${esc(optionsText(h))}</td><td>${esc(h.git || "")}</td>
        <td>${h.report_url ? `<a href="${esc(h.report_url)}" target="_blank" rel="noopener">report</a>` : ""}
          ${h.job_id ? `<button class="small job-title" data-job="${esc(h.job_id)}">job</button>` : ""}</td></tr>`).join("");
    return `<div class="detail" style="--depth:${depth}">
      <dl>${facts.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>
      ${links.length ? `<div class="links">${links.join(" ")}</div>` : ""}
      ${history ? `<table><tr><th>Attempt</th><th>Status</th><th>Options</th><th>Code</th><th></th></tr>${history}</table>
        ${view.history.length > 8 ? `<div class="muted">${view.history.length - 8} older attempt(s) not shown.</div>` : ""}`
        : `<div class="muted">No ${esc(TARGETS[target].label.toLowerCase())} attempts recorded.</div>`}</div>`;
  }

  function renderNode(node, depth, out, autoOpen) {
    const isGroup = node.kind !== "locality" && node.kind !== "cell" || (node.children && node.children.length);
    if (!isGroup) {
      if (visible(node)) out.push(leafRow(node, depth, false));
      return;
    }
    const leaves = T.leavesOf(node).filter(visible);
    if (!leaves.length) return;
    const open = expanded.has(node.id) || autoOpen;
    out.push(groupRow(node, depth, leaves, open));
    if (!open) return;
    if (node.kind === "locality") out.push(detailHtml(node, depth + 1));
    node.children.forEach((child) => renderNode(child, depth + 1, out, autoOpen));
  }

  function render() {
    const sort = $("sort").value;
    const out = [];
    const all = [...tree.leaves.values()].filter((l) => !(l.children && l.children.length));
    const shown = all.filter(visible);
    if (sort === "geo") {
      const autoOpen = filtering() && shown.length <= AUTO_EXPAND_LIMIT;
      tree.nodes.forEach((n) => renderNode(n, 0, out, autoOpen));
    } else {
      const items = shown.map((leaf) => Object.assign({ leaf }, views.get(leaf.id)));
      items.sort((a, b) => T.SORTS[sort](a, b) || String(a.leaf.name).localeCompare(String(b.leaf.name)));
      items.slice(0, FLAT_ROW_LIMIT).forEach((i) => out.push(leafRow(i.leaf, 0, true)));
      if (items.length > FLAT_ROW_LIMIT) {
        out.push(`<p class="muted" style="padding:6px 10px">Showing the first ${FLAT_ROW_LIMIT} of ${fmtInt(items.length)};
          search or filter to narrow the list.</p>`);
      }
    }
    if (!out.length) {
      out.push(`<p class="muted" style="padding:10px">${tree.leaves.size ? "No areas match the search and filters."
        : writable ? "Loading areas…" : "Open the link printed by tools/pipeline_web.py to list areas."}</p>`);
    }
    const box = $("tree");
    const focused = document.activeElement;
    const focusKey = focused && box.contains(focused)
      ? ["data-leaf", "data-group", "data-zoom", "data-twisty"].find((a) => focused.hasAttribute(a)) : null;
    const focusValue = focusKey && focused.getAttribute(focusKey);
    box.innerHTML = out.join("");
    box.querySelectorAll("[data-indet]").forEach((cb) => { cb.indeterminate = true; });
    if (focusKey) {
      const again = box.querySelector(`[${focusKey}="${CSS.escape(focusValue)}"]`);
      if (again) again.focus({ preventScroll: true });
    }
    renderLegend(all);
    renderActions();
    syncMap();
  }

  function renderLegend(all) {
    const counts = {};
    all.forEach((l) => { const s = views.get(l.id).state; counts[s] = (counts[s] || 0) + 1; });
    $("status-legend").innerHTML = `<b>${esc(TARGETS[target].label)} status</b>` + T.STATES.map((s) =>
      `<button class="item${hiddenStates.has(s) ? " off" : ""}" data-state="${s}" aria-pressed="${!hiddenStates.has(s)}"
         title="Show or hide ${esc(T.STATE_LABELS[s].toLowerCase())} areas in the list and map">
        <span class="dot" style="background:${T.STATE_COLOURS[s]}"></span>${esc(T.STATE_LABELS[s])}
        <span class="muted" style="margin-left:auto">${fmtInt(counts[s] || 0)}</span></button>`).join("") +
      (remoteMeta ? `<div class="muted">${remoteMeta}</div>` : "");
  }

  $("status-legend").addEventListener("click", (e) => {
    const b = e.target.closest("[data-state]");
    if (!b) return;
    const s = b.dataset.state;
    if (hiddenStates.has(s)) hiddenStates.delete(s); else hiddenStates.add(s);
    render();
  });

  function revealRow(id) {
    (tree.parents.get(id) || []).forEach((p) => expanded.add(p.id));
    highlighted = id;
    if (hiddenStates.size || activeFilters.size || query) {
      const leaf = tree.leaves.get(id);
      if (leaf && !visible(leaf)) $("act-notes").textContent = "That area is hidden by the current search or filters.";
    }
    render();
    const row = $("tree").querySelector(`[data-id="${CSS.escape(id)}"]`);
    if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function toggleLeaves(leaves) {
    if (!writable) return;
    const on = !leaves.every((l) => selected.has(l.id));
    leaves.forEach((l) => (on ? selected.add(l.id) : selected.delete(l.id)));
    if (on && target !== "local") leaves.filter((l) => l.kind === "cell").forEach((l) => selected.delete(l.id));
  }

  const findNode = (id) => tree.leaves.get(id) || (function search(nodes) {
    for (const n of nodes) {
      if (n.id === id) return n;
      if (n.children && n.kind !== "locality") { const hit = search(n.children); if (hit) return hit; }
    }
    return null;
  })(tree.nodes);

  $("tree").addEventListener("click", (e) => {
    const el = e.target.closest("[data-twisty], [data-zoom], [data-combine], [data-job]");
    if (!el) return;
    if (el.dataset.twisty) {
      const node = findNode(el.dataset.twisty);
      const set = node && (node.kind === "cell" || (node.kind === "locality" && !(node.children && node.children.length)))
        ? openDetail : expanded;
      const id = el.dataset.twisty;
      if (set.has(id)) set.delete(id); else set.add(id);
      if (node && node.grid && set.has(id)) showGrid(node);
      render();
    } else if (el.dataset.zoom) {
      const node = findNode(el.dataset.zoom);
      if (node) { highlighted = node.id; zoomToNode(node); render(); }
    } else if (el.dataset.combine) {
      openBuild([el.dataset.combine], { combine: true });
    } else if (el.dataset.job) {
      showJob(el.dataset.job);
    }
  });

  $("tree").addEventListener("change", (e) => {
    const el = e.target;
    if (el.dataset.leaf) {
      if (el.checked) selected.add(el.dataset.leaf); else selected.delete(el.dataset.leaf);
    } else if (el.dataset.group) {
      const node = findNode(el.dataset.group);
      if (!node) return;
      const leaves = T.leavesOf(node).filter(visible);
      if (T.checkState(node, selected, visible) === "all") leaves.forEach((l) => selected.delete(l.id));
      else toggleLeaves(leaves.filter((l) => !selected.has(l.id)));
    }
    render();
  });

  // ---- tools: search, filters, sort ------------------------------------
  $("filters").innerHTML = T.FILTERS.map(([key, label]) =>
    `<button class="chip" data-filter="${key}" aria-pressed="false">${esc(label)}</button>`).join("");
  $("filters").addEventListener("click", (e) => {
    const b = e.target.closest("[data-filter]");
    if (!b) return;
    const key = b.dataset.filter;
    if (activeFilters.has(key)) activeFilters.delete(key); else activeFilters.add(key);
    b.classList.toggle("on", activeFilters.has(key));
    b.setAttribute("aria-pressed", activeFilters.has(key));
    render();
  });
  let searchTimer = null;
  $("q").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { query = $("q").value.trim(); render(); }, 150);
  });
  $("sort").value = localStorage.getItem("pipelineSort") || "geo";
  $("sort").addEventListener("change", () => { localStorage.setItem("pipelineSort", $("sort").value); render(); });
  $("collapse-all").addEventListener("click", () => { expanded.clear(); openDetail.clear(); render(); });
  $("select-matching").addEventListener("click", () => {
    const leaves = [...tree.leaves.values()].filter((l) => !(l.children && l.children.length) && visible(l))
      .filter((l) => target === "local" || l.kind !== "cell");
    if (leaves.length > 200 && !confirm(`Select all ${fmtInt(leaves.length)} matching areas?`)) return;
    leaves.forEach((l) => selected.add(l.id));
    render();
  });

  // ---- selection bar ---------------------------------------------------
  const selectedLeaves = () => [...selected].map((id) => tree.leaves.get(id)).filter(Boolean);
  function activeJobsFor(id) {
    return PipelineProgress.matchingJobsForRegion(queueJobs, [], id, target)
      .filter((j) => j.status === "queued" || j.status === "running");
  }
  function renderActions() {
    const leaves = selectedLeaves();
    const buildings = leaves.reduce((n, l) => n + (l.buildings || 0), 0);
    const rough = leaves.reduce((n, l) => n + (T.roughSeconds(l.buildings, status.calibration) || 0), 0);
    const counts = {};
    leaves.forEach((l) => { const s = views.get(l.id).state; counts[s] = (counts[s] || 0) + 1; });
    const stageable = leaves.filter((l) => l.kind !== "cell" && views.get(l.id).view.built);
    const cancellable = leaves.filter((l) => activeJobsFor(l.id).length);
    $("sel-sum").innerHTML = leaves.length
      ? `<details><summary><b>${fmtInt(leaves.length)} selected</b> · ${fmtInt(buildings)} buildings
          ${target === "local" && rough ? ` · <span title="Processing time only; Build… adds download time and size">≈ ${fmtDur(rough)} compute</span>` : ""}
          · ${T.STATES.filter((s) => counts[s]).map((s) => `${counts[s]} ${T.STATE_LABELS[s].toLowerCase()}`).join(", ")}</summary>
          <div style="max-height:140px;overflow:auto">${leaves.map((l) => `<div>
            <button class="small" data-unselect="${esc(l.id)}" aria-label="Remove ${esc(l.name)}">✕</button>
            <span class="name" data-zoom-sel="${esc(l.id)}">${esc(l.name)}</span>
            <span class="crumb">${esc(T.breadcrumb(tree, l.id))}</span> ${stateHtml(views.get(l.id).state)}</div>`).join("")}</div></details>`
      : '<span class="muted">Tick areas in the list or click them on the map.</span>';
    $("act-build").disabled = !writable || !leaves.length;
    $("act-stage").disabled = !writable || !stageable.length || target !== "local" || pushing;
    $("act-stage").title = target !== "local" ? "Staging is local; switch the target to Local."
      : "Capture a local snapshot of each built region, then stage it. Grid cells are never staged.";
    $("act-cancel").disabled = !writable || !cancellable.length;
    $("act-clear").disabled = !leaves.length;
  }

  $("sel-sum").addEventListener("click", (e) => {
    const rm = e.target.closest("[data-unselect]");
    const zoom = e.target.closest("[data-zoom-sel]");
    if (rm) { selected.delete(rm.dataset.unselect); render(); }
    else if (zoom) { const leaf = tree.leaves.get(zoom.dataset.zoomSel); if (leaf) { zoomToNode(leaf); revealRow(leaf.id); } }
  });
  $("act-clear").addEventListener("click", () => { selected.clear(); render(); });
  $("act-build").addEventListener("click", () => openBuild([...selected], {}));
  $("act-cancel").addEventListener("click", async () => {
    const jobs = new Map();
    selectedLeaves().forEach((l) => activeJobsFor(l.id).forEach((j) => jobs.set(j.job_id, j)));
    const running = [...jobs.values()].filter((j) => j.status === "running").length;
    if (!confirm(`Cancel ${jobs.size} job(s)${running ? `, including ${running} running (finished regions are kept)` : ""}?`)) return;
    let failed = 0;
    for (const id of jobs.keys()) {
      try { await api(`jobs/${encodeURIComponent(id)}/cancel`, { method: "POST", body: {} }); } catch (e) { failed++; }
    }
    $("act-notes").textContent = failed ? `${failed} job(s) could not be cancelled.` : `Cancelled ${jobs.size} job(s).`;
    loadQueue();
    loadStatus();
  });

  // Push to staging: capture then stage each built region, one at a time.
  let pushing = false;
  async function followOperation(id) {
    for (;;) {
      const op = await api(`snapshots/operations/${encodeURIComponent(id)}`);
      if (op.status !== "running") {
        if (op.status !== "succeeded") throw new Error(op.error || op.status);
        return op;
      }
      await sleep(750);
    }
  }
  $("act-stage").addEventListener("click", async () => {
    const leaves = selectedLeaves();
    const regions = leaves.filter((l) => l.kind !== "cell" && views.get(l.id).view.built);
    const skipped = leaves.length - regions.length;
    if (!confirm(`Capture and stage ${regions.length} built region(s) locally?` +
      (skipped ? ` ${skipped} selected item(s) are not built or are grid cells and will be skipped.` : "") +
      " Builds must be idle; each copy uses extra disk.")) return;
    pushing = true;
    snapshotBusy = true;
    renderActions();
    const failures = [];
    for (const [i, leaf] of regions.entries()) {
      $("act-notes").textContent = `Staging ${i + 1} of ${regions.length}: ${leaf.name}…`;
      try {
        const capture = await followOperation((await api("snapshots", { method: "POST", body: { region: leaf.id } })).operation_id);
        await followOperation((await api("staging", { method: "POST", body: { snapshot_id: capture.snapshot_id } })).operation_id);
      } catch (e) {
        failures.push(`${leaf.name}: ${e.message}`);
        if (e.status === 409 && /active|busy/i.test(e.message)) break;
      }
    }
    pushing = false;
    snapshotBusy = false;
    $("act-notes").innerHTML = failures.length
      ? `<span class="block">${failures.length} not staged.</span> ${failures.map(esc).join("; ")}`
      : `Staged ${regions.length} region(s). Assemble a release in <a href="#config">Config</a>.`;
    statusSignature = "";
    loadStatus();
  });

  // ---- build dialog ----------------------------------------------------
  let build = null;  // {ids, combine, revision, estimates, batches}
  let buildRevision = 0;

  function openBuild(ids, { combine }) {
    if (!writable || !ids.length) return;
    build = { ids, combine: !!combine };
    $("build-title").textContent = combine
      ? `Combine ${tree.leaves.get(ids[0]).name}: build it whole`
      : `Build ${ids.length} selected area(s) · ${TARGETS[target].label}`;
    $("build-grid").hidden = target !== "local" || combine || !ids.some((id) => (tree.leaves.get(id) || {}).kind === "locality");
    if (combine) $("opt-grid").value = "off";
    $("opt-referee").disabled = !$("opt-imagery").checked;
    const dialog = $("build-dialog");
    if (!dialog.open) dialog.showModal();
    estimateBuild();
  }

  ["opt-imagery", "opt-referee", "opt-grid"].forEach((id) => $(id).addEventListener("change", () => {
    if (!$("opt-imagery").checked) $("opt-referee").checked = false;
    $("opt-referee").disabled = !$("opt-imagery").checked;
    if (build) estimateBuild();
  }));
  $("build-cancel").addEventListener("click", () => { buildRevision++; $("build-dialog").close(); });
  $("build-dialog").addEventListener("close", () => { buildRevision++; build = null; });

  // Batches for /api/estimate: whole regions by up to max_regions, and one
  // grid selection per locality (new grids list every cell).
  async function planBatches(ids, gridChoice) {
    const regions = [];
    const selections = new Map();
    for (const id of ids) {
      const leaf = tree.leaves.get(id);
      if (!leaf) continue;
      if (leaf.kind === "cell") {
        const parent = tree.leaves.get(leaf.parent_region);
        const key = leaf.parent_region + "@" + parent.grid.grid_size;
        if (!selections.has(key)) {
          selections.set(key, { parent_region: leaf.parent_region, parent_linz_id: parent.grid.parent_linz_id || parent.linz_id,
                                grid_size: parent.grid.grid_size, grid_ids: [] });
        }
        selections.get(key).grid_ids.push(leaf.grid_id);
        continue;
      }
      const size = target !== "local" || gridChoice === "off" || leaf.linz_id == null ? null
        : gridChoice === "auto" ? T.autoGridSize(leaf.buildings) : Number(gridChoice);
      if (!size) { regions.push(id); continue; }
      const fc = await api(`grid-cells?region=${encodeURIComponent(id)}&linz_id=${leaf.linz_id}&grid_size=${size}`);
      selections.set(id + "@" + size, { parent_region: id, parent_linz_id: leaf.linz_id, grid_size: fc.grid_size || size,
                                        grid_ids: fc.features.map((f) => f.properties.grid_id) });
    }
    const batches = [];
    for (let i = 0; i < regions.length; i += maxRegions) batches.push({ regions: regions.slice(i, i + maxRegions) });
    selections.forEach((selection) => batches.push({ selection }));
    return batches;
  }

  async function estimateBuild() {
    const revision = ++buildRevision;
    const current = () => revision === buildRevision && build;
    $("start-btn").disabled = true;
    $("build-foot-note").textContent = "";
    const options = { target, include_imagery: $("opt-imagery").checked, paid_referee: $("opt-referee").checked };
    const progress = (text) => { $("estimate").innerHTML = `<p class="muted">${esc(text)}</p><progress></progress>`; };
    progress("Planning…");
    const estimates = [];
    const errors = [];
    try {
      const batches = await planBatches(build.ids, build.combine ? "off" : $("opt-grid").value);
      for (const [i, batch] of batches.entries()) {
        if (!current()) return;
        progress(`Estimating batch ${i + 1} of ${batches.length}… (counting buildings with LINZ can take a minute for new areas; grid cells are counted one by one)`);
        try {
          const { estimate_id } = await api("estimate", { method: "POST", body: Object.assign({}, batch, options) });
          let est;
          for (;;) {
            est = await api("estimate/" + estimate_id);
            if (est.status !== "pending") break;
            await sleep(ESTIMATE_POLL_MS);
            if (!current()) return;
          }
          if (est.status === "error") throw new Error(est.error);
          estimates.push(est);
        } catch (e) {
          errors.push(`Batch ${i + 1}: ${e.message}`);
        }
      }
    } catch (e) {
      errors.push(e.message);
    }
    if (!current()) return;
    build.estimates = estimates;
    renderEstimates(estimates, errors);
  }

  function rowLabel(r) {
    if (r.grid_size) {
      const parent = tree.leaves.get(r.parent_region);
      return `${parent ? parent.name : r.parent_region} · cell ${(r.grid_ids || []).join(", ")} (${r.grid_size}×${r.grid_size})`;
    }
    const leaf = tree.leaves.get(r.region);
    return leaf ? `${leaf.name} <span class="crumb">${esc(T.breadcrumb(tree, r.region))}</span>` : esc(r.region);
  }

  function renderEstimates(estimates, errors) {
    const ok = estimates.filter((e) => !(e.blockers || []).length);
    const blocked = estimates.length - ok.length;
    const rows = estimates.flatMap((est) => est.regions.map((r) => `<tr>
        <td>${r.grid_size ? esc(rowLabel(r)) : rowLabel(r)}${r.elevation === "synthetic" ? " <span class='warn'>(no LiDAR)</span>" : ""}
          ${(r.warnings || []).map((w) => `<div class="warn">${esc(w)}</div>`).join("")}
          ${(r.blockers || []).map((w) => `<div class="block">${esc(w)}</div>`).join("")}</td>
        <td class="num">${fmtInt(r.buildings)}</td><td class="num">${fmtArea(r.bbox_km2)}</td>
        <td class="num">${fmtGb(r.download_gb)}</td><td class="num">${fmtDur(r.total_s)}</td></tr>`)).join("");
    const sum = (f) => ok.reduce((n, e) => n + (f(e) || 0), 0);
    const jobs = sum((e) => e.regions.length);
    const totals = { buildings: sum((e) => e.totals.buildings), download: sum((e) => e.totals.download_gb),
                     time: sum((e) => e.totals.total_s), disk: sum((e) => e.totals.disk_gb) };
    const cost = Math.round(sum((e) => e.cloud && e.cloud.cost_nzd) * 100) / 100;
    const first = estimates[0];
    const referee = sum((e) => e.paid_referee && e.paid_referee.usd_upper);
    const empty = estimates.flatMap((e) => (e.selection && e.selection.empty_grid_ids) || []);
    let costBlock = "";
    if (first && target === "local") {
      costBlock = `<div class="muted">Disk about ${fmtGb(totals.disk)} (${esc(first.machine.disk_free_gb)} GB free). Cloud cost: $0 (runs on this computer).</div>`;
    } else if (first) {
      const c = first.cloud || {};
      costBlock = `<div class="cost"><b>Cloud cost estimate: up to NZD ${esc(cost)}</b> for ${fmtInt(ok.length)} batch(es)
        <div>${esc(c.cost_text)}</div><div class="muted">${esc(c.cost_basis)}</div>
        <div class="muted">VM ${esc(c.project)}/${esc(c.zone)}/${esc(c.vm)} is <b>${esc(c.vm_status || "unknown")}</b>; it builds <b>${esc(c.ref)}</b> as pushed.
          ${target === "published" ? "<b>Publishes to the live map if the deploy gate passes.</b>" : ""}</div>
        <p><b>This is an estimate, not a spending cap; actual charges can be higher.</b></p>
        <label>Type <b>yes</b> to accept the cost: <input type="text" id="cost-yes" autocomplete="off" size="5"></label></div>`;
    }
    $("estimate").innerHTML = `
      ${errors.map((e) => `<div class="block">${esc(e)}</div>`).join("")}
      ${estimates.length ? `<table><tr><th>Area / cell</th><th class="num">Buildings</th><th class="num">Footprint</th><th class="num">Download</th><th class="num">Time</th></tr>
        ${rows}<tr><th>Total (startable)</th><th class="num">${fmtInt(totals.buildings)}</th><th></th>
        <th class="num">${fmtGb(totals.download)}</th><th class="num">${fmtDur(totals.time)}</th></tr></table>` : ""}
      ${empty.length ? `<div class="muted">${empty.length} empty grid cell(s) skipped.</div>` : ""}
      ${referee ? `<div class="muted">Paid referee: up to US$${esc(Math.round(referee * 100) / 100)}.</div>` : ""}
      ${costBlock}
      ${first ? `<div class="muted">Rate ${esc(first.calibration.seconds_per_building)} s/building from ${first.calibration.source === "history"
        ? esc(first.calibration.runs_used) + " recent runs" : "defaults"}. ${esc(first.disclaimer)}</div>` : ""}
      ${estimates.flatMap((e) => e.warnings || []).map((w) => `<div class="warn">${esc(w)}</div>`).join("")}
      ${estimates.flatMap((e) => e.blockers || []).map((w) => `<div class="block">${esc(w)}</div>`).join("")}
      ${blocked ? `<p class="block">${blocked} batch(es) are blocked and will not be queued.</p>` : ""}`;
    $("build-foot-note").textContent = jobs ? `${fmtInt(jobs)} job(s), run one after another; each can be cancelled.` : "";
    $("start-btn").textContent = TARGETS[target].start;
    const yes = $("cost-yes");
    const update = () => { $("start-btn").disabled = !jobs || (target !== "local" && (!yes || yes.value.trim().toLowerCase() !== "yes")); };
    if (yes) yes.addEventListener("input", update);
    update();
  }

  $("start-btn").addEventListener("click", async () => {
    if (!build || !build.estimates) return;
    const ok = build.estimates.filter((e) => !(e.blockers || []).length);
    const accepted = (($("cost-yes") || {}).value || "").trim().toLowerCase() === "yes";
    if (target !== "local" && !accepted) return;
    const combine = build.combine;
    $("start-btn").disabled = true;
    const errors = [];
    let queued = 0;
    for (const est of ok) {
      try {
        const res = await api("jobs", { method: "POST", body: {
          estimate_id: est.estimate_id,
          include_imagery: est.options.include_imagery,
          paid_referee: est.options.paid_referee,
          cloud_cost_acknowledged: est.target === "local" || accepted,
          target: est.target,
          cost_nzd: est.cloud ? est.cloud.cost_nzd : undefined } });
        queued += (res.job_ids || []).length;
      } catch (e) {
        errors.push(e.message);
      }
    }
    if (errors.length) {
      $("estimate").insertAdjacentHTML("beforeend", errors.map((e) => `<p class="block">${esc(e)}</p>`).join(""));
      $("start-btn").disabled = false;
    } else {
      $("build-dialog").close();
    }
    if (queued) {
      $("act-notes").textContent = `Queued ${queued} job(s).`;
      if (!combine) selected.clear();
      $("stage-queue").open = true;
      statusSignature = "";
      loadQueue();
      loadStatus();
    }
  });

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
    queueJobs = Object.values(lanes).flatMap((lane) => lane.jobs || []);
    recentQueueJobs = Object.values(lanes).flatMap((lane) => lane.recent || []);
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
    $("queue-list").querySelectorAll("[data-map-region]").forEach((button) =>
      button.addEventListener("click", () => showAreaOnMap(button.dataset.mapRegion)));
    $("queue-list").querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () =>
      cancelJob(b.dataset.q, b.dataset.running === "true", b).then(loadQueue)));
    if ($("stage-jobs").open) loadJobs();
    renderActions();
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
    loadStatus();
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
    $("job-list").innerHTML = history.slice(0, 50).map((j) => `
      <div class="item"><h3 class="job-area-titles">${PipelineProgress.renderAreaTitles(j)}
        <span class="pill ${esc(j.target || "local")}">${esc(j.target || "local")}</span> <span class="pill ${esc(j.status)}">${esc(j.status)}</span></h3>
        <div class="muted">${esc(j.finished || j.created)} · ${esc(j.job_id)}</div>
        <button class="job-title small" data-job="${esc(j.job_id)}">Details / log</button></div>`).join("") || "<p class='muted'>No completed jobs yet.</p>";
    $("job-list").querySelectorAll("[data-job]").forEach((el) => el.addEventListener("click", () => showJob(el.dataset.job)));
    $("job-list").querySelectorAll("[data-map-region]").forEach((button) =>
      button.addEventListener("click", () => showAreaOnMap(button.dataset.mapRegion)));
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
      loadRemote(false);
      loadStatus();
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
      <h3 class="job-area-titles">${PipelineProgress.renderAreaTitles(job)}
        <span class="pill ${esc(job.target || "local")}">${esc(job.target || "local")}</span>
        <span class="pill ${esc(job.status)}">${esc(job.status)}</span></h3>
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
    el.querySelectorAll("[data-map-region]").forEach((button) =>
      button.addEventListener("click", () => showAreaOnMap(button.dataset.mapRegion)));
  }

  // ---- start-up --------------------------------------------------------
  async function init() {
    try {
      const health = await fetch("/api/health").then((r) => r.json());
      maxRegions = health.max_regions || maxRegions;
      if (!token) {
        banner("Read-only: open the link printed by <code>python3 tools/pipeline_web.py</code> to list areas and start builds.");
      } else {
        await api("jobs");  // verifies the token
        writable = true;
        if (!health.linz_key) banner("No LINZ API key found (environment, .env or Secret Manager): estimates for new areas will fail.");
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
    showView();
    if (writable) {
      await loadStatus();
      await loadCatalog();
      loadGeometry();
      const snapshotOperation = sessionStorage.getItem("snapshotOperation");
      if (snapshotOperation) followSnapshot(snapshotOperation);
      loadJobs();
      loadQueue();
      setInterval(() => { loadQueue(); loadStatus(); }, QUEUE_POLL_MS);
      setInterval(() => { loadVm(false); loadRemote(false); }, VM_POLL_MS);
    } else {
      render();
    }
  }
  init();
})();
