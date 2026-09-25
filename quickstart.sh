#!/bin/bash
# QUICKSTART: run the production roof/layout and yield stages on YOUR OWN
# small area of New Zealand, with numbered logs and an incremental report.
#
#   1. cp my_area.example.json my_area.json   (edit name + bbox)
#   2. export LINZ_API_KEY=...                (free key from data.linz.govt.nz;
#                                              create it with ALL permissions ticked,
#                                              export/download included -- they cannot
#                                              be added to a key later)
#   3. bash quickstart.sh <name>
#
# The run uses production build stages but does not emit/combine map tiles.
# See docs/quickstart.md for report locations and the map-preview boundary.
set -Eeuo pipefail
cd "$(dirname "$0")"
# Windows (Git Bash / MSYS) puts the interpreter somewhere else.
if [ -x .venv/bin/python ]; then PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
else PY=.venv/bin/python
fi
AREA="${1:-}"
if [ -z "$AREA" ]; then
  echo "usage: bash quickstart.sh <area-name-from-my_area.json>"; exit 2
fi
if [ ! -x "$PY" ]; then
  echo "no .venv -- see docs/data-maintainers/local-setup.md first"; exit 2
fi
exec "$PY" tools/quickstart_run.py "$AREA" --python "$PY"
