"""Integration test for nodeiq.collectors.system.

Unlike test_system.py, nothing here is mocked — this calls the real
`collect()` against the real machine's `hostname`, `uname`, `/etc/os-
release`, and `/proc/uptime`. That only makes sense on a real Linux
system (see DECISIONS.md ADR-002: development and integration testing
happens in a Multipass Ubuntu VM), so this test is skipped automatically
everywhere else rather than failing for an unrelated reason (e.g. macOS
has no `/proc`).
"""

import platform
from datetime import datetime, timezone

import pytest

from nodeiq.collectors import system
from nodeiq.core.collector import CollectorContext

pytestmark = pytest.mark.skipif(
    platform.system() != "Linux",
    reason="requires a real Linux system (see DECISIONS.md ADR-002); "
    "run this inside the Multipass Ubuntu VM",
)


def test_collect_populates_every_field_on_a_real_linux_system():
    context = CollectorContext(scan_start_time=datetime.now(timezone.utc))

    result = system.collect(context)

    assert result.errors == [], f"unexpected collection errors: {result.errors}"
    assert result.data["hostname"]
    assert result.data["operating_system"]
    assert result.data["kernel_version"]
    assert result.data["architecture"]
    assert result.data["uptime_seconds"] > 0
