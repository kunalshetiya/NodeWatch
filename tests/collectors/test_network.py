"""Unit tests for nodeiq.collectors.network.

All command execution is mocked — no test here depends on the real
machine's actual network configuration, per PROJECT_RULES.md Section 11
and docs/collector_guidelines.md's Testing Expectations. See
tests/collectors/test_network_integration.py for a test against the
real `ip`/`ss` on a real Linux system.
"""

from datetime import datetime, timezone

import pytest

from nodeiq.collectors import network
from nodeiq.core.collector import CollectorContext
from nodeiq.core.result import CommandResult

# --- _extract_flags / _parse_interface_states -----------------------------------

_SAMPLE_LINK = (
    "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN mode DEFAULT "
    "group default qlen 1000\\    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00\n"
    "2: enp0s1: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP mode "
    "DEFAULT group default qlen 1000\\    link/ether 52:54:00:1e:f6:72 brd ff:ff:ff:ff:ff:ff\n"
    "3: dummy0: <BROADCAST,MULTICAST> mtu 1500 qdisc noop state DOWN mode DEFAULT "
    "group default qlen 1000\\    link/ether 00:00:00:00:00:00 brd ff:ff:ff:ff:ff:ff\n"
)


def test_extract_flags_parses_the_bracket_contents():
    line = "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536"
    assert network._extract_flags(line) == ["LOOPBACK", "UP", "LOWER_UP"]


def test_extract_flags_returns_empty_list_when_no_bracket_present():
    assert network._extract_flags("no brackets here") == []


def test_parse_interface_states_uses_up_flag_not_state_keyword():
    result = network._parse_interface_states(_SAMPLE_LINK)

    # lo's "state" keyword says UNKNOWN, but its flags contain UP —
    # this must report "up", not the kernel's UNKNOWN operstate.
    assert result["lo"] == "up"
    assert result["enp0s1"] == "up"
    assert result["dummy0"] == "down"


def test_parse_interface_states_raises_on_a_malformed_line():
    with pytest.raises(ValueError):
        network._parse_interface_states("onlyonetoken\n")


# --- _parse_interface_addresses --------------------------------------------------

_SAMPLE_ADDR = (
    "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever\n"
    "1: lo    inet6 ::1/128 scope host noprefixroute \\       valid_lft forever preferred_lft forever\n"
    "2: enp0s1    inet 192.168.252.2/24 metric 100 brd 192.168.252.255 scope global "
    "dynamic enp0s1\\       valid_lft 2656sec preferred_lft 2656sec\n"
    "2: enp0s1    inet6 fe80::5054:ff:fe1e:f672/64 scope link \\       valid_lft forever "
    "preferred_lft forever\n"
)


def test_parse_interface_addresses_groups_by_interface_and_family():
    result = network._parse_interface_addresses(_SAMPLE_ADDR)

    assert result["lo"]["ipv4_addresses"] == ["127.0.0.1/8"]
    assert result["lo"]["ipv6_addresses"] == ["::1/128"]
    assert result["enp0s1"]["ipv4_addresses"] == ["192.168.252.2/24"]
    assert result["enp0s1"]["ipv6_addresses"] == ["fe80::5054:ff:fe1e:f672/64"]


def test_parse_interface_addresses_raises_on_a_malformed_line():
    with pytest.raises(ValueError):
        network._parse_interface_addresses("only two tokens\n")


# --- _merge_interfaces -----------------------------------------------------------


def test_merge_interfaces_combines_state_and_addresses():
    states = {"lo": "up", "enp0s1": "up"}
    addresses = {"lo": {"ipv4_addresses": ["127.0.0.1/8"], "ipv6_addresses": []}}

    result = network._merge_interfaces(states, addresses)

    assert result == [
        {
            "name": "lo",
            "state": "up",
            "ipv4_addresses": ["127.0.0.1/8"],
            "ipv6_addresses": [],
        },
        {
            "name": "enp0s1",
            "state": "up",
            "ipv4_addresses": [],
            "ipv6_addresses": [],
        },
    ]


# --- _parse_default_route --------------------------------------------------------


def test_parse_default_route_extracts_gateway_and_interface():
    sample = "default via 192.168.252.1 dev enp0s1 proto dhcp src 192.168.252.2 metric 100\n"

    result = network._parse_default_route(sample)

    assert result == {"gateway": "192.168.252.1", "interface": "enp0s1"}


def test_parse_default_route_returns_none_when_no_default_route_exists():
    assert network._parse_default_route("") is None
    assert network._parse_default_route("   \n") is None


def test_parse_default_route_raises_when_line_is_malformed():
    with pytest.raises(ValueError):
        network._parse_default_route("default dev enp0s1\n")


# --- _parse_ss_output / _parse_process_field -------------------------------------

_SAMPLE_SS_WITH_PROCESS = (
    "Netid State  Recv-Q Send-Q        Local Address:Port Peer Address:PortProcess\n"
    'tcp   LISTEN 0      4096                0.0.0.0:22        0.0.0.0:*    users:(("sshd",pid=851,fd=3),("systemd",pid=1,fd=91))\n'
    "udp   UNCONN 0      0      192.168.252.2%enp0s1:68        0.0.0.0:*    \n"
)


def test_parse_ss_output_extracts_protocol_port_and_process():
    result = network._parse_ss_output(_SAMPLE_SS_WITH_PROCESS)

    assert result[0] == {
        "protocol": "tcp",
        "local_address": "0.0.0.0",
        "port": 22,
        "process_name": "sshd",
        "pid": 851,
    }


def test_parse_ss_output_handles_scoped_addresses_and_missing_process():
    result = network._parse_ss_output(_SAMPLE_SS_WITH_PROCESS)

    assert result[1] == {
        "protocol": "udp",
        "local_address": "192.168.252.2%enp0s1",
        "port": 68,
        "process_name": None,
        "pid": None,
    }


def test_parse_ss_output_handles_ipv6_bracket_addresses():
    sample = 'tcp   LISTEN 0      4096                   [::]:22           [::]:*    users:(("sshd",pid=851,fd=4))\n'

    result = network._parse_ss_output(sample)

    assert result[0]["local_address"] == "[::]"
    assert result[0]["port"] == 22


def test_parse_ss_output_skips_the_header_line():
    sample = "Netid State  Recv-Q Send-Q        Local Address:Port Peer Address:PortProcess\n"
    assert network._parse_ss_output(sample) == []


def test_parse_ss_output_raises_on_a_malformed_line():
    with pytest.raises(ValueError):
        network._parse_ss_output("tcp LISTEN\n")


def test_parse_process_field_extracts_the_first_process():
    name, pid = network._parse_process_field(
        'users:(("sshd",pid=851,fd=3),("systemd",pid=1,fd=91))'
    )
    assert name == "sshd"
    assert pid == 851


def test_parse_process_field_returns_none_for_an_empty_field():
    assert network._parse_process_field("") == (None, None)


# --- collect() end-to-end -------------------------------------------------------


def _context() -> CollectorContext:
    return CollectorContext(scan_start_time=datetime.now(timezone.utc))


def _succeeding(command, stdout):
    return CommandResult(
        command=command,
        returncode=0,
        stdout=stdout,
        stderr="",
        duration_seconds=0.01,
        timed_out=False,
        error=None,
    )


def _failing(command):
    return CommandResult(
        command=command,
        returncode=1,
        stdout="",
        stderr="command not found",
        duration_seconds=0.01,
        timed_out=False,
        error="command not found",
    )


def _fake_run_command_factory(responses: dict):
    # Keyed by tuple(command) since a plain list isn't hashable.
    def fake_run_command(command, timeout):
        response = responses.get(tuple(command))
        if response is None:
            raise AssertionError(f"unexpected command: {command}")
        return response(command)

    return fake_run_command


def test_collect_merges_every_source_into_one_result(monkeypatch):
    responses = {
        tuple(network._LINK_COMMAND): lambda cmd: _succeeding(cmd, _SAMPLE_LINK),
        tuple(network._ADDR_COMMAND): lambda cmd: _succeeding(cmd, _SAMPLE_ADDR),
        tuple(network._DEFAULT_ROUTE_COMMAND): lambda cmd: _succeeding(
            cmd, "default via 192.168.252.1 dev enp0s1 proto dhcp src 192.168.252.2 metric 100\n"
        ),
        tuple(network._LISTENING_PORTS_COMMAND): lambda cmd: _succeeding(cmd, _SAMPLE_SS_WITH_PROCESS),
        tuple(network._UFW_STATUS_COMMAND): lambda cmd: _failing(cmd),
        tuple(network._NFT_LIST_COMMAND): lambda cmd: _failing(cmd),
        tuple(network._IPTABLES_LIST_COMMAND): lambda cmd: _failing(cmd),
    }
    monkeypatch.setattr(network, "run_command", _fake_run_command_factory(responses))

    result = network.collect(_context())

    assert result.collector_name == "network"
    assert result.errors == []
    assert len(result.data["interfaces"]) == 3
    assert result.data["default_route"] == {"gateway": "192.168.252.1", "interface": "enp0s1"}
    assert len(result.data["listening_ports"]) == 2
    assert result.data["firewall"] == {
        "tool": None,
        "enabled": None,
        "detection_note": "Could not run a firewall detection command: command not found",
    }


def test_collect_reports_empty_interfaces_when_link_command_fails(monkeypatch):
    responses = {
        tuple(network._LINK_COMMAND): lambda cmd: _failing(cmd),
        tuple(network._DEFAULT_ROUTE_COMMAND): lambda cmd: _succeeding(cmd, ""),
        tuple(network._LISTENING_PORTS_COMMAND): lambda cmd: _succeeding(cmd, ""),
        tuple(network._UFW_STATUS_COMMAND): lambda cmd: _failing(cmd),
        tuple(network._NFT_LIST_COMMAND): lambda cmd: _failing(cmd),
        tuple(network._IPTABLES_LIST_COMMAND): lambda cmd: _failing(cmd),
    }
    monkeypatch.setattr(network, "run_command", _fake_run_command_factory(responses))

    result = network.collect(_context())

    assert result.data["interfaces"] == []
    assert len(result.errors) == 1
    assert result.success is False


# --- _parse_ufw_status ------------------------------------------------------------


def test_parse_ufw_status_recognizes_active():
    assert network._parse_ufw_status("Status: active\n") is True


def test_parse_ufw_status_recognizes_inactive():
    # A naive "active" in line substring check would incorrectly match
    # here too, since "inactive" itself contains "active" as its last
    # six characters — this must come back False, not True.
    assert network._parse_ufw_status("Status: inactive\n") is False


def test_parse_ufw_status_returns_false_for_unexpected_output():
    assert network._parse_ufw_status("") is False
    assert network._parse_ufw_status("some unrelated output\n") is False


def test_collect_detects_ufw_when_available(monkeypatch):
    responses = {
        tuple(network._LINK_COMMAND): lambda cmd: _succeeding(cmd, _SAMPLE_LINK),
        tuple(network._ADDR_COMMAND): lambda cmd: _succeeding(cmd, _SAMPLE_ADDR),
        tuple(network._DEFAULT_ROUTE_COMMAND): lambda cmd: _succeeding(cmd, ""),
        tuple(network._LISTENING_PORTS_COMMAND): lambda cmd: _succeeding(cmd, ""),
        tuple(network._UFW_STATUS_COMMAND): lambda cmd: _succeeding(cmd, "Status: active\n"),
    }
    monkeypatch.setattr(network, "run_command", _fake_run_command_factory(responses))

    result = network.collect(_context())

    assert result.data["firewall"] == {"tool": "ufw", "enabled": True, "detection_note": None}


def test_collect_detects_ufw_inactive(monkeypatch):
    responses = {
        tuple(network._LINK_COMMAND): lambda cmd: _succeeding(cmd, _SAMPLE_LINK),
        tuple(network._ADDR_COMMAND): lambda cmd: _succeeding(cmd, _SAMPLE_ADDR),
        tuple(network._DEFAULT_ROUTE_COMMAND): lambda cmd: _succeeding(cmd, ""),
        tuple(network._LISTENING_PORTS_COMMAND): lambda cmd: _succeeding(cmd, ""),
        tuple(network._UFW_STATUS_COMMAND): lambda cmd: _succeeding(cmd, "Status: inactive\n"),
    }
    monkeypatch.setattr(network, "run_command", _fake_run_command_factory(responses))

    result = network.collect(_context())

    assert result.data["firewall"] == {"tool": "ufw", "enabled": False, "detection_note": None}


def test_collect_falls_back_to_nft_when_ufw_is_unavailable(monkeypatch):
    responses = {
        tuple(network._LINK_COMMAND): lambda cmd: _succeeding(cmd, _SAMPLE_LINK),
        tuple(network._ADDR_COMMAND): lambda cmd: _succeeding(cmd, _SAMPLE_ADDR),
        tuple(network._DEFAULT_ROUTE_COMMAND): lambda cmd: _succeeding(cmd, ""),
        tuple(network._LISTENING_PORTS_COMMAND): lambda cmd: _succeeding(cmd, ""),
        tuple(network._UFW_STATUS_COMMAND): lambda cmd: _failing(cmd),
        tuple(network._NFT_LIST_COMMAND): lambda cmd: _succeeding(cmd, "table inet filter { }\n"),
    }
    monkeypatch.setattr(network, "run_command", _fake_run_command_factory(responses))

    result = network.collect(_context())

    assert result.data["firewall"] == {"tool": "nft", "enabled": True, "detection_note": None}


def test_firewall_failure_reason_uses_result_error_when_present():
    result = CommandResult(
        command=network._IPTABLES_LIST_COMMAND,
        returncode=1,
        stdout="",
        stderr="",
        duration_seconds=0.01,
        timed_out=False,
        error="Permission denied",
    )
    assert network._firewall_failure_reason(result) == (
        "Could not run a firewall detection command: Permission denied"
    )


def test_firewall_failure_reason_falls_back_to_stderr():
    result = CommandResult(
        command=network._IPTABLES_LIST_COMMAND,
        returncode=1,
        stdout="",
        stderr="iptables: Permission denied (you must be root)\n",
        duration_seconds=0.01,
        timed_out=False,
        error=None,
    )
    assert network._firewall_failure_reason(result) == (
        "Could not run a firewall detection command: "
        "iptables: Permission denied (you must be root)"
    )


def test_firewall_failure_reason_generic_fallback_when_nothing_reported():
    result = CommandResult(
        command=network._IPTABLES_LIST_COMMAND,
        returncode=1,
        stdout="",
        stderr="",
        duration_seconds=0.01,
        timed_out=False,
        error=None,
    )
    assert network._firewall_failure_reason(result) == (
        "No firewall detection tool (ufw, nft, or iptables) could be run on this scan."
    )
