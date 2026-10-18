"use strict";

const statusUrl = new URL("status.json", document.currentScript.src);
const cancelWorkflow = "https://github.com/rewiring-nz/nz-solar-potential/actions/workflows/manage-developer-run.yml";
const activeStatuses = new Set(["queued", "running", "gating", "validating", "publishing", "cancelling"]);

function addCell(row, value) {
  const cell = document.createElement("td");
  cell.textContent = value || "—";
  row.append(cell);
}

function addLink(cell, href, label) {
  const link = document.createElement("a");
  link.href = href;
  link.textContent = label;
  cell.append(link);
}

async function refresh() {
  try {
    const response = await fetch(`${statusUrl}?t=${Date.now()}`, {cache: "no-store"});
    if (!response.ok) throw new Error(`Status request failed (${response.status})`);
    const data = await response.json();
    const body = document.getElementById("runs");
    body.replaceChildren();
    for (const run of data.runs || []) {
      const row = document.createElement("tr");
      addCell(row, run.user);
      const summary = document.createElement("td");
      const id = document.createElement("code");
      id.textContent = run.run_id || "";
      summary.append(id, document.createElement("br"));
      summary.append(document.createTextNode(run.regions || "—"));
      row.append(summary);
      addCell(row, run.git_branch);
      const commitCell = document.createElement("td");
      commitCell.className = "commit";
      if (run.commit) {
        addLink(
          commitCell,
          `https://github.com/rewiring-nz/nz-solar-potential/commit/${encodeURIComponent(run.commit)}`,
          run.commit,
        );
        if (run.commit_date) {
          commitCell.append(document.createElement("br"));
          commitCell.append(document.createTextNode(run.commit_date));
        }
      } else {
        commitCell.textContent = "—";
      }
      row.append(commitCell);
      const stale = activeStatuses.has(run.status) && run.updated_at &&
        Date.now() - Date.parse(run.updated_at) > 120000;
      addCell(row, [stale ? `${run.status} (heartbeat stale)` : run.status, run.stage]
        .filter(Boolean).join(" — "));
      addCell(row, run.started_at ? new Date(run.started_at).toLocaleString() : "—");
      const actions = document.createElement("td");
      if (run.log_url) addLink(actions, run.log_url, "Log");
      if (activeStatuses.has(run.status)) {
        if (actions.childNodes.length) actions.append(document.createTextNode(" · "));
        addLink(actions, cancelWorkflow, "Request stop (GitHub login)");
      }
      if (run.preview_url) {
        if (actions.childNodes.length) actions.append(document.createTextNode(" · "));
        addLink(actions, run.preview_url, "Preview");
      }
      row.append(actions);
      body.append(row);
    }
    document.getElementById("updated").textContent =
      `Last updated: ${data.updated_at ? new Date(data.updated_at).toLocaleString() : "unknown"}. Refreshes every 15 seconds.`;
    document.getElementById("error").textContent = "";
  } catch (error) {
    document.getElementById("error").textContent = error.message;
  }
}

refresh();
setInterval(refresh, 15000);
