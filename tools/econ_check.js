// Panel-economics check for rooftop-solar.html, run in the browser console
// (or a browser automation tool) with a building open in Panel Layout and
// Individual panel economics on.
//
// A panel's colour says whether that panel pays for itself. It must not move
// when the slider shows more or fewer panels: priced at the size on screen,
// 3/23 Adelaide St's panels turned from yellow to green as the slider went
// from 42 to 71 panels. This sets the open roof's density low, middle and
// full, reads the roof's pricing each time (window.__econMeritFor) and fails
// if any figure differs. It also checks every building in view.
//
// Expected output: "ok: pricing identical at 3 sizes for N roofs".
(async () => {
  const slider = document.getElementById("vslider");
  if (!slider || !window.__econMeritFor || !window.__invalidateInView) return "FAIL: page hooks missing";
  const ids = [...new Set(window.__map.queryRenderedFeatures({ layers: ["buildings-fill"] })
    .map(f => f.properties.building_id))];
  const read = () => Object.fromEntries(ids.map(id => [id, JSON.stringify(window.__econMeritFor(id))]));
  const snaps = [];
  for (const v of [20, 50, 100]) {
    slider.value = v;
    slider.dispatchEvent(new Event("input", { bubbles: true }));
    await new Promise(r => setTimeout(r, 900));
    snaps.push(read());
  }
  const bad = ids.filter(id => snaps[0][id] !== snaps[1][id] || snaps[1][id] !== snaps[2][id]);
  const priced = ids.filter(id => snaps[0][id] !== "null").length;
  if (!priced) return "FAIL: no roof priced (is panel economics on?)";
  return bad.length
    ? `FAIL: pricing moved with the slider on ${bad.length} roofs, e.g. #${bad[0]}: ${snaps[0][bad[0]]} vs ${snaps[2][bad[0]]}`
    : `ok: pricing identical at 3 sizes for ${priced} roofs`;
})();
