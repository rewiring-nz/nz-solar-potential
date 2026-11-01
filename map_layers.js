/* Shared MapLibre layer legend. Dataset layers are loaded only when enabled. */
(function (root) {
  "use strict";
  const GROUPS = ["analysis", "derived", "source", "operations", "basemap"];
  const LABELS = {
    analysis: "Rooftop solar analysis", derived: "Derived datasets",
    source: "Source datasets", operations: "Build areas", basemap: "Basemap",
  };
  function order(entry) {
    if (entry.group !== "analysis") return entry.order || 0;
    if (entry.kind === "panel" || entry.id === "analysis-panels") return 30;
    if (entry.kind === "building" || entry.id === "analysis-outlines") return 20;
    return 10;
  }
  function aerialStyle() {
    return { version: 8, sources: {
      satellite: { type: "raster",
        tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
        tileSize: 256, attribution: "Esri World Imagery" },
      "linz-aerial": { type: "raster",
        tiles: ["https://basemaps.linz.govt.nz/v1/tiles/aerial/WebMercatorQuad/{z}/{x}/{y}.webp?api=c01m0keqqjx0anrxjrt7grvtqxh"],
        tileSize: 256, minzoom: 14, maxzoom: 21, attribution: "LINZ Basemaps" },
    }, layers: [
      { id: "satellite", type: "raster", source: "satellite" },
      { id: "linz-aerial", type: "raster", source: "linz-aerial" },
    ] };
  }
  function sorted(entries) {
    return [...entries].sort((a, b) =>
      GROUPS.indexOf(a.group) - GROUPS.indexOf(b.group) ||
      order(b) - order(a) || a.label.localeCompare(b.label));
  }
  function safeURL(path, base) {
    if (typeof path !== "string" || !path || /^(?:[a-z]+:|\/|\\)/i.test(path) ||
        path.split(/[\\/]/).some(p => decodeURIComponent(p).startsWith("."))) {
      throw new Error("Dataset URL must be a safe relative path");
    }
    return new URL(path, base).href.replace(/%7B/gi, "{").replace(/%7D/gi, "}");
  }
  function validCatalogue(catalogue) {
    if (!catalogue || catalogue.version !== 1 || !Array.isArray(catalogue.layers)) {
      throw new Error("Unsupported map-layer catalogue");
    }
    const ids = new Set();
    for (const layer of catalogue.layers) {
      if (!layer.id || ids.has(layer.id) || typeof layer.label !== "string" ||
          !["analysis", "source", "derived"].includes(layer.group) ||
          !["footprint", "vector", "vector-tiles", "raster-tiles"].includes(layer.representation)) {
        throw new Error("Invalid map-layer catalogue entry");
      }
      ids.add(layer.id);
    }
    return catalogue;
  }
  class Legend {
    constructor(options = {}) {
      this.options = options;
      this.entries = [];
      this.datasets = new Map();
      this.messages = [];
      this.generation = 0;
      this.queryId = null;
    }
    onAdd(map) {
      this.map = map;
      this.container = document.createElement("div");
      this.container.className = "maplibregl-ctrl map-layer-legend";
      const details = document.createElement("details");
      details.open = false;
      const summary = document.createElement("summary");
      summary.textContent = "Layers & legend";
      details.append(summary);
      this.body = document.createElement("div");
      this.body.className = "map-layer-body";
      details.append(this.body);
      this.container.append(details);
      this.errorHandler = event => {
        if ([...this.datasets.values()].some(d => d.source === event.sourceId)) {
          this.setMessages([...this.messages, `Dataset tiles: ${event.error?.message || "failed to load"}`]);
        }
      };
      map.on("error", this.errorHandler);
      this.clickHandler = event => this.inspect(event);
      map.on("click", this.clickHandler);
      this.render();
      return this.container;
    }
    onRemove() {
      this.clearDatasets();
      this.map.off("error", this.errorHandler);
      this.map.off("click", this.clickHandler);
      this.container.remove();
    }
    setEntries(entries) {
      this.entries = entries;
      this.render();
    }
    setMessages(messages) {
      this.messages = [...new Set(messages)];
      this.render();
    }
    render() {
      if (!this.body) return;
      const openGroups = new Map([...this.body.querySelectorAll("details[data-group]")]
        .map(element => [element.dataset.group, element.open]));
      const focus = this.body.contains(document.activeElement) ? document.activeElement.dataset.layer : null;
      this.body.replaceChildren();
      const entries = sorted([...this.entries, ...[...this.datasets.values()].map(d => d.entry)]);
      for (const group of GROUPS) {
        const members = entries.filter(e => e.group === group);
        if (!members.length) continue;
        const section = document.createElement("details");
        section.dataset.group = group;
        section.open = openGroups.get(group) ?? true;
        const heading = document.createElement("summary");
        heading.textContent = LABELS[group];
        section.append(heading);
        for (const entry of members) {
          const row = document.createElement("div");
          row.className = "map-layer-row";
          const label = document.createElement("label");
          const input = document.createElement("input");
          input.type = group === "basemap" ? "radio" : "checkbox";
          if (group === "basemap") input.name = "map-basemap";
          input.checked = Boolean(entry.getVisible());
          input.dataset.layer = entry.id;
          input.disabled = Boolean(entry.disabled || entry.pending);
          const swatch = document.createElement("span");
          swatch.className = "map-layer-swatch" + (entry.gradient ? " gradient" : "");
          swatch.style.setProperty("--layer-colour", entry.colour || "#90caf9");
          swatch.setAttribute("aria-hidden", "true");
          label.append(input, swatch, document.createTextNode(entry.label));
          input.addEventListener("change", async () => {
            entry.pending = true;
            this.render();
            try {
              await entry.setVisible(input.checked);
            } catch (error) {
              this.setMessages([...this.messages, `${entry.label}: ${error.message}`]);
            } finally {
              entry.pending = false;
              this.render();
            }
          });
          row.append(label);
          if (entry.queryLayers || (entry.representation && entry.representation !== "raster-tiles")) {
            const queryLabel = document.createElement("label");
            queryLabel.className = "map-layer-query";
            const query = document.createElement("input");
            query.type = "radio";
            query.name = "map-query-layer";
            query.checked = this.queryId === entry.id;
            query.disabled = Boolean(entry.pending);
            query.setAttribute("aria-label", `Query ${entry.label}`);
            query.addEventListener("change", async () => {
              try { await this.selectQuery(entry.id); }
              catch (error) { this.setMessages([...this.messages, `${entry.label}: ${error.message}`]); }
            });
            queryLabel.append(query, document.createTextNode("Query"));
            row.append(queryLabel);
          }
          if (entry.bounds) {
            const zoom = document.createElement("button");
            zoom.type = "button";
            zoom.textContent = "Zoom";
            zoom.title = `Zoom to ${entry.label}`;
            zoom.addEventListener("click", () =>
              this.map.fitBounds([[entry.bounds[0], entry.bounds[1]], [entry.bounds[2], entry.bounds[3]]],
                { padding: 50, maxZoom: 17 }));
            row.append(zoom);
          }
          section.append(row);
          if (entry.description) {
            const info = document.createElement("details");
            info.className = "map-layer-info";
            const title = document.createElement("summary");
            title.textContent = "Details";
            const text = document.createElement("p");
            text.textContent = entry.description;
            info.append(title, text);
            section.append(info);
          }
        }
        this.body.append(section);
      }
      for (const message of this.messages) {
        const warning = document.createElement("p");
        warning.className = "map-layer-warning";
        warning.setAttribute("role", "status");
        warning.textContent = message;
        this.body.append(warning);
      }
      if (this.queryId) {
        const clear = document.createElement("button");
        clear.type = "button";
        clear.className = "map-layer-clear-query";
        clear.textContent = "Stop querying features";
        clear.addEventListener("click", () => this.clearQuery());
        this.body.append(clear);
      }
      if (focus) {
        [...this.body.querySelectorAll("input")].find(input => input.dataset.layer === focus)?.focus();
      }
    }
    refresh() {
      this.render();
    }
    clearDatasets() {
      this.clearQuery();
      this.generation++;
      for (const dataset of this.datasets.values()) {
        for (const id of [...dataset.layers].reverse()) {
          if (this.map.getLayer(id)) this.map.removeLayer(id);
        }
        if (this.map.getSource(dataset.source)) this.map.removeSource(dataset.source);
      }
      this.datasets.clear();
    }
    allEntries() {
      return [...this.entries, ...[...this.datasets.values()].map(dataset => dataset.entry)];
    }
    clearQuery() {
      this.queryId = null;
      if (this.popup) this.popup.remove();
      this.popup = null;
      if (this.options.onQuery) this.options.onQuery(null);
      this.render();
    }
    async selectQuery(id) {
      const entry = this.allEntries().find(item => item.id === id);
      if (!entry || (!entry.queryLayers &&
          (!entry.representation || entry.representation === "raster-tiles"))) {
        throw new Error("This layer has no feature attributes");
      }
      await entry.setVisible(true);
      if (!this.allEntries().includes(entry)) return;
      this.queryId = id;
      if (this.options.onQuery) this.options.onQuery(entry);
      this.render();
    }
    inspect(event) {
      if (!this.queryId) return;
      const entry = this.allEntries().find(item => item.id === this.queryId);
      if (!entry) return;
      const layers = entry.queryLayers || this.datasets.get(entry.id)?.layers;
      const active = entry.getVisible() && layers ? layers.filter(id => this.map.getLayer(id)) : [];
      const point = event.point;
      const features = active.length ? this.map.queryRenderedFeatures(
        [[point.x - 4, point.y - 4], [point.x + 4, point.y + 4]], { layers: active }) : [];
      const content = attributeContent(entry, features, event.lngLat, !entry.getVisible());
      if (this.popup) this.popup.remove();
      this.popup = new maplibregl.Popup({ maxWidth: "420px" })
        .setLngLat(event.lngLat).setDOMContent(content).addTo(this.map);
    }
    setCatalogue(catalogue, base, { skipAnalysis = false, skipKinds = [] } = {}) {
      validCatalogue(catalogue);
      const visible = new Set([...this.datasets.values()].filter(d => d.visible).map(d => d.entry.id));
      const queryId = this.queryId;
      this.clearDatasets();
      const generation = this.generation;
      for (const spec of catalogue.layers) {
        if ((skipAnalysis && spec.group === "analysis") || skipKinds.includes(spec.kind)) continue;
        const source = "dataset-" + spec.id;
        const dataset = { source, layers: [], visible: false };
        dataset.entry = Object.assign({}, spec, {
          getVisible: () => dataset.visible,
          setVisible: async on => {
            if (on && !dataset.layers.length) {
              await this.loadDataset(dataset, spec, base, generation);
            }
            if (generation !== this.generation) return;
            dataset.visible = on;
            dataset.layers.forEach(id => this.map.setLayoutProperty(id, "visibility", on ? "visible" : "none"));
          },
        });
        this.datasets.set(spec.id, dataset);
      }
      this.setMessages(catalogue.warnings || []);
      // Refreshes preserve visibility without keeping stale geometry or URLs.
      for (const dataset of this.datasets.values()) {
        if (visible.has(dataset.entry.id) || dataset.entry.defaultVisible) {
          dataset.entry.setVisible(true).then(() => this.refresh()).catch(error =>
            this.setMessages([...this.messages, `${dataset.entry.label}: ${error.message}`]));
        }
      }
      if (queryId && this.datasets.has(queryId)) {
        this.queryId = queryId;
        if (this.options.onQuery) this.options.onQuery(this.datasets.get(queryId).entry);
        this.render();
      }
    }
    async loadDataset(dataset, spec, base, generation) {
      let source;
      if (spec.representation === "vector-tiles") {
        source = { type: "vector", url: "pmtiles://" + safeURL(spec.url, base) };
      } else if (spec.representation === "raster-tiles") {
        source = { type: "raster", tiles: [safeURL(spec.tiles, base)], tileSize: spec.tileSize || 256 };
        if (spec.minzoom != null) source.minzoom = spec.minzoom;
        if (spec.maxzoom != null) source.maxzoom = spec.maxzoom;
      } else {
        let data = spec.data;
        if (!data) {
          const response = await fetch(safeURL(spec.url, base));
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          data = await response.json();
        }
        if (!data || data.type !== "FeatureCollection" || !Array.isArray(data.features)) {
          throw new Error("Expected GeoJSON FeatureCollection");
        }
        source = { type: "geojson", data };
      }
      if (generation !== this.generation) return;
      this.map.addSource(dataset.source, source);
      const colour = spec.colour || "#90caf9";
      const common = { source: dataset.source, layout: { visibility: "none" } };
      if (spec.sourceLayer) common["source-layer"] = spec.sourceLayer;
      if (spec.kind && spec.kind !== "building") common.filter = ["==", ["get", "kind"], spec.kind];
      const layers = source.type === "raster" ? [
        { id: dataset.source + "-raster", type: "raster", paint: { "raster-opacity": 0.78 } },
      ] : [
        { id: dataset.source + "-fill", type: "fill",
          paint: { "fill-color": colour, "fill-opacity": spec.kind === "panel" ? 0.8 : 0.08 } },
        { id: dataset.source + "-line", type: "line",
          paint: { "line-color": colour, "line-width": 1.5,
            ...(spec.representation === "footprint" ? { "line-dasharray": [3, 2] } : {}) } },
      ];
      if (spec.kind === "building" || spec.kind === "facet") {
        // An invisible polygon supports clicks inside an outline, not just on its edge.
        layers[0].paint["fill-opacity"] = 0;
      }
      try {
        for (const layer of layers) {
          this.map.addLayer(Object.assign({}, common, layer));
          dataset.layers.push(layer.id);
        }
        this.restack();
      } catch (error) {
        dataset.layers.forEach(id => this.map.removeLayer(id));
        dataset.layers = [];
        this.map.removeSource(dataset.source);
        throw error;
      }
    }
    restack() {
      const before = this.options.beforeId;
      // Bottom-up moves enforce the same stack regardless of enable order.
      for (const entry of sorted([...this.datasets.values()].map(d => d.entry)).reverse()) {
        for (const id of this.datasets.get(entry.id).layers) {
          this.map.moveLayer(id, before && this.map.getLayer(before) ? before : undefined);
        }
      }
    }
  }
  function uniqueFeatures(features) {
          const seen = new Set();
          return features.filter(feature => {
            const key = feature.id != null ? `${feature.source}/${feature.sourceLayer}/${feature.id}` :
              JSON.stringify([feature.properties, feature.geometry]);
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
          });
        }
        function attributeContent(entry, features, lngLat, hidden = false) {
          const content = document.createElement("div");
          content.className = "map-feature-attributes";
          const title = document.createElement("h3");
          title.textContent = entry.label;
          const location = document.createElement("p");
          location.textContent = `${lngLat.lng.toFixed(6)}, ${lngLat.lat.toFixed(6)}`;
          content.append(title, location);
          const unique = uniqueFeatures(features);
          if (!unique.length) {
            const message = document.createElement("p");
            message.textContent = hidden ? "This query layer is hidden. Enable it to inspect features." :
              "No feature at this point in the query layer. Zoom in if detailed tiles are not visible yet.";
            content.append(message);
          }
          for (const [index, feature] of unique.slice(0, 20).entries()) {
            const heading = document.createElement("h4");
            heading.textContent = `Feature ${index + 1}` + (feature.id != null ? ` (${feature.id})` : "");
            content.append(heading);
            const table = document.createElement("table");
            for (const [key, value] of Object.entries(feature.properties || {})) {
              const row = document.createElement("tr");
              const name = document.createElement("th");
              const cell = document.createElement("td");
              name.textContent = key;
              cell.textContent = value !== null && typeof value === "object" ? JSON.stringify(value) : String(value);
              row.append(name, cell);
              table.append(row);
            }
            if (!table.children.length) {
              const message = document.createElement("p");
              message.textContent = "This feature has no attributes.";
              content.append(message);
            } else content.append(table);
          }
          if (unique.length > 20) {
            const message = document.createElement("p");
            message.textContent = `Showing 20 of ${unique.length} overlapping features. Zoom in to narrow the query.`;
            content.append(message);
          }
          return content;
        }
  function basemaps(map) {
    let selected = "aerial";
    if (!map.getSource("osm")) {
      map.addSource("osm", { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        tileSize: 256, maxzoom: 19, attribution: "&copy; OpenStreetMap contributors" });
      map.addLayer({ id: "osm", type: "raster", source: "osm", layout: { visibility: "none" } }, "satellite");
    }
    return [
      { id: "aerial", label: "Aerial (Esri / LINZ)", group: "basemap", colour: "#719568",
        description: "Esri World Imagery; LINZ aerial imagery takes over at zoom 14. Internet required." },
      { id: "osm", label: "OpenStreetMap", group: "basemap", colour: "#d5cdb5",
        description: "OpenStreetMap street tiles. Internet required; standard provider attribution and usage policy apply." },
    ].map(entry => Object.assign(entry, {
      getVisible: () => selected === entry.id,
      setVisible: on => {
        if (!on) return;
        selected = entry.id;
        ["satellite", "linz-aerial", "osm"].forEach(id =>
          map.setLayoutProperty(id, "visibility",
            (selected === "aerial" ? id !== "osm" : id === "osm") ? "visible" : "none"));
      },
    }));
  }
  const api = { Legend, basemaps, aerialStyle, sorted, safeURL, validCatalogue, uniqueFeatures, attributeContent };
  root.MapLayers = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window === "undefined" ? globalThis : window);
