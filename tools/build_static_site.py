"""Build an allowlisted GitHub Pages site; data stays in its configured bucket."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
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
    # The Status tab (methodology.js) shows which commit of the code is live.
    (output / "site_version.json").write_text(json.dumps(site_version(root)), encoding="utf-8")
    return output


def site_version(root: Path) -> dict:
    """The commit the site is built from: GITHUB_SHA in Actions, else git."""
    sha = os.environ.get("GITHUB_SHA", "")
    when = ""
    try:
        sha = sha or subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                    text=True, check=True).stdout.strip()
        when = subprocess.run(["git", "show", "-s", "--format=%cI", sha], cwd=root, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pass
    return {"commit": sha, "committed": when,
            "built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(f"Static site: {build_site(args.root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
