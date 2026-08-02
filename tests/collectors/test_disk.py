"""Unit tests for nodeiq.collectors.disk.

All command execution is mocked — no test here depends on the real
machine's actual filesystems, per PROJECT_RULES.md Section 11 and
docs/collector_guidelines.md's Testing Expectations. See
tests/collectors/test_disk_integration.py for a test against the real
`df` on a real Linux system.
"""

from datetime import datetime, timezone

import pytest

from nodeiq.collectors import disk
from nodeiq.core.collector import CollectorContext
from nodeiq.core.result import CommandResult

# --- _parse_df_output ---------------------------------------------------------

_SAMPLE_DF_B1 = (
    "Filesystem       1-blocks       Used  Available Capacity Mounted on\n"
    "tmpfs            99827712    1142784   98684928       2% /run\n"
    "efivarfs           262044      14616     247428       6% /sys/firmware/efi/efivars\n"
    "/dev/sda1      4083888128 2437246976 1629863936      60% /\n"
)


def test_parse_df_output_extracts_every_filesystem():
    result = disk._parse_df_output(_SAMPLE_DF_B1)

    assert len(result) == 3
    assert result[0] == {
        "filesystem": "tmpfs",
        "total_bytes": 99827712,
        "used_bytes": 1142784,
        "available_bytes": 98684928,
        "usage_percent": 2.0,
        "mount_point": "/run",
    }


def test_parse_df_output_handles_a_mount_point_with_spaces():
    sample = (
        "Filesystem       1-blocks       Used  Available Capacity Mounted on\n"
        "tmpfs               1000        500        500       50% /mnt/my drive\n"
    )
    result = disk._parse_df_output(sample)

    assert result[0]["mount_point"] == "/mnt/my drive"


def test_parse_df_output_skips_the_header_and_blank_lines():
    sample = "Filesystem 1-blocks Used Available Capacity Mounted on\n\n"
    assert disk._parse_df_output(sample) == []


def test_parse_df_output_raises_on_a_malformed_line():
    sample = "Filesystem 1-blocks Used Available Capacity Mounted on\ntmpfs 100\n"
    with pytest.raises(ValueError):
        disk._parse_df_output(sample)


# --- _parse_df_inode_output ---------------------------------------------------

_SAMPLE_DF_I = (
    "Filesystem     Inodes  IUsed  IFree IUse% Mounted on\n"
    "tmpfs          121855    688 121167    1% /run\n"
    "efivarfs            0      0      0     - /sys/firmware/efi/efivars\n"
    "/dev/sda1      524288 125657 398631   24% /\n"
)


def test_parse_df_inode_output_extracts_every_filesystem_keyed_by_mount_point():
    result = disk._parse_df_inode_output(_SAMPLE_DF_I)

    assert result["/run"] == {
        "inode_total": 121855,
        "inode_used": 688,
        "inode_available": 121167,
        "inode_usage_percent": 1.0,
    }
    assert result["/"] == {
        "inode_total": 524288,
        "inode_used": 125657,
        "inode_available": 398631,
        "inode_usage_percent": 24.0,
    }


def test_parse_df_inode_output_handles_a_filesystem_with_no_inode_concept():
    # efivarfs reports "-" for every inode field — not an error, just
    # "this filesystem doesn't support inodes."
    result = disk._parse_df_inode_output(_SAMPLE_DF_I)

    assert result["/sys/firmware/efi/efivars"] == {
        "inode_total": 0,
        "inode_used": 0,
        "inode_available": 0,
        "inode_usage_percent": None,
    }


def test_parse_df_inode_output_raises_on_a_malformed_line():
    sample = "Filesystem Inodes IUsed IFree IUse% Mounted on\ntmpfs 100\n"
    with pytest.raises(ValueError):
        disk._parse_df_inode_output(sample)


# --- _parse_percent / _parse_optional_int --------------------------------------


def test_parse_percent_converts_a_percentage_token():
    assert disk._parse_percent("60%") == 60.0


def test_parse_percent_returns_none_for_a_dash():
    assert disk._parse_percent("-") is None


def test_parse_percent_raises_on_garbage():
    with pytest.raises(ValueError):
        disk._parse_percent("notanumber")


def test_parse_optional_int_converts_a_numeric_token():
    assert disk._parse_optional_int("42") == 42


def test_parse_optional_int_returns_none_for_a_dash():
    assert disk._parse_optional_int("-") is None


def test_parse_optional_int_raises_on_garbage():
    with pytest.raises(ValueError):
        disk._parse_optional_int("notanumber")


# --- _merge_filesystems ---------------------------------------------------------


def test_merge_filesystems_combines_disk_and_inode_data_by_mount_point():
    filesystems = [
        {
            "filesystem": "/dev/sda1",
            "total_bytes": 1000,
            "used_bytes": 600,
            "available_bytes": 400,
            "usage_percent": 60.0,
            "mount_point": "/",
        }
    ]
    inode_usage_by_mount = {
        "/": {
            "inode_total": 100,
            "inode_used": 24,
            "inode_available": 76,
            "inode_usage_percent": 24.0,
        }
    }

    merged = disk._merge_filesystems(filesystems, inode_usage_by_mount)

    assert merged == [
        {
            "filesystem": "/dev/sda1",
            "total_bytes": 1000,
            "used_bytes": 600,
            "available_bytes": 400,
            "usage_percent": 60.0,
            "mount_point": "/",
            "inode_total": 100,
            "inode_used": 24,
            "inode_available": 76,
            "inode_usage_percent": 24.0,
        }
    ]


def test_merge_filesystems_fills_none_when_inode_data_is_missing_for_a_mount():
    filesystems = [
        {
            "filesystem": "/dev/sda1",
            "total_bytes": 1000,
            "used_bytes": 600,
            "available_bytes": 400,
            "usage_percent": 60.0,
            "mount_point": "/",
        }
    ]

    merged = disk._merge_filesystems(filesystems, {})

    assert merged[0]["inode_total"] is None
    assert merged[0]["inode_used"] is None
    assert merged[0]["inode_available"] is None
    assert merged[0]["inode_usage_percent"] is None


# --- _highest --------------------------------------------------------------------


def test_highest_returns_the_maximum_value_across_filesystems():
    filesystems = [{"usage_percent": 10.0}, {"usage_percent": 60.0}, {"usage_percent": 30.0}]
    assert disk._highest(filesystems, "usage_percent") == 60.0


def test_highest_ignores_none_values():
    filesystems = [{"usage_percent": None}, {"usage_percent": 30.0}]
    assert disk._highest(filesystems, "usage_percent") == 30.0


def test_highest_returns_none_when_every_value_is_none():
    filesystems = [{"usage_percent": None}, {"usage_percent": None}]
    assert disk._highest(filesystems, "usage_percent") is None


def test_highest_returns_none_for_an_empty_list():
    assert disk._highest([], "usage_percent") is None


# --- collect() end-to-end -------------------------------------------------------


def _context() -> CollectorContext:
    return CollectorContext(scan_start_time=datetime.now(timezone.utc))


def _succeeding_command_result(command: list[str], stdout: str) -> CommandResult:
    return CommandResult(
        command=command,
        returncode=0,
        stdout=stdout,
        stderr="",
        duration_seconds=0.01,
        timed_out=False,
        error=None,
    )


def _failing_command_result(command: list[str]) -> CommandResult:
    return CommandResult(
        command=command,
        returncode=1,
        stdout="",
        stderr="df: cannot read table of mounted file systems",
        duration_seconds=0.01,
        timed_out=False,
        error=None,
    )


def test_collect_merges_both_commands_into_one_result(monkeypatch):
    def fake_run_command(command, timeout):
        if command == disk._DISK_USAGE_COMMAND:
            return _succeeding_command_result(command, _SAMPLE_DF_B1)
        if command == disk._INODE_USAGE_COMMAND:
            return _succeeding_command_result(command, _SAMPLE_DF_I)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(disk, "run_command", fake_run_command)

    result = disk.collect(_context())

    assert result.collector_name == "disk"
    assert result.errors == []
    assert len(result.data["filesystems"]) == 3
    root = next(fs for fs in result.data["filesystems"] if fs["mount_point"] == "/")
    assert root["usage_percent"] == 60.0
    assert root["inode_usage_percent"] == 24.0
    assert result.data["highest_disk_usage_percent"] == 60.0
    assert result.data["highest_inode_usage_percent"] == 24.0


def test_collect_returns_empty_when_disk_usage_command_fails(monkeypatch):
    def fake_run_command(command, timeout):
        if command == disk._DISK_USAGE_COMMAND:
            return _failing_command_result(command)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(disk, "run_command", fake_run_command)

    result = disk.collect(_context())

    assert result.data == {
        "filesystems": [],
        "highest_disk_usage_percent": None,
        "highest_inode_usage_percent": None,
    }
    assert len(result.errors) == 1
    assert result.success is False


def test_collect_returns_disk_usage_with_none_inodes_when_inode_command_fails(
    monkeypatch,
):
    def fake_run_command(command, timeout):
        if command == disk._DISK_USAGE_COMMAND:
            return _succeeding_command_result(command, _SAMPLE_DF_B1)
        if command == disk._INODE_USAGE_COMMAND:
            return _failing_command_result(command)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(disk, "run_command", fake_run_command)

    result = disk.collect(_context())

    assert len(result.data["filesystems"]) == 3
    assert all(fs["inode_usage_percent"] is None for fs in result.data["filesystems"])
    assert result.data["highest_inode_usage_percent"] is None
    assert result.data["highest_disk_usage_percent"] == 60.0
    assert len(result.errors) == 1  # inode command failure recorded
    assert result.success is False
