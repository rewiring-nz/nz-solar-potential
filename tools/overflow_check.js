// Overlap check for rooftop-solar.html, run in the browser console (or a
// browser automation tool) like tools/contrast_check.js.
//
// Lists every visible box (an element with a background or a border) that
// pokes out of the nearest enclosing card (an ancestor with its own
// background) by more than 1px, and every pair of floating panels
// (position absolute/fixed, with a background) that overlap. A disclaimer
// card 14px wider than the dashboard it sits in was the fault that made this.
//
// Run at desktop and phone widths, in both themes, with a building open.
// Expected output: "overflow 0" and "overlaps 0".
(() => {
  const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && cs.visibility !== "hidden" && cs.display !== "none" && +cs.opacity > 0; };
  const painted = cs => (cs.backgroundColor && !/rgba\(0, 0, 0, 0\)|transparent/.test(cs.backgroundColor))
    || cs.backgroundImage !== "none" || parseFloat(cs.borderTopWidth) > 0;
  const name = el => el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") +
    (typeof el.className === "string" && el.className ? "." + el.className.trim().split(/\s+/).join(".") : "");
  const out = [];
  for (const el of document.querySelectorAll("body *")) {
    if (!vis(el) || el.closest(".maplibregl-canvas-container, .maplibregl-marker, svg")) continue;
    const cs = getComputedStyle(el);
    if (!painted(cs) || cs.position === "fixed") continue;
    let a = el.parentElement;
    while (a && a !== document.body) {
      const as = getComputedStyle(a);
      if (as.overflow !== "visible" && as.overflow !== "") break;   // clipped: cannot poke out
      if (painted(as) && as.backgroundColor !== "rgba(0, 0, 0, 0)") {
        const r = el.getBoundingClientRect(), q = a.getBoundingClientRect();
        const d = Math.max(q.left - r.left, r.right - q.right, q.top - r.top, r.bottom - q.bottom);
        if (d > 1) out.push(`${Math.round(d)}px out: ${name(el)} in ${name(a)}`);
        break;
      }
      a = a.parentElement;
    }
  }
  const floats = [...document.querySelectorAll("body *")].filter(el => {
    const cs = getComputedStyle(el);
    return vis(el) && (cs.position === "absolute" || cs.position === "fixed") && painted(cs)
      && !el.closest(".maplibregl-map") && el.getBoundingClientRect().width > 40;
  });
  const lap = [];
  for (let i = 0; i < floats.length; i++) for (let j = i + 1; j < floats.length; j++) {
    const a = floats[i], b = floats[j];
    if (a.contains(b) || b.contains(a)) continue;
    const r = a.getBoundingClientRect(), q = b.getBoundingClientRect();
    const w = Math.min(r.right, q.right) - Math.max(r.left, q.left), h = Math.min(r.bottom, q.bottom) - Math.max(r.top, q.top);
    if (w > 2 && h > 2) lap.push(`${Math.round(w)}x${Math.round(h)}px: ${name(a)} / ${name(b)}`);
  }
  return `overflow ${out.length}\n${out.join("\n")}\n\noverlaps ${lap.length}\n${lap.join("\n")}`;
})();
