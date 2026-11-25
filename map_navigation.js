(function (root) {
  "use strict";
  const loopback = hostname => ["localhost", "127.0.0.1", "[::1]"].includes(hostname);

  function viewFromURL(href) {
    const url = new URL(href);
    const value = url.searchParams.get("mapbbox");
    if (value === null) return null;
    const bounds = value.split(",").map(Number);
    const bearing = Number(url.searchParams.get("bearing") || 0);
    const pitch = Number(url.searchParams.get("pitch") || 0);
    if (bounds.length !== 4 || !bounds.every(Number.isFinite) ||
        bounds[0] >= bounds[2] || bounds[1] >= bounds[3] ||
        bounds[1] < -90 || bounds[3] > 90 ||
        !Number.isFinite(bearing) || !Number.isFinite(pitch) || pitch < 0 || pitch > 85) {
      console.warn("Ignoring invalid map navigation bounds or camera angles");
      return null;
    }
    return { bounds: [[bounds[0], bounds[1]], [bounds[2], bounds[3]]],
      bearing, pitch, fitBoundsOptions: { padding: 0, bearing, pitch } };
  }

  function linkFor(href, map, data) {
    const url = new URL(href);
    const b = map.getBounds();
    url.searchParams.set("mapbbox", [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].join(","));
    url.searchParams.set("bearing", String(map.getBearing()));
    url.searchParams.set("pitch", String(map.getPitch()));
    if (data) url.searchParams.set("data", data);
    return url.href;
  }

  function bindLinks(map, links, data, href = root.location?.href) {
    const originals = [...links].map(link => [link, link.href]);
    const incoming = href ? viewFromURL(href) : null;
    let inheritedBounds = incoming?.bounds.flat().join(",");
    const camera = () => JSON.stringify([
      map.getCenter().lng, map.getCenter().lat, map.getZoom(), map.getBearing(), map.getPitch(),
    ]);
    const initialCamera = camera();
    const update = () => {
      // Fitting a different aspect ratio adds margins; do not accumulate those
      // margins on each page switch when the user has not moved the map.
      if (camera() !== initialCamera) inheritedBounds = null;
      for (const [link, original] of originals) {
        const url = new URL(linkFor(original, map, data?.()));
        if (inheritedBounds) url.searchParams.set("mapbbox", inheritedBounds);
        link.href = url.href;
      }
    };
    map.on("moveend", update);
    for (const [link] of originals) {
      link.addEventListener("click", update);
      link.addEventListener("contextmenu", update);
    }
    update();
    return update;
  }

  function dataSettings(site, href, now = Date.now()) {
    const url = new URL(href);
    const local = loopback(url.hostname) && url.pathname === "/rooftop-solar.html" &&
      url.searchParams.get("data") !== "published";
    return {
      local,
      base: new URL(local ? "./" : (site?.dataBase || "./"), url).href,
      version: local ? String(now) : (site?.dataVersion || "1"),
    };
  }

  const api = { viewFromURL, linkFor, bindLinks, dataSettings };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.MapNavigation = api;
})(globalThis);
