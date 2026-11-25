"""
The methodology diagram ("How this map is made") must describe the code.

Fails when:
  * methodology.json is not what src/methodology.py writes from the code now;
  * a build stage in src/build_plan.py is in no item of the diagram;
  * an item cites a file or a top-level function/constant that is gone;
  * an item names a build stage the plan does not have;
  * a stable national LINZ layer is missing from data/source_metadata.json;
  * an item lacks why / try / fails / change, or names a SOLAR_* switch no
    code reads, a test file that is gone, a label the repository does not
    use, or a judge JUDGES does not define;
  * a judge's command names a repo file that is gone;
  * a word list picks no open roof (it would show an empty list), or a
    picture's cuts fall outside its slider.

Run:  python tests/test_methodology.py
"""
import ast
import json
import re
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


def t_every_item_explains_itself():
    bad = []
    for s, it in items():
        w = f"{s['title']} / {it['title']}"
        if not it.get("why"):
            bad.append(f"{w}: no why")
        if not it.get("try"):
            bad.append(f"{w}: nothing to try")
        f = it.get("fails", {})
        if not (f.get("words") or f.get("labels") or f.get("known")):
            bad.append(f"{w}: says nowhere where it fails")
        c = it.get("change", {})
        if not c.get("tests"):
            bad.append(f"{w}: no test proves a change")
        if c.get("judge") not in methodology.JUDGES:
            bad.append(f"{w}: judge {c.get('judge')!r} is not in JUDGES")
    if bad:
        raise AssertionError("; ".join(bad))


def t_change_refs_are_real():
    bad = []
    for s, it in items():
        c = it.get("change", {})
        for t in c.get("tests", []):
            if not (ROOT / t).is_file():
                bad.append(f"{t} (no file)")
        for sw in c.get("switches", []):
            try:
                home = methodology.switch_home(sw, it.get("code", []))
            except KeyError:
                bad.append(f"{sw} (no code reads it)")
                continue
            if not re.search(r"environ|getenv|\$\{?" + sw, (ROOT / home).read_text()):
                bad.append(f"{sw} ({home} does not read the environment)")
        for lab in it.get("fails", {}).get("labels", []):
            if lab not in methodology.LABELS:
                bad.append(f"label {lab!r} not in LABELS")
    if bad:
        raise AssertionError("; ".join(bad))


def t_judges_name_real_files():
    bad = []
    for k, j in methodology.JUDGES.items():
        for line in j["run"]:
            for path in re.findall(r"(?:tools|tests|src)/[\w./-]+\.(?:py|mjs|json|js|sh)\b", line):
                if not (ROOT / path).exists():
                    bad.append(f"{k}: {path}")
        for path in j["files"]:
            if not (ROOT / path).exists():
                bad.append(f"{k}: {path}")
    used = {it.get("change", {}).get("judge") for _, it in items()}
    unused = sorted(set(methodology.JUDGES) - used)
    if unused:
        bad.append(f"judges no item uses: {unused}")
    if bad:
        raise AssertionError("; ".join(bad))


def t_case_words_find_roofs():
    cases = methodology.load_cases()
    if not cases:
        raise AssertionError(f"{methodology.CASES} has no cases")
    bad = [f"{s['title']} / {it['title']}: {w!r}"
           for s, it in items() for w in it.get("fails", {}).get("words", [])
           if not methodology._cases_for([w], cases)]
    if bad:
        raise AssertionError("words that pick no open roof: " + "; ".join(bad))


def t_pictures_fit():
    bad = []
    for s in methodology.build()["steps"]:
        for it in s["items"]:
            sh = it.get("show")
            if sh and sh["kind"] == "threshold":
                ats = [c["at"] for c in sh["cuts"]]
                if ats != sorted(ats) or not all(sh["min"] < a < sh["max"] for a in ats):
                    bad.append(f"{it['title']}: cuts {ats} outside {sh['min']}..{sh['max']} or out of order")
            if sh and sh["kind"] == "losses":
                total = sum(p["pct"] for p in sh["parts"])
                # losses multiply, so kept is not 100 - sum; it must be close and below
                if not (100 - total - 1 <= sh["kept"]["pct"] <= 100 - total + 1):
                    bad.append(f"{it['title']}: kept {sh['kept']['pct']} vs losses {total}")
    if bad:
        raise AssertionError("; ".join(bad))


TESTS = [("methodology.json is current", t_current),
                 ("every build stage is in the diagram", t_every_stage_placed),
                 ("every code reference resolves", t_code_refs_resolve),
                 ("every fetch and page reference exists", t_fetch_and_page_refs),
                 ("every national LINZ layer is described", t_every_layer_described),
                 ("every item says why, what else, where it fails, how to change it", t_every_item_explains_itself),
                 ("every switch, test and label is real", t_change_refs_are_real),
                 ("every judge names real files and is used", t_judges_name_real_files),
                 ("every case word finds an open roof", t_case_words_find_roofs),
                 ("every picture fits its figures", t_pictures_fit)]
for name, fn in TESTS:
    check(name, fn)

print(f"\n{len(TESTS) - len(failures)} passed, {len(failures)} failed")
sys.exit(1 if failures else 0)
