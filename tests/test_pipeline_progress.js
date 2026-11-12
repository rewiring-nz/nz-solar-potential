"use strict";
const assert = typeof require === "function" ? require("node:assert/strict") : {
  match(value, pattern) { if (!pattern.test(value)) throw new Error("Missing " + pattern + " in " + value); },
  doesNotMatch(value, pattern) { if (pattern.test(value)) throw new Error("Unexpected " + pattern + " in " + value); },
  equal(value, expected) { if (value !== expected) throw new Error(value + " !== " + expected); },
};
const P = typeof require === "function" ? require("../pipeline_progress.js") : globalThis.PipelineProgress;

const job = {
  job_id: "abc", target: "local", status: "running",
  regions: ["christchurch-city/christchurch/christchurch-central", "x/y/riccarton"],
  current_stage: "Building panel layouts", area_number: 1,
  current_region: "christchurch-city/christchurch/christchurch-central",
  progress: 0.42, progress_known: true, elapsed_s: 1080, eta_s: 1500,
  last_activity_at: 195,
};
let html = P.renderProgress(job, 200);
assert.match(html, /value="42"/);
assert.match(html, /aria-label="Build progress for Christchurch Central, Christchurch \+ 1 more"/);
assert.match(html, /Approximately 42%/);
assert.match(html, /Building panel layouts · Area 1 of 2/);
assert.match(html, /Elapsed 18 min · About 25 min remaining \(estimate\)/);
assert.match(html, /Last activity 5 s ago/);

html = P.renderProgress({ ...job, status: "queued", queue_position: 2, estimated_total_s: 720 });
assert.doesNotMatch(html, /<progress|Elapsed|remaining/);
assert.match(html, /Queued #2 · Waiting for the local lane/);
assert.match(html, /Estimated runtime 12 min/);
assert.match(P.renderProgress({ ...job, target: "developer", status: "queued" }), /cloud lane/);

html = P.renderProgress({ ...job, progress_known: false });
assert.match(html, /<progress/);
assert.doesNotMatch(html, /value=|Approximately|42%/);
assert.match(html, /Progress not yet measurable/);
html = P.renderProgress({ ...job, waiting: "CLI lock", current_stage: "Waiting for workspace" });
assert.doesNotMatch(html, /<progress|remaining/);
assert.match(html, /CLI lock/);

html = P.renderProgress({ ...job, eta_s: 0, eta_overdue: true });
assert.match(html, /Taking longer than estimated/);
assert.doesNotMatch(html, /0 s remaining/);
html = P.renderProgress({ ...job, progress: 1, current_stage: "Combining map outputs" });
assert.match(html, /value="99"/);
assert.doesNotMatch(html, /100%/);
for (const status of ["failed", "cancelled", "interrupted"]) {
  html = P.renderProgress({ ...job, status, progress: 1 });
  assert.doesNotMatch(html, /<progress|100%|remaining|Last activity/);
  assert.match(html, /Stopped at approximately 99%/);
  html = P.renderQueueCard({ ...job, status }, false, true, 200);
  assert.match(html, /View error \/ log/);
  assert.doesNotMatch(html, /data-q=|Open preview/);
}
html = P.renderProgress({ ...job, status: "succeeded", progress: 1 });
assert.match(html, /value="100"/);
assert.match(html, /Duration 18 min/);
assert.doesNotMatch(html, /remaining|Last activity/);
assert.match(P.renderQueueCard({ ...job, status: "succeeded" }, false, true), /Open preview/);
assert.match(P.renderQueueCard(job, true, true), /data-map-region="christchurch-city\/christchurch\/christchurch-central"/);
assert.match(P.renderQueueCard(job, true, true), /aria-label="Show Christchurch Central, Christchurch on map"/);
assert.doesNotMatch(P.renderQueueCard(job, true, true), />Map:/);
assert.match(P.renderQueueCard(job, true, true), /data-map-region="x\/y\/riccarton"/);
assert.match(P.renderQueueCard(job, true, true), /data-running="true">Cancel/);
assert.doesNotMatch(P.renderQueueCard(job, true, false), /data-q=/);
assert.match(P.renderAreaTitles(job), /Christchurch Central, Christchurch/);
assert.match(P.renderAreaTitles(job), /data-map-region="x\/y\/riccarton"/);
assert.equal(P.jobTitle({ regions: ["x/queenstown/sunshine-bay/grid-abcdef"] }),
  "Sunshine Bay, Queenstown (grid selection)");
const areaJobs = [
  { job_id: "queued", target: "local", status: "queued",
    regions: ["queenstown-lakes-district/queenstown/sunshine-bay/grid-0123456789abcdef"] },
  { job_id: "running-other-target", target: "developer", status: "running",
    regions: ["queenstown-lakes-district/queenstown/sunshine-bay"] },
];
assert.equal(P.matchingJobsForRegion(areaJobs, [
  { job_id: "recent", target: "local", status: "succeeded",
    regions: ["queenstown-lakes-district/queenstown/sunshine-bay"] },
], "queenstown-lakes-district/queenstown/sunshine-bay", "local")[0].job_id, "queued");
assert.equal(P.matchingJobsForRegion([], [
  { job_id: "recent", target: "local", status: "succeeded",
    regions: ["queenstown-lakes-district/queenstown/sunshine-bay"] },
], "queenstown-lakes-district/queenstown/sunshine-bay", "local")[0].job_id, "recent");

html = P.renderQueueCard({ ...job, regions: ["<script>"], current_stage: "<img>",
  preview_url: "javascript:alert(1)", reports: [{ region: "<img>", url: "//evil.test" }] }, true, true);
assert.doesNotMatch(html, /<script>|<img>|javascript:|href="\/\/evil/);
assert.match(html, /&lt;img&gt;/);
console.log("pipeline progress presentation tests passed");
globalThis.pipelineProgressTestsPassed = true;
