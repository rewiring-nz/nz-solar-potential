"""Safely manage a dedicated single-VM GCP quickstart environment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloud_profile import CloudProfile, load_profile

REPOSITORY = "https://github.com/rewiring-nz/nz-solar-potential.git"
FIREWALL_MARKER = "Managed by nz-solar-potential cloud quickstart"

BOOTSTRAP = r"""set -Eeuo pipefail
sudo DEBIAN_FRONTEND=noninteractive apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  build-essential git libsqlite3-dev nano python3 python3-pip python3-venv zlib1g-dev
if ! command -v tippecanoe >/dev/null || ! command -v tile-join >/dev/null || ! command -v tippecanoe-decode >/dev/null; then
  work="$(mktemp -d)"
  git clone --depth 1 https://github.com/felt/tippecanoe.git "$work/tippecanoe"
  make -C "$work/tippecanoe" -j2
  sudo make -C "$work/tippecanoe" install
  rm -rf "$work"
fi
if [ ! -d "$HOME/nz-solar-potential/.git" ]; then
  git clone --depth 1 REPOSITORY_PLACEHOLDER "$HOME/nz-solar-potential"
else
  git -C "$HOME/nz-solar-potential" pull --ff-only
fi
cd "$HOME/nz-solar-potential"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
for tool in tippecanoe tile-join tippecanoe-decode; do command -v "$tool"; done
"""


def _profile_options(profile: CloudProfile) -> list[str]:
    values = profile.values
    args = [
        f"--project={values['project']}",
        f"--zone={values['zone']}",
        f"--machine-type={values['machine_type']}",
        f"--network={values['network']}",
        f"--boot-disk-size={values['disk_gb']}GB",
        "--boot-disk-type=pd-balanced",
        "--boot-disk-auto-delete",
        f"--image-family={values['image_family']}",
        f"--image-project={values['image_project']}",
        f"--tags={_target_tag(profile)}",
        "--labels=purpose=solar-quickstart,created-by=nz-solar-potential,"
        f"cloud-profile={_profile_fingerprint(profile)}",
    ]
    if values.get("subnetwork"):
        args.append(f"--subnet={values['subnetwork']}")
    if values.get("service_account"):
        args.extend([
            f"--service-account={values['service_account']}",
            "--scopes=https://www.googleapis.com/auth/cloud-platform",
        ])
    else:
        args.append("--no-service-account")
    return args


def _run(args: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=ROOT,
            check=check,
            text=True,
            capture_output=capture,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("gcloud is not installed or is not on PATH; install the Google Cloud CLI first") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"command failed with exit code {exc.returncode}: {' '.join(args)}"
                           + (f"\n{detail}" if detail else "")) from exc


def _gcloud() -> str:
    path = shutil.which("gcloud")
    if not path:
        raise RuntimeError("gcloud is not installed or is not on PATH; install the Google Cloud CLI first")
    return path


def _preflight(profile: CloudProfile, *, require_create_config: bool = False) -> None:
    gcloud = _gcloud()
    auth = _run([gcloud, "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
                capture=True)
    if not auth.stdout.strip():
        raise RuntimeError("gcloud has no active account; run `gcloud auth login` first")
    values = profile.values
    checks = [
        [gcloud, "compute", "zones", "describe", values["zone"],
         f"--project={values['project']}", "--format=value(name)"],
        [gcloud, "compute", "machine-types", "describe", values["machine_type"],
         f"--zone={values['zone']}", f"--project={values['project']}", "--format=value(name)"],
    ]
    for cmd in checks:
        _run(cmd, capture=True)
    if require_create_config and not values["ssh_source_ranges"]:
        raise RuntimeError(
            "profile must set ssh_source_ranges to your public IP as a CIDR (usually x.x.x.x/32) "
            "before creating a VM"
        )
    print(f"Profile: {profile.name}")
    print(f"Target:  {profile.target}")
    print(f"VM:      {values['machine_type']}, {values['disk_gb']} GB pd-balanced")
    print(f"SSH:     {', '.join(values['ssh_source_ranges']) or 'connect only through preconfigured network rules'}")
    print("Preflight passed. It did not create resources or enable APIs.")


def _existing_instance(profile: CloudProfile) -> dict[str, Any] | None:
    gcloud = _gcloud()
    result = _run([
        gcloud, "compute", "instances", "describe", profile.values["instance_name"],
        f"--project={profile.values['project']}", f"--zone={profile.values['zone']}",
        "--format=json",
    ], check=False, capture=True)
    if result.returncode:
        detail = (result.stderr or result.stdout or "").lower()
        if "not found" in detail and f"/instances/{profile.values['instance_name']}" in detail:
            return None
        raise RuntimeError(
            "could not inspect the selected VM; check project access and Compute Engine API status: "
            + (result.stderr or result.stdout or "gcloud returned an error").strip()
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("gcloud returned an invalid instance description") from exc


def _firewall_name(profile: CloudProfile) -> str:
    return f"{_target_tag(profile)}-ssh"


def _profile_fingerprint(profile: CloudProfile) -> str:
    return hashlib.sha256(profile.name.encode("utf-8")).hexdigest()[:16]


def _target_tag(profile: CloudProfile) -> str:
    suffix = hashlib.sha256(profile.name.encode("utf-8")).hexdigest()[:8]
    return f"solar-quickstart-{profile.name[:30]}-{suffix}"


def _firewall(profile: CloudProfile) -> dict[str, Any] | None:
    result = _run([
        _gcloud(), "compute", "firewall-rules", "describe", _firewall_name(profile),
        f"--project={profile.values['project']}", "--format=json",
    ], check=False, capture=True)
    if result.returncode:
        detail = (result.stderr or result.stdout or "").lower()
        if "not found" in detail and f"/firewalls/{_firewall_name(profile)}" in detail:
            return None
        raise RuntimeError("could not inspect the quickstart SSH firewall rule: "
                           + (result.stderr or result.stdout or "gcloud returned an error").strip())
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("gcloud returned an invalid firewall rule description") from exc


def _firewall_matches_profile(profile: CloudProfile, current: dict[str, Any]) -> bool:
    values = profile.values
    allowed = current.get("allowed", [])
    return (
        current.get("description") == f"{FIREWALL_MARKER}: {profile.name}"
        and current.get("direction") == "INGRESS"
        and not current.get("disabled", False)
        and current.get("network", "").endswith(f"/{values['network']}")
        and set(current.get("sourceRanges", [])) == set(values["ssh_source_ranges"])
        and current.get("targetTags") == [_target_tag(profile)]
        and len(allowed) == 1
        and allowed[0].get("IPProtocol") == "tcp"
        and allowed[0].get("ports") == ["22"]
    )


def _ensure_firewall(profile: CloudProfile) -> None:
    current = _firewall(profile)
    values = profile.values
    if current:
        if not _firewall_matches_profile(profile, current):
            raise RuntimeError(
                f"firewall rule {_firewall_name(profile)!r} already exists but does not match "
                "this profile; refusing to change it"
            )
        return
    expected_description = f"{FIREWALL_MARKER}: {profile.name}"
    _run([
        _gcloud(), "compute", "firewall-rules", "create", _firewall_name(profile),
        f"--project={values['project']}", f"--network={values['network']}",
        "--direction=INGRESS", "--action=ALLOW", "--rules=tcp:22",
        f"--source-ranges={','.join(values['ssh_source_ranges'])}",
        f"--target-tags={_target_tag(profile)}", f"--description={expected_description}",
    ])


def _delete_owned_firewall(profile: CloudProfile) -> None:
    current = _firewall(profile)
    if current is None:
        return
    if not _firewall_matches_profile(profile, current):
        raise RuntimeError(
            f"not deleting firewall rule {_firewall_name(profile)!r}: its ownership marker or "
            "configuration differs from the selected profile"
        )
    _run([
        _gcloud(), "compute", "firewall-rules", "delete", _firewall_name(profile),
        f"--project={profile.values['project']}", "--quiet",
    ])


def _confirm_target(profile: CloudProfile, confirmation: str | None, verb: str) -> None:
    if confirmation != profile.target:
        raise RuntimeError(
            f"{verb} requires --confirm {profile.target!r}; review the project, zone, and VM first"
        )


def _create(profile: CloudProfile, confirmation: str | None) -> None:
    _confirm_target(profile, confirmation, "VM creation")
    _preflight(profile, require_create_config=True)
    if _existing_instance(profile):
        raise RuntimeError(
            f"VM {profile.target} already exists; refusing to adopt or replace it. Use `status` or `ssh`."
        )
    gcloud = _gcloud()
    ranges = profile.values["ssh_source_ranges"]
    print(f"Creating {profile.target} ({profile.values['machine_type']}, "
          f"{profile.values['disk_gb']} GB disk). This is billable.", flush=True)
    _ensure_firewall(profile)
    _run([gcloud, "compute", "instances", "create", profile.values["instance_name"],
          *_profile_options(profile)], check=True)
    print("VM and profile-scoped SSH firewall rule created. SSH ingress is restricted to "
          f"{', '.join(ranges)}. Run `bootstrap` next.")


def _bootstrap(profile: CloudProfile) -> None:
    command = BOOTSTRAP.replace("REPOSITORY_PLACEHOLDER", REPOSITORY)
    _ssh(profile, command=command)
    print("Bootstrap complete. Add LINZ_API_KEY to the ignored VM-local .env before running.")


def _ssh(
    profile: CloudProfile,
    *,
    command: str | None = None,
    extra: list[str] | None = None,
) -> None:
    args = [_gcloud(), "compute", "ssh", profile.values["instance_name"],
            f"--project={profile.values['project']}", f"--zone={profile.values['zone']}"]
    if command is not None:
        args.append(f"--command={command}")
    if extra:
        args.extend(["--", *extra])
    _run(args)


def _ssh_stream(profile: CloudProfile, command: str) -> tuple[int, str]:
    args = [
        _gcloud(), "compute", "ssh", profile.values["instance_name"],
        f"--project={profile.values['project']}", f"--zone={profile.values['zone']}",
        f"--command={command}",
    ]
    try:
        process = subprocess.Popen(
            args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", bufsize=1,
        )
    except OSError as exc:
        raise RuntimeError(f"could not start gcloud ssh: {type(exc).__name__}: {exc}") from exc
    run_id = ""
    assert process.stdout is not None
    try:
        for line in process.stdout:
            stripped = line.rstrip("\r\n")
            if stripped.startswith("CLOUD_RUN_ID="):
                run_id = stripped.partition("=")[2]
                continue
            print(line, end="", flush=True)
    except KeyboardInterrupt:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait()
        raise
    returncode = process.wait()
    if run_id and not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z(?:-[0-9]{2})?", run_id):
        raise RuntimeError(f"remote pipeline returned an invalid run ID: {run_id!r}")
    return returncode, run_id


def _region_name(region: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", region):
        raise ValueError("region must be 1-64 letters, digits, '_' or '-', and start with a letter or digit")
    return region


def _collect_run(profile: CloudProfile, run_id: str) -> Path:
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z(?:-[0-9]{2})?", run_id):
        raise ValueError(f"invalid pipeline run ID: {run_id!r}")
    runs = ROOT / "data" / "pipeline_runs"
    local_run = runs / run_id
    if local_run.exists():
        raise RuntimeError(f"local pipeline run already exists; refusing to overwrite {local_run}")
    runs.mkdir(parents=True, exist_ok=True)
    _run([
        _gcloud(), "compute", "scp", "--recurse",
        f"--project={profile.values['project']}", f"--zone={profile.values['zone']}",
        f"{profile.values['instance_name']}:~/nz-solar-potential/data/pipeline_runs/{run_id}",
        str(runs),
    ])
    if not (local_run / "report.md").is_file() or not (local_run / "run.log").is_file():
        raise RuntimeError(
            f"remote run {run_id} was copied, but its report.md or run.log is missing"
        )
    return local_run


def _report_preview_port(report: Path) -> int | None:
    try:
        content = report.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"could not read collected pipeline report {report}: {exc}") from exc
    match = re.search(r"\*\*Map preview:\*\* \[http://127\.0\.0\.1:(\d+)/", content)
    return int(match.group(1)) if match else None


def _remote_pipeline_command(region: str, port: int) -> str:
    selected = _region_name(region)
    if not 1024 <= port <= 65535:
        raise ValueError("preview port must be between 1024 and 65535")
    return (
        "set -u; cd \"$HOME/nz-solar-potential\" || exit 2; "
        "before=$(find data/pipeline_runs -mindepth 1 -maxdepth 1 -type d -printf '%f\\n' 2>/dev/null | sort); "
        f".venv/bin/python tools/pipeline.py {selected} --port {port} --no-open-browser; "
        "pipeline_status=$?; "
        "after=$(find data/pipeline_runs -mindepth 1 -maxdepth 1 -type d -printf '%f\\n' 2>/dev/null | sort); "
        "run_id=; "
        "for candidate in $after; do "
        "case \" $before \" in *\" $candidate \"*) ;; *) run_id=$candidate ;; esac; "
        "done; "
        "printf '\\nCLOUD_RUN_ID=%s\\n' \"$run_id\"; "
        "exit \"$pipeline_status\""
    )


def _run_pipeline(profile: CloudProfile, region: str, port: int = 8765) -> int:
    selected = _region_name(region)
    print(f"Running configured region {selected!r} on {profile.target}.")
    returncode, run_id = _ssh_stream(profile, _remote_pipeline_command(selected, port))
    if not run_id:
        if returncode:
            raise RuntimeError(
                f"remote pipeline exited with status {returncode} before returning a run ID; "
                "inspect the VM's data/pipeline_runs and retry report collection"
            )
        raise RuntimeError("remote pipeline completed without returning a run ID")
    local_run = _collect_run(profile, run_id)
    print(f"Pipeline report: {local_run / 'report.md'}")
    print(f"Combined log: {local_run / 'run.log'}")
    actual_port = _report_preview_port(local_run / "report.md")
    if actual_port is not None:
        print("When complete, start the SSH preview tunnel with "
              f"`python3 tools/cloud_quickstart.py --profile {profile.name} preview --port {actual_port}`.")
    return returncode


def _status(profile: CloudProfile) -> None:
    instance = _existing_instance(profile)
    if not instance:
        print(f"VM not found: {profile.target}")
        return
    print(f"VM: {instance.get('status', 'unknown')}  target={profile.target}")
    print(f"Machine type: {instance.get('machineType', '').rsplit('/', 1)[-1]}")
    for interface in instance.get("networkInterfaces", []):
        access_configs = interface.get("accessConfigs", [])
        if access_configs:
            print(f"External IP: {access_configs[0].get('natIP', 'unknown')}")
    if (
        instance.get("labels", {}).get("purpose") != "solar-quickstart"
        or instance.get("labels", {}).get("created-by") != "nz-solar-potential"
        or instance.get("labels", {}).get("cloud-profile") != _profile_fingerprint(profile)
    ):
        print("Notice: this VM is not labelled as created by the cloud quickstart.")


def _collect(profile: CloudProfile) -> None:
    destination = ROOT / "data" / "cloud_runs" / profile.name
    destination.mkdir(parents=True, exist_ok=True)
    _run([
        _gcloud(), "compute", "scp", "--recurse",
        f"--project={profile.values['project']}", f"--zone={profile.values['zone']}",
        f"{profile.values['instance_name']}:~/nz-solar-potential/data/pipeline_runs",
        str(destination),
    ])
    print(f"Copied remote pipeline reports and logs to {destination.relative_to(ROOT)}.")


def _delete(profile: CloudProfile, confirmation: str | None) -> None:
    _confirm_target(profile, confirmation, "VM deletion")
    instance = _existing_instance(profile)
    if instance is None:
        _delete_owned_firewall(profile)
        print(f"VM is already absent: {profile.target}; removed its owned SSH firewall rule if present.")
        return
    if instance.get("labels", {}).get("purpose") != "solar-quickstart" or (
        instance.get("labels", {}).get("created-by") != "nz-solar-potential"
    ) or (
        instance.get("labels", {}).get("cloud-profile") != _profile_fingerprint(profile)
    ):
        raise RuntimeError(
            "refusing to delete this VM because it lacks the ownership labels for this profile"
        )
    print(f"Deleting {profile.target} and its auto-delete boot disk. This is destructive.", flush=True)
    _run([_gcloud(), "compute", "instances", "delete", profile.values["instance_name"],
          f"--project={profile.values['project']}", f"--zone={profile.values['zone']}", "--quiet"])
    _delete_owned_firewall(profile)
    print("VM deleted. The local clone, VM-local pipeline reports/data, and any non-auto-delete "
          "resources are not removed by this command.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", help="profile name (or SOLAR_CLOUD_PROFILE)")
    parser.add_argument("--profiles-file", help="profile file (or SOLAR_CLOUD_PROFILES_FILE)")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("preflight", help="check profile, gcloud login, zone and machine type")
    create = sub.add_parser("create", help="create a new dedicated VM; requires exact-target confirmation")
    create.add_argument("--confirm", help="must exactly equal project/zone/instance_name")
    sub.add_parser("bootstrap", help="install project and map-build dependencies on the VM")
    sub.add_parser("ssh", help="open an interactive SSH session")
    run = sub.add_parser("run", help="run the local quickstart pipeline on the VM")
    run.add_argument("region", help="configured region name, e.g. frankton_arm_lake")
    run.add_argument("--port", type=int, default=None,
                     help="preferred preview port (defaults to the profile setting)")
    sub.add_parser("collect", help="copy VM pipeline reports and logs into local data/cloud_runs/")
    preview = sub.add_parser("preview", help="open a loopback-only SSH tunnel for the map preview")
    preview.add_argument("--port", type=int, default=None,
                         help="VM preview port (defaults to the profile setting)")
    sub.add_parser("status", help="show the selected VM state")
    delete = sub.add_parser("delete", help="delete only a VM labelled as quickstart-managed")
    delete.add_argument("--confirm", help="must exactly equal project/zone/instance_name")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        profile = load_profile(args.profile, args.profiles_file)
        if args.action == "preflight":
            _preflight(profile)
        elif args.action == "create":
            _create(profile, args.confirm)
        elif args.action == "bootstrap":
            _bootstrap(profile)
        elif args.action == "ssh":
            _ssh(profile)
        elif args.action == "run":
            result = _run_pipeline(
                profile, args.region,
                args.port if args.port is not None else profile.values["preview_port"],
            )
            return result
        elif args.action == "collect":
            _collect(profile)
        elif args.action == "preview":
            port = args.port if args.port is not None else profile.values["preview_port"]
            if not 1024 <= port <= 65535:
                raise ValueError("preview port must be between 1024 and 65535")
            _ssh(profile, extra=["-N", "-L", f"{port}:127.0.0.1:{port}"])
        elif args.action == "status":
            _status(profile)
        elif args.action == "delete":
            _delete(profile, args.confirm)
        return 0
    except (RuntimeError, ValueError) as exc:
        print(f"cloud quickstart error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
