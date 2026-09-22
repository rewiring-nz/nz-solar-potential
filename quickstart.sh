#!/bin/bash
# QUICKSTART: run the full solar-potential methodology on YOUR OWN small
# area of New Zealand, end to end, and inspect every step of the result.
#
#   1. cp my_area.example.json my_area.json   (edit name + bbox)
#   2. export LINZ_API_KEY=...                (free key from data.linz.govt.nz --
#                                              see docs/data-maintainers/local-setup.md)
#   3. bash quickstart.sh <name>
#
# This is NOT a simplified re-implementation. Your area becomes a
# first-class region and runs through the IDENTICAL stages the deployed
# Queenstown map was built with -- same code paths, same thresholds, same
# gates -- so anything you verify here is a verification of the real
# methodology. See docs/quickstart.md for how to check each stage.
set -u
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
if [ ! -f my_area.json ]; then
  echo "my_area.json not found -- copy my_area.example.json and edit it"; exit 2
fi
# A key, not just the line: .env.example ships "LINZ_API_KEY=" empty, and a
# bare grep passed that and failed later inside the fetch.
if [ -z "${LINZ_API_KEY:-}" ] && ! grep -Eq '^[[:space:]]*LINZ_API_KEY=[^[:space:]]' .env 2>/dev/null; then
  echo "LINZ_API_KEY not set (env or .env) -- free key at data.linz.govt.nz"; exit 2
fi
if [ ! -x "$PY" ]; then
  echo "no .venv -- see docs/data-maintainers/local-setup.md first"; exit 2
fi
# Is my_area.json valid, and does it define THIS name? Checked here, in one
# line, rather than as "unknown region" from deep inside the fetch.
$PY - "$AREA" <<'PYEOF' || exit 2
import sys, config
area = sys.argv[1]
if config.MY_AREA_ERROR:
    sys.exit(f"my_area.json: {config.MY_AREA_ERROR}")
if not config.MY_AREA or config.MY_AREA["name"] != area:
    sys.exit(f"my_area.json names {config.MY_AREA and config.MY_AREA['name']!r}, "
             f"not {area!r} -- pass that name, or edit the file")
sv = config.MY_AREA["survey"]
print(f"area {area}: bbox {config.MY_AREA['bbox']}, survey {sv['name']}")
print(f"  DSM layer {sv.get('dsm_layer')}, imagery {sv.get('imagery_layer')}, "
      f"point cloud {sv.get('pointcloud_bulk_url') or 'none (DSM-only)'}")
PYEOF

echo "=== 1/5 fetch: outlines, DSM, wide DEM, imagery, LiDAR tiles (LINZ + OpenTopography) ==="
# fetch_regions fetches the point cloud too (its pass 3), and says so if the
# survey has none -- the build then runs DSM-only, see docs/quickstart.md.
$PY src/fetch_regions.py "$AREA" || exit 1

echo "=== 2/5 vision models (optional but part of the shipped methodology) ==="
VISION=1
WHY=""
# torchvision too: segment_anything imports it, and without it this check
# failed and the vision chain was skipped with a message blaming torch.
if ! MISSING=$($PY -c "import torch, torchvision, segment_anything" 2>&1); then
  VISION=0
  WHY="python packages: $(echo "$MISSING" | tail -1)
  install: pip install torch torchvision segment-anything  (see docs/quickstart.md)"
elif [ ! -f data/models/roof_lines_v5.pt ] || [ ! -f data/models/roof_lines_v6.pt ]; then
  VISION=0
  WHY="data/models/roof_lines_v5.pt / v6.pt missing from this checkout"
elif [ ! -f data/sam_vit_b.pth ]; then
  echo "  downloading SAM ViT-B checkpoint (358 MB, Meta AI's public release)"
  # -f: an HTTP error must not be saved AS the checkpoint. .part + rename: a
  # killed download must not leave a truncated file every later run trusts.
  if curl -fL --retry 3 -o data/sam_vit_b.pth.part \
       https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth \
     && [ "$(wc -c < data/sam_vit_b.pth.part)" -gt 300000000 ]; then
    mv data/sam_vit_b.pth.part data/sam_vit_b.pth
  else
    rm -f data/sam_vit_b.pth.part
    VISION=0
    WHY="SAM checkpoint download failed (re-run to retry)"
  fi
fi
if [ "$VISION" = "1" ]; then
  echo "=== 3/5 vision precompute: SAM + line detector + LiDAR candidates per roof ==="
  if ! $PY tools/predict_faces.py --region "$AREA"; then
    VISION=0
    WHY="tools/predict_faces.py failed (output above)"
  fi
fi
if [ "$VISION" = "0" ]; then
  echo "  vision chain skipped -- $WHY"
  echo "  The build falls back to the LiDAR partition for every roof -- the"
  echo "  same fallback the production map uses where the vision chain defers."
fi

echo "=== 4/5 build: the exact production stages ==="
export SOLAR_SELECTED_FACES=1
for s in build_layout_geojson gate_panels rerank_layouts derive_solar_potential; do
  $PY src/run_stage.py "$s" "$AREA" || exit 1
done

echo "=== 5/5 report ==="
$PY tools/quickstart_report.py "$AREA" || exit 1
echo ""
echo "open data/regions/$AREA/quickstart_report.html and follow"
echo "docs/quickstart.md to verify each stage against what you can see."
