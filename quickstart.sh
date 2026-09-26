#!/bin/bash
# Compatibility wrapper for tools/pipeline.py.
# Pass a configured region name, or omit it to use config.PIPELINE_REGION.
set -Eeuo pipefail
cd "$(dirname "$0")"
# Windows (Git Bash / MSYS) puts the interpreter somewhere else.
if [ -x .venv/bin/python ]; then PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
else PY=.venv/bin/python
fi
if [ ! -x "$PY" ]; then
  echo "no .venv -- see docs/data-maintainers/local-setup.md first"; exit 2
fi
exec "$PY" tools/pipeline.py "$@" --python "$PY"
