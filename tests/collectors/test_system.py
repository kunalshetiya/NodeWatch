"""Unit tests for nodeiq.collectors.system.

Everything here is mocked (fake CommandResults, fake files) so these tests
never depend on the real state of the machine running them — per
PROJECT_RULES.md Section 11 and docs/collector_guidelines.md's Testing
Expectations. See tests/collectors/test_system_integration.py for tests
that run against a real Linux system.
"""

from datetime import datetime, timezone

import pytest

from nodeiq.collectors import system
from nodeiq.core.collector import CollectorContext
from nodeiq.core.result import CommandResult

SAMPLE_OS_RELEASE = """\
NAME="Ubuntu"
VERSION="24.04.4 LTS (Noble Numbat)"
ID=ubuntu
ID_LIKE=debian
PRETTY_NAME="Ubuntu 24.04.4 LTS"
VERSION_ID="24.04"
"""

SAMPLE_UPTIME = "36564.34 36066.00\n"


def _context() -> CollectorContext:
    return CollectorContext(scan_start_time=datetime.now(timezone.utc))


def _fake_command_result(command: list[str], stdout: str) -> CommandResult:
    return CommandResult(
        command=command,
        returncode=0,
        stdout=stdout,
        stderr="",
        duration_seconds=0.01,
    )


def _fake_failed_command_result(command: list[str]) -> CommandResult:
    return CommandResult(
        command=command,
        returncode=None,
        stdout="",
        stderr="",
        duration_seconds=0.01,
        error="command not found: [Errno 2] No such file or directory",
    )


# --- Pure parsing functions -------------------------------------------------


def test_parse_os_release_extracts_pretty_name():
    assert system._parse_os_release(SAMPLE_OS_RELEASE) == "Ubuntu 24.04.4 LTS"


def test_parse_os_release_strips_surrounding_quotes():
    raw = 'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\n'
    assert system._parse_os_release(raw) == "Debian GNU/Linux 12 (bookworm)"


def test_parse_os_release_raises_when_pretty_name_missing():
    raw = 'NAME="Ubuntu"\nVERSION_ID="24.04"\n'
    with pytest.raises(ValueError):
        system._parse_os_release(raw)


def test_parse_uptime_extracts_seconds_as_float():
    assert system._parse_uptime(SAMPLE_UPTIME) == pytest.approx(36564.34)


def test_parse_uptime_raises_on_empty_input():
    with pytest.raises(ValueError):
        system._parse_uptime("")


def test_parse_uptime_raises_on_non_numeric_input():
    with pytest.raises(ValueError):
        system._parse_uptime("not-a-number 123\n")


# --- Command-based getters ---------------------------------------------------


def test_get_hostname_returns_stripped_output(monkeypatch):
    monkeypatch.setattr(
        system,
        "run_command",
        lambda command, timeout=None: _fake_command_result(command, "myhost\n"),
    )

    assert system._get_hostname(_context()) == "myhost"


def test_get_hostname_raises_when_command_fails(monkeypatch):
    monkeypatch.setattr(
        system,
        "run_command",
        lambda command, timeout=None: _fake_failed_command_result(command),
    )

    with pytest.raises(ValueError):
        system._get_hostname(_context())


def test_get_kernel_version_returns_stripped_output(monkeypatch):
    monkeypatch.setattr(
        system,
        "run_command",
        lambda command, timeout=None: _fake_command_result(
            command, "6.8.0-134-generic\n"
        ),
    )

    assert system._get_kernel_version(_context()) == "6.8.0-134-generic"


def test_get_architecture_returns_stripped_output(monkeypatch):
    monkeypatch.setattr(
        system,
        "run_command",
        lambda command, timeout=None: _fake_command_result(command, "aarch64\n"),
    )

    assert system._get_architecture(_context()) == "aarch64"


def test_run_and_capture_raises_when_output_is_empty(monkeypatch):
    monkeypatch.setattr(
        system,
        "run_command",
        lambda command, timeout=None: _fake_command_result(command, "   \n"),
    )

    with pytest.raises(ValueError):
        system._run_and_capture(["hostname"], _context())


# --- File-based getters -------------------------------------------------------


def test_get_os_release_reads_the_configured_path(tmp_path, monkeypatch):
    fake_path = tmp_path / "os-release"
    fake_path.write_text(SAMPLE_OS_RELEASE)
    monkeypatch.setattr(system, "_OS_RELEASE_PATH", fake_path)

    assert system._get_os_release() == "Ubuntu 24.04.4 LTS"


def test_get_os_release_raises_when_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(system, "_OS_RELEASE_PATH", tmp_path / "does-not-exist")

    with pytest.raises(ValueError):
        system._get_os_release()


def test_get_uptime_reads_the_configured_path(tmp_path, monkeypatch):
    fake_path = tmp_path / "uptime"
    fake_path.write_text(SAMPLE_UPTIME)
    monkeypatch.setattr(system, "_UPTIME_PATH", fake_path)

    assert system._get_uptime() == pytest.approx(36564.34)


def test_get_uptime_raises_when_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(system, "_UPTIME_PATH", tmp_path / "does-not-exist")

    with pytest.raises(ValueError):
        system._get_uptime()


# --- collect() end to end -----------------------------------------------------


def test_collect_returns_all_fields_when_everything_succeeds(tmp_path, monkeypatch):
    def fake_run_command(command, timeout=None):
        outputs = {
            ("hostname",): "myhost\n",
            ("uname", "-r"): "6.8.0-134-generic\n",
            ("uname", "-m"): "aarch64\n",
        }
        return _fake_command_result(command, outputs[tuple(command)])

    monkeypatch.setattr(system, "run_command", fake_run_command)

    os_release_path = tmp_path / "os-release"
    os_release_path.write_text(SAMPLE_OS_RELEASE)
    monkeypatch.setattr(system, "_OS_RELEASE_PATH", os_release_path)

    uptime_path = tmp_path / "uptime"
    uptime_path.write_text(SAMPLE_UPTIME)
    monkeypatch.setattr(system, "_UPTIME_PATH", uptime_path)

    result = system.collect(_context())

    assert result.collector_name == "system"
    assert result.data == {
        "hostname": "myhost",
        "operating_system": "Ubuntu 24.04.4 LTS",
        "kernel_version": "6.8.0-134-generic",
        "architecture": "aarch64",
        "uptime_seconds": pytest.approx(36564.34),
    }
    assert result.errors == []
    assert result.success is True
    assert result.duration_ms >= 0


def test_collect_continues_and_records_an_error_when_one_field_fails(
    tmp_path, monkeypatch
):
    def fake_run_command(command, timeout=None):
        if tuple(command) == ("hostname",):
            return _fake_failed_command_result(command)
        outputs = {
            ("uname", "-r"): "6.8.0-134-generic\n",
            ("uname", "-m"): "aarch64\n",
        }
        return _fake_command_result(command, outputs[tuple(command)])

    monkeypatch.setattr(system, "run_command", fake_run_command)

    os_release_path = tmp_path / "os-release"
    os_release_path.write_text(SAMPLE_OS_RELEASE)
    monkeypatch.setattr(system, "_OS_RELEASE_PATH", os_release_path)

    uptime_path = tmp_path / "uptime"
    uptime_path.write_text(SAMPLE_UPTIME)
    monkeypatch.setattr(system, "_UPTIME_PATH", uptime_path)

    result = system.collect(_context())

    assert result.data["hostname"] is None
    assert result.data["kernel_version"] == "6.8.0-134-generic"
    assert result.data["architecture"] == "aarch64"
    assert result.data["operating_system"] == "Ubuntu 24.04.4 LTS"
    assert result.data["uptime_seconds"] == pytest.approx(36564.34)
    assert len(result.errors) == 1
    assert result.errors[0]["severity"] == "error"
    assert result.success is False
