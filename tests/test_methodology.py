"""
The methodology diagram ("How this map is made") must describe the code.

Fails when:
  * methodology.json is not what src/methodology.py writes from the code now;
  * a build stage in src/build_plan.py is in no item of the diagram;
  * an item cites a file or a top-level function/constant that is gone;
  * an item names a build stage the plan does not have;
  * config names a LINZ layer that data/source_metadata.json does not describe.

Run:  python tests/test_methodology.py
"""
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import build_plan, methodology  # noqa: E402

failures = []


def check(name, fn):
    try:
        fn()
        print(f"  pass  {name}")
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL  {name}: {e}")
        failures.append(name)


def _top_names(path: Path) -> set:
    tree = ast.parse(path.read_text())
    names = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            names |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
    return names


def items():
    for s in methodology.STEPS:
        for it in s["items"]:
            yield s, it


def t_current():
    built = json.dumps(methodology.build(), indent=1, ensure_ascii=False) + "\n"
    have = methodology.OUT.read_text() if methodology.OUT.exists() else ""
    if built != have:
        raise AssertionError("methodology.json is out of date: run python src/methodology.py")


def t_every_stage_placed():
    plan = ({p.name for p in build_plan.PREPARE} | set(build_plan.STAGES)
            | set(build_plan.YIELD_ONLY_STAGES) | {f.name for f in build_plan.FINISH})
    placed = {st for _, it in items() for st in it.get("stages", [])}
    missing = sorted(plan - placed)
    if missing:
        raise AssertionError(f"build stages not in the diagram: {missing}")
    unknown = sorted(st for st in placed - plan if ":" not in st)
    if unknown:
        raise AssertionError(f"diagram names stages the plan does not have: {unknown}")


def t_code_refs_resolve():
    bad = []
    for s, it in items():
        for ref in it.get("code", []):
            if "~~" in ref:     # word-for-word text in the file
                path, _, text = ref.partition("~~")
                p = ROOT / path
                if not p.exists() or text not in p.read_text():
                    bad.append(f"{ref} (text not in file)")
                continue
            path, _, sym = ref.partition("::")
            p = ROOT / path
            if not p.exists():
                bad.append(f"{ref} (no file)")
            elif sym and p.suffix == ".py" and sym not in _top_names(p):
                bad.append(f"{ref} (no top-level {sym})")
            elif sym and p.suffix != ".py" and sym not in p.read_text():
                bad.append(f"{ref} ({sym} not in file)")
    if bad:
        raise AssertionError("; ".join(bad))


def t_fetch_and_page_refs():
    # "fetch:<module>" names an input-preparation script; "page:<file>" a page file
    bad = []
    for _, it in items():
        for st in it.get("stages", []):
            kind, _, name = st.partition(":")
            if kind == "fetch" and not (ROOT / name).exists():
                bad.append(st)
            if kind == "page" and not (ROOT / name).exists():
                bad.append(st)
    if bad:
        raise AssertionError(f"missing: {bad}")


def t_every_layer_described():
    methodology.sources()   # raises on an undescribed layer


for name, fn in [("methodology.json is current", t_current),
                 ("every build stage is in the diagram", t_every_stage_placed),
                 ("every code reference resolves", t_code_refs_resolve),
                 ("every fetch and page reference exists", t_fetch_and_page_refs),
                 ("every LINZ layer config names is described", t_every_layer_described)]:
    check(name, fn)

print(f"\n{5 - len(failures)} passed, {len(failures)} failed")
sys.exit(1 if failures else 0)
