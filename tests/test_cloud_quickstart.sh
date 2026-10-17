#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
BIN="$TMP/bin"
LOGS="$TMP/logs"
OUTPUT="$TMP/output"
mkdir -p "$BIN" "$LOGS"

cleanup() {
  while IFS= read -r file; do
    rm -f -- "$file"
  done < <(find "$TMP" -type f -print)
  rmdir "$BIN" "$LOGS" "$TMP"
}
trap cleanup EXIT

cat > "$BIN/gcloud" <<'MOCK'
#!/usr/bin/env bash
set -eu
case "$1 $2 $3" in
  "auth list --filter=status:ACTIVE")
    echo ACTIVE
    ;;
  "compute instances describe")
    if [[ "${MOCK_GCLOUD_MODE:-}" == "denied" ]]; then
      echo "ERROR: permission denied" >&2
      exit 1
    fi
    if [[ "${MOCK_GCLOUD_MODE:-}" == "stopped" ]]; then
      echo TERMINATED
    else
      echo RUNNING
    fi
    ;;
  "compute instances start"|"compute instances stop")
    exit 0
    ;;
  "compute ssh "*)
    if [[ "${MOCK_GCLOUD_MODE:-}" == "stopped" ]]; then
      echo "ERROR: simulated IAP connection failure" >&2
      exit 1
    fi
    echo CLOUD_QUICKSTART_SSH_OK
    ;;
  *)
    echo "unexpected gcloud command: $*" >&2
    exit 2
    ;;
esac
MOCK
chmod +x "$BIN/gcloud"
cat > "$BIN/sleep" <<'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
chmod +x "$BIN/sleep"

PATH="$BIN:$PATH" CLOUD_QUICKSTART_LOG_DIR="$LOGS" \
  "$ROOT/quickstart-cloud.sh" --check > "$OUTPUT" 2>&1
grep -q "IAP/SSH access: ready." "$OUTPUT"
grep -q "No VM was started and no build was run." "$OUTPUT"
[[ -n "$(find "$LOGS" -type f -name '*.log' -print -quit)" ]]

if PATH="$BIN:$PATH" MOCK_GCLOUD_MODE=denied \
  CLOUD_QUICKSTART_LOG_DIR="$LOGS" "$ROOT/quickstart-cloud.sh" --check > "$OUTPUT" 2>&1; then
  echo "expected VM permission failure" >&2
  exit 1
fi
grep -q "Confirm your account is in solarpotential@googlegroups.com" "$OUTPUT"

if PATH="$BIN:$PATH" MOCK_GCLOUD_MODE=stopped \
  tools/remote_build.sh --regions frankton_arm_lake > "$OUTPUT" 2>&1; then
  echo "expected IAP readiness failure" >&2
  exit 1
fi
grep -q "VM did not become reachable through IAP/SSH after 5 minutes." "$OUTPUT"

PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY=python3
"$PY" "$ROOT/tests/test_cloud_quickstart_prompt.py"

echo "cloud quickstart preflight tests passed"
