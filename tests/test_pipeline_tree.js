"use strict";
// Region tree for the Build data page: run with `node tests/test_pipeline_tree.js`.
const assert = require("node:assert/strict");
const T = require("../pipeline_tree.js");

const catalog = [
  { region: "qldc/queenstown/fernhill", linz_id: 2, name: "Fernhill", type: "Suburb", town: "Queenstown",
    council: "Queenstown-Lakes District", ta_slug: "qldc", town_slug: "queenstown", buildings: 1200,
    bbox: [168.62, -45.05, 168.63, -45.04], area_km2: 1.2 },
  { region: "qldc/queenstown/sunshine-bay", linz_id: 1, name: "Sunshine Bay", type: "Suburb", town: "Queenstown",
    council: "Queenstown-Lakes District", ta_slug: "qldc", town_slug: "queenstown", buildings: 249,
    bbox: [168.61, -45.05, 168.62, -45.04], area_km2: 0.8 },
  { region: "akl/auckland/ponsonby", linz_id: 9, name: "Ponsonby", type: "Suburb", town: "Auckland",
    council: "Auckland", ta_slug: "akl", town_slug: "auckland", buildings: 4000,
    bbox: [174.74, -36.86, 174.75, -36.85], area_km2: 2 },
];
const cell = "qldc/queenstown/fernhill/grid-0123456789abcdef";
const grids = {
  "qldc/queenstown/fernhill": { grid_size: 2, parent_linz_id: 2, built_cells: 1, active_cells: 0,
    cells: { r00c00: { region: cell, buildings: 600, built: true } } },
};

const tree = T.buildTree(catalog, ["pilot", "qldc/queenstown/sunshine-bay", cell], grids);
assert.deepEqual(tree.nodes.map((n) => n.name), ["Auckland", "Queenstown-Lakes District", "Other local areas"]);
assert.equal(tree.nodes[2].id, T.OTHER_ID);
assert.deepEqual(tree.nodes[2].children.map((c) => c.id), ["pilot"], "grid ids and catalogue ids are not 'other'");
assert.equal(T.breadcrumb(tree, "qldc/queenstown/sunshine-bay"), "Queenstown-Lakes District › Queenstown");
const fernhill = tree.leaves.get("qldc/queenstown/fernhill");
assert.equal(fernhill.children.length, 1);
assert.equal(tree.leaves.get(cell).kind, "cell");
assert.equal(tree.leaves.get(cell).grid_id, "r00c00");
assert.equal(T.breadcrumb(tree, cell), "Queenstown-Lakes District › Queenstown › Fernhill");
assert.deepEqual(T.leavesOf(tree.nodes[1]).map((l) => l.id), [cell, "qldc/queenstown/sunshine-bay"],
  "a gridded locality selects through its cells");

const all = () => true;
assert.equal(T.checkState(tree.nodes[1], new Set(), all), "none");
assert.equal(T.checkState(tree.nodes[1], new Set([cell]), all), "some");
assert.equal(T.checkState(tree.nodes[1], new Set([cell, "qldc/queenstown/sunshine-bay"]), all), "all");
assert.equal(T.checkState(tree.nodes[1], new Set([cell]), (l) => l.id === cell), "all", "only visible leaves count");

assert.ok(T.matchesQuery(tree, tree.leaves.get("akl/auckland/ponsonby"), "auck pons"));
assert.ok(!T.matchesQuery(tree, tree.leaves.get("akl/auckland/ponsonby"), "queenstown"));
assert.ok(T.matchesQuery(tree, tree.leaves.get("qldc/queenstown/sunshine-bay"), "lakes"));

assert.equal(T.autoGridSize(5000), null);
assert.equal(T.autoGridSize(12001), 2);
assert.equal(T.autoGridSize(100000), 3);
assert.equal(T.autoGridSize(5e6), 10);
assert.equal(T.roughSeconds(100, { fixed_overhead_s: 60, seconds_per_building: 2 }), 260);
assert.equal(T.roughSeconds(null, { fixed_overhead_s: 60, seconds_per_building: 2 }), null);

const ok = { status: "succeeded", target: "local", include_imagery: true, at: "2026-10-01" };
const base = { built: true, built_at: "2026-10-01", history: [ok], options: { include_imagery: true } };
const view = (status, target = "local", remote) => T.rowView(status, remote, target);
assert.equal(T.lifecycle(view({})), "none");
assert.equal(T.lifecycle(view(base)), "built");
assert.equal(T.lifecycle(view({ ...base, history: [{ status: "running" }, ok] })), "running");
assert.equal(T.lifecycle(view({ ...base, history: [{ status: "queued", target: "local" }, ok] })), "queued");
assert.equal(T.lifecycle(view({ ...base, history: [{ status: "failed" }, ok] })), "failed");
assert.equal(T.lifecycle(view({ ...base, history: [{ status: "cancelled" }, ok] })), "built");
assert.equal(T.lifecycle(view({ ...base, staged: { built_at: "2026-10-01" } })), "staged");
assert.equal(T.lifecycle(view({ ...base, staged: { built_at: "2026-09-01" } })), "built", "a newer build is not staged yet");
assert.equal(T.lifecycle(view({ ...base, live: "r3" })), "published");
assert.equal(T.lifecycle(view({ ...base, history: [{ status: "running", target: "developer" }] })), "built",
  "attempts on another target do not move the local state");
assert.equal(T.lifecycle(view({ history: [{ status: "running", target: "developer" }] }, "developer", {})), "running");
assert.equal(T.lifecycle(view({}, "developer", { state: "built" })), "built");
assert.equal(T.lifecycle(view({}, "published", { state: "built", data_version: 7 })), "published");

const pass = (key, v) => T.passesFilters(v, T.lifecycle(v), new Set([key]));
assert.ok(pass("built", view(base)));
assert.ok(!pass("built", view({})));
assert.ok(pass("never", view({})));
assert.ok(pass("with-imagery", view(base)));
assert.ok(!pass("no-imagery", view(base)));
assert.ok(pass("not-staged", view(base)));
assert.ok(pass("stale", view({ ...base, stale: true })));
assert.ok(T.passesFilters(view(base), "built", new Set()), "no filter passes everything");

const items = catalog.map((c) => ({ leaf: c, view: { built_at: c.buildings === 249 ? "2026-10-02" : null, latest: null },
  state: "none" }));
assert.equal(items.slice().sort(T.SORTS.completed)[0].leaf.name, "Sunshine Bay");
assert.equal(items.slice().sort(T.SORTS.buildings)[0].leaf.name, "Ponsonby");
assert.equal(T.STATES.every((s) => T.STATE_COLOURS[s] && T.STATE_LABELS[s]), true);

console.log("pipeline tree tests passed");
