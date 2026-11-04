"""Every import of a repo module and alias.attr must resolve, including in
nested packages, tools/, and function-local scopes.

Run: .venv/bin/python tests/test_xref.py"""
import ast
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]


def modpath(root, name):
    if name == "config":
        path = root / "config.py"
        return path if path.exists() else None
    if name == "src" or name.startswith("src."):
        base = root / "src"
        relative = name.removeprefix("src").lstrip(".")
    elif name == "tools" or name.startswith("tools."):
        base = root / "tools"
        relative = name.removeprefix("tools").lstrip(".")
    else:
        for directory in (root / "tools", root / "tools" / "research", root / "src"):
            path = _under(directory, name)
            if path is not None:
                return path
        return None
    return _under(base, relative)


def _under(base, relative):
    if not relative:
        if not base.exists():
            return None
        init = base / "__init__.py"
        return init if init.exists() else base
    path = base.joinpath(*relative.split("."))
    module = path.with_suffix(".py")
    if module.is_file():
        return module
    if path.is_dir():
        init = path / "__init__.py"
        return init if init.is_file() else path
    return None


_defs = {}


def top_names(path):
    if path in _defs:
        return _defs[path]
    source = path / "__init__.py" if path.is_dir() else path
    if not source.is_file():
        return set(), False
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = set()
    star = False
    for node in tree.body:
        for n in ast.walk(node) if isinstance(node, (ast.If, ast.Try, ast.With)) else [node]:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(n.name)
            elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for target in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                    names.update(x.id for x in ast.walk(target) if isinstance(x, ast.Name))
            elif isinstance(n, ast.Import):
                names.update((a.asname or a.name).split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                for alias in n.names:
                    star |= alias.name == "*"
                    names.add(alias.asname or alias.name)
            elif isinstance(n, ast.For):
                names.update(x.id for x in ast.walk(n.target) if isinstance(x, ast.Name))
    _defs[path] = names, star
    return _defs[path]


class Scope:
    def __init__(self, kind, parent=None):
        self.kind = kind
        self.parent = parent
        self.bound = set()
        self.non_imports = set()
        self.imports = {}
        self.globals = set()
        self.nonlocals = set()

    def bind(self, name):
        self.bound.add(name)
        self.non_imports.add(name)

    def bind_import(self, name, target):
        self.bound.add(name)
        if name in self.imports and self.imports[name] != target:
            self.imports[name] = None
        else:
            self.imports[name] = target

    def resolve(self, name):
        scope = self
        while scope is not None:
            if name in scope.globals:
                while scope.parent is not None:
                    scope = scope.parent
                continue
            if name in scope.nonlocals:
                scope = scope.parent
                while scope is not None and scope.kind == "class":
                    scope = scope.parent
                continue
            if name in scope.bound:
                if name in scope.non_imports:
                    return None
                return scope.imports.get(name)
            scope = scope.parent
            while scope is not None and scope.kind == "class":
                scope = scope.parent
        return None


class References(ast.NodeVisitor):
    def __init__(self, root, path, bad):
        self.root = root
        self.path = path
        self.bad = bad
        self.module_scope = Scope("module")
        self.scope = self.module_scope
        self.attributes = []

    def error(self, line, message):
        self.bad.append(f"{self.path.relative_to(self.root)}:{line} {message}")

    def _repository_module(self, name):
        path = modpath(self.root, name)
        if path is None and (name in ("src", "tools") or name.startswith(("src.", "tools."))):
            return None
        return path

    def visit_Import(self, node):
        for alias in node.names:
            target = alias.name if alias.asname else alias.name.split(".")[0]
            path = self._repository_module(alias.name)
            if path is None and (alias.name in ("src", "tools") or alias.name.startswith(("src.", "tools."))):
                self.error(node.lineno, f"module {alias.name} missing")
            elif path is not None:
                self.scope.bind_import(alias.asname or alias.name.split(".")[0], target)

    def visit_ImportFrom(self, node):
        if node.level or not node.module:
            for alias in node.names:
                self.scope.bind_import(alias.asname or alias.name, None)
            return

        base = self._repository_module(node.module)
        if base is None:
            if node.module in ("src", "tools") or node.module.startswith(("src.", "tools.")):
                self.error(node.lineno, f"module {node.module} missing")
            for alias in node.names:
                self.scope.bind_import(alias.asname or alias.name, None)
            return

        names, star = top_names(base)
        for alias in node.names:
            bound_name = alias.asname or alias.name
            if alias.name == "*":
                self.scope.bind_import(bound_name, None)
                continue
            submodule = modpath(self.root, f"{node.module}.{alias.name}")
            if submodule is not None:
                self.scope.bind_import(bound_name, f"{node.module}.{alias.name}")
            elif alias.name in names or star:
                self.scope.bind_import(bound_name, None)
            else:
                self.error(node.lineno, f"{node.module}.{alias.name} not defined")
                self.scope.bind_import(bound_name, None)

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.scope.bind(node.id)

    def visit_Global(self, node):
        self.scope.globals.update(node.names)

    def visit_Nonlocal(self, node):
        self.scope.nonlocals.update(node.names)

    def visit_ExceptHandler(self, node):
        if node.type:
            self.visit(node.type)
        if node.name:
            self.scope.bind(node.name)
        for statement in node.body:
            self.visit(statement)

    def visit_Attribute(self, node):
        if isinstance(node.ctx, ast.Load):
            self.attributes.append((self.scope, node))
        self.generic_visit(node)

    def _visit_comprehension(self, node, values):
        first = node.generators[0]
        self.visit(first.iter)
        previous = self.scope
        self.scope = Scope("comprehension", previous)
        for index, generator in enumerate(node.generators):
            if index:
                self.visit(generator.iter)
            self.visit(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for value in values:
            self.visit(value)
        self.scope = previous

    def visit_ListComp(self, node):
        self._visit_comprehension(node, (node.elt,))

    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node):
        self._visit_comprehension(node, (node.key, node.value))

    def _visit_scope(self, node, kind, args=()):
        child = Scope(kind, self.scope)
        for arg in args:
            child.bind(arg.arg)
        previous = self.scope
        self.scope = child
        for statement in node.body:
            self.visit(statement)
        self.scope = previous

    def visit_FunctionDef(self, node):
        self.scope.bind(node.name)
        for item in (*node.decorator_list, *node.args.defaults, *node.args.kw_defaults):
            if item is not None:
                self.visit(item)
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            if arg.annotation:
                self.visit(arg.annotation)
        if node.args.vararg and node.args.vararg.annotation:
            self.visit(node.args.vararg.annotation)
        if node.args.kwarg and node.args.kwarg.annotation:
            self.visit(node.args.kwarg.annotation)
        if node.returns:
            self.visit(node.returns)
        args = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        if node.args.vararg:
            args += (node.args.vararg,)
        if node.args.kwarg:
            args += (node.args.kwarg,)
        self._visit_scope(node, "function", args)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.scope.bind(node.name)
        for item in (*node.decorator_list, *node.bases, *(kw.value for kw in node.keywords)):
            self.visit(item)
        self._visit_scope(node, "class")

    def visit_Lambda(self, node):
        args = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        if node.args.vararg:
            args += (node.args.vararg,)
        if node.args.kwarg:
            args += (node.args.kwarg,)
        child = Scope("function", self.scope)
        for arg in args:
            child.bind(arg.arg)
        previous = self.scope
        self.scope = child
        self.visit(node.body)
        self.scope = previous

    def check_attributes(self):
        for scope, node in self.attributes:
            if not isinstance(node.value, ast.Name):
                continue
            name = node.value.id
            target = scope.resolve(name)
            if target is None:
                continue
            if modpath(self.root, f"{target}.{node.attr}") is not None:
                continue
            names, star = top_names(modpath(self.root, target))
            if node.attr not in names and not star:
                self.error(node.lineno, f"{name}.{node.attr} not defined")


def scan(root):
    root = Path(root).resolve()
    files = sorted(
        p for directory in ("src", "tools", "tests")
        for p in (root / directory).rglob("*.py")
    )
    config = root / "config.py"
    if config.is_file():
        files.append(config)
    bad = []
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            bad.append(f"{path.relative_to(root)}: syntax error {exc}")
            continue
        refs = References(root, path, bad)
        refs.visit(tree)
        refs.check_attributes()
    return sorted(set(bad))


def test_regression_fixtures():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        files = {
            "config.py": "VALUE = 1\n",
            "src/__init__.py": "",
            "src/nested/__init__.py": "",
            "src/nested/worker.py": "VALUE = 1\n",
            "tools/__init__.py": "",
            "tools/pipeline_webui/__init__.py": "",
            "tools/pipeline_webui/jobs.py": "def run(): pass\n",
            "tools/pipeline_webui/common.py": "def write(): pass\n",
            "tests/caller.py": (
                "import config\n"
                "import tools.pipeline_webui.jobs\n"
                "import src.nested.worker as worker\n"
                "import tools.pipeline_webui.common as common_alias\n"
                "from tools.pipeline_webui import jobs as job_module\n"
                "def shared(config): return config.get('x')\n"
                "def local():\n"
                "    config = {}\n"
                "    return config.get('x')\n"
                "[config.get('x') for config in []]\n"
                "config.VALUE\n"
                "def use(): return (worker.VALUE, common_alias.write(), job_module.run(), "
                "tools.pipeline_webui.jobs.run())\n"
            ),
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        assert scan(root) == [], scan(root)

        caller = root / "tests" / "caller.py"
        caller.write_text(
            caller.read_text(encoding="utf-8")
            + "import tools.pipeline_webui.missing\n"
            + "from src.nested import absent\n",
            encoding="utf-8",
        )
        failures = scan(root)
        assert any("tools.pipeline_webui.missing missing" in error for error in failures), failures
        assert any("src.nested.absent not defined" in error for error in failures), failures


if __name__ == "__main__":
    test_regression_fixtures()
    unresolved = scan(ROOT)
    for error in unresolved:
        print(error)
    print(f"xref: {len(unresolved)} unresolved")
    sys.exit(1 if unresolved else 0)
