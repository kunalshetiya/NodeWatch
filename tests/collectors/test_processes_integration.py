"""End-to-end integration test for nodeiq.collectors.processes.

Unlike test_processes.py, nothing here is mocked — this calls the real
`collect()`, which reads the real `/proc` on this machine. That only
makes sense on a real Linux system (see DECISIONS.md ADR-002), so this
test is skipped automatically everywhere else.
"""

import platform

import pytest

from datetime import datetime, timezone

from nodeiq.collectors import processes
from nodeiq.core.collector import CollectorContext

pytestmark = pytest.mark.skipif(
    platform.system() != "Linux",
    reason="requires a real Linux system (see DECISIONS.md ADR-002); "
    "run this inside the Multipass Ubuntu VM",
)


def test_collect_produces_a_sane_summary_on_a_real_linux_system():
    context = CollectorContext(scan_start_time=datetime.now(timezone.utc))

    result = processes.collect(context)

    assert result.collector_name == "processes"
    assert result.success is True

    data = result.data
    assert data["process_count"] > 0
    assert data["zombie_count"] >= 0
    assert data["blocked_process_count"] >= 0

    top_by_memory = data["top_by_memory"]
    assert len(top_by_memory) <= 10
    memory_values = [p["memory_rss_bytes"] for p in top_by_memory]
    assert memory_values == sorted(memory_values, reverse=True)

    for entry in top_by_memory:
        assert isinstance(entry["pid"], int)
        assert isinstance(entry["process_name"], str)
        assert isinstance(entry["memory_rss_bytes"], int)
        assert isinstance(entry["owner"], str)
        assert isinstance(entry["state"], str)
        assert isinstance(entry["command"], str)
        assert entry["cpu_usage_percent"] is None or isinstance(
            entry["cpu_usage_percent"], float
        )

    top_by_cpu = data["top_by_cpu"]
    assert len(top_by_cpu) <= 10
    cpu_values = [p["cpu_usage_percent"] for p in top_by_cpu if p["cpu_usage_percent"] is not None]
    assert cpu_values == sorted(cpu_values, reverse=True)
