"""Summary Engine: turns a raw snapshot into one concise Summary.

Implements the design in docs/summary_engine_design.md, with two
refinements agreed for this phase: the per-section structured-facts
field is called `evidence` (kept deliberately concise — see below), and
every section additionally carries a deterministic `status` — one of
`"healthy"`, `"warning"`, `"critical"`, or `"unknown"` — computed only
from fixed, named-constant thresholds. This engine never diagnoses,
never recommends, and never speculates: every `status`/`concerns` value
is a transparent function of a number already present in the snapshot
and a threshold written in this file, nothing else.

Mirrors the collector/coordinator architecture one layer up (see
docs/summary_engine_design.md Section 6): one pure `_summarize_<section>`
function per snapshot section, orchestrated by `summarize_snapshot()`,
which never lets one section's summarizer crash the whole Summary — the
same last-resort safety net `nodeiq.core.coordinator.run_scan()` already
applies to collectors.

Typical usage:

    from nodeiq.core.snapshot import load_latest_snapshot
    from nodeiq.summary import summarize_snapshot

    summary = summarize_snapshot(load_latest_snapshot())

`nodeiq.core.coordinator` and `nodeiq.core.snapshot` never import this
module, and this module never imports either of them — it depends only
on the plain dict shape they already produce (see
docs/summary_engine_design.md Section 2).
"""

from datetime import datetime, timezone

from nodeiq.core.errors import error_entry
from nodeiq.core.redaction import redact_secrets

# --- Thresholds -----------------------------------------------------------------
#
# Every threshold below is a fixed, named constant — the only inputs a
# section's `status`/`concerns` are ever computed from, alongside the
# snapshot's own numbers. None of these are inferred, learned, or
# configurable in v1 (see docs/summary_engine_design.md, Open Question 3).

_CPU_WARNING_PERCENT = 80.0
_CPU_CRITICAL_PERCENT = 95.0
_MEMORY_WARNING_PERCENT = 75.0
_MEMORY_CRITICAL_PERCENT = 90.0
_SWAP_WARNING_PERCENT = 50.0

_ZOMBIE_WARNING_COUNT = 10
_ZOMBIE_CRITICAL_COUNT = 50
_BLOCKED_WARNING_COUNT = 1
_BLOCKED_CRITICAL_COUNT = 5

_DISK_WARNING_PERCENT = 85.0
_DISK_CRITICAL_PERCENT = 95.0

_FAILED_SERVICES_WARNING_COUNT = 1
_FAILED_SERVICES_CRITICAL_COUNT = 5

_LOG_ERROR_WARNING_COUNT = 1
_LOG_ERROR_CRITICAL_COUNT = 10

_MAX_NAMED_ITEMS = 5
"""How many individual names (failed services, world-writable paths, ...)
a concern will list before summarizing the rest as "and N more" — keeps
`concerns` readable even if an unusually large number of items match."""

_MAX_TOP_PROCESSES_IN_EVIDENCE = 10
"""How many entries `top_processes_by_memory`/`top_processes_by_cpu`
include in evidence — matches `processes.py`'s own `_TOP_N` exactly, so
a question like "top 10 processes" can be fully answered from evidence
rather than truncated a second time at this layer."""


def summarize_snapshot(snapshot: dict) -> dict:
    """Turn a raw snapshot into one Summary: scan-level bookkeeping plus
    one SectionSummary per registered section.

    Every section is summarized independently — a section whose
    collector crashed (its value in `snapshot` is `None`) or is simply
    absent (an older snapshot, missing a section a newer NodeIQ version
    added) is reported as unavailable, never as a crash. A section whose
    *summarizer* itself raises is caught here and reported the same way,
    with the exception recorded in that section's `errors` — one
    summarizer failing never stops the rest from being summarized.
    """
    metadata = snapshot.get("metadata") or {}
    collection_errors = snapshot.get("collection_errors") or {}

    sections = {}
    for section_name, summarizer in _REGISTERED_SUMMARIZERS:
        section_data = snapshot.get(section_name)
        section_errors = list(collection_errors.get(section_name, []))
        sections[section_name] = _summarize_section(
            section_name, summarizer, section_data, section_errors
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_timestamp": metadata.get("scan_timestamp"),
        "hostname": metadata.get("hostname"),
        "sections": sections,
        "collection_errors": collection_errors,
    }


def _summarize_section(
    section_name: str, summarizer, section_data, section_errors: list
) -> dict:
    """Run one section's summarizer, isolating any failure to this one
    section — mirrors `nodeiq.core.coordinator.run_scan()`'s own
    last-resort `try`/`except` around each collector call.
    """
    if section_data is None:
        return _unavailable_section(section_errors, "No data available for this section.")

    try:
        return summarizer(section_data, section_errors)
    except Exception as exc:
        crash_error = error_entry(
            exc, message=f"summarizing '{section_name}' failed: {exc}"
        )
        return _unavailable_section(
            section_errors + [crash_error], "Could not summarize this section."
        )


def _unavailable_section(errors: list, headline: str) -> dict:
    """The SectionSummary shape for a section with no usable data —
    whether because its collector never ran, crashed, or its summarizer
    itself failed. `status` is always `"unknown"`: there is nothing to
    transparently threshold against.
    """
    return {
        "available": False,
        "status": "unknown",
        "headline": headline,
        "highlights": [],
        "concerns": [],
        "evidence": {},
        "errors": errors,
    }


# --- Formatting helpers (presentation-only, no thresholds/judgment) -------------


def _format_uptime(seconds: float) -> str:
    """Pure function: a duration in seconds in, a short human-readable
    string out (e.g. `"5d 3h"`). Purely mechanical formatting — no
    judgment about whether that uptime is "good" or "bad".
    """
    total_seconds = int(seconds)
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _format_bytes(num_bytes) -> str:
    """Pure function: a byte count in, a short human-readable size out
    (e.g. `"25.9 MB"`)."""
    if num_bytes is None:
        return "unknown size"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _join_names(names: list, limit: int = _MAX_NAMED_ITEMS) -> str:
    """Pure function: a list of names in, a comma-joined string out,
    capped at `limit` names with an "and N more" suffix if there are
    more than that — keeps a concern readable regardless of list size.
    """
    if len(names) <= limit:
        return ", ".join(names)
    shown = ", ".join(names[:limit])
    return f"{shown}, and {len(names) - limit} more"


# --- Section summarizers ---------------------------------------------------------
#
# Each is a pure function: (section_data, section_errors) -> SectionSummary.
# None calls another, none does I/O, none depends on any other section.


def _summarize_system(data: dict, errors: list) -> dict:
    """`system` has no meaningful health signal in its own data (a
    hostname, OS, kernel, architecture, and uptime aren't "healthy" or
    "unhealthy") — status is always `"healthy"` when available, with no
    thresholds to apply.
    """
    operating_system = data.get("operating_system")
    kernel_version = data.get("kernel_version")
    uptime_seconds = data.get("uptime_seconds")

    headline_parts = []
    if operating_system:
        headline_parts.append(operating_system)
    if kernel_version:
        headline_parts.append(f"kernel {kernel_version}")
    if uptime_seconds is not None:
        headline_parts.append(f"up {_format_uptime(uptime_seconds)}")
    headline = ", ".join(headline_parts) if headline_parts else "System information unavailable"

    highlights = []
    if data.get("hostname"):
        highlights.append(f"Hostname: {data['hostname']}")
    if data.get("architecture"):
        highlights.append(f"Architecture: {data['architecture']}")

    evidence = {
        "hostname": data.get("hostname"),
        "operating_system": operating_system,
        "kernel_version": kernel_version,
        "architecture": data.get("architecture"),
        "uptime_seconds": uptime_seconds,
    }

    return {
        "available": True,
        "status": "healthy",
        "headline": headline,
        "highlights": highlights,
        "concerns": [],
        "evidence": evidence,
        "errors": errors,
    }


def _summarize_cpu_memory(data: dict, errors: list) -> dict:
    """Status is driven by `cpu_usage_percent`, `memory_usage_percent`,
    and `swap_usage_percent`, each against its own fixed thresholds.
    `load_average_*` has no threshold applied — see
    docs/cpu_memory_collector.md's own note that a load average number
    alone is hard to threshold meaningfully without a core count, which
    this collector doesn't collect (deferred) — but all three windows are
    still surfaced in full in `evidence`, not just the 1-minute figure.
    """
    cpu_usage_percent = data.get("cpu_usage_percent")
    memory_usage_percent = data.get("memory_usage_percent")
    swap_usage_percent = data.get("swap_usage_percent")

    concerns = []
    has_critical = False
    has_warning = False
    evaluated_anything = False

    if cpu_usage_percent is not None:
        evaluated_anything = True
        if cpu_usage_percent >= _CPU_CRITICAL_PERCENT:
            has_critical = True
            concerns.append(
                f"CPU usage at {cpu_usage_percent:.1f}% "
                f"(critical threshold: {_CPU_CRITICAL_PERCENT:.0f}%)"
            )
        elif cpu_usage_percent >= _CPU_WARNING_PERCENT:
            has_warning = True
            concerns.append(
                f"CPU usage at {cpu_usage_percent:.1f}% "
                f"(warning threshold: {_CPU_WARNING_PERCENT:.0f}%)"
            )

    if memory_usage_percent is not None:
        evaluated_anything = True
        if memory_usage_percent >= _MEMORY_CRITICAL_PERCENT:
            has_critical = True
            concerns.append(
                f"Memory usage at {memory_usage_percent:.1f}% "
                f"(critical threshold: {_MEMORY_CRITICAL_PERCENT:.0f}%)"
            )
        elif memory_usage_percent >= _MEMORY_WARNING_PERCENT:
            has_warning = True
            concerns.append(
                f"Memory usage at {memory_usage_percent:.1f}% "
                f"(warning threshold: {_MEMORY_WARNING_PERCENT:.0f}%)"
            )

    if swap_usage_percent is not None:
        evaluated_anything = True
        if swap_usage_percent >= _SWAP_WARNING_PERCENT:
            has_warning = True
            concerns.append(
                f"Swap usage at {swap_usage_percent:.1f}% "
                f"(warning threshold: {_SWAP_WARNING_PERCENT:.0f}%)"
            )

    if has_critical:
        status = "critical"
    elif has_warning:
        status = "warning"
    elif evaluated_anything:
        status = "healthy"
    else:
        status = "unknown"

    if cpu_usage_percent is not None and memory_usage_percent is not None:
        headline = f"CPU {cpu_usage_percent:.1f}% used, memory {memory_usage_percent:.1f}% used"
    elif memory_usage_percent is not None:
        headline = f"Memory {memory_usage_percent:.1f}% used"
    elif cpu_usage_percent is not None:
        headline = f"CPU {cpu_usage_percent:.1f}% used"
    else:
        headline = "CPU/memory usage unavailable"

    highlights = []
    if data.get("load_average_1m") is not None:
        highlights.append(
            f"Load average: {data.get('load_average_1m')} (1m), "
            f"{data.get('load_average_5m')} (5m), {data.get('load_average_15m')} (15m)"
        )

    evidence = {
        "cpu_usage_percent": cpu_usage_percent,
        "memory_usage_percent": memory_usage_percent,
        "swap_usage_percent": swap_usage_percent,
        "load_average_1m": data.get("load_average_1m"),
        "load_average_5m": data.get("load_average_5m"),
        "load_average_15m": data.get("load_average_15m"),
    }

    return {
        "available": True,
        "status": status,
        "headline": headline,
        "highlights": highlights,
        "concerns": concerns,
        "evidence": evidence,
        "errors": errors,
    }


def _summarize_processes(data: dict, errors: list) -> dict:
    """Status is driven by `zombie_count` and `blocked_process_count`
    (state `D`) against fixed thresholds — the two fields
    docs/process_collector_design.md identifies as operationally
    significant on their own, unlike raw process counts.

    `evidence` includes up to `_MAX_TOP_PROCESSES_IN_EVIDENCE` processes
    each from `top_by_memory`/`top_by_cpu`, including `pid`, `owner`, and
    `state` — this system is consumed only by the project team, who
    already have direct access to this same data via the collectors, so
    there's no reason to withhold it here. `command` (the full
    `/proc/<pid>/cmdline`) is the one field passed through
    `redact_secrets()` first: unlike every other process field, a
    command line is free-form text a human could have put anything into,
    including a password or token passed as an argument.
    """
    process_count = data.get("process_count")
    zombie_count = data.get("zombie_count")
    blocked_process_count = data.get("blocked_process_count")
    top_by_memory = data.get("top_by_memory") or []
    top_by_cpu = data.get("top_by_cpu") or []

    concerns = []
    has_critical = False
    has_warning = False
    evaluated_anything = False

    if zombie_count is not None:
        evaluated_anything = True
        if zombie_count >= _ZOMBIE_CRITICAL_COUNT:
            has_critical = True
            concerns.append(
                f"{zombie_count} zombie processes (critical threshold: {_ZOMBIE_CRITICAL_COUNT})"
            )
        elif zombie_count >= _ZOMBIE_WARNING_COUNT:
            has_warning = True
            concerns.append(
                f"{zombie_count} zombie processes (warning threshold: {_ZOMBIE_WARNING_COUNT})"
            )

    if blocked_process_count is not None:
        evaluated_anything = True
        if blocked_process_count >= _BLOCKED_CRITICAL_COUNT:
            has_critical = True
            concerns.append(
                f"{blocked_process_count} processes blocked in uninterruptible sleep "
                f"(critical threshold: {_BLOCKED_CRITICAL_COUNT})"
            )
        elif blocked_process_count >= _BLOCKED_WARNING_COUNT:
            has_warning = True
            concerns.append(
                f"{blocked_process_count} process(es) blocked in uninterruptible sleep "
                f"(warning threshold: {_BLOCKED_WARNING_COUNT})"
            )

    if has_critical:
        status = "critical"
    elif has_warning:
        status = "warning"
    elif evaluated_anything:
        status = "healthy"
    else:
        status = "unknown"

    headline = (
        f"{process_count} processes running"
        if process_count is not None
        else "Process information unavailable"
    )

    highlights = []
    if top_by_memory:
        top = top_by_memory[0]
        highlights.append(
            f"Top memory consumer: {top.get('process_name')} "
            f"({_format_bytes(top.get('memory_rss_bytes'))})"
        )
    top_cpu_with_data = [p for p in top_by_cpu if p.get("cpu_usage_percent") is not None]
    if top_cpu_with_data:
        top_cpu = top_cpu_with_data[0]
        highlights.append(
            f"Top CPU consumer: {top_cpu.get('process_name')} "
            f"({top_cpu.get('cpu_usage_percent'):.1f}%)"
        )

    evidence = {
        "process_count": process_count,
        "zombie_count": zombie_count,
        "blocked_process_count": blocked_process_count,
        "top_processes_by_memory": [
            {
                "process_name": p.get("process_name"),
                "pid": p.get("pid"),
                "owner": p.get("owner"),
                "state": p.get("state"),
                "command": redact_secrets(p.get("command")),
                "memory": _format_bytes(p.get("memory_rss_bytes")),
            }
            for p in top_by_memory[:_MAX_TOP_PROCESSES_IN_EVIDENCE]
        ],
        "top_processes_by_cpu": [
            {
                "process_name": p.get("process_name"),
                "pid": p.get("pid"),
                "owner": p.get("owner"),
                "state": p.get("state"),
                "command": redact_secrets(p.get("command")),
                "cpu_usage_percent": p.get("cpu_usage_percent"),
            }
            for p in top_cpu_with_data[:_MAX_TOP_PROCESSES_IN_EVIDENCE]
        ],
    }

    return {
        "available": True,
        "status": status,
        "headline": headline,
        "highlights": highlights,
        "concerns": concerns,
        "evidence": evidence,
        "errors": errors,
    }


def _summarize_disk(data: dict, errors: list) -> dict:
    """Status is driven by `highest_disk_usage_percent` and
    `highest_inode_usage_percent` against fixed thresholds — a
    filesystem can be mostly empty by space yet exhausted by inode count
    (see docs/disk_collector.md), so both are checked independently.

    `evidence` includes every checked filesystem's own mount point, usage,
    and inode figures (not just the system-wide highest) — the number of
    mounted filesystems is small enough on any real system that there's
    no practical reason to cap this list.
    """
    highest_disk_usage_percent = data.get("highest_disk_usage_percent")
    highest_inode_usage_percent = data.get("highest_inode_usage_percent")
    filesystems = data.get("filesystems") or []

    concerns = []
    has_critical = False
    has_warning = False
    evaluated_anything = False

    if highest_disk_usage_percent is not None:
        evaluated_anything = True
        if highest_disk_usage_percent >= _DISK_CRITICAL_PERCENT:
            has_critical = True
            concerns.append(
                f"Highest filesystem usage at {highest_disk_usage_percent:.1f}% "
                f"(critical threshold: {_DISK_CRITICAL_PERCENT:.0f}%)"
            )
        elif highest_disk_usage_percent >= _DISK_WARNING_PERCENT:
            has_warning = True
            concerns.append(
                f"Highest filesystem usage at {highest_disk_usage_percent:.1f}% "
                f"(warning threshold: {_DISK_WARNING_PERCENT:.0f}%)"
            )

    if highest_inode_usage_percent is not None:
        evaluated_anything = True
        if highest_inode_usage_percent >= _DISK_CRITICAL_PERCENT:
            has_critical = True
            concerns.append(
                f"Highest inode usage at {highest_inode_usage_percent:.1f}% "
                f"(critical threshold: {_DISK_CRITICAL_PERCENT:.0f}%)"
            )
        elif highest_inode_usage_percent >= _DISK_WARNING_PERCENT:
            has_warning = True
            concerns.append(
                f"Highest inode usage at {highest_inode_usage_percent:.1f}% "
                f"(warning threshold: {_DISK_WARNING_PERCENT:.0f}%)"
            )

    if has_critical:
        status = "critical"
    elif has_warning:
        status = "warning"
    elif evaluated_anything:
        status = "healthy"
    else:
        status = "unknown"

    headline = (
        f"Fullest filesystem at {highest_disk_usage_percent:.1f}%"
        if highest_disk_usage_percent is not None
        else "Disk usage unavailable"
    )

    highlights = [f"{len(filesystems)} filesystem(s) checked"] if filesystems else []

    evidence = {
        "highest_disk_usage_percent": highest_disk_usage_percent,
        "highest_inode_usage_percent": highest_inode_usage_percent,
        "filesystem_count": len(filesystems),
        "filesystems": [
            {
                "mount_point": fs.get("mount_point"),
                "filesystem": fs.get("filesystem"),
                "usage_percent": fs.get("usage_percent"),
                "inode_usage_percent": fs.get("inode_usage_percent"),
                "total_bytes": fs.get("total_bytes"),
                "used_bytes": fs.get("used_bytes"),
                "available_bytes": fs.get("available_bytes"),
            }
            for fs in filesystems
        ],
    }

    return {
        "available": True,
        "status": status,
        "headline": headline,
        "highlights": highlights,
        "concerns": concerns,
        "evidence": evidence,
        "errors": errors,
    }


def _summarize_services(data: dict, errors: list) -> dict:
    """If systemd itself isn't available (DECISIONS.md ADR-010), status
    is `"unknown"` — there's nothing to threshold, since there's no
    service data at all, not because anything is wrong. Otherwise,
    status is driven by `failed_services_count` against fixed
    thresholds, with any currently-restarting services surfaced as at
    least a warning.

    Running service names are surfaced as a highlight (via
    `_join_names`'s existing "and N more" cap) so `ask` can name
    specific active services, without the highlight growing unbounded
    on a system with hundreds of services. `evidence` additionally
    carries the full, uncapped name+description for every running,
    failed, and restarting service — the highlight/concern strings stay
    capped for readability, but the structured evidence itself isn't
    trimmed, so a question about a specific service not in the first 5
    named can still be answered.
    """
    if not data.get("systemd_available"):
        return {
            "available": True,
            "status": "unknown",
            "headline": "systemd is not available on this system",
            "highlights": [],
            "concerns": [],
            "evidence": {"systemd_available": False},
            "errors": errors,
        }

    running_services_count = data.get("running_services_count")
    failed_services_count = data.get("failed_services_count")
    running_services = data.get("running_services") or []
    failed_services = data.get("failed_services") or []
    restarting_services = data.get("restarting_services") or []

    concerns = []
    if failed_services_count is None:
        status = "unknown"
    elif failed_services_count >= _FAILED_SERVICES_CRITICAL_COUNT:
        status = "critical"
        concerns.append(
            f"{failed_services_count} services failed: "
            f"{_join_names([s['name'] for s in failed_services])} "
            f"(critical threshold: {_FAILED_SERVICES_CRITICAL_COUNT})"
        )
    elif failed_services_count >= _FAILED_SERVICES_WARNING_COUNT:
        status = "warning"
        concerns.append(
            f"{failed_services_count} service(s) failed: "
            f"{_join_names([s['name'] for s in failed_services])} "
            f"(warning threshold: {_FAILED_SERVICES_WARNING_COUNT})"
        )
    else:
        status = "healthy"

    if restarting_services:
        concerns.append(
            f"{len(restarting_services)} service(s) currently restarting: "
            f"{_join_names([s['name'] for s in restarting_services])}"
        )
        if status == "healthy":
            status = "warning"

    headline = (
        f"{running_services_count} running, {failed_services_count} failed"
        if running_services_count is not None and failed_services_count is not None
        else "Service information unavailable"
    )

    highlights = []
    if running_services:
        highlights.append(f"Running: {_join_names([s['name'] for s in running_services])}")

    evidence = {
        "running_services_count": running_services_count,
        "failed_services_count": failed_services_count,
        "enabled_services_count": data.get("enabled_services_count"),
        "running_services": [
            {"name": s.get("name"), "description": s.get("description")}
            for s in running_services
        ],
        "failed_services": [
            {"name": s.get("name"), "description": s.get("description")}
            for s in failed_services
        ],
        "restarting_services": [
            {"name": s.get("name"), "description": s.get("description")}
            for s in restarting_services
        ],
    }

    return {
        "available": True,
        "status": status,
        "headline": headline,
        "highlights": highlights,
        "concerns": concerns,
        "evidence": evidence,
        "errors": errors,
    }


def _summarize_scheduled_jobs(data: dict, errors: list) -> dict:
    """Neither cron jobs nor systemd timers existing (or not) is
    inherently healthy or unhealthy — no thresholds apply. Status is
    `"healthy"` whenever counts were determined at all, `"unknown"`
    otherwise.

    `evidence` includes every cron job's schedule and command, and every
    systemd timer's name/unit. A cron job's `command` is passed through
    `redact_secrets()` first — like a process's command line, it's
    free-form text a human wrote, and cron entries commonly embed
    passwords or tokens directly as arguments.
    """
    cron_job_count = data.get("cron_job_count")
    timer_count = data.get("timer_count")
    cron_jobs = data.get("cron_jobs") or []
    systemd_timers = data.get("systemd_timers") or []

    if cron_job_count is None and timer_count is None:
        status = "unknown"
        headline = "Scheduled job information unavailable"
    else:
        status = "healthy"
        headline = f"{cron_job_count} cron job(s), {timer_count} systemd timer(s)"

    evidence = {
        "cron_job_count": cron_job_count,
        "timer_count": timer_count,
        "cron_jobs": [
            {
                "user": job.get("user"),
                "schedule": job.get("schedule"),
                "command": redact_secrets(job.get("command")),
            }
            for job in cron_jobs
        ],
        "systemd_timers": [
            {"name": timer.get("name"), "unit": timer.get("unit")}
            for timer in systemd_timers
        ],
    }

    return {
        "available": True,
        "status": status,
        "headline": headline,
        "highlights": [],
        "concerns": [],
        "evidence": evidence,
        "errors": errors,
    }


def _summarize_permissions(data: dict, errors: list) -> dict:
    """Status is driven entirely by whether any checked path is
    world-writable — the one "obviously suspicious" condition
    docs/permissions_collector.md flags, deliberately not a broader
    security audit. A path that couldn't be checked at all (`exists is
    None`) counts as a warning, since that's a real gap in what could be
    verified, distinct from a path that simply doesn't exist.

    `evidence` includes every checked path's own owner/group/mode, not
    just the aggregate counts — ownership and permission bits on this
    fixed, conservative path list aren't secrets, so there's no reason to
    keep them out of structured evidence.
    """
    checked_paths = data.get("checked_paths") or []

    world_writable_paths = [p["path"] for p in checked_paths if p.get("world_writable")]
    unresolvable_paths = [p["path"] for p in checked_paths if p.get("exists") is None]

    concerns = []
    if world_writable_paths:
        concerns.append(f"World-writable: {_join_names(world_writable_paths)}")
    if unresolvable_paths:
        concerns.append(f"Could not check: {_join_names(unresolvable_paths)}")

    if world_writable_paths:
        status = "critical"
    elif unresolvable_paths:
        status = "warning"
    elif checked_paths:
        status = "healthy"
    else:
        status = "unknown"

    headline = f"{len(checked_paths)} path(s) checked" if checked_paths else "No paths checked"

    evidence = {
        "checked_path_count": len(checked_paths),
        "world_writable_count": len(world_writable_paths),
        "checked_paths": [
            {
                "path": p.get("path"),
                "exists": p.get("exists"),
                "owner": p.get("owner"),
                "group": p.get("group"),
                "mode": p.get("mode"),
                "world_writable": p.get("world_writable"),
            }
            for p in checked_paths
        ],
    }

    return {
        "available": True,
        "status": status,
        "headline": headline,
        "highlights": [],
        "concerns": concerns,
        "evidence": evidence,
        "errors": errors,
    }


def _summarize_network(data: dict, errors: list) -> dict:
    """Status is driven only by whether at least one interface is up —
    firewall detection is deliberately excluded from status, since
    docs/network_collector.md establishes that no firewall tool being
    detectable is the normal, expected outcome for an unprivileged scan,
    not evidence of a problem. That exclusion applies to `status` only:
    the firewall detection result is still surfaced in `evidence` (even
    when no tool could be detected), so `ask` has something honest to
    answer with rather than nothing at all.

    `evidence` also includes every interface's name/state/addresses and
    every listening port — none of this is a secret on an internal tool
    used by the team that already runs these collectors directly.
    """
    interfaces = data.get("interfaces") or []
    listening_ports = data.get("listening_ports") or []
    default_route = data.get("default_route")
    firewall = data.get("firewall") or {}

    up_interfaces = [iface for iface in interfaces if iface.get("state") == "up"]

    concerns = []
    if interfaces and not up_interfaces:
        status = "warning"
        concerns.append("No network interfaces are up")
    elif interfaces:
        status = "healthy"
    else:
        status = "unknown"

    headline = (
        f"{len(up_interfaces)}/{len(interfaces)} interface(s) up, "
        f"{len(listening_ports)} listening port(s)"
        if interfaces
        else "Network information unavailable"
    )

    highlights = []
    if default_route:
        highlights.append(
            f"Default route via {default_route.get('gateway')} ({default_route.get('interface')})"
        )

    evidence = {
        "interface_count": len(interfaces),
        "up_interface_count": len(up_interfaces),
        "listening_port_count": len(listening_ports),
        "interfaces": [
            {
                "name": iface.get("name"),
                "state": iface.get("state"),
                "ipv4_addresses": iface.get("ipv4_addresses"),
                "ipv6_addresses": iface.get("ipv6_addresses"),
            }
            for iface in interfaces
        ],
        "listening_ports": [
            {
                "protocol": port.get("protocol"),
                "local_address": port.get("local_address"),
                "port": port.get("port"),
                "process_name": port.get("process_name"),
                "pid": port.get("pid"),
            }
            for port in listening_ports
        ],
        "firewall_tool": firewall.get("tool"),
        "firewall_enabled": firewall.get("enabled"),
        "firewall_detection_note": firewall.get("detection_note"),
    }

    return {
        "available": True,
        "status": status,
        "headline": headline,
        "highlights": highlights,
        "concerns": concerns,
        "evidence": evidence,
        "errors": errors,
    }


def _summarize_logs(data: dict, errors: list) -> dict:
    """Status is driven by `error_count` against fixed thresholds.
    `"unavailable"` (journalctl absent — see docs/logs_collector.md)
    yields `"unknown"`, not `"healthy"` — an absent log source is a real
    gap, not evidence of a clean log.

    `evidence` includes the actual `recent_entries` content (timestamp,
    severity, unit, message) — every message was already passed through
    `redact_secrets()` at collection time (`collectors/logs.py`), so this
    copy is safe to forward as-is; only the counts were being surfaced
    before, which meant a question like "what do the logs say" could
    never be answered even though the data was already collected.
    """
    source = data.get("source")
    error_count = data.get("error_count")
    warning_count = data.get("warning_count")

    if source == "unavailable" or error_count is None:
        return {
            "available": True,
            "status": "unknown",
            "headline": "Log information unavailable",
            "highlights": [],
            "concerns": [],
            "evidence": {"source": source},
            "errors": errors,
        }

    concerns = []
    if error_count >= _LOG_ERROR_CRITICAL_COUNT:
        status = "critical"
        concerns.append(
            f"{error_count} error-level log entries "
            f"(critical threshold: {_LOG_ERROR_CRITICAL_COUNT})"
        )
    elif error_count >= _LOG_ERROR_WARNING_COUNT:
        status = "warning"
        concerns.append(
            f"{error_count} error-level log entries "
            f"(warning threshold: {_LOG_ERROR_WARNING_COUNT})"
        )
    else:
        status = "healthy"

    headline = f"{warning_count} warning(s), {error_count} error(s) in recent logs"

    evidence = {
        "warning_count": warning_count,
        "error_count": error_count,
        "truncated": data.get("truncated"),
        "recent_entries": [
            {
                "timestamp": entry.get("timestamp"),
                "severity": entry.get("severity"),
                "unit": entry.get("unit"),
                "message": entry.get("message"),
            }
            for entry in data.get("recent_entries") or []
        ],
    }

    return {
        "available": True,
        "status": status,
        "headline": headline,
        "highlights": [],
        "concerns": concerns,
        "evidence": evidence,
        "errors": errors,
    }


def _summarize_users(data: dict, errors: list) -> dict:
    """No threshold applies — user accounts and login activity aren't
    inherently healthy or unhealthy on their own, the same reasoning as
    `_summarize_system`. Status is `"healthy"` whenever any of the three
    data sources was gathered at all, `"unknown"` otherwise.

    `evidence` includes the full account list, full currently-logged-in
    session list, and full recent-login-history list, uncapped — none of
    this is sensitive on an internal tool used by the team that already
    has direct access to `/etc/passwd`/`who`/`last`, and the underlying
    lists are naturally small (a handful of real accounts, a bounded
    login-history window).
    """
    users = data.get("users") or []
    logged_in_sessions = data.get("logged_in_sessions") or []
    recent_logins = data.get("recent_logins") or []

    human_users = [u for u in users if not u.get("is_system_account")]

    if not users and not logged_in_sessions and not recent_logins:
        status = "unknown"
        headline = "User information unavailable"
    else:
        status = "healthy"
        headline = (
            f"{len(users)} user account(s) ({len(human_users)} human), "
            f"{len(logged_in_sessions)} currently logged in"
        )

    highlights = []
    if logged_in_sessions:
        highlights.append(
            f"Currently logged in: {_join_names([s['name'] for s in logged_in_sessions])}"
        )

    evidence = {
        "user_count": len(users),
        "human_user_count": len(human_users),
        "users": [
            {
                "name": u.get("name"),
                "uid": u.get("uid"),
                "home": u.get("home"),
                "shell": u.get("shell"),
                "is_system_account": u.get("is_system_account"),
            }
            for u in users
        ],
        "logged_in_sessions": [
            {
                "name": s.get("name"),
                "terminal": s.get("terminal"),
                "login_time": s.get("login_time"),
                "host": s.get("host"),
            }
            for s in logged_in_sessions
        ],
        "recent_logins": [
            {
                "name": r.get("name"),
                "terminal": r.get("terminal"),
                "detail": r.get("detail"),
            }
            for r in recent_logins
        ],
    }

    return {
        "available": True,
        "status": status,
        "headline": headline,
        "highlights": highlights,
        "concerns": [],
        "evidence": evidence,
        "errors": errors,
    }


_REGISTERED_SUMMARIZERS = [
    ("system", _summarize_system),
    ("cpu_memory", _summarize_cpu_memory),
    ("processes", _summarize_processes),
    ("disk", _summarize_disk),
    ("services", _summarize_services),
    ("scheduled_jobs", _summarize_scheduled_jobs),
    ("permissions", _summarize_permissions),
    ("network", _summarize_network),
    ("logs", _summarize_logs),
    ("users", _summarize_users),
]
"""Every section summarizer the engine runs, in order. A plain list of
(name, function) pairs — no registry object, no plugin system — mirroring
`nodeiq.core.coordinator._REGISTERED_COLLECTORS` exactly. Adding a
summarizer for a future 10th collector means adding one function and one
entry here."""
