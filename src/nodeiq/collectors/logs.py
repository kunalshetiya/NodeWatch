"""Logs collector: recent warning/error entries from the systemd journal.

Answers almost any "why did X happen?" question with concrete, recent
evidence. Follows the same `CollectorContext` -> `collect()` ->
`CollectorResult` pattern as every other collector, using
`nodeiq.core.runner.run_command` to run `journalctl` with its own
`--output=json` mode — a machine-readable interface, not journalctl's
human-oriented default text format (see docs/logs_collector.md).

Deliberately bounded: this collector never sends "the logs," only the
most recent `_MAX_ENTRIES` warning-or-worse entries, per this task's
explicit "avoid sending huge logs into the snapshot" instruction.
"""

import json
import time
from datetime import datetime, timezone

from nodeiq.core.collector import CollectorContext, CollectorResult
from nodeiq.core.errors import error_entry
from nodeiq.core.redaction import redact_secrets
from nodeiq.core.runner import command_failure_message, run_command

_MAX_ENTRIES = 100
"""How many recent warning-or-worse journal entries to fetch at most.
A constant (not a magic number inline) so this limit can be adjusted
later without hunting through the collector for where it's used."""

_JOURNALCTL_COMMAND = [
    "journalctl",
    "-p",
    "warning",
    "-n",
    str(_MAX_ENTRIES),
    "--no-pager",
    "-o",
    "json",
]

_ERROR_PRIORITIES = {"0", "1", "2", "3"}
_WARNING_PRIORITY = "4"


def collect(context: CollectorContext) -> CollectorResult:
    """Gather up to `_MAX_ENTRIES` recent warning-or-worse journal
    entries, plus their warning/error counts.

    If `journalctl` is unavailable at all (no systemd journal, or the
    command doesn't exist), this degrades gracefully: `source` becomes
    `"unavailable"`, every count is `None`, and one error entry is
    recorded — never a crash.
    """
    start_time = time.monotonic()
    errors: list[dict] = []

    try:
        entries = _get_recent_log_entries(context)
    except ValueError as exc:
        errors.append(error_entry(exc))
        return CollectorResult(
            collector_name="logs",
            data={
                "source": "unavailable",
                "truncated": None,
                "warning_count": None,
                "error_count": None,
                "recent_entries": [],
            },
            errors=errors,
            duration_ms=(time.monotonic() - start_time) * 1000,
        )

    data = _summarize_entries(entries)
    data["source"] = "journalctl"

    return CollectorResult(
        collector_name="logs",
        data=data,
        errors=errors,
        duration_ms=(time.monotonic() - start_time) * 1000,
    )


def _get_recent_log_entries(context: CollectorContext) -> list[dict]:
    """Run `journalctl -p warning -n <_MAX_ENTRIES> -o json` and parse
    it into a list of log entry dicts.

    Raises `ValueError` if the command fails — including the case where
    `journalctl` doesn't exist at all (no systemd journal on this
    system), which is this collector's primary graceful-degradation path.
    """
    result = run_command(_JOURNALCTL_COMMAND, timeout=context.default_timeout)
    if not result.succeeded:
        raise ValueError(command_failure_message(_JOURNALCTL_COMMAND, result))
    return _parse_journal_json(result.stdout)


def _parse_journal_json(raw_text: str) -> list[dict]:
    """Pure function: `journalctl -o json`'s text in (one JSON object
    per line), a list of `{"timestamp", "severity", "unit", "message"}`
    dicts out.

    A single malformed line is skipped rather than failing the whole
    batch — one odd journal entry shouldn't discard every other one.
    """
    entries = []
    for line in raw_text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        entries.append(_parse_journal_record(record))
    return entries


def _parse_journal_record(record: dict) -> dict:
    """Pure function: one parsed `journalctl -o json` record in, this
    collector's entry shape out.

    Handles two real edge cases: `MESSAGE` can be a list of raw byte
    values instead of a string when the original message wasn't valid
    UTF-8, and the unit name can come from either `_SYSTEMD_UNIT` (for
    systemd-managed services) or `SYSLOG_IDENTIFIER` (for everything
    else, e.g. the kernel).
    """
    return {
        "timestamp": _parse_timestamp(record.get("__REALTIME_TIMESTAMP")),
        "severity": _parse_severity(record.get("PRIORITY")),
        "unit": record.get("_SYSTEMD_UNIT") or record.get("SYSLOG_IDENTIFIER") or "unknown",
        "message": _parse_message(record.get("MESSAGE")),
    }


def _parse_timestamp(raw_value) -> str | None:
    """Pure function: journalctl's `__REALTIME_TIMESTAMP` (a string of
    microseconds since the Unix epoch) in, an ISO 8601 UTC timestamp
    out — or `None` if the field is missing or malformed.
    """
    if raw_value is None:
        return None
    try:
        seconds = int(raw_value) / 1_000_000
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def _parse_severity(priority: str | None) -> str:
    """Pure function: a journald `PRIORITY` string ("0"-"7") in,
    `"error"` (0-3: emerg/alert/crit/err) or `"warning"` (4) out.
    Anything else (5-7, or missing) is reported as `"warning"` — this
    collector only ever requests priority 4 or higher urgency via
    `-p warning`, so this is a defensive fallback, not an expected path.
    """
    if priority in _ERROR_PRIORITIES:
        return "error"
    return "warning"


def _parse_message(raw_message) -> str:
    """Pure function: a journald `MESSAGE` field in, a plain,
    secret-redacted string out.

    `MESSAGE` is usually already a string, but journald represents
    non-UTF-8 messages as a JSON array of raw byte values instead — this
    decodes that case gracefully rather than crashing or dropping the
    entry. Every message is passed through `redact_secrets()` before
    being returned — this collector's copy of the text is the only
    thing sanitized; the real journal on disk is never touched
    (`CONTEXT.md` Section 4, `nodeiq.core.redaction`'s own docstring).
    """
    if isinstance(raw_message, list):
        text = bytes(raw_message).decode("utf-8", errors="replace")
    elif raw_message is None:
        text = ""
    else:
        text = str(raw_message)
    return redact_secrets(text)


def _summarize_entries(entries: list[dict]) -> dict:
    """Pure function: the full list of parsed log entries in, this
    collector's returned summary (counts, truncation flag, and the
    entries themselves) out.
    """
    warning_count = sum(1 for entry in entries if entry["severity"] == "warning")
    error_count = sum(1 for entry in entries if entry["severity"] == "error")

    return {
        "truncated": len(entries) >= _MAX_ENTRIES,
        "warning_count": warning_count,
        "error_count": error_count,
        "recent_entries": entries,
    }
