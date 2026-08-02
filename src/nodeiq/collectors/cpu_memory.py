"""CPU + memory collector: CPU usage, memory usage, and load average.

Answers "how loaded is this machine right now?" — one of NodeIQ's
headline example questions ("what is consuming memory?", "is this system
under load?", "what is CPU usage?"). Follows the same `CollectorContext`
-> `collect()` -> `CollectorResult` pattern established by `system.py`
(docs/system_collector.md); every field comes from reading `/proc`
files directly, no subprocess calls.

CPU usage is the one field here that isn't a single snapshot read:
`/proc/stat`'s cumulative jiffie counters only measure time *since
boot*, not a rate, so computing a percentage requires two samples a
short interval apart (`_CPU_SAMPLE_INTERVAL_SECONDS`) — the same
technique `top`/`mpstat` use. This adds a small, fixed, deliberate delay
to this one collector's own runtime; see docs/cpu_memory_collector.md.
"""

import time
from pathlib import Path

from nodeiq.core.collector import CollectorContext, CollectorResult
from nodeiq.core.errors import error_entry

_MEMINFO_PATH = Path("/proc/meminfo")
_LOADAVG_PATH = Path("/proc/loadavg")
_STAT_PATH = Path("/proc/stat")

_REQUIRED_MEMINFO_KEYS = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}

_CPU_SAMPLE_INTERVAL_SECONDS = 0.2
"""How long to wait between the two `/proc/stat` samples used to compute
CPU usage. Short enough not to meaningfully slow a scan down, long
enough for the jiffie deltas to be a meaningful, stable measurement."""


def collect(context: CollectorContext) -> CollectorResult:
    """Gather CPU usage, memory usage, and load average.

    All three are independent data sources (`/proc/stat`,
    `/proc/meminfo`, `/proc/loadavg`) — if one can't be read or parsed,
    its fields are recorded as `None` and the failure is added to
    `errors`, but the others are still collected. See
    PROJECT_RULES.md Section 7 and docs/collector_guidelines.md for why
    partial data always beats no data.
    """
    start_time = time.monotonic()
    data: dict = {}
    errors: list[dict] = []

    try:
        data["cpu_usage_percent"] = _get_cpu_usage_percent()
    except ValueError as exc:
        data["cpu_usage_percent"] = None
        errors.append(error_entry(exc))

    try:
        data.update(_get_memory_fields())
    except ValueError as exc:
        data["memory_used_bytes"] = None
        data["memory_available_bytes"] = None
        data["memory_usage_percent"] = None
        data["swap_used_bytes"] = None
        data["swap_usage_percent"] = None
        errors.append(error_entry(exc))

    try:
        data.update(_get_load_average_fields())
    except ValueError as exc:
        data["load_average_1m"] = None
        data["load_average_5m"] = None
        data["load_average_15m"] = None
        errors.append(error_entry(exc))

    return CollectorResult(
        collector_name="cpu_memory",
        data=data,
        errors=errors,
        duration_ms=(time.monotonic() - start_time) * 1000,
    )


def _get_cpu_usage_percent() -> float:
    """Sample `/proc/stat`'s aggregate CPU line twice,
    `_CPU_SAMPLE_INTERVAL_SECONDS` apart, and return the percentage of
    time spent not idle between the two samples.

    A single `/proc/stat` read only has cumulative jiffies since boot —
    there's no rate in one sample alone, so two samples and a delta are
    required. Raises `ValueError` if `/proc/stat` can't be read or
    parsed either time.
    """
    first = _read_cpu_jiffies()
    time.sleep(_CPU_SAMPLE_INTERVAL_SECONDS)
    second = _read_cpu_jiffies()
    return _compute_cpu_usage_percent(first, second)


def _read_cpu_jiffies() -> tuple:
    raw_text = _read_proc_file(_STAT_PATH)
    return _parse_stat_cpu_line(raw_text)


def _parse_stat_cpu_line(raw_text: str) -> tuple:
    """Pure function: `/proc/stat`'s text in, `(idle_jiffies,
    total_jiffies)` out — from its first `"cpu "` aggregate line (the
    combined total across all cores; per-core `"cpu0"`, `"cpu1"`, ...
    lines are not used). No file I/O — just string parsing, so it can
    be tested with a literal sample string. Raises `ValueError` if no
    such line exists or it doesn't parse as expected.

    `idle_jiffies` combines the `idle` and `iowait` fields — both
    represent time the CPU spent doing no work, whether truly idle or
    blocked waiting on I/O — matching how `top`/`mpstat` compute usage.
    """
    for line in raw_text.splitlines():
        if not line.startswith("cpu "):
            continue
        try:
            values = [int(field) for field in line.split()[1:]]
        except ValueError as exc:
            raise ValueError(f"could not parse /proc/stat cpu line: {line!r}") from exc
        if len(values) < 4:
            raise ValueError(f"/proc/stat cpu line has too few fields: {line!r}")
        idle_jiffies = values[3] + (values[4] if len(values) > 4 else 0)
        return idle_jiffies, sum(values)
    raise ValueError("no aggregate 'cpu' line found in /proc/stat")


def _compute_cpu_usage_percent(first: tuple, second: tuple) -> float:
    """Pure function: two `(idle_jiffies, total_jiffies)` samples in,
    the percentage of time spent not idle between them out. Returns
    `0.0` (rather than raising) if the total didn't advance at all
    between samples — an edge case, not a real 0% usage claim, but the
    safest default when there's no meaningful delta to compute from.
    """
    idle_first, total_first = first
    idle_second, total_second = second
    delta_idle = idle_second - idle_first
    delta_total = total_second - total_first
    if delta_total <= 0:
        return 0.0
    return round((1 - delta_idle / delta_total) * 100, 2)


def _read_proc_file(path: Path) -> str:
    """Read a `/proc` file's full text content.

    Shared by `_get_memory_fields` and `_get_load_average_fields`, since
    both read a `/proc` file the same way. Raises `ValueError` (not
    `OSError`) if the file can't be read, matching every other
    anticipated-failure path in this collector.
    """
    try:
        return path.read_text()
    except OSError as exc:
        raise ValueError(f"could not read {path}: {exc}") from exc


def _get_memory_fields() -> dict:
    """Read `/proc/meminfo` and compute memory/swap usage fields.

    Raises `ValueError` if the file can't be read, or if it's missing one
    of the four values this collector needs.
    """
    raw_text = _read_proc_file(_MEMINFO_PATH)
    raw_kb = _parse_meminfo(raw_text)
    return _compute_memory_fields(raw_kb)


def _parse_meminfo(raw_text: str) -> dict:
    """Pure function: `/proc/meminfo`'s text in, a dict of the four raw
    kB values this collector needs (`MemTotal`, `MemAvailable`,
    `SwapTotal`, `SwapFree`) out. No file I/O — just string parsing, so it
    can be tested with a literal sample string.
    """
    values = {}
    for line in raw_text.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        if key not in _REQUIRED_MEMINFO_KEYS:
            continue
        tokens = rest.strip().split()
        if not tokens:
            raise ValueError(f"{key} has no value in /proc/meminfo")
        try:
            values[key] = int(tokens[0])
        except ValueError as exc:
            raise ValueError(
                f"could not parse {key} value in /proc/meminfo: {tokens[0]!r}"
            ) from exc

    missing = _REQUIRED_MEMINFO_KEYS - values.keys()
    if missing:
        raise ValueError(f"missing required /proc/meminfo fields: {sorted(missing)}")
    return values


def _compute_memory_fields(raw_kb: dict) -> dict:
    """Pure function: raw kB values from `/proc/meminfo` in, the five
    computed memory/swap fields (bytes and percentages) out.
    """
    mem_used_kb = raw_kb["MemTotal"] - raw_kb["MemAvailable"]
    swap_used_kb = raw_kb["SwapTotal"] - raw_kb["SwapFree"]

    return {
        "memory_used_bytes": mem_used_kb * 1024,
        "memory_available_bytes": raw_kb["MemAvailable"] * 1024,
        "memory_usage_percent": _percent(mem_used_kb, raw_kb["MemTotal"]),
        "swap_used_bytes": swap_used_kb * 1024,
        "swap_usage_percent": _percent(swap_used_kb, raw_kb["SwapTotal"]),
    }


def _percent(part: int, whole: int) -> float:
    """Pure function: what percentage `part` is of `whole`, rounded to two
    decimal places. Returns `0.0` if `whole` is zero or negative (e.g. a
    machine with no swap configured at all has `SwapTotal == 0`) rather
    than raising a division-by-zero error.
    """
    if whole <= 0:
        return 0.0
    return round((part / whole) * 100, 2)


def _get_load_average_fields() -> dict:
    """Read `/proc/loadavg` and return the three load average fields.

    Raises `ValueError` if the file can't be read or doesn't have the
    expected fields.
    """
    raw_text = _read_proc_file(_LOADAVG_PATH)
    return _parse_loadavg(raw_text)


def _parse_loadavg(raw_text: str) -> dict:
    """Pure function: `/proc/loadavg`'s text in, the three load average
    fields out. No file I/O — just string parsing, so it can be tested
    with a literal sample string.
    """
    tokens = raw_text.split()
    if len(tokens) < 3:
        raise ValueError(f"/proc/loadavg did not have the expected fields: {raw_text!r}")
    try:
        return {
            "load_average_1m": float(tokens[0]),
            "load_average_5m": float(tokens[1]),
            "load_average_15m": float(tokens[2]),
        }
    except ValueError as exc:
        raise ValueError(f"could not parse /proc/loadavg values: {raw_text!r}") from exc
