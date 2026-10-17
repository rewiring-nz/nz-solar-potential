"""Load and validate local, non-secret Google Cloud environment profiles."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PROFILE_PATH = Path.home() / ".config" / "nz-solar-potential" / "cloud-profiles.json"
PROFILE_PATH_ENV = "SOLAR_CLOUD_PROFILES_FILE"

_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_REGION = re.compile(r"^[a-z]+-[a-z]+[0-9]$")
_ZONE = re.compile(r"^([a-z]+-[a-z]+[0-9])-([a-z])$")
_MACHINE = re.compile(r"^[a-z0-9-]+$")
_BUCKET = re.compile(r"^gs://[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$")
_PREFIX = re.compile(r"^[a-zA-Z0-9._/-]*$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")

_KNOWN_KEYS = {
    "project", "region", "zone", "instance_name", "machine_type", "disk_gb",
    "image_project", "image_family", "service_account", "network", "subnetwork",
    "ssh_source_ranges", "bucket", "build_prefix", "tiles_bucket",
    "preview_port", "fleet_zone", "fleet_machine_type", "fleet_disk_gb",
    "source_vm", "source_zone", "worker_image", "cost_estimate_nzd",
    "cost_estimate_basis",
}


@dataclass(frozen=True)
class CloudProfile:
    name: str
    values: dict[str, Any]

    @property
    def target(self) -> str:
        return f"{self.values['project']}/{self.values['zone']}/{self.values['instance_name']}"

    def get(self, key: str) -> Any:
        if key not in self.values:
            raise ValueError(f"profile {self.name!r} has no {key!r} setting")
        return self.values[key]


def profile_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    return Path(os.environ.get(PROFILE_PATH_ENV, DEFAULT_PROFILE_PATH)).expanduser()


def _required_string(values: dict[str, Any], key: str, pattern: re.Pattern[str]) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"profile field {key!r} is missing or invalid")
    return value


def _optional_string(values: dict[str, Any], key: str, pattern: re.Pattern[str]) -> None:
    value = values.get(key)
    if value is not None and (not isinstance(value, str) or not pattern.fullmatch(value)):
        raise ValueError(f"profile field {key!r} must be null or a valid string")


def _validate_values(name: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"profile {name!r} must be a JSON object")
    unknown = set(raw) - _KNOWN_KEYS
    if unknown:
        raise ValueError(f"profile {name!r} has unknown field(s): {', '.join(sorted(unknown))}")
    values = dict(raw)
    _required_string(values, "project", _PROJECT)
    region = _required_string(values, "region", _REGION)
    zone = _required_string(values, "zone", re.compile(_ZONE.pattern))
    zone_match = _ZONE.fullmatch(zone)
    if zone_match is None or zone_match.group(1) != region:
        raise ValueError(f"profile {name!r}: zone {zone!r} is not in region {region!r}")
    _required_string(values, "instance_name", _NAME)
    _required_string(values, "machine_type", _MACHINE)
    _required_string(values, "image_project", _NAME)
    _required_string(values, "image_family", _NAME)

    disk_gb = values.get("disk_gb")
    if isinstance(disk_gb, bool) or not isinstance(disk_gb, int) or not 30 <= disk_gb <= 2000:
        raise ValueError(f"profile {name!r}: disk_gb must be an integer from 30 to 2000")
    preview_port = values.get("preview_port", 8765)
    if isinstance(preview_port, bool) or not isinstance(preview_port, int) or not 1024 <= preview_port <= 65535:
        raise ValueError(f"profile {name!r}: preview_port must be an integer from 1024 to 65535")
    values["preview_port"] = preview_port

    network = values.get("network", "default")
    if not isinstance(network, str) or not _NAME.fullmatch(network):
        raise ValueError(f"profile {name!r}: network must be a valid GCP network name")
    values["network"] = network
    if "subnetwork" in values:
        _required_string(values, "subnetwork", _NAME)

    service_account = values.get("service_account")
    if service_account is not None and (
        not isinstance(service_account, str) or not _EMAIL.fullmatch(service_account)
    ):
        raise ValueError(f"profile {name!r}: service_account must be null or an email address")

    ranges = values.get("ssh_source_ranges", [])
    if not isinstance(ranges, list) or any(not isinstance(item, str) for item in ranges):
        raise ValueError(f"profile {name!r}: ssh_source_ranges must be a list of CIDR strings")
    for cidr in ranges:
        try:
            network = ipaddress.ip_network(cidr, strict=True)
        except ValueError as exc:
            raise ValueError(f"profile {name!r}: invalid ssh source CIDR {cidr!r}: {exc}") from exc
        if network.prefixlen == 0:
            raise ValueError(f"profile {name!r}: global SSH source ranges are not allowed")
    values["ssh_source_ranges"] = ranges

    _optional_string(values, "bucket", _BUCKET)
    _optional_string(values, "tiles_bucket", _BUCKET)
    _optional_string(values, "build_prefix", _PREFIX)
    for key in ("fleet_zone", "source_zone"):
        if key in values:
            _required_string(values, key, re.compile(_ZONE.pattern))
    for key in ("fleet_machine_type", "worker_image"):
        if key in values:
            _required_string(values, key, _MACHINE if key == "fleet_machine_type" else _NAME)
    if "fleet_disk_gb" in values and (
        isinstance(values["fleet_disk_gb"], bool)
        or not isinstance(values["fleet_disk_gb"], int)
        or not 30 <= values["fleet_disk_gb"] <= 2000
    ):
        raise ValueError(f"profile {name!r}: fleet_disk_gb must be an integer from 30 to 2000")
    has_estimate = "cost_estimate_nzd" in values
    has_basis = "cost_estimate_basis" in values
    if has_estimate != has_basis:
        raise ValueError(
            f"profile {name!r}: cost_estimate_nzd and cost_estimate_basis must be set together"
        )
    if has_estimate:
        estimate = values["cost_estimate_nzd"]
        if not isinstance(estimate, str) or not re.fullmatch(
            r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?", estimate
        ):
            raise ValueError(
                f"profile {name!r}: cost_estimate_nzd must be a non-negative amount "
                "with up to two decimal places"
            )
        basis = values["cost_estimate_basis"]
        if not isinstance(basis, str) or not basis.strip() or len(basis) > 300:
            raise ValueError(
                f"profile {name!r}: cost_estimate_basis must be 1-300 characters"
            )
    for key in ("source_vm",):
        if key in values:
            _required_string(values, key, _NAME)

    return values


def load_profile(
    name: str | None = None,
    path: str | os.PathLike[str] | None = None,
) -> CloudProfile:
    selected = name or os.environ.get("SOLAR_CLOUD_PROFILE")
    if not selected or not _NAME.fullmatch(selected):
        raise ValueError("select a profile with --profile NAME or SOLAR_CLOUD_PROFILE")
    source = profile_path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"cloud profiles file not found: {source}; copy "
            "tools/cloud-profiles.example.json and edit it"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"cloud profiles file is not valid JSON: {source}: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("profiles"), dict):
        raise ValueError(f"{source} must contain a top-level 'profiles' object")
    profiles = document["profiles"]
    if selected not in profiles:
        raise ValueError(f"profile {selected!r} is not defined in {source}")
    values = _validate_values(selected, profiles[selected])
    return CloudProfile(selected, values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", help="profile name (or SOLAR_CLOUD_PROFILE)")
    parser.add_argument("--file", help="profile JSON file (or SOLAR_CLOUD_PROFILES_FILE)")
    parser.add_argument("action", choices=("show", "get"))
    parser.add_argument("key", nargs="?")
    args = parser.parse_args()
    try:
        profile = load_profile(args.profile, args.file)
        if args.action == "show":
            print(json.dumps({"name": profile.name, **profile.values}, indent=2))
            return 0
        if not args.key:
            parser.error("get requires a field name")
        value = profile.get(args.key)
        if isinstance(value, (dict, list)):
            print(json.dumps(value))
        elif value is not None:
            print(value)
        return 0
    except ValueError as exc:
        print(f"cloud profile error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
