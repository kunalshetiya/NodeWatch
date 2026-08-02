"""Project-specific exceptions.

Kept deliberately small. The runner (`runner.py`) is designed to never let
a *system-level* failure (a missing command, a timeout, a crash) escape as
an exception — those are reported as data inside a `CommandResult`
instead (see `result.py`). `InvalidCommandError` exists for a different
kind of problem: a *programmer* calling the runner incorrectly, which
should fail fast and loudly rather than being silently absorbed.
`SnapshotError` (Phase 3.8) covers the equivalent case for
`nodeiq.core.snapshot`: a snapshot file that can't be read, isn't valid
JSON, or doesn't look like a snapshot at all.
"""


class NodeIQError(Exception):
    """Base class for all NodeIQ-specific exceptions.

    Not raised directly. It exists so that any future project-specific
    exception can share one common ancestor, letting calling code catch
    `except NodeIQError` to mean "something NodeIQ-specific went wrong"
    without also swallowing unrelated Python errors.
    """


class InvalidCommandError(NodeIQError):
    """Raised when `run_command` is called with something that isn't a
    valid command: a non-empty list of strings.

    This is a programmer error (calling the function wrong), not a
    system-level failure (the command failing to run) — so it is raised
    immediately rather than captured inside a `CommandResult`.
    """


class SnapshotError(NodeIQError):
    """Raised by `nodeiq.core.snapshot` when a snapshot can't be loaded:
    the file is missing, unreadable, not valid JSON, or doesn't look like
    a snapshot (e.g. not a JSON object, or missing `metadata`).

    Unlike a collector's own failures (which are absorbed into
    `collection_errors` so a scan never crashes — see
    `docs/collector_guidelines.md`), loading a snapshot from disk is not
    a scan — there's no partial result to fall back to, so a genuinely
    broken snapshot file is a real, raised error for the caller to handle.
    """
