(function () {
  "use strict";
  const local = ["127.0.0.1", "localhost", "[::1]"].includes(location.hostname);
  const params = new URLSearchParams(location.search);
  const selector = document.getElementById("dataset-context");
  selector.value = local && params.get("source") !== "published" ? "local" : "published";
  if (!local) selector.querySelector('[value="local"]').disabled = true;
  const status = document.getElementById("status");
  const site = window.SITE || {};
  const protocol = new pmtiles.Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);
  const view = site.defaultView || { center: [172.5, -41.5], zoom: 5 };
  const lon = Number(params.get("lng")), lat = Number(params.get("lat")), zoom = Number(params.get("z"));
  const explicitView = params.has("lng") && params.has("lat") &&
    Number.isFinite(lon) && Number.isFinite(lat) && Math.abs(lon) <= 180 && Math.abs(lat) <= 90;
  const map = new maplibregl.Map({
    container: "map", style: MapLayers.aerialStyle(),
    ...(explicitView ? { center: [lon, lat], zoom: params.has("z") && Number.isFinite(zoom) ? Math.max(0, Math.min(22, zoom)) : 15 } : view),
  });
  window.__map = map;
  map.addControl(new maplibregl.NavigationControl(), "top-right");
  const legend = new MapLayers.Legend({
    onQuery: entry => {
      status.classList.remove("error");
      status.textContent = entry ? `Querying ${entry.label}: click the map to inspect feature attributes.` :
        "Select layers independently. Choose Query beside a vector layer, then click the map. Raster layers have no feature attributes.";
    },
  });
  map.addControl(legend, "top-right");
  let request = 0;
  function setError(error) {
    status.classList.add("error");
    status.textContent = "Dataset catalogue: " + error.message;
    legend.setMessages([status.textContent]);
  }
  async function readJSON(url, headers = {}) {
    const response = await fetch(url, { headers });
    if (!response.ok) {
      const error = new Error(`${response.status} ${response.statusText}`);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }
  async function load() {
    const id = ++request;
    const context = selector.value;
    legend.clearDatasets();
    legend.setMessages([]);
    status.textContent = "Loading " + (context === "local" ? "local cached" : "published") + " datasets...";
    try {
      let catalogue;
      let base = new URL(site.dataBase || ".", location.href).href;
      if (context === "local") {
        base = new URL(".", location.href).href;
        const token = sessionStorage.getItem("pipelineToken");
        try {
          catalogue = await readJSON("/api/layers?source=local", token ? { "X-Pipeline-Token": token } : {});
        } catch (error) {
          if (error.status !== 404 && error.status !== 403) throw error;
          catalogue = await readJSON("/data/local_map_layers.json");
        }
      } else {
        catalogue = await readJSON(new URL(`data/map_layers.json?v=${site.dataVersion || "1"}`, base));
      }
      if (id !== request) return;
      legend.setCatalogue(catalogue, base);
      legend.options.onQuery(null);
      if (!catalogue.layers.length) status.textContent = "No available datasets in this context. Download or build data, then refresh.";
      const details = legend.container.querySelector("details");
      details.open = true;
    } catch (error) {
      if (id === request) setError(error);
    }
  }
  selector.addEventListener("change", load);
  document.getElementById("refresh").addEventListener("click", load);
  map.on("load", () => {
    legend.setEntries(MapLayers.basemaps(map));
    load();
  });
})();
