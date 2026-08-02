"""Building `collection_errors`-shaped entries.

Every collector catches its own anticipated failures and turns each one
into a small dict describing what went wrong, appended to the
`CollectorResult.errors` list it returns (see docs/collector_guidelines.md
and docs/snapshot_schema.md Section 12 for the shape this matches). By
Collector Sprint 2, all nine collectors had independently defined an
identical private `_error_entry` helper to build that dict — this module
is the single, shared home for it instead, extracted from that evidence
(see DECISIONS.md ADR-012's "three or more collectors" threshold).
"""


def error_entry(exception: BaseException, *, message: str | None = None) -> dict:
    """Build one `collection_errors`-shaped entry from a caught exception.

    `message` defaults to `str(exception)` — enough for most collectors,
    which raise their exception with a complete, ready-to-use message
    already embedded. Pass an explicit `message` when a caught exception
    needs more context added (e.g. `permissions.py` includes which path
    was being checked, since an `OSError` from `Path.stat()` doesn't
    carry that on its own).

    `severity` is always `"error"` — every collector's own anticipated
    failures are genuine errors by the time they reach this helper (see
    `docs/snapshot_schema.md` Section 12 for the schema's `"warning"`
    option, not currently used by any collector; add a `severity`
    parameter here if and when a real caller needs it, rather than
    speculatively now).
    """
    return {
        "message": message if message is not None else str(exception),
        "severity": "error",
        "exception_type": type(exception).__name__,
    }
