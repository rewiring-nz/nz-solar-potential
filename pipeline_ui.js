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
    if (config) { loadCatalogInfo(); loadStaging(); loadCloudOperations(); }
    else setTimeout(() => map.resize(), 0);
  }

  // Staging copies local emitted outputs (data/out), independently of target.
  let artifactBusy = false;
  let cloudRunning = false;
  let cloudHistoryLoading = false;
  let currentStaged = [];
  let cloudUploadSummary = null;
  let releaseState = null;
  let releaseRecords = [];
  async function loadCloudOperations() {
    if (!writable || cloudHistoryLoading) return;
    cloudHistoryLoading = true;
    try {
      const {operations} = await api("operations");
      cloudRunning = operations.some((operation) => operation.status === "running");
      $("cloud-push-history").innerHTML = operations.length ? operations.map((operation) => {
        const ids = (operation.value && operation.value.staging_ids) || [];
        const totalBytes = operation.value && operation.value.upload_summary && operation.value.upload_summary.bytes;
        let detail = operation.error || "";
        if (operation.status === "running") {
          if (operation.phase === "verifying_local") detail = "Verifying local staged copy";
          else if (operation.phase === "uploading") detail = `Uploading: ${operation.uploaded_artifacts || 0} of ${operation.artifact_count || 0} artifacts`;
          else if (operation.phase === "verifying_remote") detail = `Verifying cloud copy (${operation.verification_step === "before_manifest" ? "before manifest" : "final check"}): ${operation.verified_artifacts || 0} of ${operation.artifact_count || 0} artifacts`;
          else detail = "Starting cloud push";
          if (operation.staging_id) detail += ` · ${operation.staging_id}`;
        } else if (operation.status === "succeeded") {
          detail = ((operation.result && operation.result.submissions) || []).map((item) => item.region_id || item.submission_id).join(", ") + ": verified; not deployed";
        } else {
          detail += " Resolve the error, then retry the same staged version; matching uploaded objects are reused.";
        }
        return `<div class="item"><b class="${operation.status === "failed" || operation.status === "interrupted" ? "block" : ""}">${esc(operation.status)}</b> · ${esc(operation.started_at || "unknown time")}
          <div>${esc(detail)}</div><div class="muted">${totalBytes == null ? "" : `${fmtGb(totalBytes / 1_000_000_000)} · `}${esc(ids.length)} area(s)</div>
          <div class="muted">Last update: ${esc(operation.finished_at || operation.progress_at || operation.started_at || "unknown")}</div></div>`;
      }).join("") : '<p class="muted">No cloud pushes recorded in this checkout.</p>';
      const latest = operations[0];
      const message = cloudRunning ? "Cloud push in progress." : latest
        ? `Latest cloud push ${latest.status}${latest.error ? `: ${latest.error}` : "."}` : "";
      if ($("cloud-push-status").textContent !== message) $("cloud-push-status").textContent = message;
      $("act-cloud-status").textContent = cloudRunning ? cloudProgress(operations.find((item) => item.status === "running"))
        : latest && ["failed", "interrupted"].includes(latest.status)
          ? `Latest remote staging upload ${latest.status}: ${latest.error || "check Config for details."}`
          : latest && latest.status === "succeeded" ? "Latest remote staging upload verified; it was not deployed." : "";
    } catch (error) {
      $("cloud-push-status").textContent = `Cloud push status unavailable: ${error.message}. Showing last known history; reconnecting.`;
    } finally {
      cloudHistoryLoading = false;
      releaseButtons();
    }
  }

  function cloudProgress(operation) {
    if (!operation) return "Remote staging upload in progress.";
    if (operation.phase === "verifying_local") return `Verifying local staged copy ${operation.staging_id || ""}…`;
    if (operation.phase === "uploading") return `Uploading ${operation.staging_id || ""}: ${operation.uploaded_artifacts || 0} of ${operation.artifact_count || 0} artifacts.`;
    if (operation.phase === "verifying_remote") return `Verifying remote copy ${operation.staging_id || ""} (${operation.verification_step === "before_manifest" ? "before manifest" : "final check"}): ${operation.verified_artifacts || 0} of ${operation.artifact_count || 0} artifacts.`;
    return "Remote staging upload in progress.";
  }
  async function loadStaging() {
    if (!writable) return;
    try {
      const staging = await api("staging");
      currentStaged = staging.staged;
      const councils = new Map();
      for (const item of currentStaged) {
        const council = (item.region_id || "Unknown council").split("/")[0];
        if (!councils.has(council)) councils.set(council, []);
        councils.get(council).push(item);
      }
      $("staging-list").innerHTML = currentStaged.length ? [...councils].map(([council, items]) => {
        const bytes = items.reduce((total, item) => total + (item.bytes || 0), 0);
        return `<details class="stage-council"><summary>${esc(council)} · ${items.length} area(s) · ${fmtGb(bytes / 1_000_000_000)}</summary>
          <div class="stage-council-areas">${items.map((item) => {
            const parts = (item.region_id || "").split("/");
            const area = parts.slice(1).join(" / ") || item.region_id || "Unknown area";
            const status = item.error ? "unreadable" : item.verification || "not checked";
            return `<details class="qrow"><summary><span class="qname">${esc(area)}</span>
              <span class="qsum">${fmtGb((item.bytes || 0) / 1_000_000_000)} · ${esc(status)}</span></summary>
              <div class="qbody"><div class="muted">Current staging ID: <span class="job-id">${esc(item.staging_id)}</span><br>
                Staged: ${esc(item.staged_at || "unknown")} · Built: ${esc(item.built_at || "unknown")}<br>
                ${fmtInt(item.bytes || 0)} bytes · ${fmtInt(item.artifact_count || 0)} artifacts</div>
                ${item.error ? `<p class="block">${esc(item.error)}</p>` : `<button class="small" data-verify-staged="${esc(item.staging_id)}" ${artifactBusy ? "disabled" : ""}>Verify current copy</button>`}</div>
            </details>`;
          }).join("")}</div></details>`;
      }).join("") : '<p class="muted">No areas in current local staging.</p>';
      $("staging-list").querySelectorAll("[data-verify-staged]").forEach((button) =>
        button.addEventListener("click", () => runOperation(
          `staging/${encodeURIComponent(button.dataset.verifyStaged)}/verify`, {})));
      await loadReleases();
    } catch (error) {
      $("staging-status").textContent = `Cannot load staged copies: ${error.message}`;
    }
  }

  async function followOperation(identifier) {
    artifactBusy = true;
    releaseButtons();
    $("staging-list").querySelectorAll("button").forEach((button) => { button.disabled = true; });
    try {
      let operation;
      do {
        operation = await api(`operations/${encodeURIComponent(identifier)}`);
        const labels = {
          stage: ["Copying and verifying local staging", "Staged and verified"],
          verify_staged: ["Verifying staged copy", "Verified staged copy"],
          cloud_submit: ["Pushing and verifying cloud Staging", "Cloud submission verified"],
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
        const statusElement = operation.action.startsWith("release_") ? $("release-status") : $("staging-status");
        const batchProgress = operation.action === "stage_batch"
          ? `${operation.completed_regions || 0} of ${operation.region_count || 0} areas staged${operation.region_id ? ` · ${operation.region_id}` : ""}.`
          : null;
        statusElement.textContent = operation.status === "running"
          ? operation.action === "cloud_submit"
            ? cloudProgress(operation)
            : operation.action === "stage_batch" ? `Updating current local staging set: ${batchProgress}` : `${label[0]}...`
          : operation.status === "succeeded"
            ? operation.action.startsWith("release_")
              ? `${operation.action.replace("release_", "")}: completed locally.`
              : operation.action === "cloud_submit"
                ? `Cloud Staging verified: ${(operation.result.submissions || []).map((s) => `${s.region_id} (${s.submission_id})`).join(", ")}. Not approved or deployed.`
                : operation.action === "stage_batch"
                  ? `Current local staging set updated with ${(operation.result.regions || []).length} area(s).`
                : `${label[1]} ${operation.region_id || ""} ${operation.staging_id}: ${fmtInt(operation.artifact_count)} artifacts, ${fmtInt(operation.bytes)} bytes.`
            : `${operation.status}: ${operation.error || "Operation did not complete."}`;
        if (operation.action === "stage_batch") $("act-notes").textContent = statusElement.textContent;
        if (operation.result && operation.action.startsWith("release_")) renderReleaseResult(operation);
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
      $("staging-status").textContent = `Status unavailable: ${error.message}. Refresh to reconnect.`;
    } finally {
      artifactBusy = false;
      await loadStaging();
    }
  }

  function selectedCurrentStaging() {
    return selectedLeaves().map((leaf) => ({leaf, staged: views.get(leaf.id).view.staged}))
      .filter(({staged}) => staged && !staged.error && staged.staging_id);
  }

  function cloudPushButtons() {
    const count = selectedCurrentStaging().length;
    $("act-cloud-stage").disabled = !writable || artifactBusy || cloudRunning || !count || count > 100 || target !== "local";
    $("act-cloud-stage").title = count > 100 ? "Remote staging uploads are limited to 100 areas per confirmation." :
      target !== "local" ? "Remote staging uses the current local staging set; switch the target to Local." :
        "Upload selected areas' current local staged versions to private remote staging.";
  }

  $("act-cloud-stage").addEventListener("click", async () => {
    if (!writable || artifactBusy || cloudRunning) return;
    const selectedVersions = selectedCurrentStaging();
    if (!selectedVersions.length || selectedVersions.length > 100) return;
    const ids = selectedVersions.map(({staged}) => staged.staging_id);
    $("act-cloud-status").textContent = "Verifying selected current staged versions…";
    try {
      cloudUploadSummary = await api("staging/cloud-summary", {method: "POST", body: {staging_ids: ids}});
      const summary = cloudUploadSummary;
      $("cloud-confirm-summary").innerHTML = `<p><b>Confirm upload of ${(summary.bytes / 1_000_000_000).toFixed(2)} GB</b>
        across ${summary.staging.length} area(s) to private GCS staging?</p>
        <p>Destination: <code>${esc(summary.destination)}</code><br>
        ${summary.staging.map((item) => `${esc(item.region_id)} · ${(item.bytes / 1_000_000_000).toFixed(2)} GB`).join("<br>")}</p>`;
      $("cloud-confirm-dialog").showModal();
    } catch (error) {
      cloudUploadSummary = null;
      $("act-cloud-status").textContent = `Remote staging upload not started: ${error.message}`;
    }
  });

  $("cloud-confirm-cancel").addEventListener("click", () => {
    cloudUploadSummary = null;
    $("cloud-confirm-dialog").close();
    $("act-cloud-status").textContent = "Remote staging upload cancelled.";
  });

  $("cloud-confirm-upload").addEventListener("click", async () => {
    if (!cloudUploadSummary || artifactBusy || cloudRunning) return;
    const ids = cloudUploadSummary.staging.map((item) => item.staging_id);
    $("cloud-confirm-dialog").close();
    artifactBusy = true;
    releaseButtons();
    $("act-cloud-status").textContent = "Starting remote staging upload…";
    try {
      await api("staging/cloud-submit", {method: "POST", body: {
        staging_ids: ids, upload_confirmed: true,
      } });
      cloudRunning = true;
      cloudUploadSummary = null;
      await loadCloudOperations();
    } catch (error) {
      $("act-cloud-status").textContent = `Remote staging upload not started: ${error.message}`;
    } finally {
      artifactBusy = false;
      releaseButtons();
    }
  });

  async function runOperation(path, body) {
    if (!writable || artifactBusy || cloudRunning) return;
    artifactBusy = true;
    releaseButtons();
    const status = path.startsWith("releases/") ? $("release-status") : $("staging-status");
    status.textContent = "Starting local operation...";
    try {
      const operation = await api(path, { method: "POST", body });
      sessionStorage.setItem("artifactOperation", operation.operation_id);
      await followOperation(operation.operation_id);
    } catch (error) {
      status.textContent = `Operation refused: ${error.message}`;
    } finally {
      artifactBusy = false;
      await loadStaging();
    }
  }

  $("staging-refresh").addEventListener("click", async () => {
    if (artifactBusy) return;
    const operation = sessionStorage.getItem("artifactOperation");
    if (operation) await followOperation(operation);
    else await loadStaging();
  });

  function releaseButtons() {
    const disabled = !writable || artifactBusy || cloudRunning || !releaseState;
    const stageable = selectedLeaves().filter((leaf) => !stageRefusal(leaf));
    $("act-stage").disabled = !writable || target !== "local" || artifactBusy || cloudRunning || pushing ||
      !stageable.length || stageable.length > 100;
    if (stageable.length > 100) $("act-stage").title = "Push no more than 100 areas per local staging batch.";
    $("act-build").disabled = !writable || !selectedLeaves().length || artifactBusy || cloudRunning;
    $("staging-list").querySelectorAll("[data-verify-staged]").forEach((button) => {
      button.disabled = !writable || artifactBusy || cloudRunning;
    });
    $("release-create").disabled = disabled || !currentStaged.some((item) => !item.error);
    $("release-select").disabled = disabled || !releaseRecords.length;
    document.querySelectorAll("[data-release-action]").forEach((button) => {
      button.disabled = disabled || !$("release-select").value;
    });
    $("release-policy").disabled = disabled;
    $("release-cleanup-preview").disabled = disabled || !releaseState.policy;
    $("release-cleanup-apply").disabled = disabled || !releaseState.policy || !$("release-cleanup-confirm").checked;
    $("release-rollback").disabled = disabled || !releaseState.previous ||
      !releaseState.rollback_until || Date.parse(releaseState.rollback_until) <= Date.now();
    cloudPushButtons();
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
        : "No local deployment. The first candidate contains the current complete staged regions.";
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
    if (artifactBusy) return;
    const operation = sessionStorage.getItem("artifactOperation");
    if (operation) await followOperation(operation);
    else await loadReleases();
  });
  $("release-create").addEventListener("click", () => {
    if (releaseState) runOperation("releases/create", {
      staging_ids: currentStaged.filter((item) => !item.error).map((item) => item.staging_id),
      generation: releaseState.generation,
    });
  });
  document.querySelectorAll("[data-release-action]").forEach((button) =>
    button.addEventListener("click", () => {
      if (releaseState) runOperation(`releases/${button.dataset.releaseAction}`, {
        release_id: $("release-select").value, generation: releaseState.generation,
        reason: $("release-reason").value,
      });
    }));
  $("release-rollback").addEventListener("click", () => {
    if (releaseState) runOperation("releases/rollback", {generation: releaseState.generation});
  });
  $("release-policy").addEventListener("click", () => {
    const policy = {};
    document.querySelectorAll("[data-retention]").forEach((input) => { policy[input.dataset.retention] = Number(input.value); });
    runOperation("releases/policy", {policy});
  });
  $("release-cleanup-confirm").addEventListener("change", releaseButtons);
  $("release-cleanup-preview").addEventListener("click", () => runOperation("releases/cleanup", {apply:false}));
  $("release-cleanup-apply").addEventListener("click", () => {
    if ($("release-cleanup-confirm").checked) runOperation("releases/cleanup", {apply:true});
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
    scheduleDsmView();
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
    ...(MapNavigation.viewFromURL(location.href) || {}),
  });
  MapNavigation.bindLinks(map, document.querySelectorAll("[data-map-page]"),
    () => target === "local" ? "local" : "published");
  map.addControl(new maplibregl.NavigationControl(), "top-right");
  const layerLegend = new MapLayers.Legend({ beforeId: "localities-fill" });
  map.addControl(layerLegend, "top-right");
  let layerRequest = 0;
  let layersTarget = null;
  let dsmLayers = [];
  let selectedImageryAssets = [];
  let rasterQueueSignature = null;
  let rasterLayersCheckedAt = 0;
  let baseCatalogue = null;
  function applyLayers() {
    if (baseCatalogue && layersTarget === target) {
      layerLegend.setCatalogue({ ...baseCatalogue,
        layers: [...baseCatalogue.layers, ...(target === "local"
          ? [...dsmLayers, ...PipelineRasterPreview.imageryMapLayers(selectedImageryAssets, baseCatalogue)]
          : [])] }, location.href);
    }
  }
  async function loadLayers() {
    if (!writable) return;
    const source = target;
    const request = ++layerRequest;
    if (layersTarget !== source) {
      layerLegend.clearDatasets();
      layerLegend.setMessages([]);
      layersTarget = source;
      baseCatalogue = null;
    }
    try {
      const catalogue = await api("layers?source=" + source);
      if (request !== layerRequest || source !== target) return;
      baseCatalogue = catalogue;
      applyLayers();
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
  let dsmRevision = 0, dsmRunning = false, dsmTimer = null, dsmForceRefresh = false;
  function scheduleDsmView(refresh = false) {
    dsmRevision++;
    dsmForceRefresh = dsmForceRefresh || refresh;
    clearTimeout(dsmTimer);
    dsmLayers = [];
    applyLayers();
    $("dsm-status-box").hidden = target !== "local" || !writable;
    if (target !== "local" || !writable || !mapReady) return;
    if (map.getZoom() < 9) {
      $("dsm-status").textContent = "Zoom in to inspect national DSM tiles. Cached tile outlines remain visible.";
      return;
    }
    $("dsm-status").textContent = "Checking DSM tiles for this map view…";
    dsmTimer = setTimeout(runDsmView, 400);
  }
  async function runDsmView() {
    if (dsmRunning || !mapReady || target !== "local" || map.getZoom() < 9) return;
    const revision = dsmRevision;
    const bounds = map.getBounds();
    const bbox = [Math.max(-180, bounds.getWest()), Math.max(-90, bounds.getSouth()),
      Math.min(180, bounds.getEast()), Math.min(90, bounds.getNorth())];
    if (bbox[0] >= bbox[2] || bbox[1] >= bbox[3]) {
      $("dsm-status").textContent = "DSM tiles unavailable for this map extent.";
      return;
    }
    const refresh = dsmForceRefresh;
    dsmForceRefresh = false;
    dsmRunning = true;
    try {
      let offset = 0, checkedAt = null;
      const assets = [];
      for (;;) {
        const request = await api("dsm-view", { method: "POST",
          body: { bbox, refresh: refresh && offset === 0, offset } });
        let result;
        const deadline = Date.now() + 920000;
        do {
          await sleep(ESTIMATE_POLL_MS);
          result = await api("dsm-view/" + request.estimate_id);
          if (Date.now() > deadline) throw new Error("inspection timed out");
        } while (result.status === "pending");
        if (revision !== dsmRevision || target !== "local") return;
        if (result.status !== "ready") throw new Error(result.error || "inspection failed");
        if (checkedAt !== null && checkedAt !== result.metadata_checked_at) {
          scheduleDsmView();
          return;
        }
        checkedAt = result.metadata_checked_at;
        if (result.total > result.limit) {
          $("dsm-status").textContent = `${result.total} tiles in view; zoom in to inspect at most ${result.limit}.`;
          return;
        }
        assets.push(...result.assets);
        dsmLayers = PipelineRasterPreview.mapLayers(assets);
        applyLayers();
        offset += result.assets.length;
        $("dsm-status").textContent = `${offset} of ${result.total} national DSM tile(s) inspected. Metadata checked ${new Date(checkedAt * 1000).toLocaleString()}. ` +
          "Snapshot only; outlines are not valid-pixel coverage or necessarily the build source. Use Layers & legend to query.";
        if (offset >= result.total) break;
        if (!result.assets.length) throw new Error("inspection returned an incomplete batch");
      }
    } catch (error) {
      if (revision === dsmRevision && target === "local") {
        $("dsm-status").textContent = "DSM tiles unavailable: " + error.message + ". Use Refresh DSM metadata to retry.";
      }
    } finally {
      dsmRunning = false;
      if (revision !== dsmRevision) scheduleDsmView();
    }
  }
  $("dsm-refresh").addEventListener("click", () => scheduleDsmView(true));
  map.on("moveend", () => scheduleDsmView());

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
    scheduleDsmView();
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
  let selListOpen = false;
  let selSumHtml = null;
  $("sel-sum").addEventListener("toggle", (e) => {
    if (e.target.tagName === "DETAILS") selListOpen = e.target.open;
  }, true);
  // Mirrors the server rule (stage_refusal in tools/pipeline_webui/server.py):
  // a whole region, built, whose latest local build succeeded and which no
  // queued or running local job includes.
  function stageRefusal(leaf) {
    if (leaf.kind === "cell") return "grid cell";
    const view = views.get(leaf.id).view;
    if (view.staging_now) return "being staged";
    if (!view.built) return "not built";
    if (view.latest_local_status && view.latest_local_status !== "succeeded") return "last build " + view.latest_local_status;
    if (PipelineProgress.matchingJobsForRegion(queueJobs, [], leaf.id, "local")
      .some((j) => j.status === "queued" || j.status === "running")) return "in queue";
    return null;
  }
  function renderActions() {
    const leaves = selectedLeaves();
    const buildings = leaves.reduce((n, l) => n + (l.buildings || 0), 0);
    const rough = leaves.reduce((n, l) => n + (T.roughSeconds(l.buildings, status.calibration) || 0), 0);
    const counts = {};
    leaves.forEach((l) => { const s = views.get(l.id).state; counts[s] = (counts[s] || 0) + 1; });
    const stageable = leaves.filter((l) => !stageRefusal(l));
    const stageBatchTooLarge = stageable.length > 100;
    const cancellable = leaves.filter((l) => activeJobsFor(l.id).length);
    const html = leaves.length
      ? `<details><summary><b>${fmtInt(leaves.length)} selected</b> · ${fmtInt(buildings)} buildings
          ${target === "local" && rough ? ` · <span title="Processing time only; Build… adds download time and size">≈ ${fmtDur(rough)} compute</span>` : ""}
          · ${T.STATES.filter((s) => counts[s]).map((s) => `${counts[s]} ${T.STATE_LABELS[s].toLowerCase()}`).join(", ")}</summary>
          <div style="max-height:140px;overflow:auto">${leaves.map((l) => `<div>
            <button class="small" data-unselect="${esc(l.id)}" aria-label="Remove ${esc(l.name)}">✕</button>
            <span class="name" data-zoom-sel="${esc(l.id)}">${esc(l.name)}</span>
            <span class="crumb">${esc(T.breadcrumb(tree, l.id))}</span> ${stateHtml(views.get(l.id).state)}</div>`).join("")}</div></details>`
      : '<span class="muted">Tick areas in the list or click them on the map.</span>';
    // Status and queue polls call this every few seconds; rewriting identical
    // HTML would reset the list's scroll position.
    if (html !== selSumHtml) {
      $("sel-sum").innerHTML = html;
      selSumHtml = html;
      const list = $("sel-sum").querySelector("details");
      if (list) list.open = selListOpen;
    }
    $("act-build").disabled = !writable || !leaves.length;
    $("act-data-plan").disabled = !leaves.length;
    $("act-stage").disabled = !writable || !stageable.length || stageBatchTooLarge || target !== "local" || pushing || artifactBusy || cloudRunning;
    $("act-stage").title = stageBatchTooLarge ? "Push no more than 100 areas per local staging batch."
      : target !== "local" ? "Staging is local; switch the target to Local."
        : "Add each eligible area to the current local staging set; newer copies replace that area's current version.";
    $("act-cancel").disabled = !writable || !cancellable.length;
    $("act-clear").disabled = !leaves.length;
    cloudPushButtons();
  }

  $("sel-sum").addEventListener("click", (e) => {
    const rm = e.target.closest("[data-unselect]");
    const zoom = e.target.closest("[data-zoom-sel]");
    if (rm) { selected.delete(rm.dataset.unselect); render(); }
    else if (zoom) { const leaf = tree.leaves.get(zoom.dataset.zoomSel); if (leaf) { zoomToNode(leaf); revealRow(leaf.id); } }
  });
  $("act-clear").addEventListener("click", () => { selected.clear(); render(); });
  $("act-data-plan").addEventListener("click", () => {
    const areas = selectedLeaves().map((leaf) => ({
      id: leaf.id,
      name: leaf.name,
      breadcrumb: T.breadcrumb(tree, leaf.id),
    }));
    const url = new URL("dataset-plan-mock.html", location.href);
    url.searchParams.set("areas", JSON.stringify(areas));
    window.open(url, "_blank", "noopener");
  });
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

  // Add each eligible area to the current local staging set.
  let pushing = false;
  async function waitForOperation(id) {
    for (;;) {
      const op = await api(`operations/${encodeURIComponent(id)}`);
      $("act-notes").textContent = op.status === "running"
        ? `Updating local staging set: ${op.completed_regions || 0} of ${op.region_count || 0} areas` +
          (op.region_id ? ` · ${op.region_id}` : "") + "…"
        : op.status === "succeeded" ? `Current local staging set updated with ${(op.result.regions || []).length} area(s).`
          : `${op.status}: ${op.error || "Local staging did not complete."}`;
      if (op.status !== "running") {
        if (op.status !== "succeeded") throw new Error(op.error || op.status);
        return op;
      }
      await sleep(750);
    }
  }
  $("act-stage").addEventListener("click", async () => {
    if (!writable || artifactBusy || cloudRunning || pushing) return;
    const leaves = selectedLeaves();
    const skipped = {};
    const regions = leaves.filter((l) => {
      const why = stageRefusal(l);
      if (why) skipped[why] = (skipped[why] || 0) + 1;
      return !why;
    });
    const skippedText = Object.entries(skipped).map(([why, n]) => `${n} ${why}`).join(", ");
    if (!confirm(`Push ${regions.length} built area(s) to local staging?` +
      (skippedText ? ` Skipping: ${skippedText}.` : "") +
      " This adds or updates each area's current frozen copy; other staged areas remain.")) return;
    if (!regions.length) return;
    pushing = true;
    artifactBusy = true;
    renderActions();
    try {
      const operation = await api("staging", {method: "POST", body: {regions: regions.map((leaf) => leaf.id)}});
      sessionStorage.setItem("artifactOperation", operation.operation_id);
      await waitForOperation(operation.operation_id);
      if (skippedText) $("act-notes").textContent += ` Skipped: ${skippedText}.`;
      $("act-notes").innerHTML = `${esc($("act-notes").textContent)} The current set is visible in <a href="#config">Config</a>.`;
    } catch (error) {
      $("act-notes").textContent = `Local staging batch failed; the current set was not switched: ${error.message}`;
    } finally {
      pushing = false;
      artifactBusy = false;
      renderActions();
      statusSignature = "";
      loadStatus();
      if (location.hash === "#config") loadStaging();
    }
  });

  // ---- build dialog ----------------------------------------------------
  let build = null;  // {ids, combine, revision, estimates, batches}
  let buildRevision = 0;
  async function refreshImageryIndex() {
    if (!build || !build.estimates || target !== "local") return;
    const revision = ++buildRevision;
    const estimateIds = build.estimates.map((est) => est.estimate_id);
    $("start-btn").disabled = true;
    $("imagery-preview-refresh").disabled = true;
    $("estimate").innerHTML = "<p class='muted'>Refreshing imagery survey index…</p><progress></progress>";
    try {
      const started = await api("imagery-preview", { method: "POST",
        body: { estimate_ids: estimateIds, refresh: true } });
      let result;
      const deadline = Date.now() + 920000;
      do {
        await sleep(ESTIMATE_POLL_MS);
        if (!build || revision !== buildRevision) return;
        result = await api("estimate/" + started.estimate_id);
        if (Date.now() > deadline) throw new Error("imagery index refresh timed out");
      } while (result.status === "pending");
      if (!build || revision !== buildRevision) return;
      if (result.status !== "ready") throw new Error(result.error || "imagery index refresh failed");
      await estimateBuild();
    } catch (error) {
      if (build && revision === buildRevision) {
        $("estimate").innerHTML = `<div class="block">Imagery index refresh failed: ${esc(error.message)}.</div>`;
        $("imagery-preview-refresh").disabled = false;
      }
    }
  }

  function openBuild(ids, { combine }) {
    if (!writable || !ids.length) return;
    build = { ids, combine: !!combine, imageryChoices: {} };
    $("imagery-options").hidden = target !== "local";
    $("build-title").textContent = combine
      ? `Combine ${tree.leaves.get(ids[0]).name}: build it whole`
      : `Build ${ids.length} selected area(s) · ${TARGETS[target].label}`;
    $("build-grid").hidden = target !== "local" || combine || !ids.some((id) => (tree.leaves.get(id) || {}).kind === "locality");
    if (combine) $("opt-grid").value = "off";
    // Paid options start off every time the dialog opens; browsers may restore old form state.
    $("opt-referee").checked = false;
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
  $("imagery-preview-refresh").addEventListener("click", refreshImageryIndex);
  $("estimate").addEventListener("change", (event) => {
    const input = event.target.closest("select[data-imagery-region]");
    if (!input || !build) return;
    build.imageryChoices[input.dataset.imageryRegion] = Number(input.value);
    estimateBuild();
  });

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
    selectedImageryAssets = [];
    applyLayers();
    $("imagery-preview-refresh").disabled = true;
    $("build-foot-note").textContent = "";
    const options = { target, include_imagery: $("opt-imagery").checked, paid_referee: $("opt-referee").checked };
    if (target === "local" && options.include_imagery) options.imagery_choices = build.imageryChoices;
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
    selectedImageryAssets = estimates.flatMap(est => est.raster_plan?.batch?.assets || []);
    applyLayers();
    renderEstimates(estimates, errors);
    $("imagery-preview-refresh").disabled = !estimates.length;
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
          ${r.imagery_selected ? `<label>Build imagery
            <select data-imagery-region="${esc(r.region)}" aria-label="Build imagery for ${esc(r.region)}">
              ${(r.imagery_candidates || []).map((s, i) => `<option value="${s.imagery_id}" ${s.imagery_id === r.imagery_selected.imagery_id ? "selected" : ""}>${i === 0 ? "Recommended: " : ""}${esc(s.name)} · ${s.resolution_m} m · ${esc(s.flown_to)} · ${Math.round(s.coverage_fraction * 100)}% coverage</option>`).join("")}
            </select></label>
            <div class="muted">Selected imagery is pinned to this estimate and acquired through the native tile cache; processed at 0.1 m. Reference imagery remains a separate input when needed.</div>` : ""}
          ${(r.raster_sources || []).map((s) => `<div class="muted">${esc(s.role)}: ${esc(s.state === "mapped" ? "native tile cache" : s.state === "exports_fallback" ? "LINZ export" : s.state)}${s.layer_id ? ` (LINZ layer ${esc(s.layer_id)})` : ""}</div>`).join("")}
          ${(r.warnings || []).map((w) => `<div class="warn">${esc(w)}</div>`).join("")}
          ${(r.blockers || []).map((w) => `<div class="block">${esc(w)}</div>`).join("")}
          ${Number.isFinite(r.raster_metadata_checked_at) && Math.abs(r.raster_metadata_checked_at) <= 8.64e12 ? `<div class="muted">STAC metadata checked: ${esc(new Date(r.raster_metadata_checked_at * 1000).toISOString())}; rechecked at admission if overdue.</div>` : ""}
          ${r.raster_plan_sha256 ? `<div class="muted">Approved raster plan SHA-256: <code>${esc(r.raster_plan_sha256)}</code></div>` : ""}</td>
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
    const cacheOverviews = estimates.flatMap((e) => e.raster_cache_overview ? [e.raster_cache_overview.cache] : []);
    const cacheDetails = cacheOverviews.map((cache) => `<div class="muted">
      Native tile cache snapshot: ${fmtGb(cache.usage_bytes / 1e9)} of ${fmtGb(cache.budget_bytes / 1e9)};
      ${fmtGb(cache.leased_bytes / 1e9)} leased, ${fmtGb(cache.reclaimable_bytes / 1e9)} currently eligible,
      ${fmtGb(cache.unmanaged_bytes / 1e9)} unmanaged or awaiting recovery.
      These are logical bytes at inspection, not a reservation.
    </div>`).join("");
    const tileDetails = estimates.flatMap((e) => e.raster_tile_preview ? [e.raster_tile_preview] : [])
      .map((tiles) => `<div class="muted">
        Native tile plan: ${fmtInt(tiles.required_count)} unique required;
        ${fmtInt(tiles.cached_count)} cached, ${fmtInt(tiles.missing_count)} requiring transfer or recovery.
        Known missing: ${fmtGb(tiles.known_missing_bytes == null ? null : tiles.known_missing_bytes / 1e9)};
        unknown-size missing: ${fmtInt(tiles.unknown_missing_count)}.
        ${tiles.unknown_missing_count ? `Unknown-size transfer limit: ${fmtGb(tiles.unknown_asset_estimate_bytes / 1e9)} each.` : ""}
        ${tiles.cached_count == null ? "Regional download totals above remain export-based because no cache policy is configured." :
          "Regional download totals include each missing tile once per estimate; unknown sizes use the configured transfer limit."}
      </div>`).join("");
    const tileLists = estimates.map((estimate, index) =>
      PipelineRasterPreview.renderAssetList(
        estimate.raster_plan && estimate.raster_plan.batch && estimate.raster_plan.batch.assets,
        index + 1)).join("");
    const automatic = ok.flatMap((e) => e.regions.filter((r) => r.raster_auto_estimate));
    const autoDetails = automatic.map((r) => {
      const a = r.raster_auto_estimate;
      return `<div class="warn">Estimated allocations for ${esc(r.region)}: mosaics ${fmtGb(a.mosaic_bytes / 1e9)},
        scratch ${fmtGb(a.scratch_bytes / 1e9)}, output ${fmtGb(a.output_bytes / 1e9)},
        each export ${fmtGb(a.export_bytes / 1e9)}, other inputs ${fmtGb(a.other_bytes / 1e9)},
        temporary files ${fmtGb(a.scratch_bytes / 1e9)}.
        Cache: ${esc(a.cache_policy.directory)} (budget ${fmtGb(a.cache_policy.max_bytes / 1e9)},
        free-space reserve ${fmtGb(a.cache_policy.min_free_bytes / 1e9)});
        work/output: ${esc(a.work_dir)}; temporary: ${esc(a.temp_dir)}.</div>`;
    }).join("");
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
      ${cacheDetails}
      ${tileDetails}
      ${tileLists}
      ${autoDetails}
      ${first ? `<div class="muted">Rate ${esc(first.calibration.seconds_per_building)} s/building from ${first.calibration.source === "history"
        ? esc(first.calibration.runs_used) + " recent runs" : "defaults"}. ${esc(first.disclaimer)}</div>` : ""}
      ${estimates.flatMap((e) => e.warnings || []).map((w) => `<div class="warn">${esc(w)}</div>`).join("")}
      ${estimates.flatMap((e) => e.blockers || []).map((w) => `<div class="block">${esc(w)}</div>`).join("")}
      ${blocked ? `<p class="block">${blocked} batch(es) are blocked and will not be queued.</p>` : ""}`;
    $("build-foot-note").textContent = jobs ? `${fmtInt(jobs)} job(s), run one after another; each can be cancelled.` : "";
    $("start-btn").textContent = TARGETS[target].start;
    const yes = $("cost-yes");
    const update = () => { $("start-btn").disabled = !jobs ||
      (target !== "local" && (!yes || yes.value.trim().toLowerCase() !== "yes")); };
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
  // Polling re-renders the queue; remember which rows the user expanded.
  const openQueueJobs = new Set();
  $("queue-list").addEventListener("toggle", (e) => {
    const row = e.target.closest && e.target.closest("[data-qjob]");
    if (!row || row !== e.target) return;
    if (row.open) openQueueJobs.add(row.dataset.qjob); else openQueueJobs.delete(row.dataset.qjob);
  }, true);
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
    const localJobs = [...queueJobs, ...recentQueueJobs].filter(job => (job.target || "local") === "local");
    const rasterSignature = JSON.stringify(localJobs.map(job => [job.job_id, job.status]));
    if (target === "local" && (rasterSignature !== rasterQueueSignature ||
        (localJobs.some(job => job.status === "running") && Date.now() - rasterLayersCheckedAt > 15000))) {
      rasterQueueSignature = rasterSignature;
      rasterLayersCheckedAt = Date.now();
      loadLayers();
    }
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
      <div class="lane"><h3>${name === "local" ? "Local lane (this computer)" : "Cloud lane (shared VM)"}
        <span class="muted">· ${lane.jobs.length ? lane.jobs.length + " job(s)" : "idle"}</span></h3>
        ${lane.jobs.map(j => PipelineProgress.renderQueueCard(j, j.job_id === lane.active, writable,
          undefined, openQueueJobs.has(j.job_id))).join("")}
      </div>`).join("");
    $("queue-list").querySelectorAll("[data-job]").forEach((el) => el.addEventListener("click", () => showJob(el.dataset.job)));
    $("queue-list").querySelectorAll("[data-map-region]").forEach((button) =>
      button.addEventListener("click", () => showAreaOnMap(button.dataset.mapRegion)));
    $("queue-list").querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", (e) => {
      e.preventDefault();  // the button sits in the row summary; don't toggle the row
      cancelJob(b.dataset.q, b.dataset.running === "true", b).then(loadQueue);
    }));
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
  const openHistoryJobs = new Set();
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
    $("job-list").innerHTML = history.slice(0, 50).map((j) =>
      PipelineProgress.renderQueueCard(j, false, false, undefined, openHistoryJobs.has(j.job_id))
    ).join("") || "<p class='muted'>No completed jobs yet.</p>";
    $("job-list").querySelectorAll("[data-job]").forEach((el) => el.addEventListener("click", () => showJob(el.dataset.job)));
    $("job-list").querySelectorAll("[data-map-region]").forEach((button) =>
      button.addEventListener("click", () => showAreaOnMap(button.dataset.mapRegion)));
  }
  $("job-list").addEventListener("toggle", (e) => {
    const row = e.target.closest && e.target.closest("[data-qjob]");
    if (!row || row !== e.target) return;
    if (row.open) openHistoryJobs.add(row.dataset.qjob); else openHistoryJobs.delete(row.dataset.qjob);
  }, true);
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
      loadLayers();
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
      const artifactOperation = sessionStorage.getItem("artifactOperation");
      if (artifactOperation) followOperation(artifactOperation);
      loadCloudOperations();
      setInterval(loadCloudOperations, QUEUE_POLL_MS);
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
