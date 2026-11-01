"""Build an allowlisted GitHub Pages site; data stays in its configured bucket."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.site_assets import FRONTEND_FILES
from tools.render_docs import _safe_file, render_docs


def build_site(root: Path = ROOT) -> Path:
    root = Path(root).resolve()
    output = root / "site-build"
    if output.is_symlink():
        raise ValueError("site-build must not be a symlink")
    files = tuple(dict.fromkeys((*FRONTEND_FILES, "index.html",
                                "site-config.js", *render_docs(root))))
    for name in files:
        if not _safe_file(root / name, root):
            raise ValueError(f"missing or unsafe public site file: {name}")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir()
    for name in files:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    (output / ".nojekyll").write_text("", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(f"Static site: {build_site(args.root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
