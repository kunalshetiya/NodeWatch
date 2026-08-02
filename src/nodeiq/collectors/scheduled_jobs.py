"""Scheduled jobs collector: cron jobs and systemd timers.

Answers "what cron jobs exist?" — one of NodeIQ's headline example
questions. Gathers system cron jobs (`/etc/crontab`, `/etc/cron.d/*`),
user cron jobs where accessible (`/var/spool/cron/crontabs/*`), and
systemd timers (`systemctl list-timers`). Both cron and systemd timers
are legitimate ways to schedule work on Linux, so both are covered by
this one collector, matching docs/snapshot_schema.md Section 10.

See docs/scheduled_jobs_collector.md for the full rationale, including
why systemd timers' next/last-run timestamps are intentionally not
parsed in v1.
"""

import time
from pathlib import Path

from nodeiq.core.collector import CollectorContext, CollectorResult
from nodeiq.core.errors import error_entry
from nodeiq.core.runner import command_failure_message, run_command

_ETC_CRONTAB_PATH = Path("/etc/crontab")
_CRON_D_DIR = Path("/etc/cron.d")
_USER_CRONTABS_DIR = Path("/var/spool/cron/crontabs")
_LIST_TIMERS_COMMAND = ["systemctl", "list-timers", "--all", "--no-legend", "--no-pager", "--plain"]


def collect(context: CollectorContext) -> CollectorResult:
    """Gather every cron job and systemd timer this scan can find.

    Cron sources (system crontab, `/etc/cron.d/`, and per-user crontabs)
    are read directly as files — a missing file or an inaccessible
    directory is a normal, expected outcome (many systems don't use
    system cron at all, and reading another user's personal crontab
    requires privileges this scan may not have), not a collector error,
    per this task's "handle missing cron or timers gracefully"
    instruction. `systemctl list-timers` is a real command that can
    fail (e.g. no systemd), which *is* recorded as a structured error.
    """
    start_time = time.monotonic()
    errors: list[dict] = []

    cron_jobs = _get_system_cron_jobs() + _get_user_cron_jobs()

    try:
        timers = _get_systemd_timers(context)
    except ValueError as exc:
        timers = []
        errors.append(error_entry(exc))

    data = {
        "cron_job_count": len(cron_jobs),
        "cron_jobs": cron_jobs,
        "timer_count": len(timers),
        "systemd_timers": timers,
    }

    return CollectorResult(
        collector_name="scheduled_jobs",
        data=data,
        errors=errors,
        duration_ms=(time.monotonic() - start_time) * 1000,
    )


def _get_system_cron_jobs() -> list[dict]:
    """Read `/etc/crontab` and every file in `/etc/cron.d/`, parsing each
    into cron job entries.

    Neither existing is a requirement — a missing `/etc/crontab` or
    `/etc/cron.d` simply means this system doesn't use system cron,
    which is a normal outcome, not an error.
    """
    jobs: list[dict] = []

    if _ETC_CRONTAB_PATH.is_file():
        jobs.extend(_read_system_crontab_file(_ETC_CRONTAB_PATH))

    if _CRON_D_DIR.is_dir():
        for path in sorted(_CRON_D_DIR.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                jobs.extend(_read_system_crontab_file(path))

    return jobs


def _read_system_crontab_file(path: Path) -> list[dict]:
    """Read one system-crontab-format file and parse every schedulable
    line in it. An unreadable file (rare — these are normally
    world-readable) is skipped rather than treated as an error, per this
    task's "handle gracefully" instruction.
    """
    try:
        raw_text = path.read_text()
    except OSError:
        return []

    jobs = []
    for line in raw_text.splitlines():
        entry = _parse_system_crontab_line(line, str(path))
        if entry is not None:
            jobs.append(entry)
    return jobs


def _parse_system_crontab_line(line: str, source_file: str) -> dict | None:
    """Pure function: one line of a system-crontab-format file (which
    includes an explicit user field) in, a cron job dict out — or `None`
    if the line is blank, a comment, or an environment variable
    assignment rather than a schedulable job.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    tokens = stripped.split()
    if tokens[0].startswith("@"):
        if len(tokens) < 3:
            return None
        schedule = tokens[0]
        user = tokens[1]
        command = " ".join(tokens[2:])
    else:
        if len(tokens) < 7:
            return None
        schedule = " ".join(tokens[0:5])
        user = tokens[5]
        command = " ".join(tokens[6:])

    return {
        "user": user,
        "schedule": schedule,
        "command": command,
        "source_file": source_file,
    }


def _get_user_cron_jobs() -> list[dict]:
    """Read every accessible personal crontab from
    `/var/spool/cron/crontabs/`, where each file is named after its
    owning user.

    This directory is only readable by root (or the `crontab` group) by
    design — when this scan can't list or read it, that's a normal,
    expected access-control outcome ("user cron jobs where accessible"),
    not a collector error, so this returns an empty list rather than
    raising.
    """
    try:
        entries = list(_USER_CRONTABS_DIR.iterdir())
    except OSError:
        return []

    jobs = []
    for path in sorted(entries):
        if not path.is_file():
            continue
        try:
            raw_text = path.read_text()
        except OSError:
            continue
        for line in raw_text.splitlines():
            entry = _parse_user_crontab_line(line, path.name, str(path))
            if entry is not None:
                jobs.append(entry)
    return jobs


def _parse_user_crontab_line(line: str, user: str, source_file: str) -> dict | None:
    """Pure function: one line of a personal crontab (no user field —
    the file's owner *is* the user) in, a cron job dict out — or `None`
    if the line is blank, a comment, or an environment variable
    assignment rather than a schedulable job.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    tokens = stripped.split()
    if tokens[0].startswith("@"):
        if len(tokens) < 2:
            return None
        schedule = tokens[0]
        command = " ".join(tokens[1:])
    else:
        if len(tokens) < 6:
            return None
        schedule = " ".join(tokens[0:5])
        command = " ".join(tokens[5:])

    return {
        "user": user,
        "schedule": schedule,
        "command": command,
        "source_file": source_file,
    }


def _get_systemd_timers(context: CollectorContext) -> list[dict]:
    """Run `systemctl list-timers` and parse it into a list of timer
    dicts.

    Raises `ValueError` if the command fails or its output doesn't parse.
    """
    result = run_command(_LIST_TIMERS_COMMAND, timeout=context.default_timeout)
    if not result.succeeded:
        raise ValueError(command_failure_message(_LIST_TIMERS_COMMAND, result))
    return _parse_list_timers(result.stdout)


def _parse_list_timers(raw_text: str) -> list[dict]:
    """Pure function: `systemctl list-timers`'s text in, a list of
    `{"name": ..., "unit": ...}` dicts out.

    The NEXT/LAST columns are human-formatted, locale-dependent date and
    relative-time strings ("3h 48min", "2h ago") that would be fragile
    to parse positionally — this collector deliberately does not parse
    them (see docs/scheduled_jobs_collector.md). Every timer's own name
    and the service it activates are always the last two
    whitespace-separated tokens on each line (unit names never contain
    spaces), regardless of how the date columns are formatted — so this
    is a simple, robust way to extract exactly the two facts this
    collector needs.
    """
    timers = []
    for line in raw_text.splitlines():
        if not line.strip():
            continue
        tokens = line.split()
        if len(tokens) < 2:
            raise ValueError(f"could not parse list-timers line: {line!r}")
        timers.append({"name": tokens[-2], "unit": tokens[-1]})
    return timers
