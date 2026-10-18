"""Create isolated cloud developer workspaces and share validated source inputs."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_USER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$")
_REGION_INPUTS = (
    "building_outlines.geojson",
    "dsm_mosaic.tif",
    "imagery_mosaic.tif",
    "reference_imagery_mosaic.tif",
)
_ROOT_INPUTS = ("dem_wide_mosaic.tif",)


def new_run_id() -> str:
    return f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"


def _run(command: list[str], *, cwd: Path | None = None) -> str:
    return subprocess.run(
        command, cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def create_workspace(
    repo: Path,
    base_root: Path,
    user: str,
    ref: str,
    commit: str,
    run_id: str,
) -> tuple[Path, Path]:
    if not _USER.fullmatch(user):
        raise ValueError("developer ID must be a 1-39 character alphanumeric/hyphen alias")
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run ID has an invalid format")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("build commit must be a full lowercase Git SHA")
    if not ref or ref.startswith("-") or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-"
        for c in ref
    ):
        raise ValueError("Git ref contains unsupported characters")

    repo = repo.expanduser().resolve()
    developer_root = base_root.expanduser() / user.lower()
    source_checkout = developer_root / "checkout"
    run_root = developer_root / "runs" / run_id
    run_checkout = run_root / "checkout"
    lock_path = base_root.expanduser() / ".locks" / f"{user.lower()}.lock"
    worktree_lock_path = base_root.expanduser() / ".locks" / "worktrees.lock"

    with _file_lock(worktree_lock_path), _file_lock(lock_path):
        _run(["git", "check-ref-format", f"refs/heads/{ref}"], cwd=repo)
        _run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=repo)
        if not source_checkout.exists():
            source_checkout.parent.mkdir(parents=True, exist_ok=True)
            _run([
                "git", "worktree", "add", "--detach",
                str(source_checkout), commit,
            ], cwd=repo)
        elif not (source_checkout / ".git").exists():
            raise RuntimeError(f"developer checkout exists but is not a Git worktree: {source_checkout}")
        else:
            _run(["git", "checkout", "--detach", commit], cwd=source_checkout)

        run_root.mkdir(parents=True, exist_ok=False)
        try:
            _run([
                "git", "worktree", "add", "--detach", str(run_checkout), commit,
            ], cwd=repo)
            shared_venv = repo / ".venv"
            if shared_venv.exists():
                (run_checkout / ".venv").symlink_to(shared_venv, target_is_directory=True)
            shared_env = repo / ".env"
            if shared_env.is_file():
                (run_checkout / ".env").symlink_to(shared_env)
        except BaseException:
            if run_checkout.exists():
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(run_checkout)],
                    cwd=repo, check=False, capture_output=True, text=True,
                )
            shutil.rmtree(run_root)
            raise

    return run_root, run_checkout


def _load_code_config(code_root: Path):
    code_root = code_root.resolve()
    sys.path.insert(0, str(code_root))
    import config
    from src.surveys import survey_for
    return config, survey_for


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def _packed_tiles(pointcloud_dir: Path) -> set[str]:
    registry = pointcloud_dir / ".packed.json"
    if not registry.is_file():
        return set()
    try:
        names = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid packed-tile registry: {registry}") from exc
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise RuntimeError(f"invalid packed-tile registry: {registry}")
    return set(names)


def _source_items(
    source_data: Path,
    run_data: Path,
    cache_root: Path,
    code_root: Path,
    regions: list[str],
) -> list[tuple[Path, Path, Path]]:
    config, survey_for = _load_code_config(code_root)
    items = []
    dem_identity = _digest({
        "layer": getattr(config, "LINZ_DEM_WIDE_LAYER", 51768),
        "bbox": getattr(config, "DEM_WIDE_BBOX", None),
    })
    for name in _ROOT_INPUTS:
        items.append((
            source_data / name,
            run_data / name,
            cache_root / "dem" / dem_identity / name,
        ))
    packed_tiles = _packed_tiles(source_data / "pointcloud")
    for dirname in ("pointcloud",):
        src_dir = source_data / dirname
        if src_dir.is_dir():
            for src in sorted(src_dir.iterdir()):
                if (
                    src.is_file()
                    and not src.name.startswith(".")
                    and (
                        (dirname == "pointcloud" and src.name.endswith(".laz"))
                        or (dirname == "selected_faces" and src.suffix == ".json")
                    )
                    and not (dirname == "pointcloud" and src.name in packed_tiles)
                ):
                    items.append((
                        src,
                        run_data / dirname / src.name,
                        cache_root / dirname / src.name,
                    ))

    for region in regions:
        if region not in config.REGIONS:
            raise ValueError(f"developer builds require configured region names: {region}")
        bbox = list(config.REGIONS[region])
        survey = survey_for(bbox, region)
        identity = _digest({"bbox": bbox, "survey": survey})
        region_inputs = _REGION_INPUTS
        if survey.get("elevation") == "synthetic":
            region_inputs = tuple(
                name for name in region_inputs if name != "dsm_mosaic.tif"
            )
        for name in region_inputs:
            items.append((
                source_data / "regions" / region / name,
                run_data / "regions" / region / name,
                cache_root / "regions" / region / identity / name,
            ))
    return items


def _validate_source(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        if path.suffix in {".json", ".geojson"}:
            with path.open(encoding="utf-8") as source:
                document = json.load(source)
            if path.suffix == ".geojson" and (
                not isinstance(document, dict)
                or document.get("type") != "FeatureCollection"
                or not isinstance(document.get("features"), list)
            ):
                return False
        elif path.suffix in {".tif", ".tiff"}:
            import rasterio
            with rasterio.open(path) as source:
                if source.width <= 0 or source.height <= 0:
                    return False
        elif path.name.endswith(".laz"):
            import laspy
            with laspy.open(path) as source:
                if source.header.point_count <= 0:
                    return False
        return True
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _marker_path(cache_path: Path) -> Path:
    return cache_path.with_name(cache_path.name + ".complete.json")


def _cached(cache_path: Path) -> bool:
    marker = _marker_path(cache_path)
    try:
        metadata = json.loads(marker.read_text(encoding="utf-8"))
        return (
            cache_path.is_file()
            and cache_path.stat().st_size > 0
            and metadata == {"size": cache_path.stat().st_size}
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _write_marker(cache_path: Path) -> None:
    marker = _marker_path(cache_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=marker.parent, delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
        json.dump({"size": cache_path.stat().st_size}, temporary)
        temporary.flush()
        os.fsync(temporary.fileno())
    os.replace(temporary_path, marker)


def _seed_cache(
    source_path: Path,
    cache_path: Path,
    cache_root: Path,
) -> None:
    lock_path = cache_root / ".locks" / (hashlib.sha256(str(cache_path).encode()).hexdigest() + ".lock")
    with _file_lock(lock_path):
        if _cached(cache_path):
            return
        if not _validate_source(source_path):
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.unlink(missing_ok=True)
        marker = _marker_path(cache_path)
        marker.unlink(missing_ok=True)
        cache_path.symlink_to(source_path.resolve())
        _write_marker(cache_path)


def _link_cache(cache_path: Path, run_path: Path, cache_root: Path) -> None:
    if not _cached(cache_path):
        return
    if run_path.is_symlink() and run_path.resolve() == cache_path.resolve():
        return
    if run_path.exists() and not run_path.is_symlink():
        return
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.unlink(missing_ok=True)
    run_path.symlink_to(cache_path.resolve())


def prepare_sources(
    source_data: Path,
    run_data: Path,
    cache_root: Path,
    code_root: Path,
    regions: list[str],
) -> None:
    for source, target, cache_path in _source_items(
        source_data, run_data, cache_root, code_root, regions,
    ):
        _seed_cache(source, cache_path, cache_root)
        _link_cache(cache_path, target, cache_root)
    shared_faces = source_data / "selected_faces"
    if shared_faces.is_dir():
        for source in shared_faces.glob("*.json"):
            if not _validate_source(source):
                continue
            target = run_data / "selected_faces" / source.name
            if target.exists() and not target.is_symlink():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)
            target.symlink_to(source.resolve())
    packed_source = source_data / "pointcloud" / ".packed.json"
    if packed_source.is_file():
        packed_tiles = sorted(_packed_tiles(source_data / "pointcloud"))
        packed_target = run_data / "pointcloud" / ".packed.json"
        packed_target.parent.mkdir(parents=True, exist_ok=True)
        packed_target.write_text(json.dumps(packed_tiles), encoding="utf-8")


def promote_sources(
    run_data: Path,
    cache_root: Path,
    code_root: Path,
    regions: list[str],
) -> None:
    items = _source_items(run_data, run_data, cache_root, code_root, regions)
    for _source, run_path, cache_path in items:
        if not run_path.is_file() or run_path.is_symlink():
            continue
        lock_path = cache_root / ".locks" / (hashlib.sha256(str(cache_path).encode()).hexdigest() + ".lock")
        with _file_lock(lock_path):
            if not _cached(cache_path):
                if not _validate_source(run_path):
                    raise RuntimeError(f"downloaded source is incomplete or invalid: {run_path}")
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary_name = tempfile.mkstemp(
                    prefix=f".{cache_path.name}.", dir=cache_path.parent,
                )
                os.close(fd)
                temporary_path = Path(temporary_name)
                marker = _marker_path(cache_path)
                marker.unlink(missing_ok=True)
                try:
                    shutil.copy2(run_path, temporary_path)
                    if not _validate_source(temporary_path):
                        raise RuntimeError(f"downloaded source failed cache validation: {run_path}")
                    os.replace(temporary_path, cache_path)
                    _write_marker(cache_path)
                finally:
                    temporary_path.unlink(missing_ok=True)
            if _cached(cache_path):
                run_path.unlink()
                run_path.symlink_to(cache_path.resolve())


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--repo", type=Path, required=True)
    create.add_argument("--base-root", type=Path, required=True)
    create.add_argument("--user", required=True)
    create.add_argument("--ref", required=True)
    create.add_argument("--commit", required=True)
    create.add_argument("--run-id", default=None)

    for action in ("prepare", "promote"):
        command = subparsers.add_parser(action)
        command.add_argument("--source-data", type=Path, required=True)
        command.add_argument("--run-data", type=Path, required=True)
        command.add_argument("--cache-root", type=Path, required=True)
        command.add_argument("--code-root", type=Path, required=True)
        command.add_argument("--regions", required=True)

    args = parser.parse_args()
    if args.action == "create":
        run_id = args.run_id or new_run_id()
        run_root, checkout = create_workspace(
            args.repo, args.base_root, args.user, args.ref, args.commit, run_id,
        )
        print(f"RUN_ID={run_id}")
        print(f"RUN_ROOT={run_root}")
        print(f"CHECKOUT={checkout}")
        print(f"COMMIT={args.commit}")
        return 0

    regions = args.regions.split()
    if args.action == "prepare":
        prepare_sources(
            args.source_data, args.run_data, args.cache_root,
            args.code_root, regions,
        )
    else:
        promote_sources(
            args.run_data, args.cache_root, args.code_root, regions,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
