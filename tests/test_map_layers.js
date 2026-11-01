(async function () {
  "use strict";
  const M = typeof require === "function" ? require("../map_layers.js") : globalThis.MapLayers;
  let passed = 0;
  const failures = [];
  function assert(condition, message) { if (!condition) throw new Error(message); }
  async function test(name, run) {
    try { await run(); passed++; console.log("PASS " + name); }
    catch (error) { failures.push(name + ": " + error.message); console.error("FAIL " + name, error); }
  }
  class MapStub {
    constructor() {
      this.sources = new Map();
      this.layers = [
        { id: "satellite" }, { id: "linz-aerial" },
        { id: "analysis-anchor" }, { id: "selection" },
      ];
    }
    getSource(id) { return this.sources.get(id); }
    addSource(id, source) { assert(!this.sources.has(id), "duplicate source"); this.sources.set(id, source); }
    removeSource(id) { this.sources.delete(id); }
    getLayer(id) { return this.layers.find(layer => layer.id === id); }
    addLayer(layer, before) {
      assert(!this.getLayer(layer.id), "duplicate layer");
      this.layers.push(layer);
      if (before) this.moveLayer(layer.id, before);
    }
    removeLayer(id) { this.layers = this.layers.filter(layer => layer.id !== id); }
    moveLayer(id, before) {
      const layer = this.getLayer(id);
      this.removeLayer(id);
      const at = before ? this.layers.findIndex(layer => layer.id === before) : this.layers.length;
      assert(at >= 0, "missing anchor");
      this.layers.splice(at, 0, layer);
    }
    setLayoutProperty(id, key, value) {
      const layer = this.getLayer(id);
      layer.layout = Object.assign(layer.layout || {}, { [key]: value });
    }
  }
  const fc = { type: "FeatureCollection", features: [] };
  const footprint = (id, group) => ({ id, group, label: id, representation: "footprint", data: fc });
  const catalogue = layers => ({ version: 1, layers, warnings: [] });
  const legend = () => {
    const control = new M.Legend({ beforeId: "analysis-anchor" });
    control.map = new MapStub();
    return control;
  };
  await test("legend orders panels above outlines above heatmap", () => {
    const entries = M.sorted([
      { id: "heatmap", group: "analysis", label: "Heat map" },
      { id: "outlines", group: "analysis", kind: "building", label: "Outlines" },
      { id: "panels", group: "analysis", kind: "panel", label: "Panels" },
      { id: "source", group: "source", label: "Source" },
      { id: "basemap", group: "basemap", label: "Basemap" },
      { id: "derived", group: "derived", label: "Derived" },
    ]);
    assert(entries.map(e => e.id).join() === "panels,outlines,heatmap,derived,source,basemap", "incorrect stack");
  });
  await test("safe URLs retain tile placeholders and deployment prefix", () => {
    assert(M.safeURL("data/tiles/{z}/{x}/{y}.png", "https://example.org/v2/") ===
      "https://example.org/v2/data/tiles/{z}/{x}/{y}.png", "bad tile URL");
    for (const url of ["../secret", "%2e%2e/secret", "/absolute", "https://elsewhere/a", "//elsewhere/a", "\\secret"]) {
      let rejected = false;
      try { M.safeURL(url, "https://example.org/"); } catch (error) { rejected = true; }
      assert(rejected, "accepted unsafe URL " + url);
    }
  });
  await test("catalogue validation rejects unknown schema and duplicate IDs", () => {
    for (const data of [{}, { version: 2, layers: [] },
      catalogue([footprint("same", "source"), footprint("same", "source")])]) {
      let rejected = false;
      try { M.validCatalogue(data); } catch (error) { rejected = true; }
      assert(rejected, "accepted invalid catalogue");
    }
  });
  await test("datasets are lazy and stack remains correct in reverse toggle order", async () => {
    const control = legend();
    control.setCatalogue(catalogue([footprint("source", "source"), footprint("derived", "derived")]), "https://example.org/");
    assert(control.map.sources.size === 0, "loaded hidden data");
    await control.datasets.get("derived").entry.setVisible(true);
    await control.datasets.get("source").entry.setVisible(true);
    const ids = control.map.layers.map(layer => layer.id);
    assert(ids.indexOf("dataset-source-line") < ids.indexOf("dataset-derived-fill"), "source above derived");
    assert(ids.indexOf("dataset-derived-line") < ids.indexOf("analysis-anchor"), "derived above analysis");
    assert(ids.indexOf("satellite") < ids.indexOf("dataset-source-fill"), "source below basemap");
    await control.datasets.get("source").entry.setVisible(false);
    assert(control.map.getLayer("dataset-source-line").layout.visibility === "none", "not hidden");
    assert(control.map.getLayer("selection"), "removed selection");
  });
  await test("tile catalogue paints panels above outlines above raster regardless of enable order", async () => {
    const control = legend();
    control.setCatalogue(catalogue([
      { id: "p", label: "Panels", group: "analysis", kind: "panel", representation: "vector-tiles", url: "data/p.pmtiles", sourceLayer: "layout" },
      { id: "b", label: "Outlines", group: "analysis", kind: "building", representation: "vector-tiles", url: "data/b.pmtiles", sourceLayer: "buildings" },
      { id: "h", label: "Heat map", group: "analysis", representation: "raster-tiles", tiles: "data/h/{z}/{x}/{y}.png" },
    ]), "https://example.org/");
    for (const id of ["p", "b", "h"]) await control.datasets.get(id).entry.setVisible(true);
    const ids = control.map.layers.map(layer => layer.id);
    assert(ids.indexOf("dataset-h-raster") < ids.indexOf("dataset-b-line") &&
      ids.indexOf("dataset-b-line") < ids.indexOf("dataset-p-fill"), "incorrect analysis paint order");
    assert(!control.map.getLayer("dataset-b-fill"), "outlines must not obscure heat map");
    assert(control.map.getLayer("dataset-p-fill").filter[2] === "panel", "missing kind filter");
  });
  await test("preview excludes unavailable analysis and duplicate derived kinds", () => {
    const control = legend();
    control.setCatalogue(catalogue([
      footprint("cache", "source"),
      { ...footprint("p", "analysis"), kind: "panel" },
      { ...footprint("f", "derived"), kind: "facet" },
    ]), "https://example.org/", { skipAnalysis: true, skipKinds: ["facet"] });
    assert([...control.datasets.keys()].join() === "cache", "incorrect preview layers");
  });
  await test("changing target clears local sources and old async loads cannot return", async () => {
    const control = legend();
    const fetch0 = globalThis.fetch;
    let finish;
    globalThis.fetch = () => new Promise(resolve => { finish = resolve; });
    try {
      control.setCatalogue(catalogue([{ id: "local", label: "Local", group: "source", representation: "vector", url: "data/local.geojson" }]), "https://example.org/");
      const loading = control.datasets.get("local").entry.setVisible(true);
      control.setCatalogue(catalogue([]), "https://example.org/");
      finish({ ok: true, json: async () => fc });
      await loading;
      assert(control.map.sources.size === 0 && control.datasets.size === 0, "local data leaked into remote target");
    } finally { globalThis.fetch = fetch0; }
  });
  await test("failed vector loads report failure and remain off", async () => {
    const control = legend();
    const fetch0 = globalThis.fetch;
    globalThis.fetch = async () => ({ ok: false, status: 503 });
    try {
      control.setCatalogue(catalogue([{ id: "bad", label: "Bad", group: "source", representation: "vector", url: "data/bad.geojson" }]), "https://example.org/");
      let failed = false;
      try { await control.datasets.get("bad").entry.setVisible(true); } catch (error) { failed = error.message === "HTTP 503"; }
      assert(failed && !control.datasets.get("bad").visible && !control.map.sources.size, "failure appeared successful");
    } finally { globalThis.fetch = fetch0; }
  });
  await test("basemap radio switching preserves overlays and aerial composite", async () => {
    const map = new MapStub();
    const entries = M.basemaps(map);
    await entries[1].setVisible(true);
    assert(map.getLayer("satellite").layout.visibility === "none" &&
      map.getLayer("linz-aerial").layout.visibility === "none" &&
      map.getLayer("osm").layout.visibility === "visible", "OSM switch wrong");
    await entries[0].setVisible(true);
    assert(map.getLayer("satellite").layout.visibility === "visible" &&
      map.getLayer("linz-aerial").layout.visibility === "visible" &&
      map.getLayer("osm").layout.visibility === "none", "aerial switch wrong");
    assert(map.getLayer("selection") && entries.filter(e => e.getVisible()).length === 1, "lost overlays or radio state");
  });
  if (typeof maplibregl !== "undefined") {
    await test("actual MapLibre control and catalogue paint order", async () => {
      const map = new maplibregl.Map({
        container: "map", center: [168.66, -45.03], zoom: 14,
        style: { version: 8, sources: {}, layers: [
          { id: "satellite", type: "background", paint: { "background-color": "#293544" } },
          { id: "linz-aerial", type: "background", paint: { "background-color": "#293544" } },
          { id: "analysis-anchor", type: "background", paint: { "background-opacity": 0 } },
        ] },
      });
      await new Promise(resolve => map.on("load", resolve));
      const control = new M.Legend({ beforeId: "analysis-anchor" });
      map.addControl(control, "top-right");
      control.setEntries(M.basemaps(map));
      const data = { type: "FeatureCollection", features: [{ type: "Feature", properties: {},
        geometry: { type: "Polygon", coordinates: [[[168.65, -45.04], [168.67, -45.04],
          [168.67, -45.02], [168.65, -45.02], [168.65, -45.04]]] } }] };
      control.setCatalogue(catalogue([
        { ...footprint("imagery", "source"), label: "Cached imagery", data, bounds: [168.65, -45.04, 168.67, -45.02] },
        { ...footprint("fitted", "derived"), label: "Fitted outlines", data },
      ]), location.href);
      await control.datasets.get("fitted").entry.setVisible(true);
      await control.datasets.get("imagery").entry.setVisible(true);
      control.refresh();
      const ids = map.getStyle().layers.map(layer => layer.id);
      assert(ids.indexOf("dataset-imagery-line") < ids.indexOf("dataset-fitted-fill") &&
        ids.indexOf("dataset-fitted-line") < ids.indexOf("analysis-anchor"), "wrong real MapLibre order");
      globalThis.layerTestMap = map;
      globalThis.layerTestControl = control;
    });
  }
  globalThis.mapLayersTestResult = { passed, failures };
  if (typeof process !== "undefined") process.exitCode = failures.length ? 1 : 0;
  if (typeof document !== "undefined") {
    document.getElementById("result").textContent = JSON.stringify(globalThis.mapLayersTestResult, null, 2);
  }
})();
