"""Unit tests for nodeiq.collectors.users.

All command execution and file access is mocked — no test here depends
on the real machine's actual /etc/passwd, who is logged in, or login
history, per PROJECT_RULES.md Section 11 and docs/collector_guidelines.md's
Testing Expectations. See tests/collectors/test_users_integration.py for
a test against the real files/commands on a real Linux system.
"""

from datetime import datetime, timezone

from nodeiq.collectors import users
from nodeiq.core.collector import CollectorContext
from nodeiq.core.result import CommandResult

# --- _parse_passwd_line / _parse_passwd -----------------------------------------

_SAMPLE_PASSWD = (
    "root:x:0:0:root:/root:/bin/bash\n"
    "syslog:x:104:110::/home/syslog:/usr/sbin/nologin\n"
    "ubuntu:x:1000:1000:Ubuntu:/home/ubuntu:/bin/bash\n"
)


def test_parse_passwd_extracts_every_account():
    result = users._parse_passwd(_SAMPLE_PASSWD)

    assert len(result) == 3
    assert result[2] == {
        "name": "ubuntu",
        "uid": 1000,
        "gid": 1000,
        "home": "/home/ubuntu",
        "shell": "/bin/bash",
        "is_system_account": False,
    }


def test_parse_passwd_flags_system_accounts_below_min_human_uid():
    result = users._parse_passwd(_SAMPLE_PASSWD)

    assert result[0]["is_system_account"] is True  # root, uid 0
    assert result[1]["is_system_account"] is True  # syslog, uid 104
    assert result[2]["is_system_account"] is False  # ubuntu, uid 1000


def test_parse_passwd_skips_blank_lines():
    sample = _SAMPLE_PASSWD + "\n\n"
    assert len(users._parse_passwd(sample)) == 3


def test_parse_passwd_skips_comment_lines():
    sample = "## User Database\n# See DirectoryService(8)\n##\n" + _SAMPLE_PASSWD
    assert len(users._parse_passwd(sample)) == 3


def test_parse_passwd_line_raises_on_too_few_fields():
    import pytest

    with pytest.raises(ValueError):
        users._parse_passwd_line("onlyonefield")


def test_parse_passwd_line_raises_on_non_numeric_uid():
    import pytest

    with pytest.raises(ValueError):
        users._parse_passwd_line("bad:x:notanumber:0:comment:/home/bad:/bin/bash")


# --- _get_users --------------------------------------------------------------------


def test_get_users_reads_the_configured_path(tmp_path, monkeypatch):
    fake_path = tmp_path / "passwd"
    fake_path.write_text(_SAMPLE_PASSWD)
    monkeypatch.setattr(users, "_PASSWD_PATH", fake_path)

    result = users._get_users()

    assert len(result) == 3


def test_get_users_raises_when_file_is_missing(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(users, "_PASSWD_PATH", tmp_path / "does-not-exist")

    with pytest.raises(ValueError):
        users._get_users()


# --- _parse_who_line / _parse_who_output --------------------------------------------

_SAMPLE_WHO = (
    "ubuntu   pts/0        2026-07-17 10:02 (192.168.1.5)\n"
    "root     tty1         2026-07-17 08:00\n"
)


def test_parse_who_output_extracts_every_session():
    result = users._parse_who_output(_SAMPLE_WHO)

    assert len(result) == 2
    assert result[0] == {
        "name": "ubuntu",
        "terminal": "pts/0",
        "login_time": "2026-07-17 10:02",
        "host": "192.168.1.5",
    }


def test_parse_who_line_has_no_host_for_a_local_session():
    result = users._parse_who_line("root     tty1         2026-07-17 08:00")

    assert result["host"] is None


def test_parse_who_line_raises_on_too_few_fields():
    import pytest

    with pytest.raises(ValueError):
        users._parse_who_line("ubuntu pts/0")


def test_parse_who_output_skips_blank_lines():
    sample = _SAMPLE_WHO + "\n"
    assert len(users._parse_who_output(sample)) == 2


# --- _parse_last_line / _parse_last_output ------------------------------------------

_SAMPLE_LAST = (
    "ubuntu   pts/0        192.168.1.5      Thu Jul 17 10:02   still logged in\n"
    "ubuntu   pts/0        192.168.1.5      Wed Jul 16 21:40 - 22:15  (00:35)\n"
    "reboot   system boot  6.8.0-134-generic Wed Jul 16 05:21   still running\n"
    "\n"
    "wtmp begins Wed Jul 16 05:20:00 2026\n"
)


def test_parse_last_output_extracts_real_login_entries_only():
    result = users._parse_last_output(_SAMPLE_LAST)

    assert len(result) == 2
    assert result[0]["name"] == "ubuntu"
    assert result[0]["terminal"] == "pts/0"
    assert "still logged in" in result[0]["detail"]


def test_parse_last_output_skips_reboot_entries():
    result = users._parse_last_output(_SAMPLE_LAST)

    assert all(entry["name"] != "reboot" for entry in result)


def test_parse_last_output_skips_the_wtmp_begins_trailer():
    result = users._parse_last_output(_SAMPLE_LAST)

    assert all(entry["name"] != "wtmp" for entry in result)


def test_parse_last_line_returns_none_for_a_line_with_too_few_tokens():
    assert users._parse_last_line("onlyoneword") is None


# --- collect() end-to-end -----------------------------------------------------------


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


def _failing(command: list[str]) -> CommandResult:
    return CommandResult(
        command=command,
        returncode=1,
        stdout="",
        stderr="",
        duration_seconds=0.01,
        timed_out=False,
        error="command not found",
    )


def test_collect_merges_all_three_sources_into_one_result(tmp_path, monkeypatch):
    fake_path = tmp_path / "passwd"
    fake_path.write_text(_SAMPLE_PASSWD)
    monkeypatch.setattr(users, "_PASSWD_PATH", fake_path)

    def fake_run_command(command, timeout):
        if command == users._WHO_COMMAND:
            return _succeeding(command, _SAMPLE_WHO)
        if command == users._LAST_COMMAND:
            return _succeeding(command, _SAMPLE_LAST)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(users, "run_command", fake_run_command)

    result = users.collect(_context())

    assert result.collector_name == "users"
    assert result.errors == []
    assert len(result.data["users"]) == 3
    assert len(result.data["logged_in_sessions"]) == 2
    assert len(result.data["recent_logins"]) == 2


def test_collect_returns_partial_data_when_last_command_fails(tmp_path, monkeypatch):
    fake_path = tmp_path / "passwd"
    fake_path.write_text(_SAMPLE_PASSWD)
    monkeypatch.setattr(users, "_PASSWD_PATH", fake_path)

    def fake_run_command(command, timeout):
        if command == users._WHO_COMMAND:
            return _succeeding(command, _SAMPLE_WHO)
        if command == users._LAST_COMMAND:
            return _failing(command)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(users, "run_command", fake_run_command)

    result = users.collect(_context())

    assert result.data["users"] is not None
    assert result.data["logged_in_sessions"] is not None
    assert result.data["recent_logins"] is None
    assert len(result.errors) == 1
    assert result.success is False


def test_collect_returns_none_for_users_when_passwd_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "_PASSWD_PATH", tmp_path / "does-not-exist")

    def fake_run_command(command, timeout):
        if command == users._WHO_COMMAND:
            return _succeeding(command, "")
        if command == users._LAST_COMMAND:
            return _succeeding(command, "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(users, "run_command", fake_run_command)

    result = users.collect(_context())

    assert result.data["users"] is None
    assert result.data["logged_in_sessions"] == []
    assert result.data["recent_logins"] == []
    assert len(result.errors) == 1
