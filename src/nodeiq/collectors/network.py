"""Network collector: interfaces, routing, listening ports, and firewall.

Answers "what ports are open?" — one of NodeIQ's headline example
questions — plus basic interface and connectivity context. Follows the
same `CollectorContext` -> `collect()` -> `CollectorResult` pattern as
every other collector, using `nodeiq.core.runner.run_command` for `ip`
and `ss` (there is no `/proc` file that reports all of this cleanly).
See docs/network_collector.md for the full rationale, including why
firewall detection is necessarily best-effort.
"""

import re
import time

from nodeiq.core.collector import CollectorContext, CollectorResult
from nodeiq.core.errors import error_entry
from nodeiq.core.runner import command_failure_message, run_command

_LINK_COMMAND = ["ip", "-o", "link", "show"]
_ADDR_COMMAND = ["ip", "-o", "addr", "show"]
_DEFAULT_ROUTE_COMMAND = ["ip", "route", "show", "default"]
_LISTENING_PORTS_COMMAND = ["ss", "-tulpn"]

_UFW_STATUS_COMMAND = ["ufw", "status"]
_NFT_LIST_COMMAND = ["nft", "list", "ruleset"]
_IPTABLES_LIST_COMMAND = ["iptables", "-L", "-n"]

_PROCESS_FIELD_PATTERN = re.compile(r'"(?P<name>[^"]+)",pid=(?P<pid>\d+)')


def collect(context: CollectorContext) -> CollectorResult:
    """Gather interfaces, the default route, listening ports, and
    (best-effort) firewall status.

    Interfaces come from two independent `ip` invocations merged by
    name — if the link-state source fails, nothing about interfaces can
    be known at all; if only the address source fails, interface names
    and states are still returned. The default route and listening
    ports are each their own independent source. Firewall detection is
    explicitly best-effort (see docs/network_collector.md) and never
    contributes an error, since not detecting any of the three tools is
    a normal, expected outcome, not a failure.
    """
    start_time = time.monotonic()
    errors: list[dict] = []

    try:
        states = _get_interface_states(context)
    except ValueError as exc:
        errors.append(error_entry(exc))
        interfaces = []
    else:
        try:
            addresses = _get_interface_addresses(context)
        except ValueError as exc:
            addresses = {}
            errors.append(error_entry(exc))
        interfaces = _merge_interfaces(states, addresses)

    try:
        default_route = _get_default_route(context)
    except ValueError as exc:
        default_route = None
        errors.append(error_entry(exc))

    try:
        listening_ports = _get_listening_ports(context)
    except ValueError as exc:
        listening_ports = []
        errors.append(error_entry(exc))

    data = {
        "interfaces": interfaces,
        "default_route": default_route,
        "listening_ports": listening_ports,
        "firewall": _detect_firewall(context),
    }

    return CollectorResult(
        collector_name="network",
        data=data,
        errors=errors,
        duration_ms=(time.monotonic() - start_time) * 1000,
    )


def _get_interface_states(context: CollectorContext) -> dict:
    """Run `ip -o link show` and parse it into a dict mapping interface
    name to `"up"`/`"down"`.

    Raises `ValueError` if the command fails or its output doesn't parse.
    """
    result = run_command(_LINK_COMMAND, timeout=context.default_timeout)
    if not result.succeeded:
        raise ValueError(command_failure_message(_LINK_COMMAND, result))
    return _parse_interface_states(result.stdout)


def _parse_interface_states(raw_text: str) -> dict:
    """Pure function: `ip -o link show`'s text in, a dict mapping
    interface name to `"up"`/`"down"` out.

    State is read from the `<FLAGS,...>` bracket (present on every line),
    not the `state <WORD>` keyword — the keyword can be `UNKNOWN` for
    loopback interfaces (a real kernel quirk, not a parsing bug), while
    the flags list reliably contains `UP` for any administratively-up
    interface, loopback included.
    """
    states = {}
    for line in raw_text.splitlines():
        if not line.strip():
            continue
        tokens = line.split()
        if len(tokens) < 2:
            raise ValueError(f"could not parse ip link line: {line!r}")
        name = tokens[1].rstrip(":")
        flags = _extract_flags(line)
        states[name] = "up" if "UP" in flags else "down"
    return states


def _extract_flags(line: str) -> list[str]:
    """Pure function: one `ip -o link show` line in, the contents of its
    `<FLAG,FLAG,...>` bracket out (empty list if no bracket is present).
    """
    start = line.find("<")
    end = line.find(">")
    if start == -1 or end == -1 or end <= start:
        return []
    return line[start + 1 : end].split(",")


def _get_interface_addresses(context: CollectorContext) -> dict:
    """Run `ip -o addr show` and parse it into a dict mapping interface
    name to its IPv4/IPv6 addresses.

    Raises `ValueError` if the command fails or its output doesn't parse.
    """
    result = run_command(_ADDR_COMMAND, timeout=context.default_timeout)
    if not result.succeeded:
        raise ValueError(command_failure_message(_ADDR_COMMAND, result))
    return _parse_interface_addresses(result.stdout)


def _parse_interface_addresses(raw_text: str) -> dict:
    """Pure function: `ip -o addr show`'s text in, a dict mapping
    interface name to `{"ipv4_addresses": [...], "ipv6_addresses": [...]}`
    out.
    """
    addresses: dict = {}
    for line in raw_text.splitlines():
        if not line.strip():
            continue
        tokens = line.split()
        if len(tokens) < 4:
            raise ValueError(f"could not parse ip addr line: {line!r}")
        name = tokens[1]
        family = tokens[2]
        address = tokens[3]
        if family not in ("inet", "inet6"):
            continue
        entry = addresses.setdefault(
            name, {"ipv4_addresses": [], "ipv6_addresses": []}
        )
        if family == "inet":
            entry["ipv4_addresses"].append(address)
        else:
            entry["ipv6_addresses"].append(address)
    return addresses


def _merge_interfaces(states: dict, addresses: dict) -> list[dict]:
    """Combine interface states with their matching addresses, in the
    order interfaces were reported by `ip -o link show`.

    An interface with no addresses at all (e.g. a down interface) simply
    gets empty address lists, not a missing entry.
    """
    interfaces = []
    for name, state in states.items():
        addr_entry = addresses.get(name, {"ipv4_addresses": [], "ipv6_addresses": []})
        interfaces.append(
            {
                "name": name,
                "state": state,
                "ipv4_addresses": addr_entry["ipv4_addresses"],
                "ipv6_addresses": addr_entry["ipv6_addresses"],
            }
        )
    return interfaces


def _get_default_route(context: CollectorContext) -> dict | None:
    """Run `ip route show default` and parse it into a `{"gateway":
    ..., "interface": ...}` dict — or `None` if no default route is
    configured at all (a legitimate state, not a failure).

    Raises `ValueError` if the command fails or its output doesn't parse.
    """
    result = run_command(_DEFAULT_ROUTE_COMMAND, timeout=context.default_timeout)
    if not result.succeeded:
        raise ValueError(command_failure_message(_DEFAULT_ROUTE_COMMAND, result))
    return _parse_default_route(result.stdout)


def _parse_default_route(raw_text: str) -> dict | None:
    """Pure function: `ip route show default`'s text in, the primary
    default route out — or `None` if the output is empty (no default
    route configured). If more than one default route is present (e.g.
    multiple NICs), only the first (lowest-metric, preferred) one is
    used.
    """
    lines = [line for line in raw_text.splitlines() if line.strip()]
    if not lines:
        return None

    tokens = lines[0].split()
    gateway = None
    interface = None
    for index, token in enumerate(tokens):
        if token == "via" and index + 1 < len(tokens):
            gateway = tokens[index + 1]
        elif token == "dev" and index + 1 < len(tokens):
            interface = tokens[index + 1]

    if gateway is None or interface is None:
        raise ValueError(f"could not parse default route: {lines[0]!r}")
    return {"gateway": gateway, "interface": interface}


def _get_listening_ports(context: CollectorContext) -> list[dict]:
    """Run `ss -tulpn` and parse it into a list of listening-port dicts.

    Raises `ValueError` if the command fails or its output doesn't parse.
    """
    result = run_command(_LISTENING_PORTS_COMMAND, timeout=context.default_timeout)
    if not result.succeeded:
        raise ValueError(command_failure_message(_LISTENING_PORTS_COMMAND, result))
    return _parse_ss_output(result.stdout)


def _parse_ss_output(raw_text: str) -> list[dict]:
    """Pure function: `ss -tulpn`'s text in, a list of listening-port
    dicts out.

    `-tulpn` already restricts to listening TCP/bound UDP sockets only,
    so every non-header line is included. The process/program column is
    genuinely absent when this scan isn't running with enough privilege
    to see it (`process_name`/`pid` become `None`, not an error) — see
    docs/network_collector.md.
    """
    ports = []
    for line in raw_text.splitlines():
        if not line.strip() or line.startswith("Netid"):
            continue
        tokens = line.split()
        if len(tokens) < 6:
            raise ValueError(f"could not parse ss -tulpn line: {line!r}")

        local_address_full = tokens[4]
        address, _, port_text = local_address_full.rpartition(":")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ValueError(
                f"could not parse port from {local_address_full!r}"
            ) from exc

        process_name, pid = (
            _parse_process_field(tokens[6]) if len(tokens) > 6 else (None, None)
        )

        ports.append(
            {
                "protocol": tokens[0],
                "local_address": address,
                "port": port,
                "process_name": process_name,
                "pid": pid,
            }
        )
    return ports


def _parse_process_field(field: str) -> tuple:
    """Pure function: an `ss -tulpn` process field like
    `users:(("sshd",pid=851,fd=3),("systemd",pid=1,fd=91))` in, the
    first process's `(name, pid)` out — or `(None, None)` if the field
    doesn't match the expected shape (e.g. it's empty, as it is whenever
    this scan lacks the privilege to see process ownership).
    """
    match = _PROCESS_FIELD_PATTERN.search(field)
    if not match:
        return None, None
    return match.group("name"), int(match.group("pid"))


def _detect_firewall(context: CollectorContext) -> dict:
    """Best-effort firewall detection: try `ufw`, then `nft`, then
    `iptables`, in that order, and report the first one that's actually
    usable on this system.

    This never raises and never contributes a `collection_errors`
    entry — detecting a firewall tool's status typically requires
    root privilege (verified for real: all three commands fail with a
    permission error when this scan runs unprivileged), and not being
    able to detect one is a normal, expected outcome for an unprivileged
    scan, not a collector failure. See docs/network_collector.md.
    """
    result = run_command(_UFW_STATUS_COMMAND, timeout=context.default_timeout)
    if result.succeeded:
        return {"tool": "ufw", "enabled": _parse_ufw_status(result.stdout), "detection_note": None}

    result = run_command(_NFT_LIST_COMMAND, timeout=context.default_timeout)
    if result.succeeded:
        return {"tool": "nft", "enabled": bool(result.stdout.strip()), "detection_note": None}

    result = run_command(_IPTABLES_LIST_COMMAND, timeout=context.default_timeout)
    if result.succeeded:
        # iptables has no single, unambiguous "enabled" signal without
        # deeper rule inspection, which this task's scope excludes (see
        # docs/network_collector.md) — reported as detected but
        # undeterminable, rather than guessed.
        return {"tool": "iptables", "enabled": None, "detection_note": None}

    return {"tool": None, "enabled": None, "detection_note": _firewall_failure_reason(result)}


def _firewall_failure_reason(result) -> str:
    """A short, factual note quoting why the last firewall detection
    attempt (`iptables`, the final fallback) failed — this is the
    command's own reported reason, never an inferred explanation, so
    `ask` has something concrete to explain *why* firewall status is
    unknown rather than only being able to say that it is.
    """
    if result.error:
        return f"Could not run a firewall detection command: {result.error}"
    stderr = result.stderr.strip()
    if stderr:
        return f"Could not run a firewall detection command: {stderr.splitlines()[0]}"
    return "No firewall detection tool (ufw, nft, or iptables) could be run on this scan."


def _parse_ufw_status(raw_text: str) -> bool:
    """Pure function: `ufw status`'s first line (`"Status: active"` or
    `"Status: inactive"`) in, whether ufw is enabled out.

    Deliberately compares the exact word after the colon rather than
    checking `"active" in line` — that substring check would incorrectly
    match `"inactive"` too, since `"inactive"` itself contains `"active"`
    as its last six characters.
    """
    first_line = raw_text.strip().splitlines()[0] if raw_text.strip() else ""
    status_word = first_line.split(":", 1)[-1].strip().lower() if ":" in first_line else ""
    return status_word == "active"
