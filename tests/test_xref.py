"""Every `from X import name` and `alias.attr` on a repo module, anywhere in the
repo (module level or inside functions), must resolve to a name the target
module defines at top level. Prints the unresolved ones; exit 1 if any.

WHY. test_imports_and_arity imports every src module, which only exercises
module-level imports. Most of this repo imports inside functions, and tools/
is never imported at all, so a deleted or renamed function could leave a
caller that fails only when that path finally runs on the VM. Written for the
24 Sep 2026 cleanup, which deleted 1,100 lines and needed proof that nothing
still reached them.

Run: .venv/bin/python tests/test_xref.py"""
import ast
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
FILES = [p for d in ("src", "tools", "tests") for p in (ROOT / d).glob("*.py")] + [ROOT / "config.py"]


def modpath(name):
    if name.startswith("src."):
        p = ROOT / "src" / (name[4:].replace(".", "/") + ".py")
    elif name.startswith("tools."):
        p = ROOT / "tools" / (name[6:] + ".py")
    elif name == "config":
        p = ROOT / "config.py"
    else:  # tools import each other by bare name via sys.path
        p = ROOT / "tools" / (name + ".py")
        if not p.exists():
            p = ROOT / "src" / (name + ".py")
    return p if p.exists() else None


_defs = {}


def top_names(p):
    if p in _defs:
        return _defs[p]
    tree = ast.parse(p.read_text())
    names = set()
    star = False
    for node in tree.body:
        for n in ast.walk(node) if isinstance(node, (ast.If, ast.Try, ast.With)) else [node]:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(n.name)
            elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                    for x in ast.walk(t):
                        if isinstance(x, ast.Name):
                            names.add(x.id)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    names.add((a.asname or a.name).split(".")[0])
            elif isinstance(n, ast.ImportFrom):
                for a in n.names:
                    if a.name == "*":
                        star = True
                    names.add(a.asname or a.name)
            elif isinstance(n, (ast.For,)):
                for x in ast.walk(n.target):
                    if isinstance(x, ast.Name):
                        names.add(x.id)
    _defs[p] = (names, star)
    return _defs[p]


bad = []
for f in FILES:
    try:
        tree = ast.parse(f.read_text())
    except SyntaxError as e:
        bad.append(f"{f}: syntax error {e}")
        continue
    aliases = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            if n.module in ("src", "tools"):
                for a in n.names:
                    sub = modpath(f"{n.module}.{a.name}")
                    if sub is None:
                        bad.append(f"{f.relative_to(ROOT)}:{n.lineno} module {n.module}.{a.name} missing")
                    else:
                        aliases[a.asname or a.name] = sub
                continue
            mp = modpath(n.module)
            if mp is None:
                if n.module.startswith(("src", "tools")):
                    bad.append(f"{f.relative_to(ROOT)}:{n.lineno} module {n.module} missing")
                continue
            names, star = top_names(mp)
            for a in n.names:
                if a.name == "*":
                    continue
                sub = modpath(f"{n.module}.{a.name}") if n.module in ("src", "tools") else None
                if a.name not in names and not star and sub is None:
                    bad.append(f"{f.relative_to(ROOT)}:{n.lineno} {n.module}.{a.name} not defined")
                if sub is not None:
                    aliases[a.asname or a.name] = sub
        elif isinstance(n, ast.Import):
            for a in n.names:
                mp = modpath(a.name)
                if mp is not None and (a.name.startswith(("src.", "tools.")) or a.name == "config"):
                    aliases[a.asname or a.name] = mp
    for n in ast.walk(tree):
        if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                and n.value.id in aliases and isinstance(n.ctx, ast.Load)):
            names, star = top_names(aliases[n.value.id])
            if n.attr not in names and not star:
                bad.append(f"{f.relative_to(ROOT)}:{n.lineno} {n.value.id}.{n.attr} not defined")
for b in sorted(set(bad)):
    print(b)
print(f"xref: {len(set(bad))} unresolved")
sys.exit(1 if bad else 0)
