// Money-mode check for rooftop-solar.html, run in the browser console (or a
// browser automation tool) in Panel Layout at zoom 16+.
//
// A roof's money pill on the map must equal the net saving its card shows
// when it is opened: 43 Ballarat St read $1,600/yr on the map and $1,700/yr
// on its card until 7 Nov, because the map priced closed roofs another way.
// This opens up to N roofs in view, one after another, and compares the
// card's figure with the figure the map prices that roof at (roofMoney).
//
// Expected output: "ok: map and card agree on N roofs".
(async (N = 12) => {
  const map = window.__map;
  if (!map || !window.__roofMoney) return "FAIL: page hooks missing";
  const ids = [...new Set(map.queryRenderedFeatures({ layers: ["buildings-fill"] })
    .map(f => f.properties.building_id))];
  const before = Object.fromEntries(ids.map(id => [id, window.__roofMoney(id)]));
  const priced = ids.filter(id => before[id] != null).slice(0, N);
  if (!priced.length) return "FAIL: no roof priced (zoom in, Panel Layout)";
  const bad = [];
  for (const id of priced) {
    const f = map.queryRenderedFeatures({ layers: ["buildings-fill"] }).find(x => x.properties.building_id === id);
    if (!f) continue;
    window.__moneyCheck = null;
    // click the middle of the roof's outline, as a reader would
    const ring = f.geometry.type === "Polygon" ? f.geometry.coordinates[0] : f.geometry.coordinates[0][0];
    const at = ring.reduce((a, [x, y]) => [a[0] + x / ring.length, a[1] + y / ring.length], [0, 0]);
    const lngLat = new maplibregl.LngLat(at[0], at[1]);
    map.fire("click", { point: map.project(lngLat), lngLat, originalEvent: {} });
    for (let i = 0; i < 40 && !(window.__moneyCheck && window.__moneyCheck.buildingId === id); i++) {
      await new Promise(r => setTimeout(r, 150));
    }
    const c = window.__moneyCheck;
    if (!c || c.buildingId !== id) { bad.push(`#${id}: card did not open`); continue; }
    if (Math.abs(c.card - before[id]) > 0.5) bad.push(`#${id}: map ${before[id]} before opening, card ${c.card}`);
    if (c.pill != null && Math.abs(c.card - c.pill) > 0.5) bad.push(`#${id}: map ${c.pill} while open, card ${c.card}`);
  }
  return bad.length ? `FAIL: ${bad.length} of ${priced.length} roofs differ: ${bad.slice(0, 5).join("; ")}`
                    : `ok: map and card agree on ${priced.length} roofs`;
})();
