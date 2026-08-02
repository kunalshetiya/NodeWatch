"""System metadata collector.

Answers the most basic operational question NodeIQ can ask: "what machine
is this, and what is it running?" This is deliberately the first real
collector built (see docs/system_collector.md) — its whole point is to
validate the CollectorContext -> collect() -> CollectorResult pattern with
real Linux commands and real files, before any other collector is built
on top of the same pattern.

v1 scope is intentionally narrow: hostname, operating_system,
kernel_version, architecture, uptime_seconds. See docs/system_collector.md
for which fields were deliberately deferred and why.
"""

import time
from pathlib import Path

from nodeiq.core.collector import CollectorContext, CollectorResult
from nodeiq.core.errors import error_entry
from nodeiq.core.runner import command_failure_message, run_command

_OS_RELEASE_PATH = Path("/etc/os-release")
_UPTIME_PATH = Path("/proc/uptime")


def collect(context: CollectorContext) -> CollectorResult:
    """Gather this machine's basic identity: hostname, OS, kernel,
    architecture, and uptime.

    Each of the five fields is gathered independently. If one fails (a
    command isn't found, a file can't be read, output doesn't parse), that
    field is recorded as `None` and the failure is added to `errors` —
    every other field is still collected. See PROJECT_RULES.md Section 7
    and docs/collector_guidelines.md for why partial data always beats no
    data.
    """
    start_time = time.monotonic()
    data: dict = {}
    errors: list[dict] = []

    try:
        data["hostname"] = _get_hostname(context)
    except ValueError as exc:
        data["hostname"] = None
        errors.append(error_entry(exc))

    try:
        data["operating_system"] = _get_os_release()
    except ValueError as exc:
        data["operating_system"] = None
        errors.append(error_entry(exc))

    try:
        data["kernel_version"] = _get_kernel_version(context)
    except ValueError as exc:
        data["kernel_version"] = None
        errors.append(error_entry(exc))

    try:
        data["architecture"] = _get_architecture(context)
    except ValueError as exc:
        data["architecture"] = None
        errors.append(error_entry(exc))

    try:
        data["uptime_seconds"] = _get_uptime()
    except ValueError as exc:
        data["uptime_seconds"] = None
        errors.append(error_entry(exc))

    return CollectorResult(
        collector_name="system",
        data=data,
        errors=errors,
        duration_ms=(time.monotonic() - start_time) * 1000,
    )


def _run_and_capture(command: list[str], context: CollectorContext) -> str:
    """Run a command expected to print exactly one line, and return that
    line with surrounding whitespace stripped.

    Shared by `_get_hostname`, `_get_kernel_version`, and
    `_get_architecture` below, since all three are the same shape: run a
    trivial command, expect one line of output. Raises `ValueError` if
    the command fails or produces no output.
    """
    result = run_command(command, timeout=context.default_timeout)
    if not result.succeeded:
        raise ValueError(command_failure_message(command, result))
    output = result.stdout.strip()
    if not output:
        raise ValueError(f"{' '.join(command)} produced no output")
    return output


def _get_hostname(context: CollectorContext) -> str:
    """Get this machine's hostname by running `hostname`.

    The hostname is the single most basic fact identifying which machine
    a snapshot came from — every future report and every answer to a
    question needs to say what machine it's talking about.
    """
    return _run_and_capture(["hostname"], context)


def _get_kernel_version(context: CollectorContext) -> str:
    """Get the running Linux kernel's version by running `uname -r`.

    The kernel version matters operationally because it determines which
    kernel features, drivers, and security patches are actually present on
    this machine, independent of which distribution or userland version is
    installed.
    """
    return _run_and_capture(["uname", "-r"], context)


def _get_architecture(context: CollectorContext) -> str:
    """Get the machine's hardware architecture by running `uname -m`.

    Knowing the architecture (e.g. `x86_64` vs `aarch64`) matters because
    it determines which compiled binaries and packages can even run on
    this machine — a common source of confusing "wrong architecture"
    failures when software was built for a different one.
    """
    return _run_and_capture(["uname", "-m"], context)


def _get_os_release() -> str:
    """Get a human-readable OS description by reading `/etc/os-release`.

    `/etc/os-release` is the standard machine-readable file describing
    which Linux distribution is installed — reading it directly avoids
    parsing any command's free-form text output for the same information
    (see docs/system_collector.md for why this is preferred). Raises
    `ValueError` if the file is missing, unreadable, or doesn't contain a
    `PRETTY_NAME` line.
    """
    try:
        raw_text = _OS_RELEASE_PATH.read_text()
    except OSError as exc:
        raise ValueError(f"could not read {_OS_RELEASE_PATH}: {exc}") from exc
    return _parse_os_release(raw_text)


def _parse_os_release(raw_text: str) -> str:
    """Pure function: `/etc/os-release`'s text in, its `PRETTY_NAME` value
    out. No file I/O — just string parsing, so it can be tested with a
    literal sample string.
    """
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key == "PRETTY_NAME":
            return value.strip().strip('"').strip("'")
    raise ValueError("PRETTY_NAME not found in /etc/os-release")


def _get_uptime() -> float:
    """Get how long this machine has been running, in seconds, by reading
    `/proc/uptime`.

    `/proc/uptime` is the kernel's own machine-readable uptime counter —
    reading it directly avoids parsing `uptime`'s free-form,
    human-oriented summary line for the same number (see
    docs/system_collector.md). Raises `ValueError` if the file is missing,
    unreadable, or not in the expected format.
    """
    try:
        raw_text = _UPTIME_PATH.read_text()
    except OSError as exc:
        raise ValueError(f"could not read {_UPTIME_PATH}: {exc}") from exc
    return _parse_uptime(raw_text)


def _parse_uptime(raw_text: str) -> float:
    """Pure function: `/proc/uptime`'s text in, the uptime in seconds out.
    No file I/O — just string parsing, so it can be tested with a literal
    sample string.
    """
    tokens = raw_text.split()
    if not tokens:
        raise ValueError("/proc/uptime was empty")
    try:
        return float(tokens[0])
    except ValueError as exc:
        raise ValueError(f"could not parse uptime value {tokens[0]!r}") from exc
