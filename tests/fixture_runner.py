"""Run a test module's test_* functions without pytest, which this repo's
venv does not install. Supplies the two pytest fixtures the label tests use,
`tmp_path` and `monkeypatch`, undoing every patch after each test so one test's
labels never leak into the next."""
import inspect
import os
import sys
import tempfile
from pathlib import Path

_UNSET = object()


class MonkeyPatch:
    def __init__(self):
        self._undo = []

    def setattr(self, obj, name, value):
        self._undo.append((setattr, obj, name, getattr(obj, name, _UNSET)))
        setattr(obj, name, value)

    def setenv(self, name, value):
        self._undo.append((None, os.environ, name, os.environ.get(name, _UNSET)))
        os.environ[name] = str(value)

    def delenv(self, name, raising=True):
        if name not in os.environ:
            if raising:
                raise KeyError(name)
            return
        self._undo.append((None, os.environ, name, os.environ[name]))
        del os.environ[name]

    def undo(self):
        for kind, obj, name, old in reversed(self._undo):
            if kind is setattr:
                if old is _UNSET:
                    delattr(obj, name)
                else:
                    setattr(obj, name, old)
            elif old is _UNSET:
                obj.pop(name, None)
            else:
                obj[name] = old
        self._undo = []


def run(module_globals):
    """Call every test_* in the module; exit 1 if any fails."""
    fails = 0
    for name, fn in sorted(module_globals.items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        params = inspect.signature(fn).parameters
        mp = MonkeyPatch()
        with tempfile.TemporaryDirectory() as td:
            kw = {"tmp_path": Path(td), "monkeypatch": mp}
            try:
                fn(**{k: kw[k] for k in params})
                print("  pass ", name)
            except Exception as e:          # noqa: BLE001 -- report and go on
                fails += 1
                print("  FAIL ", name, type(e).__name__, e)
            finally:
                mp.undo()
    sys.exit(1 if fails else 0)
