"""Unit tests for nodeiq.collectors.scheduled_jobs.

All file/command access is mocked or redirected to tmp_path — no test
here depends on the real machine's actual cron jobs or timers, per
PROJECT_RULES.md Section 11 and docs/collector_guidelines.md's Testing
Expectations. See tests/collectors/test_scheduled_jobs_integration.py
for a test against the real system on a real Linux machine.
"""

from datetime import datetime, timezone

import pytest

from nodeiq.collectors import scheduled_jobs
from nodeiq.core.collector import CollectorContext
from nodeiq.core.result import CommandResult

# --- _parse_system_crontab_line -------------------------------------------------


def test_parse_system_crontab_line_extracts_a_normal_entry():
    line = "17 *\t* * *\troot\tcd / && run-parts --report /etc/cron.hourly"
    entry = scheduled_jobs._parse_system_crontab_line(line, "/etc/crontab")

    assert entry == {
        "user": "root",
        "schedule": "17 * * * *",
        "command": "cd / && run-parts --report /etc/cron.hourly",
        "source_file": "/etc/crontab",
    }


def test_parse_system_crontab_line_handles_special_schedules():
    entry = scheduled_jobs._parse_system_crontab_line(
        "@reboot root /usr/local/bin/on-boot.sh", "/etc/cron.d/custom"
    )

    assert entry["schedule"] == "@reboot"
    assert entry["user"] == "root"
    assert entry["command"] == "/usr/local/bin/on-boot.sh"


def test_parse_system_crontab_line_skips_comments_and_blank_lines():
    assert scheduled_jobs._parse_system_crontab_line("# a comment", "f") is None
    assert scheduled_jobs._parse_system_crontab_line("   ", "f") is None


def test_parse_system_crontab_line_skips_environment_assignments():
    assert scheduled_jobs._parse_system_crontab_line("PATH=/usr/bin:/bin", "f") is None
    assert scheduled_jobs._parse_system_crontab_line("SHELL=/bin/sh", "f") is None


# --- _parse_user_crontab_line ----------------------------------------------------


def test_parse_user_crontab_line_extracts_a_normal_entry():
    entry = scheduled_jobs._parse_user_crontab_line(
        "30 2 * * * /home/alice/backup.sh", "alice", "/var/spool/cron/crontabs/alice"
    )

    assert entry == {
        "user": "alice",
        "schedule": "30 2 * * *",
        "command": "/home/alice/backup.sh",
        "source_file": "/var/spool/cron/crontabs/alice",
    }


def test_parse_user_crontab_line_handles_special_schedules():
    entry = scheduled_jobs._parse_user_crontab_line("@daily /home/bob/cleanup.sh", "bob", "f")

    assert entry["schedule"] == "@daily"
    assert entry["command"] == "/home/bob/cleanup.sh"


def test_parse_user_crontab_line_skips_comments_and_blank_lines():
    assert scheduled_jobs._parse_user_crontab_line("# comment", "bob", "f") is None
    assert scheduled_jobs._parse_user_crontab_line("", "bob", "f") is None


# --- _get_system_cron_jobs -------------------------------------------------------


def test_get_system_cron_jobs_reads_etc_crontab_and_cron_d(tmp_path, monkeypatch):
    etc_crontab = tmp_path / "crontab"
    etc_crontab.write_text("17 * * * * root run-parts /etc/cron.hourly\n")

    cron_d = tmp_path / "cron.d"
    cron_d.mkdir()
    (cron_d / "sysstat").write_text("5 * * * * root debian-sa1 1 1\n")

    monkeypatch.setattr(scheduled_jobs, "_ETC_CRONTAB_PATH", etc_crontab)
    monkeypatch.setattr(scheduled_jobs, "_CRON_D_DIR", cron_d)

    jobs = scheduled_jobs._get_system_cron_jobs()

    assert len(jobs) == 2


def test_get_system_cron_jobs_handles_missing_files_gracefully(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduled_jobs, "_ETC_CRONTAB_PATH", tmp_path / "does_not_exist")
    monkeypatch.setattr(scheduled_jobs, "_CRON_D_DIR", tmp_path / "also_missing")

    assert scheduled_jobs._get_system_cron_jobs() == []


def test_get_system_cron_jobs_skips_hidden_files_in_cron_d(tmp_path, monkeypatch):
    cron_d = tmp_path / "cron.d"
    cron_d.mkdir()
    (cron_d / ".placeholder").write_text("30 3 * * * root something\n")

    monkeypatch.setattr(scheduled_jobs, "_ETC_CRONTAB_PATH", tmp_path / "no_such_file")
    monkeypatch.setattr(scheduled_jobs, "_CRON_D_DIR", cron_d)

    assert scheduled_jobs._get_system_cron_jobs() == []


# --- _get_user_cron_jobs ---------------------------------------------------------


def test_get_user_cron_jobs_reads_every_accessible_file(tmp_path, monkeypatch):
    crontabs_dir = tmp_path / "crontabs"
    crontabs_dir.mkdir()
    (crontabs_dir / "alice").write_text("0 9 * * * /home/alice/standup.sh\n")

    monkeypatch.setattr(scheduled_jobs, "_USER_CRONTABS_DIR", crontabs_dir)

    jobs = scheduled_jobs._get_user_cron_jobs()

    assert jobs == [
        {
            "user": "alice",
            "schedule": "0 9 * * *",
            "command": "/home/alice/standup.sh",
            "source_file": str(crontabs_dir / "alice"),
        }
    ]


def test_get_user_cron_jobs_returns_empty_when_directory_is_inaccessible(
    tmp_path, monkeypatch
):
    # Simulates the real, expected case where /var/spool/cron/crontabs
    # can't be listed by a non-root user — not an error, just empty.
    monkeypatch.setattr(scheduled_jobs, "_USER_CRONTABS_DIR", tmp_path / "no_permission")

    assert scheduled_jobs._get_user_cron_jobs() == []


# --- _parse_list_timers ----------------------------------------------------------

_SAMPLE_LIST_TIMERS = (
    "Wed 2026-07-15 07:10:00 UTC      30s Wed 2026-07-15 07:00:00 UTC     9min ago "
    "sysstat-collect.timer          sysstat-collect.service\n"
    "Thu 2026-07-16 00:07:00 UTC      16h -                                      - "
    "sysstat-summary.timer          sysstat-summary.service\n"
)


def test_parse_list_timers_extracts_name_and_unit_ignoring_date_columns():
    result = scheduled_jobs._parse_list_timers(_SAMPLE_LIST_TIMERS)

    assert result == [
        {"name": "sysstat-collect.timer", "unit": "sysstat-collect.service"},
        {"name": "sysstat-summary.timer", "unit": "sysstat-summary.service"},
    ]


def test_parse_list_timers_raises_on_a_malformed_line():
    with pytest.raises(ValueError):
        scheduled_jobs._parse_list_timers("onlyonetoken\n")


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
        returncode=1,
        stdout="",
        stderr="systemctl not found",
        duration_seconds=0.01,
        timed_out=False,
        error=None,
    )


def test_collect_merges_cron_and_timers(monkeypatch, tmp_path):
    etc_crontab = tmp_path / "crontab"
    etc_crontab.write_text("17 * * * * root run-parts /etc/cron.hourly\n")
    monkeypatch.setattr(scheduled_jobs, "_ETC_CRONTAB_PATH", etc_crontab)
    monkeypatch.setattr(scheduled_jobs, "_CRON_D_DIR", tmp_path / "no_cron_d")
    monkeypatch.setattr(scheduled_jobs, "_USER_CRONTABS_DIR", tmp_path / "no_spool")

    monkeypatch.setattr(
        scheduled_jobs, "run_command", lambda command, timeout: _succeeding(command, _SAMPLE_LIST_TIMERS)
    )

    result = scheduled_jobs.collect(_context())

    assert result.collector_name == "scheduled_jobs"
    assert result.errors == []
    assert result.data["cron_job_count"] == 1
    assert result.data["timer_count"] == 2


def test_collect_records_an_error_when_list_timers_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduled_jobs, "_ETC_CRONTAB_PATH", tmp_path / "none")
    monkeypatch.setattr(scheduled_jobs, "_CRON_D_DIR", tmp_path / "none2")
    monkeypatch.setattr(scheduled_jobs, "_USER_CRONTABS_DIR", tmp_path / "none3")
    monkeypatch.setattr(scheduled_jobs, "run_command", lambda command, timeout: _failing(command))

    result = scheduled_jobs.collect(_context())

    assert result.data["timer_count"] == 0
    assert result.data["systemd_timers"] == []
    assert len(result.errors) == 1
    assert result.success is False
