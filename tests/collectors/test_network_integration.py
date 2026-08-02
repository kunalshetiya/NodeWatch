"""End-to-end integration test for nodeiq.collectors.network.

Unlike test_network.py, nothing here is mocked — this calls the real
`collect()`, which runs the real `ip`/`ss` (and firewall tools, if any)
on this machine. `ip`/`ss` behavior is only verified against the real
target environment here (see DECISIONS.md ADR-002), so this test is
skipped everywhere else.
"""

import platform

import pytest

from datetime import datetime, timezone

from nodeiq.collectors import network
from nodeiq.core.collector import CollectorContext

pytestmark = pytest.mark.skipif(
    platform.system() != "Linux",
    reason="requires a real Linux system (see DECISIONS.md ADR-002); "
    "run this inside the Multipass Ubuntu VM",
)


def test_collect_produces_a_sane_summary_on_a_real_linux_system():
    context = CollectorContext(scan_start_time=datetime.now(timezone.utc))

    result = network.collect(context)

    assert result.collector_name == "network"

    # Every real Linux system has at least a loopback interface.
    assert len(result.data["interfaces"]) >= 1
    assert any(iface["name"] == "lo" for iface in result.data["interfaces"])

    for iface in result.data["interfaces"]:
        assert iface["state"] in ("up", "down")
        assert isinstance(iface["ipv4_addresses"], list)
        assert isinstance(iface["ipv6_addresses"], list)

    # The default route, if present, is well-formed.
    if result.data["default_route"] is not None:
        assert result.data["default_route"]["gateway"]
        assert result.data["default_route"]["interface"]

    # Listening ports parse correctly.
    for port in result.data["listening_ports"]:
        assert port["protocol"] in ("tcp", "udp")
        assert isinstance(port["port"], int)
        assert 0 < port["port"] <= 65535

    # Firewall detection never errors, even if nothing is detectable.
    assert "tool" in result.data["firewall"]
    assert "enabled" in result.data["firewall"]
