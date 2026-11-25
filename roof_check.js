// Card choice for the volunteer roof check (check_roofs.html).
// Rules: docs/maintainers/roof-check.md. Pure, so tests/test_roof_check.js
// runs the same code as the page.
//
// One person sees each roof aspect once: a card's "seen key" is its photo and
// aspect, so the real drawing and a planted copy of the same roof aspect never
// both reach one person (side by side, the planted fault would give itself
// away). A round takes the aspects in turn, so no one checks only lines.
(function (root) {
  function seenKey(card) { return card.photo + ":" + card.aspect; }

  function shuffle(a, rnd) {
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(rnd() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  // cards: the deck's cards; seen: Set of seen keys; n: cards in a round.
  function pickRound(cards, seen, n, rnd) {
    rnd = rnd || Math.random;
    const byAspect = {};
    shuffle(cards.slice(), rnd).forEach(c => {
      if (seen.has(seenKey(c))) return;
      (byAspect[c.aspect] = byAspect[c.aspect] || []).push(c);
    });
    const aspects = shuffle(Object.keys(byAspect), rnd);
    const out = [], taken = new Set();
    let progress = true;
    while (out.length < n && progress) {
      progress = false;
      for (const a of aspects) {
        if (out.length >= n) break;
        const list = byAspect[a];
        while (list.length && taken.has(seenKey(list[0]))) list.shift();
        if (!list.length) continue;
        const c = list.shift();
        taken.add(seenKey(c));
        out.push(c);
        progress = true;
      }
    }
    return out;
  }

  function newVolunteerId(rnd) {
    rnd = rnd || Math.random;
    let s = "";
    for (let i = 0; i < 16; i++) s += Math.floor(rnd() * 16).toString(16);
    return s;
  }

  const api = { seenKey, pickRound, newVolunteerId };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.RoofCheck = api;
})(this);
