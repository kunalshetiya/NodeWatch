"""Process collector: a summarized view of currently running processes.

Answers "what is consuming memory?", "which process is using the most
CPU?", and "is anything stuck?" at the process level. Follows the same
`CollectorContext` -> `collect()` -> `CollectorResult` pattern
established by `system.py` and `cpu_memory.py`, reading only
`/proc/<pid>/status`, `/proc/<pid>/cmdline`, `/proc/<pid>/comm`, and
`/proc/<pid>/stat` directly — no commands (`ps`) are ever run. See
docs/process_collector_design.md for the full design rationale and
docs/process_collector.md for this implementation's specifics.

v1 scope is intentionally narrow and summarized, not full per-process
detail: `process_count`, `zombie_count`, `blocked_process_count` (state
`D`), and the top 10 processes by memory and by CPU usage
(`top_by_memory`, `top_by_cpu`).

Per-process CPU usage, like the system-wide figure in `cpu_memory.py`,
requires two samples a short interval apart (`/proc/<pid>/stat`'s
`utime`/`stime` are cumulative since the process started, not a rate) —
see "Computing Per-Process CPU Usage" below.
"""

import os
import time
from pathlib import Path

from nodeiq.core.collector import CollectorContext, CollectorResult
from nodeiq.core.errors import error_entry
from nodeiq.core.identity import resolve_username

_PROC_ROOT = Path("/proc")
_TOP_N = 10
_ZOMBIE_STATE = "Z"
_BLOCKED_STATE = "D"

_CPU_SAMPLE_INTERVAL_SECONDS = 0.2
"""Matches `cpu_memory.py`'s own `_CPU_SAMPLE_INTERVAL_SECONDS` — the
same two-sample technique, applied per process instead of system-wide."""

try:
    _CLOCK_TICKS_PER_SECOND = os.sysconf("SC_CLK_TCK")
except (AttributeError, ValueError, OSError):
    # Non-Linux systems (e.g. macOS, during local development) may not
    # expose this — 100 is the near-universal Linux default, and this
    # constant is only ever exercised for real on Linux in the first place.
    _CLOCK_TICKS_PER_SECOND = 100


def collect(context: CollectorContext) -> CollectorResult:
    """Gather a summarized view of every currently running process.

    Discovers every PID under `/proc`, samples each one's CPU time
    twice (`_CPU_SAMPLE_INTERVAL_SECONDS` apart) around reading each
    one's `status`, `comm`, and `cmdline` once, and returns only a
    summary — `process_count`, `zombie_count`, `blocked_process_count`,
    and the top 10 processes by memory and by CPU usage — never every
    process's raw data (see docs/process_collector_design.md,
    "Recommended Process Summarization Strategy"). A process that exits
    at any point during this is skipped silently: that's an expected
    race, not a collector failure (see docs/process_collector.md,
    "Race Conditions When Scanning Processes").
    """
    start_time = time.monotonic()
    errors: list[dict] = []

    try:
        pids = _discover_pids()
    except ValueError as exc:
        errors.append(error_entry(exc))
        return CollectorResult(
            collector_name="processes",
            data={
                "process_count": None,
                "zombie_count": None,
                "blocked_process_count": None,
                "top_by_memory": None,
                "top_by_cpu": None,
            },
            errors=errors,
            duration_ms=(time.monotonic() - start_time) * 1000,
        )

    first_cpu_times = _read_process_cpu_times(pids)
    sample_start = time.monotonic()
    time.sleep(_CPU_SAMPLE_INTERVAL_SECONDS)
    elapsed_seconds = time.monotonic() - sample_start

    processes: list[dict] = []
    for pid in pids:
        entry = _read_process(pid)
        if entry is not None:
            processes.append(entry)

    second_cpu_times = _read_process_cpu_times(pids)
    _attach_cpu_usage_percent(processes, first_cpu_times, second_cpu_times, elapsed_seconds)

    return CollectorResult(
        collector_name="processes",
        data=_summarize(processes),
        errors=errors,
        duration_ms=(time.monotonic() - start_time) * 1000,
    )


def _discover_pids() -> list[int]:
    """List every currently-running PID by reading `/proc`'s numbered
    directory entries.

    Raises `ValueError` if `/proc` itself can't be listed at all (e.g. on
    a system with no `/proc`, such as macOS) — this is the one failure
    mode that means nothing about processes can be collected at all, so
    it's the only case this collector treats as a real error rather than
    a per-process skip.
    """
    try:
        return sorted(
            int(entry.name) for entry in _PROC_ROOT.iterdir() if entry.name.isdigit()
        )
    except OSError as exc:
        raise ValueError(f"could not list {_PROC_ROOT}: {exc}") from exc


def _read_process(pid: int) -> dict | None:
    """Gather one process's state, memory, owner, name, and command line.

    Returns `None` if the process no longer exists by the time we get
    around to reading it — a process can legitimately exit between being
    listed by `_discover_pids` and having its files read here. This is an
    expected race (see docs/process_collector.md), not an error: the
    caller simply skips this PID and keeps scanning the rest.
    """
    pid_dir = _PROC_ROOT / str(pid)
    try:
        status_text = (pid_dir / "status").read_text()
        comm_text = (pid_dir / "comm").read_text()
        cmdline_text = (pid_dir / "cmdline").read_text()
    except OSError:
        return None

    try:
        status_fields = _parse_status(status_text)
    except ValueError:
        return None

    return {
        "pid": pid,
        "process_name": comm_text.strip(),
        "memory_rss_bytes": status_fields["memory_rss_bytes"],
        "owner": resolve_username(status_fields["uid"]),
        "state": status_fields["state"],
        "command": _parse_cmdline(cmdline_text),
    }


def _read_process_cpu_times(pids: list) -> dict:
    """Read `/proc/<pid>/stat`'s CPU time for every PID in `pids`.

    Returns a `{pid: total_ticks}` dict — a PID that can't be read (it
    exited, or a permission error) is simply absent from the result,
    the same "skip, don't fail" handling as the rest of this collector.
    """
    cpu_times = {}
    for pid in pids:
        try:
            raw_text = (_PROC_ROOT / str(pid) / "stat").read_text()
            cpu_times[pid] = _parse_stat_cpu_time(raw_text)
        except (OSError, ValueError):
            continue
    return cpu_times


def _parse_stat_cpu_time(raw_text: str) -> int:
    """Pure function: `/proc/<pid>/stat`'s text in, that process's total
    CPU time so far (`utime + stime`, in clock ticks) out.

    `/proc/<pid>/stat` is a single space-separated line, but its second
    field (`comm`, the process name) is parenthesized and can itself
    contain spaces or parentheses — so fields are located by searching
    for the *last* `)` in the line and splitting only what comes after
    it, rather than a naive `split()` on the whole line. Everything
    after that point is fixed-position: `state` is first, `utime` is
    the 12th field, `stime` is the 13th (see `man proc`).
    """
    closing_paren_index = raw_text.rfind(")")
    if closing_paren_index == -1:
        raise ValueError(f"could not find comm field in /proc/<pid>/stat: {raw_text!r}")
    fields = raw_text[closing_paren_index + 1 :].split()
    if len(fields) < 13:
        raise ValueError(f"/proc/<pid>/stat has too few fields: {raw_text!r}")
    try:
        utime = int(fields[11])
        stime = int(fields[12])
    except ValueError as exc:
        raise ValueError(
            f"could not parse utime/stime in /proc/<pid>/stat: {raw_text!r}"
        ) from exc
    return utime + stime


def _attach_cpu_usage_percent(
    processes: list, first_cpu_times: dict, second_cpu_times: dict, elapsed_seconds: float
) -> None:
    """Mutate each process dict in `processes` in place, adding
    `cpu_usage_percent` computed from its two CPU-time samples.

    `None` (not `0.0`) when a process is missing from either sample —
    e.g. it started or exited between samples — since that's a real gap
    in what could be measured, not a genuine "using no CPU" reading.
    """
    for entry in processes:
        pid = entry["pid"]
        first = first_cpu_times.get(pid)
        second = second_cpu_times.get(pid)
        if first is None or second is None:
            entry["cpu_usage_percent"] = None
            continue
        entry["cpu_usage_percent"] = _compute_process_cpu_percent(
            max(second - first, 0), elapsed_seconds
        )


def _compute_process_cpu_percent(delta_ticks: int, elapsed_seconds: float) -> float:
    """Pure function: a jiffie delta and the real time it was measured
    over in, that process's CPU usage as a percentage of one core out
    (the same convention `top` uses — a process fully using two cores
    reports 200%, not 100%). Returns `0.0` if `elapsed_seconds` is zero
    or negative rather than dividing by zero.
    """
    if elapsed_seconds <= 0:
        return 0.0
    return round((delta_ticks / _CLOCK_TICKS_PER_SECOND) / elapsed_seconds * 100, 2)


def _parse_status(raw_text: str) -> dict:
    """Pure function: `/proc/<pid>/status`'s text in, this collector's
    three status-derived fields (`state`, `memory_rss_bytes`, `uid`) out.

    `state` and `uid` are required for every process and raise
    `ValueError` if missing. `memory_rss_bytes` defaults to `0` rather
    than raising when absent, since kernel threads (e.g. `kthreadd`)
    legitimately have no `VmRSS` line at all — they have no memory
    address space to report.
    """
    state = None
    memory_rss_bytes = None
    uid = None

    for line in raw_text.splitlines():
        if line.startswith("State:"):
            state = _parse_state(line)
        elif line.startswith("VmRSS:"):
            memory_rss_bytes = _parse_vmrss(line)
        elif line.startswith("Uid:"):
            uid = _parse_uid(line)

    if state is None:
        raise ValueError("State not found in /proc/<pid>/status")
    if uid is None:
        raise ValueError("Uid not found in /proc/<pid>/status")

    return {
        "state": state,
        "memory_rss_bytes": memory_rss_bytes if memory_rss_bytes is not None else 0,
        "uid": uid,
    }


def _parse_state(line: str) -> str:
    """Pure function: a status line like `State:\tS (sleeping)` in, the
    single-letter state code (`S`) out.
    """
    _, _, rest = line.partition(":")
    rest = rest.strip()
    if not rest:
        raise ValueError(f"could not parse process state from: {line!r}")
    return rest.split()[0]


def _parse_vmrss(line: str) -> int:
    """Pure function: a status line like `VmRSS:\t    3120 kB` in, RSS
    memory in bytes out.
    """
    _, _, rest = line.partition(":")
    tokens = rest.split()
    if not tokens:
        raise ValueError(f"could not parse VmRSS from: {line!r}")
    try:
        return int(tokens[0]) * 1024
    except ValueError as exc:
        raise ValueError(f"could not parse VmRSS value {tokens[0]!r}") from exc


def _parse_uid(line: str) -> int:
    """Pure function: a status line like `Uid:\t1000\t1000\t1000\t1000` in,
    the real UID (the first of the four values) out.
    """
    _, _, rest = line.partition(":")
    tokens = rest.split()
    if not tokens:
        raise ValueError(f"could not parse Uid from: {line!r}")
    try:
        return int(tokens[0])
    except ValueError as exc:
        raise ValueError(f"could not parse Uid value {tokens[0]!r}") from exc


def _parse_cmdline(raw_text: str) -> str:
    """Pure function: `/proc/<pid>/cmdline`'s NUL-separated text in, a
    space-joined command string out. Empty for kernel threads, which
    legitimately have no command line at all — not treated as an error.
    """
    return " ".join(part for part in raw_text.split("\0") if part)


def _summarize(processes: list[dict]) -> dict:
    """Turn the full per-process list into the summarized snapshot shape:
    total counts plus the top N by memory and by CPU usage.

    Per docs/process_collector_design.md's "Recommended Process
    Summarization Strategy," NodeIQ never sends every process's data
    onward — only this summary, which is what this collector actually
    returns.
    """
    zombie_count = sum(1 for process in processes if process["state"] == _ZOMBIE_STATE)
    blocked_process_count = sum(
        1 for process in processes if process["state"] == _BLOCKED_STATE
    )
    top_by_memory = sorted(
        processes, key=lambda process: process["memory_rss_bytes"], reverse=True
    )[:_TOP_N]
    top_by_cpu = sorted(
        processes, key=lambda process: process.get("cpu_usage_percent") or -1, reverse=True
    )[:_TOP_N]

    return {
        "process_count": len(processes),
        "zombie_count": zombie_count,
        "blocked_process_count": blocked_process_count,
        "top_by_memory": top_by_memory,
        "top_by_cpu": top_by_cpu,
    }
