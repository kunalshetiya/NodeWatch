"""End-to-end integration test for nodeiq.collectors.logs.

Unlike test_logs.py, nothing here is mocked — this calls the real
`collect()`, which runs the real `journalctl` on this machine. This
requires a real systemd journal (see DECISIONS.md ADR-002), so this test
is skipped everywhere else.
"""

import platform

import pytest

from datetime import datetime, timezone

from nodeiq.collectors import logs
from nodeiq.core.collector import CollectorContext

pytestmark = pytest.mark.skipif(
    platform.system() != "Linux",
    reason="requires a real Linux system with a systemd journal (see DECISIONS.md ADR-002); "
    "run this inside the Multipass Ubuntu VM",
)


def test_collect_produces_a_bounded_sane_summary_on_a_real_linux_system():
    context = CollectorContext(scan_start_time=datetime.now(timezone.utc))

    result = logs.collect(context)

    assert result.collector_name == "logs"
    assert result.data["source"] == "journalctl"
    assert result.data["warning_count"] >= 0
    assert result.data["error_count"] >= 0
    assert len(result.data["recent_entries"]) <= logs._MAX_ENTRIES
    assert result.data["warning_count"] + result.data["error_count"] == len(
        result.data["recent_entries"]
    )

    for entry in result.data["recent_entries"]:
        assert entry["severity"] in ("warning", "error")
        assert entry["unit"]
        assert isinstance(entry["message"], str)
