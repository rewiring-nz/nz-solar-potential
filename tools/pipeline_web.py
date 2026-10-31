#!/usr/bin/env python3
"""Open the pipeline web panel: pick areas on a map, see estimates, run builds.

    python3 tools/pipeline_web.py [--port 8766] [--no-open-browser]

See docs/data-maintainers/web-pipeline.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pipeline_webui.server import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
