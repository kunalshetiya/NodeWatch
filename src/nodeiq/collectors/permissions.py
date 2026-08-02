"""Permissions collector: ownership and permission facts for a small,
conservative set of security-sensitive paths.

This is intentionally the narrowest-scoped collector in NodeIQ v1 — see
docs/snapshot_schema.md Section 11, which explicitly left this
section's exact scope as an open question until implementation. It
checks a fixed, conservative list of paths (not a security audit, not a
recursive filesystem scan) and reports each one's existence, owner,
group, permission mode, and whether it's world-writable.

Needs no commands at all: every fact comes from `pathlib.Path.stat()`
plus `pwd`/`grp` lookups, the same pattern `processes.py` already uses
for UID resolution.
"""

import time
from pathlib import Path

from nodeiq.core.collector import CollectorContext, CollectorResult
from nodeiq.core.errors import error_entry
from nodeiq.core.identity import resolve_groupname, resolve_username

_CHECKED_PATHS = [
    Path("/etc/passwd"),
    Path("/etc/shadow"),
    Path("/etc/ssh"),
    Path("/var/log"),
]


def collect(context: CollectorContext) -> CollectorResult:
    """Check ownership and permissions for every path in
    `_CHECKED_PATHS`.

    Each path is checked independently — one path that can't be
    `stat()`-ed (a genuine permission error, not simple absence) never
    prevents the rest from being checked. A path that doesn't exist at
    all is valid data (`exists: False`), never an error, per
    PROJECT_RULES.md Section 7's "the system genuinely has none of
    this" vs. "we couldn't check" distinction.
    """
    start_time = time.monotonic()
    errors: list[dict] = []
    checked_paths = []

    for path in _CHECKED_PATHS:
        entry, error = _check_path(path)
        checked_paths.append(entry)
        if error is not None:
            errors.append(error)

    return CollectorResult(
        collector_name="permissions",
        data={"checked_paths": checked_paths},
        errors=errors,
        duration_ms=(time.monotonic() - start_time) * 1000,
    )


def _check_path(path: Path) -> tuple[dict, dict | None]:
    """Check one path's existence, ownership, and permissions.

    Returns a `(entry, error)` pair — `error` is `None` unless `stat()`
    failed for a reason other than the path simply not existing (e.g. a
    permission error on a parent directory), in which case `entry`'s
    fields are all `None` except `exists`, which is also `None` (unknown,
    not "false") since we couldn't actually determine whether the path
    exists.
    """
    try:
        stat_result = path.stat()
    except FileNotFoundError:
        return _empty_entry(path, exists=False), None
    except OSError as exc:
        error = error_entry(exc, message=f"could not check {path}: {exc}")
        return _empty_entry(path, exists=None), error

    return _entry_from_stat(path, stat_result), None


def _empty_entry(path: Path, exists: bool | None) -> dict:
    """The shape returned for a path with no permission data available —
    either because it doesn't exist (`exists=False`) or because it
    couldn't be checked at all (`exists=None`)."""
    return {
        "path": str(path),
        "exists": exists,
        "owner": None,
        "group": None,
        "mode": None,
        "world_writable": None,
    }


def _entry_from_stat(path: Path, stat_result) -> dict:
    """Build a fully-populated entry from a successful `stat()` result."""
    mode = stat_result.st_mode & 0o777
    return {
        "path": str(path),
        "exists": True,
        "owner": resolve_username(stat_result.st_uid),
        "group": resolve_groupname(stat_result.st_gid),
        "mode": oct(mode)[2:].zfill(3),
        "world_writable": bool(mode & 0o002),
    }
