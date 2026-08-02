"""Unit tests for nodeiq.summary.

Section fixtures are hand-built dicts modeled directly on the real
shapes all 9 collectors actually produce (verified against a real
Multipass VM snapshot during Phase 4.1B) rather than reading any
gitignored `snapshots/*.json` file, so these tests never depend on a
file that isn't part of the repository. See
test_summarize_snapshot_handles_a_real_run_scan_result below for a test
against a genuinely real (not hand-built) snapshot.
"""

import copy

import pytest

from nodeiq import summary


def _full_snapshot() -> dict:
    """A hand-built snapshot modeled on real collector output, with
    every section healthy — the baseline every test starts from and
    modifies."""
    return {
        "metadata": {
            "scan_timestamp": "2026-07-15T08:35:17.525799+00:00",
            "scan_duration_ms": 12.3,
            "collector_count": 9,
            "nodeiq_version": "0.1.0",
            "hostname": "main-cattle",
        },
        "collection_errors": {},
        "system": {
            "hostname": "main-cattle",
            "operating_system": "Ubuntu 24.04.4 LTS",
            "kernel_version": "6.8.0-134-generic",
            "architecture": "aarch64",
            "uptime_seconds": 446417.0,
        },
        "cpu_memory": {
            "memory_used_bytes": 265728000,
            "memory_available_bytes": 732512256,
            "memory_usage_percent": 26.62,
            "swap_used_bytes": 0,
            "swap_usage_percent": 0.0,
            "load_average_1m": 0.01,
            "load_average_5m": 0.01,
            "load_average_15m": 0.0,
        },
        "processes": {
            "process_count": 91,
            "zombie_count": 0,
            "blocked_process_count": 0,
            "top_by_memory": [
                {
                    "pid": 316,
                    "process_name": "multipathd",
                    "memory_rss_bytes": 27107328,
                    "owner": "root",
                    "state": "S",
                    "command": "/sbin/multipathd -d -s",
                }
            ],
        },
        "disk": {
            "filesystems": [
                {
                    "filesystem": "/dev/sda1",
                    "mount_point": "/",
                    "total_bytes": 4083888128,
                    "used_bytes": 2437246976,
                    "available_bytes": 1629863936,
                    "usage_percent": 60.0,
                    "inode_total": 524288,
                    "inode_used": 125657,
                    "inode_available": 398631,
                    "inode_usage_percent": 24.0,
                }
            ],
            "highest_disk_usage_percent": 60.0,
            "highest_inode_usage_percent": 24.0,
        },
        "services": {
            "systemd_available": True,
            "running_services_count": 54,
            "failed_services_count": 0,
            "enabled_services_count": 45,
            "failed_services": [],
            "restarting_services": [],
        },
        "scheduled_jobs": {
            "cron_job_count": 8,
            "cron_jobs": [
                {
                    "user": "root",
                    "schedule": "17 * * * *",
                    "command": "cd / && run-parts --report /etc/cron.hourly",
                    "source_file": "/etc/crontab",
                }
            ],
            "timer_count": 17,
            "systemd_timers": [{"name": "apt-daily.timer", "unit": "apt-daily.service"}],
        },
        "permissions": {
            "checked_paths": [
                {
                    "path": "/etc/passwd",
                    "exists": True,
                    "owner": "root",
                    "group": "root",
                    "mode": "644",
                    "world_writable": False,
                },
                {
                    "path": "/etc/shadow",
                    "exists": True,
                    "owner": "root",
                    "group": "shadow",
                    "mode": "640",
                    "world_writable": False,
                },
            ]
        },
        "network": {
            "interfaces": [
                {
                    "name": "enp0s1",
                    "state": "up",
                    "ipv4_addresses": ["192.168.252.2/24"],
                    "ipv6_addresses": [],
                }
            ],
            "default_route": {"gateway": "192.168.252.1", "interface": "enp0s1"},
            "listening_ports": [
                {
                    "protocol": "tcp",
                    "local_address": "0.0.0.0",
                    "port": 22,
                    "process_name": None,
                    "pid": None,
                }
            ],
            "firewall": {"tool": None, "enabled": None},
        },
        "logs": {
            "source": "journalctl",
            "truncated": False,
            "warning_count": 29,
            "error_count": 0,
            "recent_entries": [
                {
                    "timestamp": "2026-07-10T05:21:23.233335+00:00",
                    "severity": "warning",
                    "unit": "kernel",
                    "message": "KASLR disabled due to lack of seed",
                }
            ],
        },
        "users": {
            "users": [
                {
                    "name": "ubuntu",
                    "uid": 1000,
                    "gid": 1000,
                    "home": "/home/ubuntu",
                    "shell": "/bin/bash",
                    "is_system_account": False,
                }
            ],
            "logged_in_sessions": [
                {
                    "name": "ubuntu",
                    "terminal": "pts/0",
                    "login_time": "2026-07-15 10:02",
                    "host": "192.168.1.5",
                }
            ],
            "recent_logins": [
                {"name": "ubuntu", "terminal": "pts/0", "detail": "still logged in"}
            ],
        },
    }


_ALL_SECTIONS = [
    "system",
    "cpu_memory",
    "processes",
    "disk",
    "services",
    "scheduled_jobs",
    "permissions",
    "network",
    "logs",
    "users",
]


# --- Overall structure -----------------------------------------------------------


def test_summarize_snapshot_returns_the_expected_top_level_shape():
    result = summary.summarize_snapshot(_full_snapshot())

    assert set(result.keys()) == {
        "generated_at",
        "snapshot_timestamp",
        "hostname",
        "sections",
        "collection_errors",
    }
    assert set(result["sections"].keys()) == set(_ALL_SECTIONS)


def test_summarize_snapshot_echoes_metadata_fields():
    result = summary.summarize_snapshot(_full_snapshot())

    assert result["snapshot_timestamp"] == "2026-07-15T08:35:17.525799+00:00"
    assert result["hostname"] == "main-cattle"


def test_every_section_summary_has_the_required_keys():
    result = summary.summarize_snapshot(_full_snapshot())

    for section_name in _ALL_SECTIONS:
        section = result["sections"][section_name]
        assert set(section.keys()) == {
            "available",
            "status",
            "headline",
            "highlights",
            "concerns",
            "evidence",
            "errors",
        }
        assert section["status"] in ("healthy", "warning", "critical", "unknown")


def test_every_section_is_healthy_for_the_fully_healthy_fixture():
    result = summary.summarize_snapshot(_full_snapshot())

    for section_name in _ALL_SECTIONS:
        assert result["sections"][section_name]["status"] == "healthy"
        assert result["sections"][section_name]["available"] is True


# --- Determinism -------------------------------------------------------------------


def test_summarize_snapshot_is_deterministic_aside_from_generated_at():
    snapshot = _full_snapshot()

    first = summary.summarize_snapshot(snapshot)
    second = summary.summarize_snapshot(snapshot)

    assert first["sections"] == second["sections"]
    assert first["snapshot_timestamp"] == second["snapshot_timestamp"]
    assert first["hostname"] == second["hostname"]
    assert first["collection_errors"] == second["collection_errors"]


def test_summarize_snapshot_does_not_mutate_its_input():
    snapshot = _full_snapshot()
    original = copy.deepcopy(snapshot)

    summary.summarize_snapshot(snapshot)

    assert snapshot == original


# --- Missing / unavailable sections -----------------------------------------------


def test_a_crashed_collector_section_is_reported_as_unavailable():
    snapshot = _full_snapshot()
    snapshot["disk"] = None
    snapshot["collection_errors"]["disk"] = [
        {"message": "df failed", "severity": "error", "exception_type": "ValueError"}
    ]

    result = summary.summarize_snapshot(snapshot)

    disk_summary = result["sections"]["disk"]
    assert disk_summary["available"] is False
    assert disk_summary["status"] == "unknown"
    assert disk_summary["evidence"] == {}
    assert disk_summary["errors"] == snapshot["collection_errors"]["disk"]


def test_a_section_entirely_absent_from_the_snapshot_is_reported_as_unavailable():
    snapshot = _full_snapshot()
    del snapshot["logs"]

    result = summary.summarize_snapshot(snapshot)

    assert result["sections"]["logs"]["available"] is False
    assert result["sections"]["logs"]["status"] == "unknown"


def test_missing_metadata_is_handled_gracefully():
    snapshot = _full_snapshot()
    del snapshot["metadata"]

    result = summary.summarize_snapshot(snapshot)

    assert result["snapshot_timestamp"] is None
    assert result["hostname"] is None


# --- Summarizer failure isolation --------------------------------------------------


def test_one_summarizer_crashing_does_not_stop_the_others(monkeypatch):
    def _boom(data, errors):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        summary,
        "_REGISTERED_SUMMARIZERS",
        [
            ("system", summary._summarize_system),
            ("cpu_memory", _boom),
            ("processes", summary._summarize_processes),
        ],
    )

    result = summary.summarize_snapshot(_full_snapshot())

    assert result["sections"]["system"]["available"] is True
    assert result["sections"]["processes"]["available"] is True
    assert result["sections"]["cpu_memory"]["available"] is False
    assert result["sections"]["cpu_memory"]["status"] == "unknown"


def test_a_crashing_summarizer_records_the_exception_in_errors(monkeypatch):
    def _boom(data, errors):
        raise ValueError("something went wrong")

    monkeypatch.setattr(summary, "_REGISTERED_SUMMARIZERS", [("cpu_memory", _boom)])

    result = summary.summarize_snapshot(_full_snapshot())

    cpu_errors = result["sections"]["cpu_memory"]["errors"]
    assert len(cpu_errors) == 1
    assert cpu_errors[0]["exception_type"] == "ValueError"
    assert "something went wrong" in cpu_errors[0]["message"]


# --- cpu_memory status logic -------------------------------------------------------


def test_cpu_memory_is_healthy_below_both_thresholds():
    result = summary._summarize_cpu_memory(
        {"memory_usage_percent": 50.0, "swap_usage_percent": 0.0}, []
    )
    assert result["status"] == "healthy"
    assert result["concerns"] == []


def test_cpu_memory_is_warning_at_the_memory_warning_threshold():
    result = summary._summarize_cpu_memory(
        {"memory_usage_percent": summary._MEMORY_WARNING_PERCENT, "swap_usage_percent": 0.0}, []
    )
    assert result["status"] == "warning"
    assert len(result["concerns"]) == 1


def test_cpu_memory_is_critical_at_the_memory_critical_threshold():
    result = summary._summarize_cpu_memory(
        {"memory_usage_percent": summary._MEMORY_CRITICAL_PERCENT, "swap_usage_percent": 0.0}, []
    )
    assert result["status"] == "critical"


def test_cpu_memory_is_warning_from_swap_even_when_memory_is_healthy():
    result = summary._summarize_cpu_memory(
        {"memory_usage_percent": 10.0, "swap_usage_percent": summary._SWAP_WARNING_PERCENT}, []
    )
    assert result["status"] == "warning"


def test_cpu_memory_is_unknown_when_no_fields_are_present():
    result = summary._summarize_cpu_memory({}, [])
    assert result["status"] == "unknown"
    assert result["headline"] == "CPU/memory usage unavailable"


def test_cpu_memory_is_warning_at_the_cpu_warning_threshold():
    result = summary._summarize_cpu_memory(
        {"cpu_usage_percent": summary._CPU_WARNING_PERCENT, "memory_usage_percent": 10.0}, []
    )
    assert result["status"] == "warning"
    assert len(result["concerns"]) == 1
    assert "CPU usage" in result["concerns"][0]


def test_cpu_memory_is_critical_at_the_cpu_critical_threshold():
    result = summary._summarize_cpu_memory(
        {"cpu_usage_percent": summary._CPU_CRITICAL_PERCENT, "memory_usage_percent": 10.0}, []
    )
    assert result["status"] == "critical"


def test_cpu_memory_headline_combines_cpu_and_memory_when_both_present():
    result = summary._summarize_cpu_memory(
        {"cpu_usage_percent": 12.5, "memory_usage_percent": 27.7}, []
    )
    assert result["headline"] == "CPU 12.5% used, memory 27.7% used"


def test_cpu_memory_headline_falls_back_to_cpu_only_when_memory_missing():
    result = summary._summarize_cpu_memory({"cpu_usage_percent": 12.5}, [])
    assert result["headline"] == "CPU 12.5% used"


def test_cpu_memory_evidence_includes_cpu_usage_percent():
    result = summary._summarize_cpu_memory(
        {"cpu_usage_percent": 12.5, "memory_usage_percent": 27.7}, []
    )
    assert result["evidence"]["cpu_usage_percent"] == 12.5


def test_cpu_memory_evidence_includes_all_load_average_windows():
    result = summary._summarize_cpu_memory(
        {
            "cpu_usage_percent": 12.5,
            "memory_usage_percent": 27.7,
            "load_average_1m": 0.5,
            "load_average_5m": 0.4,
            "load_average_15m": 0.3,
        },
        [],
    )
    assert result["evidence"]["load_average_1m"] == 0.5
    assert result["evidence"]["load_average_5m"] == 0.4
    assert result["evidence"]["load_average_15m"] == 0.3


# --- processes status logic ---------------------------------------------------------


def test_processes_healthy_with_no_zombies_or_blocked():
    result = summary._summarize_processes(
        {"process_count": 10, "zombie_count": 0, "blocked_process_count": 0}, []
    )
    assert result["status"] == "healthy"


def test_processes_warning_at_zombie_warning_threshold():
    result = summary._summarize_processes(
        {"zombie_count": summary._ZOMBIE_WARNING_COUNT, "blocked_process_count": 0}, []
    )
    assert result["status"] == "warning"


def test_processes_critical_at_zombie_critical_threshold():
    result = summary._summarize_processes(
        {"zombie_count": summary._ZOMBIE_CRITICAL_COUNT, "blocked_process_count": 0}, []
    )
    assert result["status"] == "critical"


def test_processes_warning_for_any_blocked_process():
    result = summary._summarize_processes(
        {"zombie_count": 0, "blocked_process_count": summary._BLOCKED_WARNING_COUNT}, []
    )
    assert result["status"] == "warning"


def test_processes_critical_at_blocked_critical_threshold():
    result = summary._summarize_processes(
        {"zombie_count": 0, "blocked_process_count": summary._BLOCKED_CRITICAL_COUNT}, []
    )
    assert result["status"] == "critical"


def test_processes_highlight_names_the_top_memory_consumer():
    result = summary._summarize_processes(
        {
            "process_count": 1,
            "zombie_count": 0,
            "blocked_process_count": 0,
            "top_by_memory": [{"process_name": "sshd", "memory_rss_bytes": 1048576}],
        },
        [],
    )
    assert "sshd" in result["highlights"][0]
    assert "1.0 MB" in result["highlights"][0]


def test_processes_highlight_names_the_top_cpu_consumer():
    result = summary._summarize_processes(
        {
            "process_count": 1,
            "zombie_count": 0,
            "blocked_process_count": 0,
            "top_by_cpu": [{"process_name": "nodeiq", "cpu_usage_percent": 42.5}],
        },
        [],
    )
    assert any("nodeiq" in h and "42.5%" in h for h in result["highlights"])


def test_processes_evidence_includes_top_processes_by_memory_and_cpu():
    result = summary._summarize_processes(
        {
            "process_count": 2,
            "zombie_count": 0,
            "blocked_process_count": 0,
            "top_by_memory": [
                {
                    "process_name": "sshd",
                    "pid": 851,
                    "owner": "root",
                    "state": "S",
                    "command": "/usr/sbin/sshd -D",
                    "memory_rss_bytes": 1048576,
                },
                {
                    "process_name": "nodeiq",
                    "pid": 2001,
                    "owner": "ubuntu",
                    "state": "R",
                    "command": "nodeiq ask cpu usage",
                    "memory_rss_bytes": 524288,
                },
            ],
            "top_by_cpu": [
                {
                    "process_name": "nodeiq",
                    "pid": 2001,
                    "owner": "ubuntu",
                    "state": "R",
                    "command": "nodeiq ask cpu usage",
                    "cpu_usage_percent": 42.5,
                },
                {
                    "process_name": "sshd",
                    "pid": 851,
                    "owner": "root",
                    "state": "S",
                    "command": "/usr/sbin/sshd -D",
                    "cpu_usage_percent": None,
                },
            ],
        },
        [],
    )
    assert result["evidence"]["top_processes_by_memory"] == [
        {
            "process_name": "sshd",
            "pid": 851,
            "owner": "root",
            "state": "S",
            "command": "/usr/sbin/sshd -D",
            "memory": "1.0 MB",
        },
        {
            "process_name": "nodeiq",
            "pid": 2001,
            "owner": "ubuntu",
            "state": "R",
            "command": "nodeiq ask cpu usage",
            "memory": "512.0 KB",
        },
    ]
    assert result["evidence"]["top_processes_by_cpu"] == [
        {
            "process_name": "nodeiq",
            "pid": 2001,
            "owner": "ubuntu",
            "state": "R",
            "command": "nodeiq ask cpu usage",
            "cpu_usage_percent": 42.5,
        },
    ]


def test_processes_evidence_redacts_secrets_in_command():
    result = summary._summarize_processes(
        {
            "process_count": 1,
            "zombie_count": 0,
            "blocked_process_count": 0,
            "top_by_memory": [
                {
                    "process_name": "myapp",
                    "pid": 42,
                    "owner": "ubuntu",
                    "state": "R",
                    "command": "myapp --password=hunter2",
                    "memory_rss_bytes": 1024,
                },
            ],
        },
        [],
    )
    command = result["evidence"]["top_processes_by_memory"][0]["command"]
    assert "hunter2" not in command
    assert "[REDACTED]" in command


def test_processes_evidence_top_lists_capped_at_max_top_processes():
    top_by_memory = [
        {"process_name": f"proc{i}", "memory_rss_bytes": i} for i in range(15, 0, -1)
    ]
    result = summary._summarize_processes(
        {"process_count": 15, "zombie_count": 0, "blocked_process_count": 0, "top_by_memory": top_by_memory},
        [],
    )
    assert len(result["evidence"]["top_processes_by_memory"]) == summary._MAX_TOP_PROCESSES_IN_EVIDENCE


def test_processes_evidence_top_processes_matches_collectors_own_top_n():
    # The collector's own _TOP_N (processes.py) is 10 -- evidence should
    # be able to include all of them, not truncate a second time.
    top_by_memory = [{"process_name": f"proc{i}", "memory_rss_bytes": i} for i in range(10, 0, -1)]
    result = summary._summarize_processes(
        {"process_count": 10, "zombie_count": 0, "blocked_process_count": 0, "top_by_memory": top_by_memory},
        [],
    )
    assert len(result["evidence"]["top_processes_by_memory"]) == 10


def test_processes_no_cpu_highlight_when_no_cpu_data_available():
    result = summary._summarize_processes(
        {
            "process_count": 1,
            "zombie_count": 0,
            "blocked_process_count": 0,
            "top_by_cpu": [{"process_name": "sshd", "cpu_usage_percent": None}],
        },
        [],
    )
    assert not any("Top CPU consumer" in h for h in result["highlights"])
    assert result["evidence"]["top_processes_by_cpu"] == []


# --- disk status logic ---------------------------------------------------------------


def test_disk_healthy_below_both_thresholds():
    result = summary._summarize_disk(
        {"highest_disk_usage_percent": 10.0, "highest_inode_usage_percent": 10.0}, []
    )
    assert result["status"] == "healthy"


def test_disk_warning_at_disk_usage_warning_threshold():
    result = summary._summarize_disk(
        {"highest_disk_usage_percent": summary._DISK_WARNING_PERCENT, "highest_inode_usage_percent": 0.0}, []
    )
    assert result["status"] == "warning"


def test_disk_critical_at_inode_usage_critical_threshold():
    result = summary._summarize_disk(
        {"highest_disk_usage_percent": 0.0, "highest_inode_usage_percent": summary._DISK_CRITICAL_PERCENT}, []
    )
    assert result["status"] == "critical"


def test_disk_evidence_includes_every_filesystem():
    result = summary._summarize_disk(
        {
            "highest_disk_usage_percent": 60.0,
            "highest_inode_usage_percent": 10.0,
            "filesystems": [
                {
                    "mount_point": "/",
                    "filesystem": "/dev/sda1",
                    "usage_percent": 60.0,
                    "inode_usage_percent": 10.0,
                    "total_bytes": 1000,
                    "used_bytes": 600,
                    "available_bytes": 400,
                },
                {
                    "mount_point": "/boot",
                    "filesystem": "/dev/sda2",
                    "usage_percent": 20.0,
                    "inode_usage_percent": 5.0,
                    "total_bytes": 500,
                    "used_bytes": 100,
                    "available_bytes": 400,
                },
            ],
        },
        [],
    )
    assert result["evidence"]["filesystems"] == [
        {
            "mount_point": "/",
            "filesystem": "/dev/sda1",
            "usage_percent": 60.0,
            "inode_usage_percent": 10.0,
            "total_bytes": 1000,
            "used_bytes": 600,
            "available_bytes": 400,
        },
        {
            "mount_point": "/boot",
            "filesystem": "/dev/sda2",
            "usage_percent": 20.0,
            "inode_usage_percent": 5.0,
            "total_bytes": 500,
            "used_bytes": 100,
            "available_bytes": 400,
        },
    ]


# --- services status logic -----------------------------------------------------------


def test_services_unknown_when_systemd_is_unavailable():
    result = summary._summarize_services({"systemd_available": False}, [])
    assert result["status"] == "unknown"
    assert result["evidence"] == {"systemd_available": False}


def test_services_healthy_with_no_failures():
    result = summary._summarize_services(
        {
            "systemd_available": True,
            "running_services_count": 10,
            "failed_services_count": 0,
            "failed_services": [],
            "restarting_services": [],
        },
        [],
    )
    assert result["status"] == "healthy"


def test_services_highlight_names_running_services():
    result = summary._summarize_services(
        {
            "systemd_available": True,
            "running_services_count": 2,
            "failed_services_count": 0,
            "running_services": [{"name": "cron.service"}, {"name": "ssh.service"}],
            "failed_services": [],
            "restarting_services": [],
        },
        [],
    )
    assert any("cron.service" in h and "ssh.service" in h for h in result["highlights"])


def test_services_highlight_caps_running_service_names():
    running = [{"name": f"svc{i}.service"} for i in range(20)]
    result = summary._summarize_services(
        {
            "systemd_available": True,
            "running_services_count": 20,
            "failed_services_count": 0,
            "running_services": running,
            "failed_services": [],
            "restarting_services": [],
        },
        [],
    )
    assert "and 15 more" in result["highlights"][0]


def test_services_no_running_highlight_when_list_is_empty():
    result = summary._summarize_services(
        {
            "systemd_available": True,
            "running_services_count": 0,
            "failed_services_count": 0,
            "failed_services": [],
            "restarting_services": [],
        },
        [],
    )
    assert result["highlights"] == []


def test_services_critical_at_failed_critical_threshold():
    failed = [{"name": f"svc{i}.service"} for i in range(summary._FAILED_SERVICES_CRITICAL_COUNT)]
    result = summary._summarize_services(
        {
            "systemd_available": True,
            "running_services_count": 10,
            "failed_services_count": len(failed),
            "failed_services": failed,
            "restarting_services": [],
        },
        [],
    )
    assert result["status"] == "critical"


def test_services_concern_lists_failed_service_names_capped():
    failed = [{"name": f"svc{i}.service"} for i in range(10)]
    result = summary._summarize_services(
        {
            "systemd_available": True,
            "running_services_count": 10,
            "failed_services_count": len(failed),
            "failed_services": failed,
            "restarting_services": [],
        },
        [],
    )
    assert "and 5 more" in result["concerns"][0]


def test_services_restarting_escalates_healthy_to_warning():
    result = summary._summarize_services(
        {
            "systemd_available": True,
            "running_services_count": 10,
            "failed_services_count": 0,
            "failed_services": [],
            "restarting_services": [{"name": "flaky.service"}],
        },
        [],
    )
    assert result["status"] == "warning"


def test_services_evidence_lists_are_uncapped_and_include_descriptions():
    failed = [
        {"name": f"svc{i}.service", "description": f"Service {i}"} for i in range(10)
    ]
    result = summary._summarize_services(
        {
            "systemd_available": True,
            "running_services_count": 1,
            "running_services": [{"name": "ssh.service", "description": "OpenSSH server"}],
            "failed_services_count": len(failed),
            "failed_services": failed,
            "restarting_services": [],
        },
        [],
    )
    assert len(result["evidence"]["failed_services"]) == 10
    assert result["evidence"]["failed_services"][9] == {
        "name": "svc9.service",
        "description": "Service 9",
    }
    assert result["evidence"]["running_services"] == [
        {"name": "ssh.service", "description": "OpenSSH server"}
    ]


# --- permissions status logic ---------------------------------------------------------


def test_permissions_healthy_when_nothing_world_writable():
    result = summary._summarize_permissions(
        {"checked_paths": [{"path": "/etc/passwd", "exists": True, "world_writable": False}]}, []
    )
    assert result["status"] == "healthy"


def test_permissions_critical_when_world_writable():
    result = summary._summarize_permissions(
        {"checked_paths": [{"path": "/etc/shadow", "exists": True, "world_writable": True}]}, []
    )
    assert result["status"] == "critical"
    assert "/etc/shadow" in result["concerns"][0]


def test_permissions_warning_when_a_path_could_not_be_checked():
    result = summary._summarize_permissions(
        {"checked_paths": [{"path": "/etc/shadow", "exists": None, "world_writable": None}]}, []
    )
    assert result["status"] == "warning"


def test_permissions_unknown_when_no_paths_checked():
    result = summary._summarize_permissions({"checked_paths": []}, [])
    assert result["status"] == "unknown"


def test_permissions_evidence_includes_owner_group_mode_per_path():
    result = summary._summarize_permissions(
        {
            "checked_paths": [
                {
                    "path": "/etc/shadow",
                    "exists": True,
                    "owner": "root",
                    "group": "shadow",
                    "mode": "640",
                    "world_writable": False,
                }
            ]
        },
        [],
    )
    assert result["evidence"]["checked_paths"] == [
        {
            "path": "/etc/shadow",
            "exists": True,
            "owner": "root",
            "group": "shadow",
            "mode": "640",
            "world_writable": False,
        }
    ]


# --- network status logic -----------------------------------------------------------


def test_network_healthy_when_an_interface_is_up():
    result = summary._summarize_network(
        {"interfaces": [{"name": "eth0", "state": "up"}], "listening_ports": [], "default_route": None}, []
    )
    assert result["status"] == "healthy"


def test_network_warning_when_no_interface_is_up():
    result = summary._summarize_network(
        {"interfaces": [{"name": "eth0", "state": "down"}], "listening_ports": [], "default_route": None}, []
    )
    assert result["status"] == "warning"


def test_network_unknown_when_no_interfaces_at_all():
    result = summary._summarize_network({"interfaces": [], "listening_ports": [], "default_route": None}, [])
    assert result["status"] == "unknown"


def test_network_never_flags_undetected_firewall_as_a_concern():
    result = summary._summarize_network(
        {
            "interfaces": [{"name": "eth0", "state": "up"}],
            "listening_ports": [],
            "default_route": None,
            "firewall": {"tool": None, "enabled": None},
        },
        [],
    )
    assert result["status"] == "healthy"
    assert result["concerns"] == []


def test_network_evidence_includes_firewall_result_even_when_undetected():
    result = summary._summarize_network(
        {
            "interfaces": [{"name": "eth0", "state": "up"}],
            "listening_ports": [],
            "default_route": None,
            "firewall": {"tool": None, "enabled": None},
        },
        [],
    )
    assert result["evidence"]["firewall_tool"] is None
    assert result["evidence"]["firewall_enabled"] is None


def test_network_evidence_includes_firewall_detection_note():
    result = summary._summarize_network(
        {
            "interfaces": [{"name": "eth0", "state": "up"}],
            "listening_ports": [],
            "default_route": None,
            "firewall": {
                "tool": None,
                "enabled": None,
                "detection_note": "Could not run a firewall detection command: Permission denied",
            },
        },
        [],
    )
    assert (
        result["evidence"]["firewall_detection_note"]
        == "Could not run a firewall detection command: Permission denied"
    )


def test_network_evidence_includes_interfaces_and_listening_ports():
    result = summary._summarize_network(
        {
            "interfaces": [
                {
                    "name": "eth0",
                    "state": "up",
                    "ipv4_addresses": ["10.0.0.5"],
                    "ipv6_addresses": [],
                }
            ],
            "listening_ports": [
                {
                    "protocol": "tcp",
                    "local_address": "0.0.0.0",
                    "port": 22,
                    "process_name": "sshd",
                    "pid": 851,
                }
            ],
            "default_route": None,
            "firewall": {"tool": "ufw", "enabled": True},
        },
        [],
    )
    assert result["evidence"]["interfaces"] == [
        {
            "name": "eth0",
            "state": "up",
            "ipv4_addresses": ["10.0.0.5"],
            "ipv6_addresses": [],
        }
    ]
    assert result["evidence"]["listening_ports"] == [
        {
            "protocol": "tcp",
            "local_address": "0.0.0.0",
            "port": 22,
            "process_name": "sshd",
            "pid": 851,
        }
    ]
    assert result["evidence"]["firewall_tool"] == "ufw"
    assert result["evidence"]["firewall_enabled"] is True


# --- logs status logic ---------------------------------------------------------------


def test_logs_unknown_when_source_is_unavailable():
    result = summary._summarize_logs({"source": "unavailable"}, [])
    assert result["status"] == "unknown"


def test_logs_healthy_with_zero_errors():
    result = summary._summarize_logs(
        {"source": "journalctl", "warning_count": 5, "error_count": 0, "truncated": False}, []
    )
    assert result["status"] == "healthy"


def test_logs_warning_at_error_warning_threshold():
    result = summary._summarize_logs(
        {
            "source": "journalctl",
            "warning_count": 0,
            "error_count": summary._LOG_ERROR_WARNING_COUNT,
            "truncated": False,
        },
        [],
    )
    assert result["status"] == "warning"


def test_logs_critical_at_error_critical_threshold():
    result = summary._summarize_logs(
        {
            "source": "journalctl",
            "warning_count": 0,
            "error_count": summary._LOG_ERROR_CRITICAL_COUNT,
            "truncated": False,
        },
        [],
    )
    assert result["status"] == "critical"


def test_logs_evidence_includes_recent_entries_content():
    result = summary._summarize_logs(
        {
            "source": "journalctl",
            "warning_count": 1,
            "error_count": 0,
            "truncated": False,
            "recent_entries": [
                {
                    "timestamp": "2026-07-15T10:00:00+00:00",
                    "severity": "warning",
                    "unit": "sshd.service",
                    "message": "Accepted publickey for ubuntu",
                }
            ],
        },
        [],
    )
    assert result["evidence"]["recent_entries"] == [
        {
            "timestamp": "2026-07-15T10:00:00+00:00",
            "severity": "warning",
            "unit": "sshd.service",
            "message": "Accepted publickey for ubuntu",
        }
    ]


# --- system / scheduled_jobs: no thresholds apply -------------------------------------


def test_system_is_always_healthy_when_available():
    result = summary._summarize_system(
        {
            "hostname": "host",
            "operating_system": "Ubuntu 24.04",
            "kernel_version": "6.8.0",
            "architecture": "x86_64",
            "uptime_seconds": 3600.0,
        },
        [],
    )
    assert result["status"] == "healthy"
    assert "Ubuntu 24.04" in result["headline"]
    assert "up 1h 0m" in result["headline"]


def test_scheduled_jobs_healthy_when_counts_present():
    result = summary._summarize_scheduled_jobs({"cron_job_count": 3, "timer_count": 2}, [])
    assert result["status"] == "healthy"


def test_scheduled_jobs_unknown_when_no_counts_present():
    result = summary._summarize_scheduled_jobs({}, [])
    assert result["status"] == "unknown"


def test_scheduled_jobs_evidence_includes_cron_jobs_and_timers():
    result = summary._summarize_scheduled_jobs(
        {
            "cron_job_count": 1,
            "timer_count": 1,
            "cron_jobs": [
                {
                    "user": "root",
                    "schedule": "0 2 * * *",
                    "command": "/usr/local/bin/backup.sh",
                    "source_file": "/etc/crontab",
                }
            ],
            "systemd_timers": [{"name": "apt-daily.timer", "unit": "apt-daily.service"}],
        },
        [],
    )
    assert result["evidence"]["cron_jobs"] == [
        {
            "user": "root",
            "schedule": "0 2 * * *",
            "command": "/usr/local/bin/backup.sh",
        }
    ]
    assert result["evidence"]["systemd_timers"] == [
        {"name": "apt-daily.timer", "unit": "apt-daily.service"}
    ]


def test_scheduled_jobs_evidence_redacts_secrets_in_cron_command():
    result = summary._summarize_scheduled_jobs(
        {
            "cron_job_count": 1,
            "timer_count": 0,
            "cron_jobs": [
                {
                    "user": "root",
                    "schedule": "0 2 * * *",
                    "command": "curl -H 'Authorization: Bearer abc123' https://example.com",
                    "source_file": "/etc/crontab",
                }
            ],
        },
        [],
    )
    command = result["evidence"]["cron_jobs"][0]["command"]
    assert "abc123" not in command
    assert "[REDACTED]" in command


# --- users status logic ---------------------------------------------------------------


def test_users_unknown_when_no_data_present():
    result = summary._summarize_users({}, [])
    assert result["status"] == "unknown"


def test_users_healthy_when_accounts_present():
    result = summary._summarize_users(
        {"users": [{"name": "root", "uid": 0, "is_system_account": True}]}, []
    )
    assert result["status"] == "healthy"


def test_users_headline_counts_human_vs_system_accounts():
    result = summary._summarize_users(
        {
            "users": [
                {"name": "root", "uid": 0, "is_system_account": True},
                {"name": "syslog", "uid": 104, "is_system_account": True},
                {"name": "ubuntu", "uid": 1000, "is_system_account": False},
            ],
            "logged_in_sessions": [],
        },
        [],
    )
    assert "3 user account(s) (1 human)" in result["headline"]


def test_users_highlight_names_logged_in_sessions():
    result = summary._summarize_users(
        {
            "users": [],
            "logged_in_sessions": [
                {"name": "ubuntu", "terminal": "pts/0", "login_time": "x", "host": None}
            ],
        },
        [],
    )
    assert any("Currently logged in: ubuntu" in h for h in result["highlights"])


def test_users_no_login_highlight_when_nobody_is_logged_in():
    result = summary._summarize_users({"users": [], "logged_in_sessions": []}, [])
    assert result["highlights"] == []


def test_users_evidence_includes_full_account_and_session_lists():
    result = summary._summarize_users(
        {
            "users": [
                {
                    "name": "ubuntu",
                    "uid": 1000,
                    "gid": 1000,
                    "home": "/home/ubuntu",
                    "shell": "/bin/bash",
                    "is_system_account": False,
                }
            ],
            "logged_in_sessions": [
                {
                    "name": "ubuntu",
                    "terminal": "pts/0",
                    "login_time": "2026-07-17 10:02",
                    "host": "192.168.1.5",
                }
            ],
            "recent_logins": [
                {"name": "ubuntu", "terminal": "pts/0", "detail": "still logged in"}
            ],
        },
        [],
    )
    assert result["evidence"]["user_count"] == 1
    assert result["evidence"]["human_user_count"] == 1
    assert result["evidence"]["users"][0]["home"] == "/home/ubuntu"
    assert result["evidence"]["logged_in_sessions"][0]["host"] == "192.168.1.5"
    assert result["evidence"]["recent_logins"][0]["detail"] == "still logged in"


# --- formatting helpers -----------------------------------------------------------------


def test_format_uptime_days_and_hours():
    assert summary._format_uptime(446417) == "5d 4h"


def test_format_uptime_hours_and_minutes():
    assert summary._format_uptime(3660) == "1h 1m"


def test_format_uptime_minutes_only():
    assert summary._format_uptime(120) == "2m"


def test_format_bytes_scales_units():
    assert summary._format_bytes(500) == "500 B"
    assert summary._format_bytes(1536) == "1.5 KB"
    assert summary._format_bytes(1048576) == "1.0 MB"


def test_format_bytes_handles_none():
    assert summary._format_bytes(None) == "unknown size"


def test_join_names_under_the_limit():
    assert summary._join_names(["a", "b"]) == "a, b"


def test_join_names_over_the_limit():
    names = [f"svc{i}" for i in range(7)]
    result = summary._join_names(names, limit=5)
    assert result == "svc0, svc1, svc2, svc3, svc4, and 2 more"


# --- real, unmocked run_scan() integration -------------------------------------------


def test_summarize_snapshot_handles_a_real_run_scan_result():
    """summarize_snapshot() is pure data transformation with no OS
    dependency of its own, so — unlike collector integration tests —
    this can run on any platform: it exercises the real, current
    run_scan() (which itself degrades gracefully on non-Linux systems)
    together with the real Summary Engine, with nothing mocked.
    """
    from nodeiq.core.coordinator import run_scan

    real_snapshot = run_scan()

    result = summary.summarize_snapshot(real_snapshot)

    assert set(result["sections"].keys()) == set(_ALL_SECTIONS)
    for section_name in _ALL_SECTIONS:
        section = result["sections"][section_name]
        assert section["status"] in ("healthy", "warning", "critical", "unknown")
        assert isinstance(section["highlights"], list)
        assert isinstance(section["concerns"], list)
        assert isinstance(section["evidence"], dict)
