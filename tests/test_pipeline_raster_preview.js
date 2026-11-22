"use strict";
const assert = typeof require === "function" ? require("node:assert/strict") : {
  match(value, pattern) { if (!pattern.test(value)) throw new Error("Missing " + pattern + " in " + value); },
  doesNotMatch(value, pattern) { if (pattern.test(value)) throw new Error("Unexpected " + pattern + " in " + value); },
  equal(value, expected) { if (value !== expected) throw new Error(value + " !== " + expected); },
};
const R = typeof require === "function" ? require("../pipeline_raster_preview.js") : globalThis.PipelineRasterPreview;
const MapLayers = typeof require === "function" ? require("../map_layers.js") : globalThis.MapLayers;

const assets = [
  { item_id: "CC11", collection_id: "national", asset_key: "visual", cache_state: "cached",
    size_bytes: 432269026, regions: ["council/town/one", "council/town/two"] },
  { item_id: "<script>", collection_id: "other", asset_key: "visual", cache_state: "missing",
    size_bytes: null, regions: ["<img>"] },
  { item_id: "CC12", collection_id: "national", asset_key: "visual", cache_state: "unversioned" },
  { item_id: "CC13", collection_id: "national", asset_key: "visual", cache_state: "unknown" },
  { item_id: "CC14", collection_id: "national", asset_key: "visual", cache_state: "recovery_required" },
];
const html = R.renderAssetList(assets, 2);
assert.match(html, /5 native tile\(s\) in estimate 2/);
assert.match(html, /Cached and validated/);
assert.match(html, /Missing/);
assert.match(html, /Unversioned; requires transfer/);
assert.match(html, /Not inspected/);
assert.match(html, /Recovery required/);
assert.match(html, /council\/town\/one, council\/town\/two/);
assert.match(html, /different estimates may list the same tile/i);
assert.match(html, /&lt;script&gt;/);
assert.match(html, /&lt;img&gt;/);
assert.doesNotMatch(html, /<script>|<img>/);
assert.equal(R.renderAssetList([], 1), "");
const layers = R.mapLayers(assets.map((asset) => Object.assign({ bbox_wgs84: [170, -45, 171, -44] }, asset)));
assert.equal(layers.length, 3);
assert.equal(layers[0].data.features.length, 1);
assert.equal(layers[1].data.features.length, 1);
assert.equal(layers[2].data.features.length, 3);
assert.equal(layers[0].data.features[0].properties.regions, "council/town/one, council/town/two");
assert.equal(layers[2].data.features[2].properties.state, "recovery_required");
assert.equal(layers[0].group, "source");
assert.equal(layers[0].data.features[0].geometry.coordinates[0][0][0], 170);
assert.equal(MapLayers.validCatalogue({ version: 1, layers }).layers.length, 3);
console.log("pipeline raster preview tests passed");
