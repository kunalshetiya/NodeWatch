"""Integration test for nodeiq.collectors.cpu_memory.

Unlike test_cpu_memory.py, nothing here is mocked — this calls the real
`collect()` against the real machine's `/proc/meminfo` and `/proc/loadavg`.
That only makes sense on a real Linux system (see DECISIONS.md ADR-002:
development and integration testing happens in a Multipass Ubuntu VM), so
this test is skipped automatically everywhere else rather than failing for
an unrelated reason (e.g. macOS has no `/proc`).
"""

import platform
from datetime import datetime, timezone

import pytest

from nodeiq.collectors import cpu_memory
from nodeiq.core.collector import CollectorContext

pytestmark = pytest.mark.skipif(
    platform.system() != "Linux",
    reason="requires a real Linux system (see DECISIONS.md ADR-002); "
    "run this inside the Multipass Ubuntu VM",
)


def test_collect_populates_every_field_on_a_real_linux_system():
    context = CollectorContext(scan_start_time=datetime.now(timezone.utc))

    result = cpu_memory.collect(context)

    assert result.errors == [], f"unexpected collection errors: {result.errors}"
    assert isinstance(result.data["cpu_usage_percent"], float)
    assert 0.0 <= result.data["cpu_usage_percent"] <= 100.0
    assert result.data["memory_used_bytes"] > 0
    assert result.data["memory_available_bytes"] >= 0
    assert isinstance(result.data["memory_usage_percent"], float)
    assert isinstance(result.data["swap_used_bytes"], int)
    assert isinstance(result.data["swap_usage_percent"], float)
    assert isinstance(result.data["load_average_1m"], float)
    assert isinstance(result.data["load_average_5m"], float)
    assert isinstance(result.data["load_average_15m"], float)
