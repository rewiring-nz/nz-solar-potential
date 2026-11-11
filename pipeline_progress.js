// Shared, side-effect-free progress presentation for the queue and job details.
(function (root) {
  "use strict";
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const terminal = (job) => ["succeeded", "failed", "cancelled", "interrupted"].includes(job.status);
  const fmtDur = (s) => {
    s = Math.max(0, Math.round(s));
    if (s < 90) return s + " s";
    if (s < 5400) return Math.round(s / 60) + " min";
    return (s / 3600).toFixed(1) + " h";
  };
  function regionTitle(region) {
    const parts = String(region).split("/");
    const grid = /^grid-/.test(parts[parts.length - 1]);
    if (grid) parts.pop();
    const pretty = (part) => part.replace(/-/g, " ").replace(/\b[a-z]/g, c => c.toUpperCase());
    const name = parts.slice(-2).reverse().map(pretty).join(", ");
    return name + (grid ? " (grid selection)" : "");
  }
  function jobTitle(job) {
    const regions = job.regions || [];
    return regions.length ? regionTitle(regions[0]) +
      (regions.length > 1 ? " + " + (regions.length - 1) + " more" : "") : "Build";
  }
  function renderProgress(job, now = Date.now() / 1000) {
    if (job.status === "queued") {
      return `<div class="muted">Queued${job.queue_position ? " #" + esc(job.queue_position) : ""} · ${
        esc(job.waiting || "Waiting for the " + (job.target === "local" || !job.target ? "local" : "cloud") + " lane")
      }${job.estimated_total_s != null ? "<br>Estimated runtime " + fmtDur(job.estimated_total_s) : ""}</div>`;
    }
    const done = terminal(job);
    const known = job.progress_known && Number.isFinite(job.progress);
    const percent = job.status === "succeeded" ? 100 : Math.min(99, Math.max(0, Math.round((job.progress || 0) * 100)));
    const label = "Build progress for " + jobTitle(job);
    const bar = done && job.status !== "succeeded" || job.waiting ? "" :
      `<progress max="100"${known || job.status === "succeeded" ? ` value="${percent}"` : ""}
        aria-label="${esc(label)}"></progress>`;
    const stage = done ? job.status.charAt(0).toUpperCase() + job.status.slice(1) :
      job.current_stage || "Preparing build";
    const area = job.area_number && (job.regions || []).length > 1 ?
      ` · Area ${esc(job.area_number)} of ${job.regions.length}` : "";
    const region = job.current_region && (job.regions || []).length > 1 ?
      `<div class="muted">${esc(regionTitle(job.current_region))}</div>` : "";
    const fraction = job.status === "succeeded" ? "100%" :
      known ? (done ? "Stopped at approximately " : "Approximately ") + percent + "%" :
      done ? "" : "Progress not yet measurable";
    const timing = [];
    if (job.elapsed_s != null) timing.push((done ? "Duration " : "Elapsed ") + fmtDur(job.elapsed_s));
    if (!done && !job.waiting) {
      if (job.eta_overdue || job.eta_s === 0) timing.push("Taking longer than estimated");
      else if (job.eta_s != null) timing.push("About " + fmtDur(job.eta_s) + " remaining (estimate)");
    }
    const activity = !done && job.last_activity_at != null ?
      `<div class="muted">Last activity ${fmtDur(now - job.last_activity_at)} ago</div>` : "";
    return `<div class="job-progress ${esc(job.status)}">
      <div>${esc(stage)}${area}</div>${region}${bar}
      <div class="muted">${fraction}</div>
      ${job.waiting ? `<div class="warn">${esc(job.waiting)}</div>` : ""}
      <div class="muted">${timing.join(" · ")}</div>${activity}</div>`;
  }
  function safeLink(url) {
    return typeof url === "string" && (/^https?:\/\//.test(url) || /^\/(?!\/)/.test(url)) ? esc(url) : null;
  }
  function renderQueueCard(job, active, writable, now) {
    const done = terminal(job);
    const cost = job.approved && job.approved.cloud_cost_nzd && job.approved.cloud_cost_nzd !== "0" ?
      "Approved estimate: up to NZD " + esc(job.approved.cloud_cost_nzd) : "";
    const preview = safeLink(job.preview_url) ||
      (job.status === "succeeded" && (job.target || "local") === "local" ? "/rooftop-solar.html" : null);
    return `<div class="item">
      ${writable && !done ? `<span class="row-actions"><button class="small" data-q="${esc(job.job_id)}"
        data-running="${active}">${active ? "Cancel" : "Remove"}</button></span>` : ""}
      <h3><button class="job-title" data-job="${esc(job.job_id)}">${esc(jobTitle(job))}</button></h3>
      <span class="pill ${esc(job.target || "local")}">${esc(job.target || "local")}</span>
      <span class="pill ${esc(job.status)}">${esc(job.status)}</span>
      <div class="muted job-id">${esc(job.job_id)}</div>
      ${renderProgress(job, now)}${cost ? `<div class="muted">${cost}</div>` : ""}
      ${job.error ? `<div class="block">${esc(job.error)}</div>` : ""}
      <div class="links">
        <button class="job-title small" data-job="${esc(job.job_id)}">${done && job.status !== "succeeded" ? "View error / log" : "Details / log"}</button>
        ${preview ? `<a href="${preview}" target="_blank" rel="noopener">Open preview</a>` : ""}
        ${(job.reports || []).filter(r => safeLink(r.url)).map(r =>
          `<a href="${safeLink(r.url)}" target="_blank" rel="noopener">${esc(regionTitle(r.region))} report</a>`).join("")}
      </div></div>`;
  }
  const api = { jobTitle, renderProgress, renderQueueCard };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PipelineProgress = api;
})(globalThis);
