// "How this map is made": the methodology diagram behind the bottom-left
// button.
//
// Everything it shows comes from methodology.json, which src/methodology.py
// writes from the code itself: config values, the build's stage plan
// (src/build_plan.py), and LINZ's own metadata for every layer the build
// reads (data/source_metadata.json). tests/test_methodology.py fails when the
// file differs from what the code would write now, when a build stage is
// missing from the diagram, or when a named function or file no longer
// exists. This file only draws; it holds no facts.
(function () {
  "use strict";
  const REPO = "https://github.com/rewiring-nz/nz-solar-potential/blob/main/";
  let data = null, box = null, current = 0;

  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function codeLink(ref) {
    const [path, sym] = ref.split("::");
    return `<a href="${REPO}${esc(path)}" target="_blank" rel="noopener"><code>${esc(path)}</code></a>` +
      (sym ? ` <code>${esc(sym)}</code>` : "");
  }

  function itemHtml(it) {
    const facts = (it.facts || []).map(f =>
      `<tr><th>${esc(f.label)}</th><td>${esc(f.value)}</td><td class="mth-src">${esc(f.source)}</td></tr>`).join("");
    const code = (it.code || []).map(c => `<li>${codeLink(c)}</li>`).join("");
    const stages = (it.stages || []).length
      ? `<p class="mth-stages">Build step${it.stages.length > 1 ? "s" : ""}: ${it.stages.map(s => `<code>${esc(s)}</code>`).join(", ")}</p>` : "";
    const detail = facts || code || stages
      ? `<details class="mth-more"><summary>Details</summary>` +
        (facts ? `<table class="mth-facts"><thead><tr><th>What</th><th>Value</th><th>Where it comes from</th></tr></thead><tbody>${facts}</tbody></table>` : "") +
        stages + (code ? `<p class="mth-code-h">In the code</p><ul class="mth-code">${code}</ul>` : "") +
        `</details>` : "";
    return `<div class="mth-item"><h4>${esc(it.title)}</h4><p>${esc(it.text)}</p>${detail}</div>`;
  }

  function sourcesHtml() {
    const rows = (data.sources || []).map(s =>
      `<tr><td><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a></td>` +
      `<td>${esc(s.used_for)}</td><td>${esc(s.resolution || "")}</td>` +
      `<td>${s.accuracy ? esc(s.accuracy) : '<span class="mth-dim">not stated by LINZ</span>'}</td></tr>`).join("");
    return `<details class="mth-more mth-sources"><summary>Every data layer the build reads (${(data.sources || []).length})</summary>` +
      `<div class="mth-scroll"><table class="mth-facts"><thead><tr><th>Layer</th><th>Used for</th><th>Resolution</th><th>LINZ's stated accuracy</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
  }

  function render() {
    const steps = data.steps;
    const flow = steps.map((s, i) =>
      (i ? `<div class="mth-arrow" aria-hidden="true">&#8594;</div>` : "") +
      `<button type="button" class="mth-step${i === current ? " on" : ""}" data-i="${i}" aria-pressed="${i === current}">` +
      `<span class="mth-n">${i + 1}</span><span class="mth-ic" aria-hidden="true">${s.icon || ""}</span>` +
      `<span class="mth-t">${esc(s.title)}</span><span class="mth-s">${esc(s.summary)}</span></button>`).join("");
    const s = steps[current];
    box.querySelector(".mth-body").innerHTML =
      `<p class="mth-intro">${esc(data.intro)}</p>` +
      `<div class="mth-flow" role="group" aria-label="Steps">${flow}</div>` +
      `<section class="mth-detail"><h3><span class="mth-n">${current + 1}</span> ${esc(s.title)}</h3>` +
      s.items.map(itemHtml).join("") + `</section>` + sourcesHtml() +
      `<p class="mth-foot">${esc(data.footer)}</p>`;
    box.querySelectorAll(".mth-step").forEach(b => b.addEventListener("click", () => {
      current = +b.dataset.i; render();
      box.querySelector(".mth-detail").scrollIntoView({ block: "nearest", behavior: "smooth" });
    }));
  }

  async function open() {
    if (!box) {
      box = document.createElement("div");
      box.id = "methodology-lightbox";
      box.setAttribute("role", "dialog");
      box.setAttribute("aria-modal", "true");
      box.setAttribute("aria-label", "How this map is made");
      box.innerHTML = `<div class="mth-card"><div class="mth-head"><h2>How this map is made</h2>` +
        `<button type="button" class="mth-close" aria-label="Close">&times;</button></div><div class="mth-body"><p>Loading&hellip;</p></div></div>`;
      document.body.appendChild(box);
      box.addEventListener("click", e => { if (e.target === box) close(); });
      box.querySelector(".mth-close").addEventListener("click", close);
      document.addEventListener("keydown", e => { if (e.key === "Escape" && !box.hidden) close(); });
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

  function close() { if (box) box.hidden = true; }

  window.openMethodology = open;
  document.addEventListener("DOMContentLoaded", () => {
    const b = document.getElementById("open-methodology");
    if (b) b.addEventListener("click", open);
  });
})();
