cloud_profile_value() {
  local field="$1"
  local profile_python="${PY:-}"
  local profile_root
  if [ -z "$profile_python" ] || [ ! -x "$profile_python" ]; then
    profile_python=python3
  fi
  if [ -z "${SOLAR_CLOUD_PROFILE:-}" ]; then
    echo "SOLAR_CLOUD_PROFILE is not set" >&2
    return 2
  fi
  profile_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  "$profile_python" "$profile_root/cloud_profile.py" \
    --profile "$SOLAR_CLOUD_PROFILE" get "$field"
}
