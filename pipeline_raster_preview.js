// Presentation of the inspected raster assets in an estimate.
(function (root) {
  "use strict";
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const states = {
    cached: "Cached and validated",
    missing: "Missing",
    invalid: "Invalid cached copy",
    superseded: "Source changed",
    unversioned: "Unversioned; requires transfer",
    recovery_required: "Recovery required",
    unknown: "Not inspected",
  };
  function renderAssetList(assets, batchNumber) {
    if (!Array.isArray(assets) || !assets.length) return "";
    const rows = assets.map((asset) => {
      const state = states[asset.cache_state] || "Unknown state";
      const bytes = Number.isSafeInteger(asset.size_bytes) && asset.size_bytes > 0
        ? `${asset.size_bytes.toLocaleString()} bytes` : "Unknown";
      const regions = Array.isArray(asset.regions) ? asset.regions.join(", ") : "";
      return `<tr><td>${esc(asset.item_id)}</td><td>${esc(asset.collection_id)} / ${esc(asset.asset_key)}</td>
        <td>${esc(state)}</td><td>${esc(bytes)}</td><td>${esc(regions)}</td></tr>`;
    }).join("");
    return `<details><summary>Inspect ${assets.length} native tile(s) in estimate ${esc(batchNumber)}</summary>
      <p class="muted">Snapshot only; cached tiles are not reserved. Tile bounds do not establish valid-pixel coverage.
      Different estimates may list the same tile.</p>
      <table><tr><th>Tile</th><th>Collection / asset</th><th>Cache state</th><th>Source size</th><th>Areas</th></tr>
      ${rows}</table></details>`;
  }
  function mapLayers(assets) {
    const groups = [
      { key: "cached", label: "National DSM: cached and validated", colour: "#15803d" },
      { key: "missing", label: "National DSM: missing", colour: "#dc2626" },
      { key: "attention", label: "National DSM: attention", colour: "#d97706" },
    ];
    return groups.map((group) => {
      const features = (assets || []).filter((asset) =>
        (asset.cache_state === "cached" ? "cached" : asset.cache_state === "missing" ? "missing" : "attention") === group.key
      ).map((asset) => {
        const [west, south, east, north] = asset.bbox_wgs84;
        return {
          type: "Feature",
          geometry: { type: "Polygon", coordinates: [[[west, south], [east, south],
            [east, north], [west, north], [west, south]]] },
          properties: { name: asset.item_id, layer_id: "dsm-preview-" + group.key,
            collection: asset.collection_id, asset: asset.asset_key, state: asset.cache_state,
            size_bytes: asset.size_bytes, regions: (asset.regions || []).join(", ") },
        };
      });
      return {
        id: "dsm-preview-" + group.key, label: group.label, group: "source",
        representation: "footprint", colour: group.colour, defaultVisible: true,
        description: "National DSM tiles intersecting the map view, not necessarily required by a build. Rectangles are item extents, not valid-pixel coverage. Cache state is a snapshot, not a reservation. Select Query and click a tile for details.",
        data: { type: "FeatureCollection", features },
      };
    }).filter((layer) => layer.data.features.length);
  }
  function imageryMapLayers(assets, catalogue) {
    const downloaded = new Set((catalogue?.layers || [])
      .filter(layer => layer.id === "source-native-imagery")
      .flatMap(layer => layer.data.features)
      .map(feature => JSON.stringify([feature.properties.collection_id,
        feature.properties.name, feature.properties.asset_key])));
    const unique = new Map();
    for (const asset of assets || []) {
      const uses = (asset.uses || []).filter(use => ["imagery", "reference_imagery"].includes(use.role));
      if (!uses.length) continue;
      const key = JSON.stringify([asset.collection_id, asset.item_id, asset.asset_key]);
      const existing = unique.get(key);
      unique.set(key, { ...asset, uses: [...(existing?.uses || []), ...uses] });
    }
    return [
      { key: "missing", label: "Selected imagery: awaiting download", colour: "#f9a8d4" },
      { key: "downloaded", label: "Selected imagery: downloaded locally", colour: "#db2777" },
    ].map(group => ({
      id: "imagery-selected-" + group.key, label: group.label, group: "source",
      representation: "footprint", colour: group.colour, defaultVisible: true,
      description: "Imagery tiles pinned to the latest build estimate in this page, including reference photos. " +
        "Pink rectangles show tile extents, not photos or valid-pixel coverage. " +
        "Downloaded status uses local headers when available, not a new integrity check.",
      data: { type: "FeatureCollection", features: [...unique.entries()]
        .filter(([key, asset]) => (catalogue ? downloaded.has(key) : asset.cache_state === "cached")
          === (group.key === "downloaded"))
        .map(([, asset]) => {
          const [w, s, e, n] = asset.bbox_wgs84;
          return { type: "Feature", geometry: { type: "Polygon",
            coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] },
          properties: { name: asset.item_id, layer_id: "imagery-selected-" + group.key,
            collection_id: asset.collection_id, asset_key: asset.asset_key,
            status: group.key, size_bytes: asset.size_bytes,
            regions: [...new Set(asset.uses.map(use => use.region))].join(", "),
            roles: [...new Set(asset.uses.map(use => use.role))].join(", "),
            imagery_layers: [...new Set(asset.uses.map(use => use.layer_id))].join(", ") } };
        }) },
    })).filter(layer => layer.data.features.length);
  }
  const api = { renderAssetList, mapLayers, imageryMapLayers };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PipelineRasterPreview = api;
})(globalThis);
