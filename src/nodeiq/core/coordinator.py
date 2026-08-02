"""The scan coordinator.

Runs every registered collector, times the whole scan, aggregates
whatever went wrong, and assembles one in-memory snapshot dict. This is
the MVP implementation (Phase 3.4) — it never writes anything to disk and
knows nothing about the CLI; both are later phases (5). See
docs/coordinator.md for the full design, including where this MVP
deliberately simplifies the fuller envelope in docs/snapshot_schema.md.

Orchestration role
-------------------
The coordinator is the *only* piece of code that knows about every
collector at once. Each collector only knows about its own job and the
shared `nodeiq.core` infrastructure — collectors never know about each
other, and never know they're part of a larger scan (see
docs/collector_guidelines.md). The coordinator is what turns "a pile of
independent collectors" into one coherent snapshot.

Interaction with collectors
-----------------------------
The coordinator builds exactly one `CollectorContext` per scan and passes
the same instance to every registered collector's
`collect(context: CollectorContext) -> CollectorResult`. It also catches
anything a collector raises — a last-resort safety net; each collector is
already expected to catch its own anticipated failures first, per
docs/collector_guidelines.md, so this should rarely trigger in practice.

Snapshot assembly
-------------------
Every registered collector's `CollectorResult.data` is stored under its
own `collector_name` key; every non-empty `CollectorResult.errors` is
stored under the same key in `collection_errors`. The coordinator also
builds `metadata` itself, since that's a fact about the scan process, not
about the machine, and isn't any individual collector's job.
"""

import time
from datetime import datetime, timezone

from nodeiq import __version__ as _NODEIQ_VERSION
from nodeiq.collectors import (
    cpu_memory,
    disk,
    logs,
    network,
    permissions,
    processes,
    scheduled_jobs,
    services,
    system,
    users,
)
from nodeiq.core.collector import CollectorContext

_REGISTERED_COLLECTORS = [
    system,
    cpu_memory,
    processes,
    disk,
    services,
    scheduled_jobs,
    permissions,
    network,
    logs,
    users,
]
"""Every collector the coordinator runs, in the order they run. A plain
list — no registry object, no discovery mechanism, no plugin system.
Adding a collector to a future scan means adding one line here."""

_COLLECTOR_BY_NAME = {
    "system": system,
    "cpu_memory": cpu_memory,
    "processes": processes,
    "disk": disk,
    "services": services,
    "scheduled_jobs": scheduled_jobs,
    "permissions": permissions,
    "network": network,
    "logs": logs,
    "users": users,
}
"""Name -> module lookup for `run_selected_collectors()` — the on-demand,
selective counterpart to `run_scan()`'s full sweep. Deliberately a plain
dict literal (not derived from `_REGISTERED_COLLECTORS` via
introspection) so both stay simple to read and keep in sync by hand."""

_REQUIRED_SECTIONS = (
    "metadata",
    "system",
    "cpu_memory",
    "processes",
    "disk",
    "services",
    "scheduled_jobs",
    "permissions",
    "network",
    "logs",
    "users",
    "collection_errors",
)


def run_scan() -> dict:
    """Run every registered collector and assemble one in-memory snapshot.

    Builds one `CollectorContext`, calls every collector in
    `_REGISTERED_COLLECTORS` with it, and combines the results into a
    single dict shaped like:

        {
            "metadata": {...},
            "collection_errors": {...},
            "system": {...},
            "cpu_memory": {...},
            "processes": {...},
            "disk": {...},
            "services": {...},
            "scheduled_jobs": {...},
            "permissions": {...},
            "network": {...},
            "logs": {...},
        }

    Never writes to disk — returns a plain dict.
    """
    scan_start_time = datetime.now(timezone.utc)
    scan_start = time.monotonic()
    context = CollectorContext(scan_start_time=scan_start_time)

    sections, collection_errors = _run_collectors(_REGISTERED_COLLECTORS, context)

    system_data = sections.get("system") or {}
    metadata = {
        "scan_timestamp": scan_start_time.isoformat(),
        "scan_duration_ms": (time.monotonic() - scan_start) * 1000,
        "collector_count": len(_REGISTERED_COLLECTORS),
        "nodeiq_version": _NODEIQ_VERSION,
        "hostname": system_data.get("hostname"),
    }

    snapshot = {
        "metadata": metadata,
        "collection_errors": collection_errors,
        **sections,
    }

    _validate_snapshot(snapshot)
    return snapshot


def run_selected_collectors(names: list) -> dict:
    """Run only the given collector(s), live, on demand — the collection
    step behind `ask`'s tiered routing (see the keyword/intent/tool-calling
    routers in `nodeiq.routing`), as opposed to `run_scan()`'s full sweep.

    Returns a **partial** snapshot: only the requested section(s) are
    populated, everything else simply absent — which
    `nodeiq.summary.summarize_snapshot()` already handles correctly (a
    missing section key reports as `"unavailable"`, the same as an
    explicit `None`), so no changes were needed there to support this.

    `metadata.collection_mode` is `"on_demand"` (vs. `run_scan()`'s
    implicit full sweep) and `metadata.collectors_run` names exactly
    which collector(s) executed — this is what lets a partial result be
    told apart from "a full scan where 8 collectors happened to crash,"
    for debugging/audit purposes.

    Raises `ValueError` immediately for an unknown collector name — this
    is a programmer/caller error (a router returning a name that isn't a
    real collector), not a runtime condition to degrade gracefully from.
    """
    unknown = [name for name in names if name not in _COLLECTOR_BY_NAME]
    if unknown:
        raise ValueError(f"unknown collector name(s): {unknown}")

    scan_start_time = datetime.now(timezone.utc)
    scan_start = time.monotonic()
    context = CollectorContext(scan_start_time=scan_start_time)

    modules = [_COLLECTOR_BY_NAME[name] for name in names]
    sections, collection_errors = _run_collectors(modules, context)

    system_data = sections.get("system") or {}
    metadata = {
        "scan_timestamp": scan_start_time.isoformat(),
        "scan_duration_ms": (time.monotonic() - scan_start) * 1000,
        "collector_count": len(names),
        "nodeiq_version": _NODEIQ_VERSION,
        "hostname": system_data.get("hostname"),
        "collection_mode": "on_demand",
        "collectors_run": list(names),
    }

    return {
        "metadata": metadata,
        "collection_errors": collection_errors,
        **sections,
    }


def _run_collectors(collector_modules: list, context: CollectorContext) -> tuple:
    """Run each given collector module with `context`, isolating any
    crash to that one collector (a last-resort safety net — each
    collector is already expected to catch its own anticipated failures
    first, per `docs/collector_guidelines.md`). Shared by `run_scan()`
    (the full sweep) and `run_selected_collectors()` (an on-demand
    subset) so this crash-isolation logic exists in exactly one place.

    Returns `(sections, collection_errors)`.
    """
    sections: dict = {}
    collection_errors: dict = {}

    for collector_module in collector_modules:
        fallback_name = collector_module.__name__.rsplit(".", 1)[-1]
        try:
            result = collector_module.collect(context)
        except Exception as exc:
            sections[fallback_name] = None
            collection_errors[fallback_name] = [
                {
                    "message": f"collector crashed: {exc}",
                    "severity": "error",
                    "exception_type": type(exc).__name__,
                }
            ]
            continue

        sections[result.collector_name] = result.data
        if result.errors:
            collection_errors[result.collector_name] = result.errors

    return sections, collection_errors


def _validate_snapshot(snapshot: dict) -> None:
    """Sanity-check that the assembled snapshot has every section this
    MVP coordinator is expected to produce.

    This is a defensive check on the coordinator's *own* output, not on
    external input — with the current registered collectors, every
    section is always populated (successfully or as a recorded crash), so
    this should never actually fail. If it ever does, that means a bug in
    `run_scan()` itself (e.g. a registered collector whose declared
    `collector_name` doesn't match what was expected), which is exactly
    the kind of mistake this check exists to catch loudly rather than
    silently return an incomplete snapshot. No external validation
    library is used — this is a plain key-presence check.
    """
    missing = [key for key in _REQUIRED_SECTIONS if key not in snapshot]
    if missing:
        raise ValueError(
            f"assembled snapshot is missing required section(s): {missing}"
        )
