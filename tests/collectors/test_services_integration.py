"""End-to-end integration test for nodeiq.collectors.services.

Unlike test_services.py, nothing here is mocked — this calls the real
`collect()`, which runs the real `systemctl` on this machine. That only
makes sense on a real Linux system with systemd (see DECISIONS.md
ADR-002), so this test is skipped everywhere else.
"""

import platform

import pytest

from datetime import datetime, timezone

from nodeiq.collectors import services
from nodeiq.core.collector import CollectorContext

pytestmark = pytest.mark.skipif(
    platform.system() != "Linux",
    reason="requires a real Linux system with systemd (see DECISIONS.md ADR-002); "
    "run this inside the Multipass Ubuntu VM",
)


def test_collect_produces_a_sane_summary_on_a_real_linux_system():
    context = CollectorContext(scan_start_time=datetime.now(timezone.utc))

    result = services.collect(context)

    assert result.collector_name == "services"
    assert result.data["systemd_available"] is True
    assert result.data["running_services_count"] > 0
    assert result.data["failed_services_count"] >= 0
    assert result.data["enabled_services_count"] >= 0
    assert isinstance(result.data["running_services"], list)
    assert len(result.data["running_services"]) == result.data["running_services_count"]
    assert isinstance(result.data["failed_services"], list)
    assert isinstance(result.data["restarting_services"], list)
