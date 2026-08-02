"""Unit tests for nodeiq.core.snapshot.

Every test redirects `_SNAPSHOTS_DIR` to `tmp_path`, so nothing here
reads or writes the real project's `snapshots/` directory, per
PROJECT_RULES.md Section 11.
"""

import json

import pytest

from nodeiq.core import snapshot
from nodeiq.core.exceptions import SnapshotError


def _sample_snapshot(scan_timestamp: str = "2026-07-15T12:34:56.789012+00:00") -> dict:
    return {
        "metadata": {
            "scan_timestamp": scan_timestamp,
            "scan_duration_ms": 12.3,
            "collector_count": 9,
            "nodeiq_version": "0.1.0",
            "hostname": "test-host",
        },
        "collection_errors": {},
        "system": {"hostname": "test-host"},
    }


# --- save_snapshot ---------------------------------------------------------------


def test_save_snapshot_creates_the_directory_if_missing(tmp_path, monkeypatch):
    target_dir = tmp_path / "snapshots"
    monkeypatch.setattr(snapshot, "_SNAPSHOTS_DIR", target_dir)
    assert not target_dir.exists()

    snapshot.save_snapshot(_sample_snapshot())

    assert target_dir.is_dir()


def test_save_snapshot_writes_valid_readable_json(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "_SNAPSHOTS_DIR", tmp_path)

    saved_path = snapshot.save_snapshot(_sample_snapshot())

    raw_text = saved_path.read_text()
    assert json.loads(raw_text) == _sample_snapshot()
    # "Readable" means indented, not a single unbroken line.
    assert "\n" in raw_text


def test_save_snapshot_returns_the_path_it_wrote(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "_SNAPSHOTS_DIR", tmp_path)

    saved_path = snapshot.save_snapshot(_sample_snapshot())

    assert saved_path.parent == tmp_path
    assert saved_path.exists()


def test_save_snapshot_filename_is_derived_from_scan_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "_SNAPSHOTS_DIR", tmp_path)

    saved_path = snapshot.save_snapshot(
        _sample_snapshot(scan_timestamp="2026-01-02T03:04:05.678901+00:00")
    )

    assert saved_path.name == "snapshot_20260102T030405678901.json"


def test_save_snapshot_falls_back_to_now_when_scan_timestamp_is_missing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(snapshot, "_SNAPSHOTS_DIR", tmp_path)

    saved_path = snapshot.save_snapshot({"metadata": {}})

    assert saved_path.name.startswith("snapshot_")
    assert saved_path.name.endswith(".json")


def test_save_snapshot_falls_back_to_now_when_scan_timestamp_is_malformed(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(snapshot, "_SNAPSHOTS_DIR", tmp_path)

    saved_path = snapshot.save_snapshot(
        {"metadata": {"scan_timestamp": "not-a-real-timestamp"}}
    )

    assert saved_path.name.startswith("snapshot_")


# --- load_snapshot -----------------------------------------------------------------


def test_load_snapshot_restores_identical_data(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "_SNAPSHOTS_DIR", tmp_path)
    original = _sample_snapshot()
    saved_path = snapshot.save_snapshot(original)

    loaded = snapshot.load_snapshot(saved_path)

    assert loaded == original


def test_load_snapshot_accepts_a_string_path(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "_SNAPSHOTS_DIR", tmp_path)
    saved_path = snapshot.save_snapshot(_sample_snapshot())

    loaded = snapshot.load_snapshot(str(saved_path))

    assert loaded == _sample_snapshot()


def test_load_snapshot_raises_when_the_file_does_not_exist(tmp_path):
    with pytest.raises(SnapshotError):
        snapshot.load_snapshot(tmp_path / "does_not_exist.json")


def test_load_snapshot_raises_on_malformed_json(tmp_path):
    bad_file = tmp_path / "broken.json"
    bad_file.write_text("{not valid json,,,")

    with pytest.raises(SnapshotError):
        snapshot.load_snapshot(bad_file)


def test_load_snapshot_raises_when_json_is_not_an_object(tmp_path):
    not_an_object = tmp_path / "list.json"
    not_an_object.write_text(json.dumps(["just", "a", "list"]))

    with pytest.raises(SnapshotError):
        snapshot.load_snapshot(not_an_object)


def test_load_snapshot_raises_when_metadata_is_missing(tmp_path):
    no_metadata = tmp_path / "no_metadata.json"
    no_metadata.write_text(json.dumps({"system": {}}))

    with pytest.raises(SnapshotError):
        snapshot.load_snapshot(no_metadata)


# --- load_latest_snapshot ------------------------------------------------------------


def test_load_latest_snapshot_selects_the_newest_by_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "_SNAPSHOTS_DIR", tmp_path)
    snapshot.save_snapshot(_sample_snapshot(scan_timestamp="2026-01-01T00:00:00+00:00"))
    newest = _sample_snapshot(scan_timestamp="2026-06-01T00:00:00+00:00")
    snapshot.save_snapshot(newest)
    snapshot.save_snapshot(_sample_snapshot(scan_timestamp="2026-03-01T00:00:00+00:00"))

    loaded = snapshot.load_latest_snapshot()

    assert loaded["metadata"]["scan_timestamp"] == "2026-06-01T00:00:00+00:00"


def test_load_latest_snapshot_raises_gracefully_when_directory_is_missing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(snapshot, "_SNAPSHOTS_DIR", tmp_path / "does_not_exist")

    with pytest.raises(SnapshotError):
        snapshot.load_latest_snapshot()


def test_load_latest_snapshot_raises_gracefully_when_directory_is_empty(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(snapshot, "_SNAPSHOTS_DIR", tmp_path)

    with pytest.raises(SnapshotError):
        snapshot.load_latest_snapshot()


def test_load_latest_snapshot_ignores_unrelated_files(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "_SNAPSHOTS_DIR", tmp_path)
    (tmp_path / "notes.txt").write_text("not a snapshot")
    snapshot.save_snapshot(_sample_snapshot())

    loaded = snapshot.load_latest_snapshot()

    assert loaded == _sample_snapshot()
