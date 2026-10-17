#!/usr/bin/env bash
# Check cloud access, then build and gate one small region without publishing.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${CLOUD_PROJECT:-downloads-417521}"
VM="${CLOUD_VM:-claude-doing-things}"
ZONE="${CLOUD_ZONE:-australia-southeast1-b}"
VM_USER="${VM_USER:-j}"
REGIONS="${CLOUD_REGIONS:-frankton_arm_lake}"
REF=main
CHECK_ONLY=0
FETCH=0
KEEP_RUNNING=0
COST_ESTIMATE_NZD="${CLOUD_COST_ESTIMATE_NZD:-}"

usage() {
  cat <<'EOF'
Usage: ./quickstart-cloud.sh [options]

With no options, checks local/cloud access and builds and gates
frankton_arm_lake on the shared VM. It does not publish.

Options:
  --check              Check local tools, authentication, VM access, and (if
                       running) IAP/SSH access without starting the VM/building.
  --regions "a b"      Regions to build (default: frankton_arm_lake).
  --fetch              Fetch source data for the selected region(s) first.
  --ref REF            Git branch/ref to sync to the VM (default: main).
  --keep-running       Do not stop the VM if this command started it.
  --estimate-nzd AMT   Set a planning estimate for non-default or multiple
                       regions (required unless CLOUD_COST_ESTIMATE_NZD is set).
  -h, --help           Show this help.

Build output and preflight logs are saved under data/cloud_quickstart_runs/.
EOF
}

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  if [[ -n "${LOG_FILE:-}" ]]; then
    printf 'Details: %s\n' "$LOG_FILE" >&2
  fi
  exit 1
}

while (($#)); do
  case "$1" in
    --check)
      CHECK_ONLY=1
      shift
      ;;
    --regions)
      (($# >= 2)) || { usage >&2; exit 2; }
      REGIONS="$2"
      shift 2
      ;;
    --fetch)
      FETCH=1
      shift
      ;;
    --ref)
      (($# >= 2)) || { usage >&2; exit 2; }
      REF="$2"
      shift 2
      ;;
    --keep-running)
      KEEP_RUNNING=1
      shift
      ;;
    --estimate-nzd)
      (($# >= 2)) || { usage >&2; exit 2; }
      COST_ESTIMATE_NZD="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ -n "$REGIONS" ]] || fail "region list is empty; pass --regions REGION."
case "$REGIONS" in
  *[!a-z0-9_\ \-]*)
    fail "region names may contain only lowercase letters, digits, underscores, and hyphens."
    ;;
esac
[[ -n "$REF" ]] || fail "git ref is empty; pass --ref REF."

LOG_DIR="${CLOUD_QUICKSTART_LOG_DIR:-$ROOT/data/cloud_quickstart_runs}"
mkdir -p "$LOG_DIR" || fail "could not create log directory: $LOG_DIR"
LOG_FILE="$LOG_DIR/$(date -u +%Y%m%dT%H%M%SZ).log"
if [[ -e "$LOG_FILE" ]]; then
  suffix=1
  while [[ -e "${LOG_FILE%.log}-$suffix.log" ]]; do
    ((suffix += 1))
  done
  LOG_FILE="${LOG_FILE%.log}-$suffix.log"
fi
exec > >(tee -a "$LOG_FILE") 2>&1

printf 'Cloud quickstart started: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf 'Target: %s / %s / %s (SSH user: %s)\n' "$PROJECT" "$ZONE" "$VM" "$VM_USER"
printf 'Regions: %s; ref: %s\n' "$REGIONS" "$REF"

if (( ! CHECK_ONLY )); then
  if [[ "$REGIONS" == "frankton_arm_lake" ]]; then
    COST_ESTIMATE_NZD="${COST_ESTIMATE_NZD:-3.00}"
    ESTIMATE_BASIS="provisional planning estimate for one small region (about 79 buildings), informed by indicative VM pricing and a roughly 2.5-hour upper-region build time; not verified against project billing"
  elif [[ -z "$COST_ESTIMATE_NZD" ]]; then
    fail "a region-specific cost estimate is required for non-default or multiple regions. Re-run with --estimate-nzd AMOUNT (NZD), or set CLOUD_COST_ESTIMATE_NZD."
  else
    ESTIMATE_BASIS="user-supplied planning estimate for the selected region(s)"
  fi

  [[ "$COST_ESTIMATE_NZD" =~ ^[0-9]+([.][0-9]{1,2})?$ ]] \
    || fail "cost estimate must be a non-negative amount with up to two decimal places, such as 3 or 3.00."

  printf 'This quickstart will build and gate region(s): %s on the shared VM.\n' "$REGIONS"
  printf 'Estimated cloud cost: NZD $%s (%s). This is not a spending cap; actual charges can be higher and may include disk, storage, or data-transfer costs.\n' \
    "$COST_ESTIMATE_NZD" "$ESTIMATE_BASIS"
  printf 'Continue? (yes/NO) '
  if [[ ! -r /dev/tty ]]; then
    fail "interactive confirmation is required before cloud work. Run this command in a terminal; no cloud action was started."
  fi
  answer=""
  IFS= read -r answer < /dev/tty || answer=""
  case "$answer" in
    [yY][eE][sS]) ;;
    *)
      echo "Human confirmation: declined. No cloud action was started."
      printf 'Log: %s\n' "$LOG_FILE"
      exit 0
      ;;
  esac
  echo "Human confirmation: approved."
fi

command -v git >/dev/null 2>&1 || fail "Git is not installed; install Git and run this script from a repository checkout."
GCLOUD="$(command -v gcloud || true)"
if [[ -z "$GCLOUD" ]]; then
  BREW="$(command -v brew || true)"
  if [[ -z "$BREW" ]]; then
    fail "Google Cloud CLI (gcloud) and Homebrew are not installed or not on PATH. Install Homebrew from https://brew.sh, open a new terminal so its PATH setup takes effect, then rerun this command. The quickstart does not install Homebrew."
  fi

  printf 'Google Cloud CLI (gcloud) is missing. Install it with Homebrew? (yes/NO) '
  if [[ ! -r /dev/tty ]]; then
    fail "gcloud is missing and package installation requires interactive confirmation. Install it with 'brew install --cask gcloud-cli', then run 'gcloud auth login' and retry."
  fi
  answer=""
  IFS= read -r answer < /dev/tty || answer=""
  case "$answer" in
    [yY][eE][sS])
      echo "Installing Google Cloud CLI with Homebrew: brew install --cask gcloud-cli"
      if ! "$BREW" install --cask gcloud-cli; then
        fail "Homebrew could not install gcloud. Review the installation error above, resolve it, and retry."
      fi
      ;;
    *)
      fail "gcloud installation declined; no package was installed. To install later, run 'brew install --cask gcloud-cli', then 'gcloud auth login'."
      ;;
  esac

  hash -r
  GCLOUD="$(command -v gcloud || true)"
  [[ -n "$GCLOUD" ]] || fail "Homebrew reported a successful gcloud installation, but gcloud is not on PATH. Follow Google's Homebrew setup guidance at https://docs.cloud.google.com/sdk/docs/downloads-homebrew, open a new terminal, and retry."
fi
printf 'gcloud found: %s\n' "$GCLOUD"

if ! git -C "$ROOT" rev-parse --show-toplevel >/dev/null 2>&1; then
  fail "this script must be run from a Git checkout; clone the repository and retry."
fi

if ! active_account=$("$GCLOUD" auth list --filter=status:ACTIVE --format='value(account)' 2>&1); then
  printf '%s\n' "$active_account"
  fail "gcloud could not inspect authentication. Run 'gcloud auth login', then retry."
fi
if [[ -z "$active_account" ]]; then
  fail "gcloud has no active Google account. Run 'gcloud auth login', then retry. If you just logged in, verify the active account with 'gcloud auth list'."
fi
echo "Google authentication: active."

if ! vm_status=$("$GCLOUD" compute instances describe "$VM" \
  --project "$PROJECT" --zone "$ZONE" --format='value(status)' 2>&1); then
  printf '%s\n' "$vm_status"
  fail "could not inspect the shared VM in project $PROJECT. Confirm your account is in solarpotential@googlegroups.com, wait for IAM changes to propagate, and retry. If access was just granted, run 'gcloud auth login' again."
fi
[[ -n "$vm_status" ]] || fail "gcloud returned no VM status; verify project $PROJECT, zone $ZONE, VM $VM, and Compute Engine API access."
printf 'VM status: %s\n' "$vm_status"

if [[ "$vm_status" == RUNNING ]]; then
  echo "Checking IAP/SSH access with a harmless remote command..."
  if ! ssh_output=$("$GCLOUD" compute ssh "${VM_USER}@${VM}" \
    --project "$PROJECT" --zone "$ZONE" --tunnel-through-iap \
    --command 'printf "CLOUD_QUICKSTART_SSH_OK\n"' 2>&1); then
    printf '%s\n' "$ssh_output"
    fail "IAP/SSH check failed. Confirm IAP tunnel and OS Login permissions, then retry. See docs/data-maintainers/team-access.md."
  fi
  [[ "$ssh_output" == *CLOUD_QUICKSTART_SSH_OK* ]] || {
    printf '%s\n' "$ssh_output"
    fail "SSH connected but the probe did not return its success marker; inspect the output and retry."
  }
  echo "IAP/SSH access: ready."
else
  echo "IAP/SSH check deferred because the VM is not running; remote_build.sh will start it and check SSH before syncing or building."
fi

if ((CHECK_ONLY)); then
  echo "Preflight passed. No VM was started and no build was run."
  printf 'Log: %s\n' "$LOG_FILE"
  exit 0
fi

echo "Preflight passed. The next step may start the shared VM and incur compute charges."
echo "This run builds and gates only; it will not publish."

remote_args=(--regions "$REGIONS" --ref "$REF")
((FETCH)) && remote_args+=(--fetch)
((KEEP_RUNNING)) && remote_args+=(--keep-running)

if "$ROOT/tools/remote_build.sh" "${remote_args[@]}"; then
  echo "Cloud quickstart completed successfully."
else
  status=$?
  fail "remote build exited with status $status. Inspect the build output above and this run log before retrying."
fi

printf 'Log: %s\n' "$LOG_FILE"
