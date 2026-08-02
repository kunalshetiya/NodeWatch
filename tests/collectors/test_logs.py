"""Unit tests for nodeiq.collectors.logs.

All command execution is mocked — no test here depends on the real
machine's actual journal contents, per PROJECT_RULES.md Section 11 and
docs/collector_guidelines.md's Testing Expectations. See
tests/collectors/test_logs_integration.py for a test against the real
`journalctl` on a real Linux system.
"""

import json
from datetime import datetime, timezone

from nodeiq.collectors import logs
from nodeiq.core.collector import CollectorContext
from nodeiq.core.result import CommandResult

# --- _parse_severity -------------------------------------------------------------


def test_parse_severity_maps_low_priorities_to_error():
    assert logs._parse_severity("0") == "error"
    assert logs._parse_severity("3") == "error"


def test_parse_severity_maps_priority_four_to_warning():
    assert logs._parse_severity("4") == "warning"


def test_parse_severity_defaults_to_warning_for_unexpected_values():
    assert logs._parse_severity(None) == "warning"
    assert logs._parse_severity("7") == "warning"


# --- _parse_timestamp -------------------------------------------------------------


def test_parse_timestamp_converts_microseconds_to_iso8601():
    # 1704067200000000 microseconds = 2024-01-01T00:00:00+00:00
    result = logs._parse_timestamp("1704067200000000")
    assert result.startswith("2024-01-01T00:00:00")


def test_parse_timestamp_returns_none_for_missing_or_malformed_values():
    assert logs._parse_timestamp(None) is None
    assert logs._parse_timestamp("not-a-number") is None


# --- _parse_message ----------------------------------------------------------------


def test_parse_message_returns_a_plain_string_as_is():
    assert logs._parse_message("hello world") == "hello world"


def test_parse_message_decodes_a_byte_array():
    raw = list(b"hello")
    assert logs._parse_message(raw) == "hello"


def test_parse_message_returns_empty_string_when_missing():
    assert logs._parse_message(None) == ""


def test_parse_message_redacts_a_secret():
    result = logs._parse_message("OPENAI_API_KEY=sk-proj-abcdef123")
    assert result == "OPENAI_API_KEY=[REDACTED]"
    assert "sk-proj-abcdef123" not in result


def test_parse_message_redacts_a_secret_in_a_decoded_byte_array():
    raw = list(b"password=hunter2")
    result = logs._parse_message(raw)
    assert result == "password=[REDACTED]"


# --- _parse_journal_record ---------------------------------------------------------


def test_parse_journal_record_prefers_systemd_unit_over_syslog_identifier():
    record = {
        "__REALTIME_TIMESTAMP": "1704067200000000",
        "PRIORITY": "3",
        "_SYSTEMD_UNIT": "nginx.service",
        "SYSLOG_IDENTIFIER": "nginx",
        "MESSAGE": "worker process exited",
    }

    entry = logs._parse_journal_record(record)

    assert entry["unit"] == "nginx.service"
    assert entry["severity"] == "error"
    assert entry["message"] == "worker process exited"


def test_parse_journal_record_falls_back_to_syslog_identifier():
    record = {
        "__REALTIME_TIMESTAMP": "1704067200000000",
        "PRIORITY": "4",
        "SYSLOG_IDENTIFIER": "kernel",
        "MESSAGE": "some kernel message",
    }

    entry = logs._parse_journal_record(record)

    assert entry["unit"] == "kernel"


def test_parse_journal_record_falls_back_to_unknown_when_no_unit_field_present():
    record = {"__REALTIME_TIMESTAMP": "1704067200000000", "PRIORITY": "4", "MESSAGE": "x"}

    entry = logs._parse_journal_record(record)

    assert entry["unit"] == "unknown"


# --- _parse_journal_json -------------------------------------------------------------


def test_parse_journal_json_parses_one_object_per_line():
    lines = "\n".join(
        json.dumps(
            {
                "__REALTIME_TIMESTAMP": "1704067200000000",
                "PRIORITY": str(priority),
                "SYSLOG_IDENTIFIER": "kernel",
                "MESSAGE": f"message {priority}",
            }
        )
        for priority in (3, 4)
    )

    entries = logs._parse_journal_json(lines)

    assert len(entries) == 2
    assert entries[0]["severity"] == "error"
    assert entries[1]["severity"] == "warning"


def test_parse_journal_json_skips_a_malformed_line():
    good_line = json.dumps(
        {"__REALTIME_TIMESTAMP": "1704067200000000", "PRIORITY": "4", "MESSAGE": "ok"}
    )
    raw_text = f"{good_line}\nnot valid json\n"

    entries = logs._parse_journal_json(raw_text)

    assert len(entries) == 1


def test_parse_journal_json_skips_blank_lines():
    assert logs._parse_journal_json("\n\n") == []


# --- _summarize_entries -----------------------------------------------------------


def _entry(severity: str) -> dict:
    return {"timestamp": "2024-01-01T00:00:00+00:00", "severity": severity, "unit": "x", "message": "m"}


def test_summarize_entries_counts_warnings_and_errors():
    entries = [_entry("warning"), _entry("warning"), _entry("error")]

    result = logs._summarize_entries(entries)

    assert result["warning_count"] == 2
    assert result["error_count"] == 1
    assert result["recent_entries"] == entries


def test_summarize_entries_sets_truncated_when_at_the_limit(monkeypatch):
    monkeypatch.setattr(logs, "_MAX_ENTRIES", 2)
    entries = [_entry("warning"), _entry("warning")]

    result = logs._summarize_entries(entries)

    assert result["truncated"] is True


def test_summarize_entries_sets_truncated_false_when_under_the_limit(monkeypatch):
    monkeypatch.setattr(logs, "_MAX_ENTRIES", 100)
    entries = [_entry("warning")]

    result = logs._summarize_entries(entries)

    assert result["truncated"] is False


# --- collect() end-to-end -------------------------------------------------------


def _context() -> CollectorContext:
    return CollectorContext(scan_start_time=datetime.now(timezone.utc))


def _succeeding(command, stdout):
    return CommandResult(
        command=command,
        returncode=0,
        stdout=stdout,
        stderr="",
        duration_seconds=0.01,
        timed_out=False,
        error=None,
    )


def _failing(command):
    return CommandResult(
        command=command,
        returncode=None,
        stdout="",
        stderr="",
        duration_seconds=0.01,
        timed_out=False,
        error="journalctl: command not found",
    )


def test_collect_summarizes_recent_journal_entries(monkeypatch):
    sample = "\n".join(
        json.dumps(
            {
                "__REALTIME_TIMESTAMP": "1704067200000000",
                "PRIORITY": str(priority),
                "SYSLOG_IDENTIFIER": "kernel",
                "MESSAGE": "m",
            }
        )
        for priority in (3, 4, 4)
    )
    monkeypatch.setattr(logs, "run_command", lambda command, timeout: _succeeding(command, sample))

    result = logs.collect(_context())

    assert result.collector_name == "logs"
    assert result.errors == []
    assert result.data["source"] == "journalctl"
    assert result.data["warning_count"] == 2
    assert result.data["error_count"] == 1
    assert len(result.data["recent_entries"]) == 3


def test_collect_degrades_gracefully_when_journalctl_is_unavailable(monkeypatch):
    monkeypatch.setattr(logs, "run_command", lambda command, timeout: _failing(command))

    result = logs.collect(_context())

    assert result.data == {
        "source": "unavailable",
        "truncated": None,
        "warning_count": None,
        "error_count": None,
        "recent_entries": [],
    }
    assert len(result.errors) == 1
    assert result.success is False
