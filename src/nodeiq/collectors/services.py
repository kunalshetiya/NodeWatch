"""Services collector: systemd service health.

Answers "what service failed?" — one of NodeIQ's headline example
questions. Follows the same `CollectorContext` -> `collect()` ->
`CollectorResult` pattern as every other collector, using
`nodeiq.core.runner.run_command` for two `systemctl` invocations (there
is no `/proc` file that reports this — `systemctl` is the canonical
interface). See docs/services_collector.md for the full rationale,
including how a missing/absent systemd (DECISIONS.md ADR-010) is
detected and reported via `systemd_available`.
"""

import time

from nodeiq.core.collector import CollectorContext, CollectorResult
from nodeiq.core.errors import error_entry
from nodeiq.core.runner import command_failure_message, run_command

_LIST_UNITS_COMMAND = [
    "systemctl",
    "list-units",
    "--type=service",
    "--all",
    "--no-legend",
    "--no-pager",
    "--plain",
]
_LIST_UNIT_FILES_COMMAND = [
    "systemctl",
    "list-unit-files",
    "--type=service",
    "--no-legend",
    "--no-pager",
    "--plain",
]

_RESTARTING_SUB_STATE = "auto-restart"


def collect(context: CollectorContext) -> CollectorResult:
    """Gather a summary of systemd service health.

    `systemctl list-units` (every service's current state) and
    `systemctl list-unit-files` (whether each service is enabled to
    start at boot) are two independent data sources. If listing units
    fails at all — most notably because `systemctl` doesn't exist on
    this system (DECISIONS.md ADR-010) — nothing about service state can
    be determined, so `systemd_available` is `False` and every count is
    `None`. If only the enabled-count source fails, everything derived
    from `list-units` is still returned in full.
    """
    start_time = time.monotonic()
    errors: list[dict] = []

    try:
        units = _get_service_units(context)
    except ValueError as exc:
        errors.append(error_entry(exc))
        return CollectorResult(
            collector_name="services",
            data={
                "systemd_available": False,
                "running_services_count": None,
                "failed_services_count": None,
                "enabled_services_count": None,
                "running_services": [],
                "failed_services": [],
                "restarting_services": [],
            },
            errors=errors,
            duration_ms=(time.monotonic() - start_time) * 1000,
        )

    data = _summarize_services(units)
    data["systemd_available"] = True

    try:
        unit_file_states = _get_unit_file_states(context)
        data["enabled_services_count"] = sum(
            1 for state in unit_file_states.values() if state == "enabled"
        )
    except ValueError as exc:
        data["enabled_services_count"] = None
        errors.append(error_entry(exc))

    return CollectorResult(
        collector_name="services",
        data=data,
        errors=errors,
        duration_ms=(time.monotonic() - start_time) * 1000,
    )


def _get_service_units(context: CollectorContext) -> list[dict]:
    """Run `systemctl list-units` and parse it into a list of per-service
    state dicts.

    Raises `ValueError` if the command fails or its output doesn't parse
    — including the case where `systemctl` doesn't exist at all (no
    systemd on this system).
    """
    result = run_command(_LIST_UNITS_COMMAND, timeout=context.default_timeout)
    if not result.succeeded:
        raise ValueError(command_failure_message(_LIST_UNITS_COMMAND, result))
    return _parse_service_units(result.stdout)


def _parse_service_units(raw_text: str) -> list[dict]:
    """Pure function: `systemctl list-units`'s text in, a list of
    per-service dicts (`name`, `load_state`, `active_state`, `sub_state`,
    `description`) out. No subprocess calls, no I/O — just string
    parsing, so it can be tested with a literal sample string.
    """
    units = []
    for line in raw_text.splitlines():
        if not line.strip():
            continue
        tokens = line.split()
        if len(tokens) < 4:
            raise ValueError(f"could not parse list-units line: {line!r}")
        units.append(
            {
                "name": tokens[0],
                "load_state": tokens[1],
                "active_state": tokens[2],
                "sub_state": tokens[3],
                "description": " ".join(tokens[4:]),
            }
        )
    return units


def _summarize_services(units: list[dict]) -> dict:
    """Pure function: the full per-service list in, the counts and
    detail lists this collector actually returns out (everything derived
    from `list-units` alone — `enabled_services_count`, which needs the
    second command, is computed separately in `collect()`).
    """
    running_services = [unit for unit in units if unit["active_state"] == "active"]
    failed_services = [unit for unit in units if unit["active_state"] == "failed"]
    restarting_services = [
        unit for unit in units if unit["sub_state"] == _RESTARTING_SUB_STATE
    ]

    return {
        "running_services_count": len(running_services),
        "failed_services_count": len(failed_services),
        "running_services": running_services,
        "failed_services": failed_services,
        "restarting_services": restarting_services,
    }


def _get_unit_file_states(context: CollectorContext) -> dict:
    """Run `systemctl list-unit-files` and parse it into a dict mapping
    service name to its enablement state (e.g. `"enabled"`, `"static"`,
    `"disabled"`).

    Raises `ValueError` if the command fails or its output doesn't parse.
    """
    result = run_command(_LIST_UNIT_FILES_COMMAND, timeout=context.default_timeout)
    if not result.succeeded:
        raise ValueError(command_failure_message(_LIST_UNIT_FILES_COMMAND, result))
    return _parse_unit_files(result.stdout)


def _parse_unit_files(raw_text: str) -> dict:
    """Pure function: `systemctl list-unit-files`'s text in, a dict
    mapping service name to enablement state out. No subprocess calls,
    no I/O — just string parsing, so it can be tested with a literal
    sample string.
    """
    states = {}
    for line in raw_text.splitlines():
        if not line.strip():
            continue
        tokens = line.split()
        if len(tokens) < 2:
            raise ValueError(f"could not parse list-unit-files line: {line!r}")
        states[tokens[0]] = tokens[1]
    return states
