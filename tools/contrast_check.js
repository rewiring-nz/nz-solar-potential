// Text contrast check for rooftop-solar.html, run in the browser console
// (or a browser automation tool) with a building open.
//
// Opens every <details>, then lists each visible text element whose colour
// against its effective background is under WCAG AA: 4.5:1, or 3:1 for
// large text (24px, or 18.66px bold). It runs once in the light theme and
// once in the dark one. A see-through ground (a panel over the map photo)
// is tested over black, white and mid-grey and the worst is reported.
// Text on a gradient is skipped.
//
// Run before every front-end release: open a building in Panel Layout, run
// it, switch to Heat Map, run it again, and repeat at phone width. Expected
// output in both themes: only the AI banner's close glyph (4.3:1, an icon
// control, where 3:1 applies).
(() => {
  const parse = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const lum = ([r, g, b]) => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const over = (top, bot) => { const a = top[3]; return [0, 1, 2].map(i => top[i] * a + bot[i] * (1 - a)).concat([1]); };
  const check = () => {
    const out = [], seen = new Set();
    for (const el of document.querySelectorAll("body *")) {
      const cs = getComputedStyle(el);
      if (!el.offsetParent && cs.position !== "fixed") continue;
      const txt = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(" ").trim();
      if (!txt) continue;
      const r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2) continue;
      if (cs.visibility === "hidden" || +cs.opacity === 0) continue;
      const fg = parse(cs.color); if (!fg || fg[3] === 0) continue;
      const layers = []; let e = el, grad = false;
      while (e && e !== document.documentElement) {
        const s = getComputedStyle(e), b = parse(s.backgroundColor);
        if (s.backgroundImage && s.backgroundImage.includes("gradient")) { grad = true; break; }
        if (b && b[3] > 0) { layers.push(b); if (b[3] >= 0.95) break; }
        e = e.parentElement;
      }
      if (grad) continue;
      const opaque = layers.length && layers[layers.length - 1][3] >= 0.95;
      const bases = opaque ? [[255, 255, 255, 1]] : [[0, 0, 0, 1], [255, 255, 255, 1], [128, 128, 128, 1]];
      let worst = 99;
      for (const base of bases) {
        let c = base; for (let i = layers.length - 1; i >= 0; i--) c = over(layers[i], c);
        const f = over(fg, c), L1 = lum(f), L2 = lum(c);
        worst = Math.min(worst, (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05));
      }
      const size = parseFloat(cs.fontSize), bold = +cs.fontWeight >= 700;
      const need = (size >= 24 || (bold && size >= 18.66)) ? 3 : 4.5;
      if (worst < need) {
        const cn = typeof el.className === "string" ? el.className : "";
        const key = cn + "|" + txt.slice(0, 30); if (seen.has(key)) continue; seen.add(key);
        out.push(worst.toFixed(2) + " " + (opaque ? "" : "[see-through] ") + "<" + el.tagName.toLowerCase() + "." + cn + "> \"" + txt.slice(0, 45) + "\" " + cs.color);
      }
    }
    return out.sort();
  };
  document.querySelectorAll("details").forEach(d => { d.open = true; });
  const root = document.documentElement, wasLight = root.classList.contains("light");
  root.classList.add("light"); const light = check();
  root.classList.remove("light"); const dark = check();
  root.classList.toggle("light", wasLight);
  return "LIGHT " + light.length + "\n" + light.join("\n") + "\n\nDARK " + dark.length + "\n" + dark.join("\n");
})();
