"""Unit tests for nodeiq.cli.freshness."""

from datetime import datetime, timedelta, timezone

from nodeiq.cli.freshness import check_snapshot_freshness

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)


def test_fresh_snapshot_produces_no_warning():
    metadata = {"scan_timestamp": (_NOW - timedelta(hours=1)).isoformat()}
    assert check_snapshot_freshness(metadata, now=_NOW) is None


def test_snapshot_just_under_the_threshold_produces_no_warning():
    metadata = {"scan_timestamp": (_NOW - timedelta(hours=23, minutes=59)).isoformat()}
    assert check_snapshot_freshness(metadata, now=_NOW) is None


def test_stale_snapshot_produces_a_warning():
    metadata = {"scan_timestamp": (_NOW - timedelta(days=3)).isoformat()}
    warning = check_snapshot_freshness(metadata, now=_NOW)

    assert warning is not None
    assert "3 days" in warning
    assert "nodeiq scan" in warning


def test_warning_matches_the_documented_example_wording():
    metadata = {"scan_timestamp": (_NOW - timedelta(days=3)).isoformat()}
    warning = check_snapshot_freshness(metadata, now=_NOW)

    assert warning == (
        "Warning: this snapshot was generated 3 days ago. "
        "Run `nodeiq scan` for fresh diagnostics."
    )


def test_stale_by_exactly_24_hours_produces_a_warning():
    metadata = {"scan_timestamp": (_NOW - timedelta(hours=24)).isoformat()}
    assert check_snapshot_freshness(metadata, now=_NOW) is not None


def test_format_age_uses_hour_wording_under_a_day():
    from nodeiq.cli.freshness import _format_age

    assert _format_age(timedelta(hours=5)) == "5 hours"
    assert _format_age(timedelta(hours=1)) == "1 hour"


def test_format_age_uses_minute_wording_under_an_hour():
    from nodeiq.cli.freshness import _format_age

    assert _format_age(timedelta(minutes=5)) == "5 minutes"


def test_missing_scan_timestamp_produces_no_warning():
    assert check_snapshot_freshness({}, now=_NOW) is None


def test_none_metadata_produces_no_warning():
    assert check_snapshot_freshness(None, now=_NOW) is None


def test_unparseable_scan_timestamp_produces_no_warning():
    metadata = {"scan_timestamp": "not-a-real-timestamp"}
    assert check_snapshot_freshness(metadata, now=_NOW) is None


def test_defaults_to_the_real_current_time_when_now_not_given():
    # A snapshot timestamped far in the past should still be flagged
    # stale using the function's own real-time default.
    metadata = {"scan_timestamp": "2000-01-01T00:00:00+00:00"}
    warning = check_snapshot_freshness(metadata)

    assert warning is not None
    assert "nodeiq scan" in warning
