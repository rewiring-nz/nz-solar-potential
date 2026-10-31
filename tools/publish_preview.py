"""Publish a public, temporary developer preview without changing production."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from tools.served_outputs import SERVED_DIRS, SERVED_FILES, STATIC_DIRS

DATA = ROOT / "data"
FRONTEND_FILES = ("preview.html", "economics.js", "bill_tool.js", "panel_editor.js", "workings.js",
                  "generation.js", "sun_shadows.js", "buildings3d.js", "low_solar.js")
CORE_DATA_FILES = ("panel_layouts.pmtiles", "buildings.pmtiles", "building_cells.pmtiles")
IMMUTABLE_CACHE = "public,max-age=31536000,immutable"
REVALIDATE_CACHE = "public,no-cache,max-age=0"
_USER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$")


def _preview_bucket() -> str:
    value = config.CLOUD_SHARED_BUILD.get("preview_bucket")
    if not isinstance(value, str) or not re.fullmatch(r"gs://[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", value):
        raise ValueError("config.CLOUD_SHARED_BUILD.preview_bucket must be a valid gs:// bucket")
    return value


def _run_id() -> str:
    return f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"


def _replace_site_config(
    source: str, data_base: str, version: str, regions: str,
) -> str:
    names = regions.split()
    if not names:
        raise ValueError("preview regions must name at least one region")
    from src.region_build import area_bbox_wgs84
    from src.region_ids import validate
    try:
        bounds = [area_bbox_wgs84(validate(name)) for name in names]
    except (KeyError, ValueError) as exc:
        raise ValueError(f"preview regions must be built or configured regions: {exc}") from exc
    min_lon = min(box[0] for box in bounds)
    min_lat = min(box[1] for box in bounds)
    max_lon = max(box[2] for box in bounds)
    max_lat = max(box[3] for box in bounds)
    span = max(max_lon - min_lon, max_lat - min_lat)
    zoom = min(16.5, max(8.0, math.log2(360 / span) - 1))
    source, version_count = re.subn(
        r'(?m)^(\s*dataVersion:\s*)"[^"]*"',
        lambda match: f'{match.group(1)}"{version}"',
        source,
    )
    source, base_count = re.subn(
        r'(?m)^(\s*dataBase:\s*)"[^"]*"',
        lambda match: f'{match.group(1)}"{data_base}"',
        source,
    )
    if version_count != 1 or base_count != 1:
        raise ValueError("site-config.js must contain exactly one dataVersion and dataBase setting")
    source, view_count = re.subn(
        r'(?m)^(\s*defaultView:\s*\{)\s*center:\s*\[[^\]]+\],\s*zoom:\s*[\d.]+(\s*\},?)',
        lambda match: (
            f"{match.group(1)} center: [{(min_lon + max_lon) / 2:.6f}, "
            f"{(min_lat + max_lat) / 2:.6f}], zoom: {zoom:.1f}{match.group(2)}"
        ),
        source,
    )
    if view_count != 1:
        raise ValueError("site-config.js must contain exactly one defaultView")
    return source


def _gcloud_run(
    command: Sequence[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    runner(list(command), check=True, cwd=ROOT, text=True)


def configure_bucket(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Make the dedicated preview bucket public-read and expire developer objects."""
    bucket = _preview_bucket()
    settings = config.CLOUD_SHARED_BUILD
    retention_days = settings.get("preview_retention_days")
    if isinstance(retention_days, bool) or not isinstance(retention_days, int) or retention_days < 1:
        raise ValueError("config.CLOUD_SHARED_BUILD.preview_retention_days must be a positive integer")

    instance = runner([
        "gcloud", "compute", "instances", "describe", settings["vm"],
        f"--project={settings['project']}", f"--zone={settings['zone']}",
        "--format=json",
    ], check=True, capture_output=True, cwd=ROOT, text=True)
    try:
        instance_info = json.loads(instance.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("gcloud returned invalid shared VM details") from exc
    service_accounts = instance_info.get("serviceAccounts", [])
    if (
        not isinstance(service_accounts, list)
        or len(service_accounts) != 1
        or not isinstance(service_accounts[0], dict)
        or not isinstance(service_accounts[0].get("email"), str)
        or not re.fullmatch(r"[^@\s]+@[^@\s]+", service_accounts[0]["email"])
    ):
        raise RuntimeError(
            "shared VM must have exactly one service account so preview upload access "
            "can be granted narrowly"
        )
    service_account = service_accounts[0]["email"]

    bucket_info = runner(
        ["gcloud", "storage", "buckets", "describe", bucket, "--format=json"],
        check=True, capture_output=True, cwd=ROOT, text=True,
    )
    try:
        existing_bucket = json.loads(bucket_info.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("gcloud returned invalid preview bucket details") from exc
    existing_lifecycle = existing_bucket.get("lifecycle") or {}
    if not isinstance(existing_lifecycle, dict):
        raise RuntimeError("preview bucket has an invalid lifecycle policy")
    existing_rules = existing_lifecycle.get("rule", [])
    if not isinstance(existing_rules, list):
        raise RuntimeError("preview bucket has an invalid lifecycle policy")
    _gcloud_run([
        "gcloud", "storage", "buckets", "add-iam-policy-binding", bucket,
        "--member=allUsers", "--role=roles/storage.objectViewer", "-q",
    ], runner=runner)
    _gcloud_run([
        "gcloud", "storage", "buckets", "add-iam-policy-binding", bucket,
        f"--member=serviceAccount:{service_account}",
        "--role=roles/storage.objectUser", "-q",
    ], runner=runner)
    preview_rule = {
        "action": {"type": "Delete"},
        "condition": {"age": retention_days, "matchesPrefix": ["devel/"]},
    }
    lifecycle = {"rule": existing_rules}
    if preview_rule not in existing_rules:
        lifecycle["rule"].append(preview_rule)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json", delete=False,
    ) as policy_file:
        policy_path = Path(policy_file.name)
        json.dump(lifecycle, policy_file)
    try:
        _gcloud_run([
            "gcloud", "storage", "buckets", "update", bucket,
            f"--lifecycle-file={policy_path}",
        ], runner=runner)
    finally:
        policy_path.unlink(missing_ok=True)


def publish(
    user: str,
    regions: str,
    *,
    data_dir: Path = DATA,
    root: Path = ROOT,
    run_id: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    if not _USER.fullmatch(user):
        raise ValueError("preview user must be a 1-39 character alphanumeric/hyphen alias")
    selected_run_id = run_id or _run_id()
    if not _RUN_ID.fullmatch(selected_run_id):
        raise ValueError("preview run ID has an invalid format")

    bucket = _preview_bucket()
    user_prefix = f"{bucket}/devel/{user.lower()}"
    run_prefix = f"{user_prefix}/runs/{selected_run_id}"
    run_data = f"{run_prefix}/data"
    required = [data_dir / name for name in CORE_DATA_FILES]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "combined build is missing required preview artifacts: " + ", ".join(missing)
        )
    missing_frontend = [str(root / name) for name in FRONTEND_FILES if not (root / name).is_file()]
    if missing_frontend:
        raise FileNotFoundError(
            "preview frontend is missing required files: " + ", ".join(missing_frontend)
        )
    config_source = root / "site-config.js"
    if not config_source.is_file():
        raise FileNotFoundError(f"preview site configuration is missing: {config_source}")
    data_base = f"https://storage.googleapis.com/{bucket.removeprefix('gs://')}/devel/{user.lower()}/runs/{selected_run_id}/"
    rendered_config = _replace_site_config(
        config_source.read_text(encoding="utf-8"), data_base, selected_run_id, regions,
    )

    # Upload the immutable dataset first. Only move the stable preview pointer
    # after every data object and frontend asset has been written successfully.
    for name in SERVED_FILES:
        source = data_dir / name
        if source.is_file():
            _gcloud_run([
                "gcloud", "storage", "cp", "-q",
                f"--cache-control={IMMUTABLE_CACHE}",
                str(source), f"{run_data}/{name}",
            ], runner=runner)
    for name in (*SERVED_DIRS, *STATIC_DIRS):
        source = data_dir / name
        if source.is_dir():
            _gcloud_run([
                "gcloud", "storage", "rsync", "-r", "-q",
                f"--cache-control={IMMUTABLE_CACHE}",
                str(source), f"{run_data}/{name}",
            ], runner=runner)

    for name in FRONTEND_FILES:
        source = root / name
        if not source.is_file():
            raise FileNotFoundError(f"preview frontend asset is missing: {source}")
        _gcloud_run([
            "gcloud", "storage", "cp", "-q",
            f"--cache-control={REVALIDATE_CACHE}",
            str(source), f"{user_prefix}/{name}",
        ], runner=runner)

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".js", delete=False,
    ) as config_file:
        config_path = Path(config_file.name)
        config_file.write(rendered_config)
    try:
        _gcloud_run([
            "gcloud", "storage", "cp", "-q",
            f"--cache-control={REVALIDATE_CACHE}",
            str(config_path), f"{user_prefix}/site-config.js",
        ], runner=runner)
    finally:
        config_path.unlink(missing_ok=True)

    return f"https://storage.googleapis.com/{bucket.removeprefix('gs://')}/devel/{user.lower()}/preview.html"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user", help="publish a preview for this developer alias")
    group.add_argument(
        "--configure-bucket", action="store_true",
        help="grant public read and set the configured preview-object expiry policy",
    )
    parser.add_argument("--regions", help="space-separated configured region names")
    args = parser.parse_args()
    if args.configure_bucket:
        if args.regions:
            parser.error("--regions cannot be used with --configure-bucket")
        configure_bucket()
        print(
            f"Configured {_preview_bucket()} for public read and shared-VM uploads; "
                f"developer preview objects expire after "
                f"{config.CLOUD_SHARED_BUILD['preview_retention_days']} days."
        )
        return 0
    if not args.regions:
        parser.error("--regions is required when publishing a preview")
    url = publish(args.user, args.regions)
    print(f"PREVIEW_URL={url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
