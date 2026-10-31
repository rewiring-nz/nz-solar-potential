"""Region identifiers and where their directories live.

A region id is its path relative to data/regions/ (and data/out/):

  <territorial-authority>/<town-city>/<suburb-locality>
      A LINZ NZ Suburbs and Localities area (src/localities.py), e.g.
      queenstown-lakes-district/queenstown/frankton. Each segment is the
      slug of the LINZ ASCII name.

  <name>
      A legacy bounding-box region from config.REGIONS, or `pilot`.

Ids are used directly as relative paths, so code that does
`REGIONS_DIR / region` handles both shapes. Where a region must be a single
file name (build markers, logs, queue files) use file_slug().

Standard library only: imported by queue validation and CLI argument checks.
"""

import re
from pathlib import Path

_SEGMENT = r"[a-z0-9][a-z0-9_-]{0,63}"
_LOCALITY_RE = re.compile(rf"{_SEGMENT}/{_SEGMENT}/{_SEGMENT}")
_LEGACY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
LOCALITY_DEPTH = 3


def is_locality_id(region: str) -> bool:
    return bool(_LOCALITY_RE.fullmatch(region))


def validate(region: str) -> str:
    """Return `region` if it is a path-safe region id, else raise ValueError."""
    if isinstance(region, str) and (_LEGACY_RE.fullmatch(region) or is_locality_id(region)):
        return region
    raise ValueError(
        "region must be a configured name (letters, digits, '_' or '-') or a LINZ "
        "locality id '<territorial-authority>/<town-city>/<suburb-locality>' of "
        "lower-case slugs; find one with: python tools/find_area.py <name>")


def file_slug(region: str) -> str:
    """A single file-name segment for `region` (markers, logs, queue files)."""
    return validate(region).replace("/", "__")


def list_regions(root: Path, sentinel: str | None = None) -> list[str]:
    """Region ids under `root` (data/regions or data/out).

    A top-level directory holding only directories, whose third-level
    directories hold files, is a territorial-authority container and those
    third-level directories are locality regions. Any other top-level
    directory holding a file is a legacy region; directories with no files
    (an empty council container) are skipped. With `sentinel`, only regions whose
    directory contains that file are returned.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    found = []

    def keep(p: Path) -> bool:
        return not p.name.startswith((".", "_")) and p.is_dir()

    def has_file(p: Path) -> bool:
        return any(c.is_file() for c in p.iterdir())

    for top in sorted(filter(keep, root.iterdir())):
        localities = []
        if not has_file(top):
            for town in sorted(filter(keep, top.iterdir())):
                if has_file(town):
                    localities = []
                    break
                localities += [a for a in sorted(filter(keep, town.iterdir()))
                               if has_file(a) and is_locality_id(a.relative_to(root).as_posix())]
        found += localities or ([top] if has_file(top) else [])
    ids = []
    for p in found:
        rid = p.relative_to(root).as_posix()
        try:
            validate(rid)
        except ValueError:
            continue
        if sentinel is None or (p / sentinel).exists():
            ids.append(rid)
    return ids
