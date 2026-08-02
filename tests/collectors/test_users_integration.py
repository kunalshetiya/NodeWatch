"""End-to-end integration test for nodeiq.collectors.users.

Unlike test_users.py, nothing here is mocked — this calls the real
`collect()`, which reads the real /etc/passwd and runs the real `who`/
`last` on this machine. That only makes sense on a real Linux system
(see DECISIONS.md ADR-002), so this test is skipped automatically
everywhere else.
"""

import platform

import pytest

from datetime import datetime, timezone

from nodeiq.collectors import users
from nodeiq.core.collector import CollectorContext

pytestmark = pytest.mark.skipif(
    platform.system() != "Linux",
    reason="requires a real Linux system (see DECISIONS.md ADR-002); "
    "run this inside the Multipass Ubuntu VM",
)


def test_collect_produces_a_sane_summary_on_a_real_linux_system():
    context = CollectorContext(scan_start_time=datetime.now(timezone.utc))

    result = users.collect(context)

    assert result.collector_name == "users"
    assert result.data["users"] is not None
    assert len(result.data["users"]) > 0

    # root always exists on a real Linux system, uid 0.
    root = next(u for u in result.data["users"] if u["name"] == "root")
    assert root["uid"] == 0
    assert root["is_system_account"] is True

    # The account actually running this test is currently logged in
    # somewhere, or `who` genuinely has no sessions — both are valid
    # real states, so this only asserts the shape, not a specific count.
    assert isinstance(result.data["logged_in_sessions"], list)
    assert isinstance(result.data["recent_logins"], list)
