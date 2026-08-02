"""Unit tests for nodeiq.collectors.services.

All command execution is mocked — no test here depends on the real
machine's actual services, per PROJECT_RULES.md Section 11 and
docs/collector_guidelines.md's Testing Expectations. See
tests/collectors/test_services_integration.py for a test against the
real `systemctl` on a real Linux system.
"""

from datetime import datetime, timezone

from nodeiq.collectors import services
from nodeiq.core.collector import CollectorContext
from nodeiq.core.result import CommandResult

# --- _parse_service_units ------------------------------------------------------

_SAMPLE_LIST_UNITS = (
    "apparmor.service loaded active exited Load AppArmor profiles\n"
    "cron.service loaded failed failed Regular background program processing daemon\n"
    "ssh.service loaded active running OpenBSD Secure Shell server\n"
)


def test_parse_service_units_extracts_every_service():
    result = services._parse_service_units(_SAMPLE_LIST_UNITS)

    assert len(result) == 3
    assert result[0] == {
        "name": "apparmor.service",
        "load_state": "loaded",
        "active_state": "active",
        "sub_state": "exited",
        "description": "Load AppArmor profiles",
    }


def test_parse_service_units_skips_blank_lines():
    sample = "ssh.service loaded active running OpenBSD Secure Shell server\n\n"
    assert len(services._parse_service_units(sample)) == 1


def test_parse_service_units_raises_on_a_malformed_line():
    sample = "onlyonetoken\n"
    import pytest

    with pytest.raises(ValueError):
        services._parse_service_units(sample)


# --- _summarize_services --------------------------------------------------------


def test_summarize_services_counts_running_and_failed():
    units = services._parse_service_units(_SAMPLE_LIST_UNITS)

    result = services._summarize_services(units)

    assert result["running_services_count"] == 2
    assert result["failed_services_count"] == 1
    assert result["failed_services"][0]["name"] == "cron.service"


def test_summarize_services_lists_running_service_names():
    units = services._parse_service_units(_SAMPLE_LIST_UNITS)

    result = services._summarize_services(units)

    assert len(result["running_services"]) == result["running_services_count"]
    names = {unit["name"] for unit in result["running_services"]}
    assert "cron.service" not in names  # cron.service is failed, not running


def test_summarize_services_detects_restarting_services():
    units = [
        {
            "name": "flaky.service",
            "load_state": "loaded",
            "active_state": "failed",
            "sub_state": "auto-restart",
            "description": "A flaky service",
        }
    ]

    result = services._summarize_services(units)

    assert len(result["restarting_services"]) == 1
    assert result["restarting_services"][0]["name"] == "flaky.service"


def test_summarize_services_handles_no_services():
    result = services._summarize_services([])

    assert result == {
        "running_services_count": 0,
        "failed_services_count": 0,
        "running_services": [],
        "failed_services": [],
        "restarting_services": [],
    }


# --- _parse_unit_files -----------------------------------------------------------

_SAMPLE_LIST_UNIT_FILES = (
    "apparmor.service enabled enabled\n"
    "apport.service static -\n"
    "ssh.service enabled enabled\n"
)


def test_parse_unit_files_maps_name_to_state():
    result = services._parse_unit_files(_SAMPLE_LIST_UNIT_FILES)

    assert result == {
        "apparmor.service": "enabled",
        "apport.service": "static",
        "ssh.service": "enabled",
    }


def test_parse_unit_files_raises_on_a_malformed_line():
    import pytest

    with pytest.raises(ValueError):
        services._parse_unit_files("onlyonetoken\n")


# --- collect() end-to-end -------------------------------------------------------


def _context() -> CollectorContext:
    return CollectorContext(scan_start_time=datetime.now(timezone.utc))


def _succeeding(command: list[str], stdout: str) -> CommandResult:
    return CommandResult(
        command=command,
        returncode=0,
        stdout=stdout,
        stderr="",
        duration_seconds=0.01,
        timed_out=False,
        error=None,
    )


def _failing(command: list[str], error: str | None = None) -> CommandResult:
    return CommandResult(
        command=command,
        returncode=1 if error is None else None,
        stdout="",
        stderr="",
        duration_seconds=0.01,
        timed_out=False,
        error=error,
    )


def test_collect_merges_both_commands_into_one_result(monkeypatch):
    def fake_run_command(command, timeout):
        if command == services._LIST_UNITS_COMMAND:
            return _succeeding(command, _SAMPLE_LIST_UNITS)
        if command == services._LIST_UNIT_FILES_COMMAND:
            return _succeeding(command, _SAMPLE_LIST_UNIT_FILES)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(services, "run_command", fake_run_command)

    result = services.collect(_context())

    assert result.collector_name == "services"
    assert result.errors == []
    assert result.data["systemd_available"] is True
    assert result.data["running_services_count"] == 2
    assert result.data["failed_services_count"] == 1
    assert result.data["enabled_services_count"] == 2


def test_collect_reports_systemd_unavailable_when_systemctl_is_missing(monkeypatch):
    def fake_run_command(command, timeout):
        return _failing(command, error="systemctl not found")

    monkeypatch.setattr(services, "run_command", fake_run_command)

    result = services.collect(_context())

    assert result.data == {
        "systemd_available": False,
        "running_services_count": None,
        "failed_services_count": None,
        "enabled_services_count": None,
        "running_services": [],
        "failed_services": [],
        "restarting_services": [],
    }
    assert len(result.errors) == 1
    assert result.success is False


def test_collect_returns_partial_data_when_only_unit_files_command_fails(monkeypatch):
    def fake_run_command(command, timeout):
        if command == services._LIST_UNITS_COMMAND:
            return _succeeding(command, _SAMPLE_LIST_UNITS)
        if command == services._LIST_UNIT_FILES_COMMAND:
            return _failing(command)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(services, "run_command", fake_run_command)

    result = services.collect(_context())

    assert result.data["systemd_available"] is True
    assert result.data["running_services_count"] == 2
    assert result.data["enabled_services_count"] is None
    assert len(result.errors) == 1
    assert result.success is False
