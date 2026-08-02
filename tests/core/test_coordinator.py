"""Unit tests for nodeiq.core.coordinator.run_scan.

Everything here uses fake "collector modules" (simple objects with a
`collect(context)` method and a `__name__` attribute, standing in for a
real collector module) instead of the real `system`/`cpu_memory`
collectors — so these tests verify the coordinator's own orchestration
logic (running collectors, aggregating errors, building metadata) without
depending on the real machine's actual hostname, memory, or load. See
tests/core/test_coordinator_integration.py for a test against the real
collectors.
"""

import pytest

from nodeiq import __version__ as nodeiq_version
from nodeiq.core import coordinator
from nodeiq.core.collector import CollectorResult


class _FakeCollectorModule:
    """Stands in for a real collector module: something with a `collect`
    function and a dotted `__name__`, which is all `run_scan` needs."""

    def __init__(self, name: str, collect_fn):
        self.__name__ = f"nodeiq.collectors.{name}"
        self.collect = collect_fn


def _succeeding_collector(name: str, data: dict, errors: list | None = None):
    def collect(context):
        return CollectorResult(
            collector_name=name, data=data, errors=errors or [], duration_ms=1.0
        )

    return _FakeCollectorModule(name, collect)


def _crashing_collector(name: str, exception: Exception):
    def collect(context):
        raise exception

    return _FakeCollectorModule(name, collect)


# --- run_scan(): basic orchestration -----------------------------------------


def test_run_scan_executes_every_registered_collector(monkeypatch):
    # Uses "system"/"cpu_memory"/"processes" as the fake names (not
    # "alpha"/"beta"/"gamma") so this still satisfies _validate_snapshot's
    # required-section check — that check is exercised separately below.
    calls = []

    def make_tracking_collector(name):
        def collect(context):
            calls.append(name)
            return CollectorResult(
                collector_name=name, data={}, errors=[], duration_ms=1.0
            )

        return _FakeCollectorModule(name, collect)

    monkeypatch.setattr(
        coordinator,
        "_REGISTERED_COLLECTORS",
        [
            make_tracking_collector("system"),
            make_tracking_collector("cpu_memory"),
            make_tracking_collector("processes"),
            make_tracking_collector("disk"),
            make_tracking_collector("services"),
            make_tracking_collector("scheduled_jobs"),
            make_tracking_collector("permissions"),
            make_tracking_collector("network"),
            make_tracking_collector("logs"),
            make_tracking_collector("users"),
        ],
    )

    coordinator.run_scan()

    assert calls == [
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


def test_run_scan_assembles_data_from_every_collector_under_its_own_name(monkeypatch):
    monkeypatch.setattr(
        coordinator,
        "_REGISTERED_COLLECTORS",
        [
            _succeeding_collector("system", {"hostname": "myhost"}),
            _succeeding_collector("cpu_memory", {"memory_used_bytes": 123}),
            _succeeding_collector("processes", {"process_count": 42}),
            _succeeding_collector("disk", {"filesystems": []}),
            _succeeding_collector("services", {"running_services_count": 10}),
            _succeeding_collector("scheduled_jobs", {"cron_job_count": 5}),
            _succeeding_collector("permissions", {"checked_paths": []}),
            _succeeding_collector("network", {"interfaces": []}),
            _succeeding_collector("logs", {"source": "journalctl"}),
            _succeeding_collector("users", {}),
        ],
    )

    snapshot = coordinator.run_scan()

    assert snapshot["system"] == {"hostname": "myhost"}
    assert snapshot["cpu_memory"] == {"memory_used_bytes": 123}
    assert snapshot["processes"] == {"process_count": 42}
    assert snapshot["disk"] == {"filesystems": []}
    assert snapshot["services"] == {"running_services_count": 10}
    assert snapshot["scheduled_jobs"] == {"cron_job_count": 5}
    assert snapshot["permissions"] == {"checked_paths": []}
    assert snapshot["network"] == {"interfaces": []}
    assert snapshot["logs"] == {"source": "journalctl"}


def test_run_scan_returns_expected_top_level_sections(monkeypatch):
    monkeypatch.setattr(
        coordinator,
        "_REGISTERED_COLLECTORS",
        [
            _succeeding_collector("system", {}),
            _succeeding_collector("cpu_memory", {}),
            _succeeding_collector("processes", {}),
            _succeeding_collector("disk", {}),
            _succeeding_collector("services", {}),
            _succeeding_collector("scheduled_jobs", {}),
            _succeeding_collector("permissions", {}),
            _succeeding_collector("network", {}),
            _succeeding_collector("logs", {}),
            _succeeding_collector("users", {}),
        ],
    )

    snapshot = coordinator.run_scan()

    assert set(snapshot.keys()) == {
        "metadata",
        "collection_errors",
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
    }


# --- collection_errors aggregation -------------------------------------------


def test_run_scan_aggregates_errors_reported_by_a_collector(monkeypatch):
    error_entry = {
        "message": "could not read something",
        "severity": "error",
        "exception_type": "ValueError",
    }
    monkeypatch.setattr(
        coordinator,
        "_REGISTERED_COLLECTORS",
        [
            _succeeding_collector("system", {"hostname": None}, errors=[error_entry]),
            _succeeding_collector("cpu_memory", {}),
            _succeeding_collector("processes", {}),
            _succeeding_collector("disk", {}),
            _succeeding_collector("services", {}),
            _succeeding_collector("scheduled_jobs", {}),
            _succeeding_collector("permissions", {}),
            _succeeding_collector("network", {}),
            _succeeding_collector("logs", {}),
            _succeeding_collector("users", {}),
        ],
    )

    snapshot = coordinator.run_scan()

    assert snapshot["collection_errors"] == {"system": [error_entry]}


def test_run_scan_collection_errors_is_empty_when_nothing_went_wrong(monkeypatch):
    monkeypatch.setattr(
        coordinator,
        "_REGISTERED_COLLECTORS",
        [
            _succeeding_collector("system", {}),
            _succeeding_collector("cpu_memory", {}),
            _succeeding_collector("processes", {}),
            _succeeding_collector("disk", {}),
            _succeeding_collector("services", {}),
            _succeeding_collector("scheduled_jobs", {}),
            _succeeding_collector("permissions", {}),
            _succeeding_collector("network", {}),
            _succeeding_collector("logs", {}),
            _succeeding_collector("users", {}),
        ],
    )

    snapshot = coordinator.run_scan()

    assert snapshot["collection_errors"] == {}


def test_run_scan_continues_and_records_a_crash_when_a_collector_raises(monkeypatch):
    monkeypatch.setattr(
        coordinator,
        "_REGISTERED_COLLECTORS",
        [
            _crashing_collector("system", RuntimeError("boom")),
            _succeeding_collector("cpu_memory", {"memory_used_bytes": 123}),
            _succeeding_collector("processes", {}),
            _succeeding_collector("disk", {}),
            _succeeding_collector("services", {}),
            _succeeding_collector("scheduled_jobs", {}),
            _succeeding_collector("permissions", {}),
            _succeeding_collector("network", {}),
            _succeeding_collector("logs", {}),
            _succeeding_collector("users", {}),
        ],
    )

    snapshot = coordinator.run_scan()

    assert snapshot["system"] is None
    assert snapshot["cpu_memory"] == {"memory_used_bytes": 123}
    assert len(snapshot["collection_errors"]["system"]) == 1
    assert snapshot["collection_errors"]["system"][0]["exception_type"] == "RuntimeError"
    assert "boom" in snapshot["collection_errors"]["system"][0]["message"]


# --- metadata -----------------------------------------------------------------


def test_run_scan_populates_metadata_fields(monkeypatch):
    monkeypatch.setattr(
        coordinator,
        "_REGISTERED_COLLECTORS",
        [
            _succeeding_collector("system", {"hostname": "myhost"}),
            _succeeding_collector("cpu_memory", {}),
            _succeeding_collector("processes", {}),
            _succeeding_collector("disk", {}),
            _succeeding_collector("services", {}),
            _succeeding_collector("scheduled_jobs", {}),
            _succeeding_collector("permissions", {}),
            _succeeding_collector("network", {}),
            _succeeding_collector("logs", {}),
            _succeeding_collector("users", {}),
        ],
    )

    snapshot = coordinator.run_scan()
    metadata = snapshot["metadata"]

    assert metadata["collector_count"] == 10
    assert metadata["nodeiq_version"] == nodeiq_version
    assert metadata["hostname"] == "myhost"
    assert metadata["scan_duration_ms"] >= 0
    assert isinstance(metadata["scan_timestamp"], str)


def test_run_scan_metadata_hostname_is_none_when_system_data_has_no_hostname(
    monkeypatch,
):
    monkeypatch.setattr(
        coordinator,
        "_REGISTERED_COLLECTORS",
        [
            _succeeding_collector("system", {}),
            _succeeding_collector("cpu_memory", {}),
            _succeeding_collector("processes", {}),
            _succeeding_collector("disk", {}),
            _succeeding_collector("services", {}),
            _succeeding_collector("scheduled_jobs", {}),
            _succeeding_collector("permissions", {}),
            _succeeding_collector("network", {}),
            _succeeding_collector("logs", {}),
            _succeeding_collector("users", {}),
        ],
    )

    snapshot = coordinator.run_scan()

    assert snapshot["metadata"]["hostname"] is None


def test_run_scan_metadata_hostname_is_none_when_system_collector_crashes(monkeypatch):
    monkeypatch.setattr(
        coordinator,
        "_REGISTERED_COLLECTORS",
        [
            _crashing_collector("system", RuntimeError("boom")),
            _succeeding_collector("cpu_memory", {}),
            _succeeding_collector("processes", {}),
            _succeeding_collector("disk", {}),
            _succeeding_collector("services", {}),
            _succeeding_collector("scheduled_jobs", {}),
            _succeeding_collector("permissions", {}),
            _succeeding_collector("network", {}),
            _succeeding_collector("logs", {}),
            _succeeding_collector("users", {}),
        ],
    )

    snapshot = coordinator.run_scan()

    assert snapshot["metadata"]["hostname"] is None


# --- snapshot validation -------------------------------------------------------


def test_validate_snapshot_passes_when_all_required_sections_are_present():
    coordinator._validate_snapshot(
        {
            "metadata": {},
            "system": {},
            "cpu_memory": {},
            "processes": {},
            "disk": {},
            "services": {},
            "scheduled_jobs": {},
            "permissions": {},
            "network": {},
            "logs": {},
            "users": {},
            "collection_errors": {},
        }
    )


def test_validate_snapshot_raises_when_a_required_section_is_missing():
    with pytest.raises(ValueError, match="cpu_memory"):
        coordinator._validate_snapshot(
            {"metadata": {}, "system": {}, "collection_errors": {}}
        )


# --- run_selected_collectors(): on-demand, selective collection ----------------------


def test_run_selected_collectors_runs_only_the_requested_ones(monkeypatch):
    calls = []

    def make_tracking_collector(name):
        def collect(context):
            calls.append(name)
            return CollectorResult(collector_name=name, data={}, errors=[], duration_ms=1.0)

        return _FakeCollectorModule(name, collect)

    monkeypatch.setattr(
        coordinator,
        "_COLLECTOR_BY_NAME",
        {
            "disk": make_tracking_collector("disk"),
            "cpu_memory": make_tracking_collector("cpu_memory"),
            "users": make_tracking_collector("users"),
        },
    )

    coordinator.run_selected_collectors(["disk", "users"])

    assert calls == ["disk", "users"]


def test_run_selected_collectors_returns_a_partial_snapshot(monkeypatch):
    monkeypatch.setattr(
        coordinator,
        "_COLLECTOR_BY_NAME",
        {"disk": _succeeding_collector("disk", {"highest_disk_usage_percent": 60.0})},
    )

    snapshot = coordinator.run_selected_collectors(["disk"])

    assert set(snapshot.keys()) == {"metadata", "collection_errors", "disk"}
    assert snapshot["disk"] == {"highest_disk_usage_percent": 60.0}


def test_run_selected_collectors_marks_metadata_as_on_demand(monkeypatch):
    monkeypatch.setattr(
        coordinator, "_COLLECTOR_BY_NAME", {"disk": _succeeding_collector("disk", {})}
    )

    snapshot = coordinator.run_selected_collectors(["disk"])

    assert snapshot["metadata"]["collection_mode"] == "on_demand"
    assert snapshot["metadata"]["collectors_run"] == ["disk"]
    assert snapshot["metadata"]["collector_count"] == 1


def test_run_selected_collectors_isolates_a_crash_to_one_collector(monkeypatch):
    monkeypatch.setattr(
        coordinator,
        "_COLLECTOR_BY_NAME",
        {
            "disk": _crashing_collector("disk", RuntimeError("boom")),
            "users": _succeeding_collector("users", {"users": []}),
        },
    )

    snapshot = coordinator.run_selected_collectors(["disk", "users"])

    assert snapshot["disk"] is None
    assert snapshot["users"] == {"users": []}
    assert snapshot["collection_errors"]["disk"][0]["exception_type"] == "RuntimeError"


def test_run_selected_collectors_raises_on_an_unknown_collector_name():
    with pytest.raises(ValueError, match="not_a_real_collector"):
        coordinator.run_selected_collectors(["not_a_real_collector"])


def test_run_selected_collectors_does_not_require_every_section_present(monkeypatch):
    # Unlike run_scan(), this never calls _validate_snapshot() — a
    # partial result missing 9 of 10 sections is the whole point, not a
    # bug to catch.
    monkeypatch.setattr(
        coordinator, "_COLLECTOR_BY_NAME", {"disk": _succeeding_collector("disk", {})}
    )

    snapshot = coordinator.run_selected_collectors(["disk"])

    assert "system" not in snapshot
    assert "logs" not in snapshot
