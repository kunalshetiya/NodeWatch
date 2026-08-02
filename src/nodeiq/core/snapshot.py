"""Saving and loading snapshots on disk.

`nodeiq.core.coordinator.run_scan()` produces a snapshot entirely in
memory and never writes anything to disk (see `docs/coordinator.md`,
"MVP Simplifications") — persistence is a separate responsibility,
handled here instead. This is what turns a snapshot from "one scan's
in-memory result" into the durable, shareable artifact `report` and
`ask` (Phases 4 and 6) will eventually read — see
`docs/snapshot_persistence.md` for the full rationale.

Typical usage, once a scan has run:

    from nodeiq.core.coordinator import run_scan
    from nodeiq.core.snapshot import save_snapshot

    snapshot = run_scan()
    saved_path = save_snapshot(snapshot)

`nodeiq.core.coordinator` never imports this module, and this module
never imports `nodeiq.core.coordinator` — the coordinator's only job is
producing a snapshot; what happens to it afterward isn't its concern.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from nodeiq.core.exceptions import SnapshotError

_SNAPSHOTS_DIR = Path("snapshots")
_FILENAME_PREFIX = "snapshot_"
_FILENAME_SUFFIX = ".json"


def save_snapshot(snapshot: dict) -> Path:
    """Write a snapshot to `snapshots/` as formatted JSON and return the
    path it was saved to.

    Creates `snapshots/` first if it doesn't exist yet. The filename is
    derived from the snapshot's own `metadata.scan_timestamp` (falling
    back to the current time if that field is missing or unparseable),
    so it's deterministic — the same snapshot content always produces
    the same filename — and sorts chronologically, which is what lets
    `load_latest_snapshot()` find the newest one by name alone.
    """
    _SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    path = _SNAPSHOTS_DIR / _generate_filename(snapshot)
    path.write_text(json.dumps(snapshot, indent=2))
    return path


def _generate_filename(snapshot: dict) -> str:
    """Pure function: a snapshot dict in, its `snapshot_<timestamp>.json`
    filename out.
    """
    return f"{_FILENAME_PREFIX}{_filename_timestamp(snapshot)}{_FILENAME_SUFFIX}"


def _filename_timestamp(snapshot: dict) -> str:
    """Pure function: a snapshot dict in, a compact, filesystem-safe
    timestamp string out (microsecond precision, so two snapshots saved
    within the same second still get distinct filenames).

    Reads `metadata.scan_timestamp` (an ISO 8601 string) when present and
    parseable; falls back to the current UTC time otherwise, since a
    snapshot is still worth saving even if its own timestamp is missing
    or malformed.
    """
    raw_timestamp = snapshot.get("metadata", {}).get("scan_timestamp")
    if isinstance(raw_timestamp, str):
        try:
            when = datetime.fromisoformat(raw_timestamp)
        except ValueError:
            when = datetime.now(timezone.utc)
    else:
        when = datetime.now(timezone.utc)
    return when.strftime("%Y%m%dT%H%M%S%f")


def load_snapshot(path: Path | str) -> dict:
    """Load one snapshot file from disk and return it as a dict.

    Raises `SnapshotError` if the file can't be read, isn't valid JSON,
    or doesn't look like a snapshot (not a JSON object, or missing the
    `metadata` section every snapshot is required to have — see
    `docs/snapshot_schema.md` Section 1).
    """
    path = Path(path)

    try:
        raw_text = path.read_text()
    except OSError as exc:
        raise SnapshotError(f"could not read snapshot {path}: {exc}") from exc

    try:
        snapshot = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"snapshot {path} is not valid JSON: {exc}") from exc

    _validate_snapshot_shape(snapshot, path)
    return snapshot


def _validate_snapshot_shape(snapshot, path: Path) -> None:
    """Sanity-check that loaded JSON is at least shaped like a snapshot.

    Deliberately lightweight — this only confirms the JSON is an object
    with a `metadata` section, not that every collector section is
    present. Checking a snapshot's full section-by-section shape is
    `nodeiq.core.coordinator._validate_snapshot`'s job, for snapshots it
    just assembled; this function's job is only to catch "this file
    clearly isn't a snapshot at all" (e.g. some other JSON file, or a
    truncated/corrupted write).
    """
    if not isinstance(snapshot, dict):
        raise SnapshotError(f"snapshot {path} is not a JSON object")
    if "metadata" not in snapshot:
        raise SnapshotError(f"snapshot {path} is missing its 'metadata' section")


def load_latest_snapshot() -> dict:
    """Find the newest snapshot in `snapshots/` and load it.

    Raises `SnapshotError` if `snapshots/` doesn't exist or contains no
    snapshot files — both are the same "nothing to load yet" outcome
    from the caller's perspective, so both are reported the same way.
    """
    candidates = sorted(_SNAPSHOTS_DIR.glob(f"{_FILENAME_PREFIX}*{_FILENAME_SUFFIX}"))
    if not candidates:
        raise SnapshotError(f"no snapshot files found in {_SNAPSHOTS_DIR}")
    return load_snapshot(candidates[-1])
