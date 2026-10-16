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

MODE=local
EXPECT_MODE_VALUE=0
for arg in "$@"; do
  if [ "$EXPECT_MODE_VALUE" -eq 1 ]; then
    MODE="$arg"
    EXPECT_MODE_VALUE=0
  elif [ "$arg" = "--mode" ]; then
    EXPECT_MODE_VALUE=1
  elif [ "$arg" = "--mode=cloud" ]; then
    MODE=cloud
  elif [ "$arg" = "--mode=local" ]; then
    MODE=local
  fi
done

if [ "$MODE" = "cloud" ]; then
  if [ ! -x "$PY" ]; then
    PY=$(command -v python3 || true)
  fi
  if [ -z "$PY" ] || [ ! -x "$PY" ]; then
    echo "cloud mode needs Python 3 to run the pipeline"; exit 2
  fi
  exec "$PY" tools/pipeline.py "$@"
fi

if [ ! -x "$PY" ]; then
  echo "no .venv -- see docs/data-maintainers/local-setup.md first"; exit 2
fi
exec "$PY" tools/pipeline.py "$@" --python "$PY"
