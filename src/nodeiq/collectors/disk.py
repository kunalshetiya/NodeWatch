"""Disk + inodes collector: filesystem capacity and inode usage.

Answers "why might disk space be running out?" — one of NodeIQ's
headline example questions — for every mounted filesystem. Follows the
same `CollectorContext` -> `collect()` -> `CollectorResult` pattern
established by `system.py`, `cpu_memory.py`, and `processes.py`. Unlike
those, there is no single kernel-provided file exposing this
information in a simpler machine-readable form (see docs/disk_collector.md,
"Why df Was Chosen"), so this collector runs `df` through
`nodeiq.core.runner.run_command` — the one collector so far that needs
commands rather than `/proc` file reads.

Two independent `df` invocations are gathered and merged by mount point:
`df -P -B1` (byte-precise filesystem capacity) and `df -P -i` (inode
usage). `-P` forces POSIX output — one line per filesystem, a stable
column layout — regardless of how long a device name is; see
docs/disk_collector.md for why.
"""

import time

from nodeiq.core.collector import CollectorContext, CollectorResult
from nodeiq.core.errors import error_entry
from nodeiq.core.runner import command_failure_message, run_command

_DISK_USAGE_COMMAND = ["df", "-P", "-B1"]
_INODE_USAGE_COMMAND = ["df", "-P", "-i"]


def collect(context: CollectorContext) -> CollectorResult:
    """Gather filesystem capacity and inode usage for every mounted
    filesystem, merged into one list.

    Disk usage (`df -P -B1`) and inode usage (`df -P -i`) are two
    independent data sources. If disk usage can't be determined, nothing
    useful can be returned at all (there would be no filesystems to
    attach inode data to), so that failure is recorded and an empty
    result is returned. If only inode usage fails, the disk usage data
    collected is still returned in full, with every filesystem's inode
    fields set to `None` and one error entry describing what went wrong
    — partial data always beats no data (PROJECT_RULES.md Section 7).
    """
    start_time = time.monotonic()
    errors: list[dict] = []

    try:
        filesystems = _get_disk_usage(context)
    except ValueError as exc:
        errors.append(error_entry(exc))
        return CollectorResult(
            collector_name="disk",
            data={
                "filesystems": [],
                "highest_disk_usage_percent": None,
                "highest_inode_usage_percent": None,
            },
            errors=errors,
            duration_ms=(time.monotonic() - start_time) * 1000,
        )

    try:
        inode_usage_by_mount = _get_inode_usage(context)
    except ValueError as exc:
        inode_usage_by_mount = {}
        errors.append(error_entry(exc))

    merged = _merge_filesystems(filesystems, inode_usage_by_mount)

    data = {
        "filesystems": merged,
        "highest_disk_usage_percent": _highest(merged, "usage_percent"),
        "highest_inode_usage_percent": _highest(merged, "inode_usage_percent"),
    }

    return CollectorResult(
        collector_name="disk",
        data=data,
        errors=errors,
        duration_ms=(time.monotonic() - start_time) * 1000,
    )


def _get_disk_usage(context: CollectorContext) -> list[dict]:
    """Run `df -P -B1` and parse it into a list of per-filesystem disk
    usage dicts.

    Raises `ValueError` if the command fails or its output doesn't parse.
    """
    result = run_command(_DISK_USAGE_COMMAND, timeout=context.default_timeout)
    if not result.succeeded:
        raise ValueError(command_failure_message(_DISK_USAGE_COMMAND, result))
    return _parse_df_output(result.stdout)


def _parse_df_output(raw_text: str) -> list[dict]:
    """Pure function: `df -P -B1`'s text in, a list of per-filesystem disk
    usage dicts out. No subprocess calls, no I/O — just string parsing, so
    it can be tested with a literal sample string.
    """
    filesystems = []
    for line in raw_text.splitlines():
        if not line.strip() or line.startswith("Filesystem"):
            continue
        tokens = line.split()
        if len(tokens) < 6:
            raise ValueError(f"could not parse df -P -B1 line: {line!r}")
        try:
            filesystems.append(
                {
                    "filesystem": tokens[0],
                    "total_bytes": int(tokens[1]),
                    "used_bytes": int(tokens[2]),
                    "available_bytes": int(tokens[3]),
                    "usage_percent": _parse_percent(tokens[4]),
                    "mount_point": " ".join(tokens[5:]),
                }
            )
        except ValueError as exc:
            raise ValueError(f"could not parse df -P -B1 line {line!r}: {exc}") from exc
    return filesystems


def _get_inode_usage(context: CollectorContext) -> dict:
    """Run `df -P -i` and parse it into a dict of inode usage keyed by
    mount point.

    Raises `ValueError` if the command fails or its output doesn't parse.
    """
    result = run_command(_INODE_USAGE_COMMAND, timeout=context.default_timeout)
    if not result.succeeded:
        raise ValueError(command_failure_message(_INODE_USAGE_COMMAND, result))
    return _parse_df_inode_output(result.stdout)


def _parse_df_inode_output(raw_text: str) -> dict:
    """Pure function: `df -P -i`'s text in, a dict of inode usage keyed by
    mount point out. No subprocess calls, no I/O — just string parsing, so
    it can be tested with a literal sample string.

    Some filesystems (e.g. `efivarfs`, a `vfat` boot partition) report
    `-` for every inode field, since they don't support the inode concept
    at all — this is parsed as `None`, not an error (see
    docs/disk_collector.md, "What an Inode Is").
    """
    inode_usage_by_mount = {}
    for line in raw_text.splitlines():
        if not line.strip() or line.startswith("Filesystem"):
            continue
        tokens = line.split()
        if len(tokens) < 6:
            raise ValueError(f"could not parse df -P -i line: {line!r}")
        mount_point = " ".join(tokens[5:])
        inode_usage_by_mount[mount_point] = {
            "inode_total": _parse_optional_int(tokens[1]),
            "inode_used": _parse_optional_int(tokens[2]),
            "inode_available": _parse_optional_int(tokens[3]),
            "inode_usage_percent": _parse_percent(tokens[4]),
        }
    return inode_usage_by_mount


def _parse_percent(token: str) -> float | None:
    """Pure function: a percentage token like `'60%'` in, `60.0` out.
    Returns `None` for `'-'`, which `df` prints when a filesystem doesn't
    support the concept being measured (e.g. inodes on `efivarfs`).
    """
    if token == "-":
        return None
    try:
        return float(token.rstrip("%"))
    except ValueError as exc:
        raise ValueError(f"could not parse percentage {token!r}") from exc


def _parse_optional_int(token: str) -> int | None:
    """Pure function: a numeric token in, an int out — or `None` for
    `'-'`, which `df -i` prints for filesystems with no inode concept.
    """
    if token == "-":
        return None
    try:
        return int(token)
    except ValueError as exc:
        raise ValueError(f"could not parse integer {token!r}") from exc


def _merge_filesystems(filesystems: list[dict], inode_usage_by_mount: dict) -> list[dict]:
    """Combine disk usage entries with their matching inode usage entries,
    keyed by mount point.

    A filesystem with no matching inode data (either `df -P -i` failed
    entirely, or that specific mount point wasn't in its output) gets
    `None` for all four inode fields rather than being dropped — disk
    usage is still real, useful data on its own.
    """
    merged = []
    for fs in filesystems:
        inode_fields = inode_usage_by_mount.get(
            fs["mount_point"],
            {
                "inode_total": None,
                "inode_used": None,
                "inode_available": None,
                "inode_usage_percent": None,
            },
        )
        merged.append({**fs, **inode_fields})
    return merged


def _highest(filesystems: list[dict], field: str) -> float | None:
    """Pure function: the highest value of `field` across every
    filesystem that has one, or `None` if no filesystem does (an empty
    list, or every value for `field` is `None`).
    """
    values = [fs[field] for fs in filesystems if fs[field] is not None]
    return max(values) if values else None
