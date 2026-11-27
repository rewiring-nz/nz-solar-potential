// Investigate heights (Advanced menu): the height tiles the site already
// serves, drawn as shaded relief over the photo, for checking what the build
// and the 3D view were given.
//
//   LiDAR surface   data/terrain: the 1 m DSM as the laser saw it -- roofs,
//                   trees and ground. A roof here is what the roof faces and
//                   shading were measured from.
//   Ground under 3D data/terrain_ground: the same surface with every outlined
//                   building pressed flat to the ground beside it
//                   (tools/build_terrain_tiles.py --ground). A roof still
//                   standing here pokes through its own solid in the 3D view.
//
// Shaded relief and not a height colour ramp: the heights are absolute
// (300-1,000 m across the district), so a ramp spends its colours on the
// hillside and a 6 m roof barely changes it; relief shows every edge a few
// metres high wherever it is. The relief sits directly over the aerial photo
// and under every analysis layer, so panels and outlines stay on top.
// Nothing new is served: both tile sets are already fetched for the 3D view.
(function () {
  "use strict";

  const MODES = {
    off: null,
    dsm: { dir: "terrain", label: "LiDAR surface" },
    ground: { dir: "terrain_ground", label: "Ground under 3D" },
  };
  const LAYER = "investigate-relief";
  let mode = "off";

  function base() {
    return (typeof DATA_BASE !== "undefined" ? DATA_BASE : "");
  }
  function ver() {
    return (typeof DATA_VERSION !== "undefined" ? DATA_VERSION : "1");
  }

  // the first layer above the photo, so the relief goes over the photo and
  // under everything the analysis draws
  function beforeId(map) {
    const ls = map.getStyle().layers;
    const i = ls.findIndex(l => l.id === "linz-aerial");
    return i >= 0 && i + 1 < ls.length ? ls[i + 1].id : undefined;
  }

  async function sourceFor(map, key) {
    const id = `investigate-${key}`;
    if (map.getSource(id)) return id;
    const dir = MODES[key].dir;
    // the zoom range the tiles were built for, as the 3D view reads it
    let minzoom = 8, maxzoom = 17;
    try {
      const r = await fetch(`${base()}data/${dir}/meta.json?v=${ver()}`);
      if (!r.ok) return null;
      const m = await r.json();
      minzoom = m.minzoom ?? minzoom; maxzoom = m.maxzoom ?? maxzoom;
    } catch (e) {
      return null;
    }
    if (!map.getSource(id)) {
      map.addSource(id, {
        type: "raster-dem", encoding: "mapbox", tileSize: 256, minzoom, maxzoom,
        tiles: [`${base()}data/${dir}/{z}/{x}/{y}.png?v=${ver()}`],
      });
    }
    return id;
  }

  async function set(map, key, onMissing) {
    mode = key;
    if (map.getLayer(LAYER)) map.removeLayer(LAYER);
    if (!MODES[key]) return true;
    const src = await sourceFor(map, key);
    if (mode !== key) return true;            // changed meanwhile
    if (!src) { mode = "off"; if (onMissing) onMissing(MODES[key].label); return false; }
    map.addLayer({
      id: LAYER, type: "hillshade", source: src,
      paint: {
        "hillshade-exaggeration": 1,
        "hillshade-illumination-direction": 315,
        "hillshade-shadow-color": "#1a0f00",
        "hillshade-highlight-color": "#fff4d6",
        "hillshade-accent-color": "#000000",
      },
    }, beforeId(map));
    return true;
  }

  // the Advanced menu's three-way switch
  function wire(map) {
    const row = document.getElementById("investigate-row");
    if (!row || row.dataset.wired) return;
    row.dataset.wired = "1";
    const btns = Array.from(row.querySelectorAll("button[data-mode]"));
    const show = () => btns.forEach(b => {
      const on = b.dataset.mode === mode && mode !== "off";
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    btns.forEach(b => b.addEventListener("click", async () => {
      const key = b.dataset.mode === mode ? "off" : b.dataset.mode;
      await set(map, key, label => alert(`${label}: no tiles in this data version.`));
      show();
    }));
    show();
  }

  window.InvestigateLayers = { set, wire, get mode() { return mode; }, MODES };
})();
