"""Shared types for the collector contract.

Every collector implements `collect(context: CollectorContext) ->
CollectorResult`. This module defines those two types — nothing else.
Deliberately not a framework: no base class a collector must extend, no
registry, no plugin discovery. A collector is just a module with a
function; these two dataclasses are the shape of that function's input
and output. See docs/collector_guidelines.md for the full contract and
docs/architecture.md for how this fits the rest of the codebase.
"""

from dataclasses import dataclass
from datetime import datetime

from nodeiq.core.runner import DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class CollectorContext:
    """Information shared with every collector for one scan.

    A single `CollectorContext` is created once per scan (by the future
    scan coordinator) and passed to every collector's `collect()` call, so
    all collectors in the same scan agree on things like "when did this
    scan start" and "how long should a command be allowed to run" without
    each collector deciding independently.

    Kept intentionally small. Only two fields exist because only two are
    needed right now — adding a new field later (with a default value, so
    existing collectors that ignore it keep working unchanged) is trivial
    for a plain dataclass. There's no separate "extensibility mechanism"
    here beyond that; see DECISIONS.md ADR-014 for why that's enough.
    """

    scan_start_time: datetime
    """When this scan began, as a timezone-aware UTC `datetime`. Lets every
    collector agree on one "now" for the scan, rather than each calling
    `datetime.now()` independently and getting slightly different times."""

    default_timeout: float = DEFAULT_TIMEOUT_SECONDS
    """Seconds a collector should pass to `run_command` unless it has a
    specific reason to use a different value. Centralizing this here means
    a future "quick scan" or "thorough scan" mode can change every
    collector's timeout at once, by constructing a different context —
    without editing a single collector."""


@dataclass(frozen=True)
class CollectorResult:
    """The outcome of running one collector.

    This is the object every collector's `collect()` function returns,
    replacing the earlier `(data, errors)` tuple (see DECISIONS.md
    ADR-014 for why). Naming every field means the future scan coordinator
    reads `result.data`, `result.errors`, etc. — self-documenting at every
    call site, rather than a tuple whose two positions have to be
    remembered.
    """

    collector_name: str
    """Which collector produced this result, e.g. `"disk"` — matching the
    snapshot section name it fills in (see docs/snapshot_schema.md)."""

    data: dict
    """The section's data, matching its documented schema — as fully as
    could be determined, possibly partially filled if some (but not all)
    of the underlying commands or file reads failed."""

    errors: list[dict]
    """Error-detail dicts (see docs/snapshot_schema.md Section 12,
    collection_errors) — empty if nothing went wrong."""

    duration_ms: float
    """How long this collector took to run, in milliseconds, measured by
    the collector itself (mirroring how `CommandResult.duration_seconds`
    is measured by the runner — see core/runner.py)."""

    @property
    def success(self) -> bool:
        """Whether this collector's result should be considered a success.

        `True` unless `errors` contains at least one entry with
        `severity == "error"`. A collector that only produced warnings
        still counts as successful — warnings note something worth
        mentioning, not a failure to gather usable data.
        """
        return not any(error.get("severity") == "error" for error in self.errors)
