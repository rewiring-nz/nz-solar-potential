// Side-effect-free region tree for the Build data page (pipeline.html):
// council > town > locality from the national catalogue, joined with build
// status, plus grid cells under a locality while a gridded build is open.
(function (root) {
  "use strict";

  // Lifecycle order for map colours and the status sort; failed stands apart.
  const STATES = ["none", "queued", "running", "built", "staged", "published", "failed"];
  const STATE_LABELS = {
    none: "Not built", queued: "Queued", running: "Running", built: "Built",
    staged: "Staged", published: "Live", failed: "Failed",
  };
  // Okabe-Ito based, colour-blind safe.
  const STATE_COLOURS = {
    none: "#c9ced4", queued: "#e6c229", running: "#56b4e9", built: "#009e73",
    staged: "#0072b2", published: "#5b3f99", failed: "#d55e00",
  };
  const ACTIVE = new Set(["queued", "running"]);
  const UNSUCCESSFUL = new Set(["failed", "cancelled", "interrupted"]);
  const GRID_BUILDINGS_PER_CELL = 12000;
  const OTHER_ID = "__other__";

  // An attempt counts for a target when it ran there; runs started outside
  // the panel have no job and ran locally.
  function forTarget(history, target) {
    return (history || []).filter((h) => (h.target || "local") === target);
  }

  // One row's view for a target. Local reads the status join; developer and
  // published read the remote area record plus this panel's cloud jobs.
  function rowView(status, remote, target) {
    status = status || {};
    const history = forTarget(status.history, target);
    const latest = history[0] || null;
    if (target === "local") {
      return {
        latest, history, built: !!status.built, built_at: status.built_at || null,
        stale: status.stale === true, options: status.options || null,
        staged: status.staged || null, live: status.live || null,
        n: status.n, kwp: status.kwp, preview_url: status.preview_url || null,
      };
    }
    remote = remote || {};
    return {
      latest, history, built: remote.state === "built", built_at: remote.built_at || null,
      stale: false, options: null, staged: null,
      live: target === "published" && remote.state === "built" ? (remote.data_version || "live") : null,
      n: remote.n, kwp: remote.kwp, preview_url: remote.preview_url || null,
    };
  }

  function lifecycle(view) {
    const latest = view.latest;
    if (latest && ACTIVE.has(latest.status)) return latest.status;
    if (latest && UNSUCCESSFUL.has(latest.status) && latest.status !== "cancelled") return "failed";
    if (view.live) return "published";
    if (view.staged && String(view.staged.built_at || "") >= String(view.built_at || "")) return "staged";
    if (view.built) return "built";
    if (latest && latest.status === "cancelled") return "none";
    return "none";
  }

  function autoGridSize(buildings) {
    if (!buildings || buildings <= GRID_BUILDINGS_PER_CELL) return null;
    return Math.min(10, Math.max(2, Math.ceil(Math.sqrt(buildings / GRID_BUILDINGS_PER_CELL))));
  }

  function roughSeconds(buildings, calibration) {
    if (buildings == null || !calibration) return null;
    return calibration.fixed_overhead_s + buildings * calibration.seconds_per_building;
  }

  const pretty = (slug) => String(slug).replace(/-/g, " ").replace(/\b[a-z]/g, (c) => c.toUpperCase());

  // catalog: national_catalog regions; localIds: region ids with local
  // status that the catalogue does not list (pilot, legacy, renamed areas);
  // grids: region_status grids keyed by locality id.
  function buildTree(catalogRegions, localIds, grids) {
    const councils = new Map();
    const leaves = new Map();
    const parents = new Map();
    for (const r of catalogRegions || []) {
      let ta = councils.get(r.ta_slug);
      if (!ta) {
        ta = { id: r.ta_slug, name: r.council, kind: "council", children: [], index: new Map() };
        councils.set(r.ta_slug, ta);
      }
      const townId = r.ta_slug + "/" + r.town_slug;
      let town = ta.index.get(townId);
      if (!town) {
        town = { id: townId, name: r.town || "(no town)", kind: "town", children: [] };
        ta.index.set(townId, town);
        ta.children.push(town);
        parents.set(townId, [ta]);
      }
      const leaf = {
        id: r.region, name: r.name, kind: "locality", buildings: r.buildings,
        area_km2: r.area_km2, bbox: r.bbox, linz_id: r.linz_id, type: r.type,
      };
      town.children.push(leaf);
      leaves.set(leaf.id, leaf);
      parents.set(leaf.id, [ta, town]);
    }
    const nodes = [...councils.values()];
    const extra = (localIds || []).filter((id) => !leaves.has(id) && !/\/grid-[0-9a-f]{16}$/.test(id));
    if (extra.length) {
      const other = { id: OTHER_ID, name: "Other local areas", kind: "council", children: [] };
      for (const id of extra.sort()) {
        const leaf = { id, name: pretty(id.split("/").pop()), kind: "locality", buildings: null,
                       area_km2: null, bbox: null, linz_id: null, legacy: true };
        other.children.push(leaf);
        leaves.set(id, leaf);
        parents.set(id, [other]);
      }
      nodes.push(other);
    }
    for (const [parentId, grid] of Object.entries(grids || {})) {
      const locality = leaves.get(parentId);
      if (!locality) continue;
      locality.grid = grid;
      locality.children = Object.entries(grid.cells || {}).sort(([a], [b]) => a.localeCompare(b)).map(
        ([gridId, cell]) => {
          const leaf = { id: cell.region, name: "Cell " + gridId, kind: "cell", grid_id: gridId,
                         buildings: cell.buildings == null ? null : cell.buildings,
                         parent_region: parentId, linz_id: locality.linz_id };
          leaves.set(leaf.id, leaf);
          parents.set(leaf.id, [...parents.get(parentId), locality]);
          return leaf;
        });
    }
    const sortNodes = (list) => {
      list.sort((a, b) => (a.id === OTHER_ID) - (b.id === OTHER_ID) || String(a.name).localeCompare(String(b.name)));
      list.forEach((n) => { delete n.index; if (n.children && n.kind !== "locality") sortNodes(n.children); });
    };
    sortNodes(nodes);
    return { nodes, leaves, parents };
  }

  // Selectable leaves under a node: a gridded locality's cells, otherwise the
  // locality itself.
  function leavesOf(node) {
    if (node.kind === "cell") return [node];
    if (node.kind === "locality") return node.children && node.children.length ? node.children : [node];
    return node.children.flatMap(leavesOf);
  }

  function breadcrumb(tree, id) {
    return (tree.parents.get(id) || []).map((p) => p.name).join(" › ");
  }

  function matchesQuery(tree, leaf, query) {
    if (!query) return true;
    const text = (breadcrumb(tree, leaf.id) + " " + leaf.name + " " + leaf.id).toLowerCase();
    return query.toLowerCase().split(/\s+/).filter(Boolean).every((word) => text.includes(word));
  }

  // Fixed filters; each is a predicate on a row view and its state.
  const FILTERS = [
    ["built", "Built OK", (v, s) => ["built", "staged", "published"].includes(s)],
    ["failed", "Failed", (v, s) => s === "failed"],
    ["active", "Queued / running", (v, s) => s === "queued" || s === "running"],
    ["never", "Never built", (v) => !v.built && !v.history.length],
    ["stale", "Stale", (v) => v.stale],
    ["no-imagery", "Built without imagery", (v) => v.built && !!v.options && v.options.include_imagery === false],
    ["with-imagery", "Built with imagery", (v) => v.built && !!v.options && v.options.include_imagery === true],
    ["referee", "Built with paid referee", (v) => v.built && !!v.options && v.options.paid_referee === true],
    ["not-staged", "Built, not staged", (v, s) => s === "built"],
  ];

  function passesFilters(view, state, active) {
    for (const key of active || []) {
      const filter = FILTERS.find((f) => f[0] === key);
      if (filter && !filter[2](view, state)) return false;
    }
    return true;
  }

  // Selection state of a group over its visible leaves.
  function checkState(node, selected, visible) {
    const leaves = leavesOf(node).filter(visible);
    const ticked = leaves.filter((l) => selected.has(l.id)).length;
    return ticked === 0 ? "none" : ticked === leaves.length ? "all" : "some";
  }

  const SORTS = {
    geo: null,
    completed: (a, b) => String(b.view.built_at || "").localeCompare(String(a.view.built_at || "")),
    attempt: (a, b) => String((b.view.latest || {}).at || "").localeCompare(String((a.view.latest || {}).at || "")),
    status: (a, b) => STATES.indexOf(a.state) - STATES.indexOf(b.state),
    buildings: (a, b) => (b.leaf.buildings || 0) - (a.leaf.buildings || 0),
    name: (a, b) => String(a.leaf.name).localeCompare(String(b.leaf.name)),
  };

  const api = {
    STATES, STATE_LABELS, STATE_COLOURS, FILTERS, SORTS, OTHER_ID, GRID_BUILDINGS_PER_CELL,
    autoGridSize, breadcrumb, buildTree, checkState, forTarget, leavesOf, lifecycle,
    matchesQuery, passesFilters, roughSeconds, rowView,
  };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PipelineTree = api;
})(globalThis);
