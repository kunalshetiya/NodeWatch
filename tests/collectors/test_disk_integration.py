"""End-to-end integration test for nodeiq.collectors.disk.

Unlike test_disk.py, nothing here is mocked — this calls the real
`collect()`, which runs the real `df` on this machine. `df` exists on
both Linux and macOS, but its exact flag support and output format is
only guaranteed to match this collector's expectations on Linux (see
DECISIONS.md ADR-002), so this test is skipped everywhere else.
"""

import platform

import pytest

from datetime import datetime, timezone

from nodeiq.collectors import disk
from nodeiq.core.collector import CollectorContext

pytestmark = pytest.mark.skipif(
    platform.system() != "Linux",
    reason="requires a real Linux system (see DECISIONS.md ADR-002); "
    "run this inside the Multipass Ubuntu VM",
)


def test_collect_produces_a_sane_summary_on_a_real_linux_system():
    context = CollectorContext(scan_start_time=datetime.now(timezone.utc))

    result = disk.collect(context)

    assert result.collector_name == "disk"
    assert result.success is True

    filesystems = result.data["filesystems"]
    assert len(filesystems) >= 1

    for fs in filesystems:
        assert 0.0 <= fs["usage_percent"] <= 100.0
        assert fs["total_bytes"] >= 0
        assert fs["used_bytes"] >= 0
        assert fs["available_bytes"] >= 0
        if fs["inode_usage_percent"] is not None:
            assert 0.0 <= fs["inode_usage_percent"] <= 100.0

    assert 0.0 <= result.data["highest_disk_usage_percent"] <= 100.0
