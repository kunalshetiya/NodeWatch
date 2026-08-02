"""End-to-end integration test for nodeiq.collectors.permissions.

Unlike test_permissions.py, nothing here is mocked — this calls the real
`collect()`, which checks the real configured paths on this machine.
`/etc/passwd` and friends exist on both Linux and macOS, but exact
ownership/permission conventions are only verified against the real
target environment here (see DECISIONS.md ADR-002), so this test is
skipped everywhere else.
"""

import platform

import pytest

from datetime import datetime, timezone

from nodeiq.collectors import permissions
from nodeiq.core.collector import CollectorContext

pytestmark = pytest.mark.skipif(
    platform.system() != "Linux",
    reason="requires a real Linux system (see DECISIONS.md ADR-002); "
    "run this inside the Multipass Ubuntu VM",
)


def test_collect_checks_every_configured_path_on_a_real_linux_system():
    context = CollectorContext(scan_start_time=datetime.now(timezone.utc))

    result = permissions.collect(context)

    assert result.collector_name == "permissions"
    assert len(result.data["checked_paths"]) == len(permissions._CHECKED_PATHS)

    # /etc/passwd always exists on a real Linux system.
    passwd_entry = next(
        entry for entry in result.data["checked_paths"] if entry["path"] == "/etc/passwd"
    )
    assert passwd_entry["exists"] is True
    assert passwd_entry["owner"] == "root"
    assert passwd_entry["mode"] is not None
    assert passwd_entry["world_writable"] is False
