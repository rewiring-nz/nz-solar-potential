"""
What built what: code fingerprints for stages, and per-building build keys.

TWO QUESTIONS A RESUMABLE, INCREMENTAL BUILD HAS TO ANSWER, and until 24 Sep
2026 it could answer neither from the code:

1. Is this stage's marker still valid? run_stage.py compared file mtimes, so a
   CODE change never invalidated anything: fix a bug in panel_fitting.py,
   run the district build with --skip-done, and every region skipped every
   stage on its old marker. stage_code_hash() fingerprints a stage's script,
   every src module it imports (transitively), config.py and the lock file,
   and the marker records it.

2. Which buildings are stale? data/built_from.json recorded only a hash of
   each building's selected-faces reading, so a code change never made a
   building stale either, and buildings with no reading were never checked at
   all. building_key() is the reading hash PLUS the geometry code hash, for
   every building; a building is stale when its recorded key differs.

Keys live per region (data/regions/<r>/built_from.json) so fleet workers that
build different regions never write the same file. The old global file is
still read, but its entries carry no code hash, so every building it lists
reads as stale once -- correctly: nothing says which code built it.
"""

import ast
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DATA = ROOT / "data"

# The stage whose code decides a building's geometry (and everything it
# imports: segmentation, partition, panel fitting, obstructions, shading).
GEOMETRY_STAGE = "build_layout_geojson"

_closure_cache = {}


def _src_imports(path):
    """src modules imported anywhere in `path` (module level or inside
    functions -- most of this repo imports lazily)."""
    try:
        tree = ast.parse(path.read_text())
    except (OSError, SyntaxError):
        return set()
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            if n.module == "src":
                out.update(a.name for a in n.names)
            elif n.module.startswith("src."):
                out.add(n.module.split(".")[1])
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.startswith("src."):
                    out.add(a.name.split(".")[1])
    return {m for m in out if (SRC / f"{m}.py").exists()}


def module_closure(stage):
    """Every src module `stage` can reach through imports, itself included."""
    if stage in _closure_cache:
        return _closure_cache[stage]
    seen, todo = set(), [stage]
    while todo:
        m = todo.pop()
        if m in seen or not (SRC / f"{m}.py").exists():
            continue
        seen.add(m)
        todo.extend(_src_imports(SRC / f"{m}.py") - seen)
    _closure_cache[stage] = sorted(seen)
    return _closure_cache[stage]


def stage_code_hash(stage):
    """Hash of the code a stage runs: its closure of src modules, config.py and
    the dependency lock. Twelve hex characters. A tools/ script imported by a
    stage at runtime (face_regions -> tools/train_face_regions) is not
    followed; neither is data such as models, which stage inputs cover."""
    h = hashlib.sha256()
    for m in module_closure(stage):
        h.update(m.encode())
        h.update((SRC / f"{m}.py").read_bytes())
    for extra in (ROOT / "config.py", ROOT / "requirements.lock.txt"):
        if extra.exists():
            h.update(extra.name.encode())
            h.update(extra.read_bytes())
    return h.hexdigest()[:12]


def reading_hash(building_id):
    """Hash of the selected-faces reading a building is built from, or "none".
    Same 12-character md5 the original built_from.json recorded."""
    p = DATA / "selected_faces" / f"{building_id}.json"
    return hashlib.md5(p.read_bytes()).hexdigest()[:12] if p.exists() else "none"


def building_key(building_id, geometry_hash=None):
    """What a building's layout is built from: its reading and the geometry
    code. Drawn markup (data/roof_labels.json) is an input too and is in the
    key through its own hash, so a newly drawn roof reads as stale."""
    return f"{reading_hash(building_id)}+{labels_hash(building_id)}+" \
           f"{geometry_hash or stage_code_hash(GEOMETRY_STAGE)}"


_labels = None


def labels_hash(building_id):
    global _labels
    if _labels is None:
        try:
            _labels = json.loads((DATA / "roof_labels.json").read_text())
        except (OSError, ValueError):
            _labels = {}
        if isinstance(_labels, dict) and "buildings" in _labels:
            _labels = _labels["buildings"]
    lab = _labels.get(str(building_id)) if isinstance(_labels, dict) else None
    if lab is None:
        return "nolab"
    return hashlib.md5(json.dumps(lab, sort_keys=True).encode()).hexdigest()[:8]


def keys_path(region):
    return DATA / "regions" / region / "built_from.json"


def load_keys(region):
    try:
        return json.loads(keys_path(region).read_text())
    except (OSError, ValueError):
        return {}


def record_keys(region, building_ids, geometry_hash=None, replace=False):
    """Record the current key for these buildings (after building them).
    replace=True starts the file afresh -- a full region build, after which
    no other building's old key should survive."""
    gh = geometry_hash or stage_code_hash(GEOMETRY_STAGE)
    keys = {} if replace else load_keys(region)
    for bid in building_ids:
        keys[str(int(bid))] = building_key(int(bid), gh)
    p = keys_path(region)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(keys, sort_keys=True))
    os.replace(tmp, p)


def stale_buildings(region, building_ids, geometry_hash=None):
    """The subset of building_ids whose recorded key differs from now."""
    gh = geometry_hash or stage_code_hash(GEOMETRY_STAGE)
    keys = load_keys(region)
    return [int(b) for b in building_ids if keys.get(str(int(b))) != building_key(int(b), gh)]
