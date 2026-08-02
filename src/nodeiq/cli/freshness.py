"""Snapshot freshness — warns when `report`/`ask` are about to answer
from a snapshot that may no longer reflect the machine's real state.

Presentation-only, like the rest of `nodeiq.cli`: this reads a
snapshot's own `metadata.scan_timestamp` (a field every snapshot
already has) and compares it to the current time. It never re-scans,
never decides anything about *what* is stale in the underlying system
— only how old the evidence itself is. `scan` never needs this check,
since a snapshot it just produced is never stale; only `report` and
`ask`, which may load an old snapshot from disk, do.
"""

from datetime import datetime, timedelta, timezone

_STALE_THRESHOLD = timedelta(hours=24)
"""How old a snapshot must be before a warning is shown. A day is
generous enough not to nag on a snapshot from an hour ago, while still
catching the case this check exists for: reporting on evidence that's
old enough operational conditions have plausibly changed since."""


def check_snapshot_freshness(metadata: dict, *, now: datetime | None = None) -> str | None:
    """Return a warning string if the snapshot described by `metadata`
    (a snapshot's own `metadata` dict — see `docs/snapshot_schema.md`)
    is older than `_STALE_THRESHOLD`, or `None` if it's fresh enough (or
    its age can't be determined at all, e.g. `scan_timestamp` is
    missing/unparseable — an unknown age is not treated as a stale one).

    Takes just `metadata`, not a full snapshot, so callers that already
    have it in hand (e.g. `nodeiq.llm.ask.answer_question()`'s result)
    never need to pass — or this module never needs to see — anything
    else in the snapshot.

    `now` is injectable for deterministic testing; defaults to the
    real current UTC time.
    """
    metadata = metadata or {}
    scan_timestamp = metadata.get("scan_timestamp")
    if not scan_timestamp:
        return None

    try:
        scanned_at = datetime.fromisoformat(scan_timestamp)
    except ValueError:
        return None

    current_time = now if now is not None else datetime.now(timezone.utc)
    age = current_time - scanned_at
    if age < _STALE_THRESHOLD:
        return None

    return (
        f"Warning: this snapshot was generated {_format_age(age)} ago. "
        "Run `nodeiq scan` for fresh diagnostics."
    )


def _format_age(age: timedelta) -> str:
    """Pure function: a duration in, a short human-readable age out
    (e.g. `"3 days"`, `"5 hours"`) — the coarsest unit that applies,
    since a warning only needs to convey "roughly how stale," not
    precise timing.
    """
    total_seconds = int(age.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    if days:
        return f"{days} day{'s' if days != 1 else ''}"
    if hours:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''}"
