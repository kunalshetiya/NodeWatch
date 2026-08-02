"""Tests for nodeiq.core.collector: CollectorContext and CollectorResult.

These are plain dataclasses with no I/O, so tests just construct them
directly and check their values and behavior — no mocking needed.
"""

from datetime import datetime, timezone

import pytest

from nodeiq.core.collector import CollectorContext, CollectorResult
from nodeiq.core.runner import DEFAULT_TIMEOUT_SECONDS


def test_collector_context_default_timeout_matches_runner_default():
    """CollectorContext's default_timeout falls back to the same constant
    the runner itself uses, so a collector that doesn't override it gets
    the same default as calling run_command() directly."""
    context = CollectorContext(scan_start_time=datetime.now(timezone.utc))

    assert context.default_timeout == DEFAULT_TIMEOUT_SECONDS


def test_collector_context_allows_overriding_the_timeout():
    context = CollectorContext(
        scan_start_time=datetime.now(timezone.utc), default_timeout=2.5
    )

    assert context.default_timeout == 2.5


def test_collector_context_is_immutable():
    context = CollectorContext(scan_start_time=datetime.now(timezone.utc))

    with pytest.raises(AttributeError):
        context.default_timeout = 99.0


def test_collector_result_succeeds_with_no_errors():
    result = CollectorResult(
        collector_name="disk",
        data={"filesystems": []},
        errors=[],
        duration_ms=12.5,
    )

    assert result.success is True


def test_collector_result_succeeds_with_only_warnings():
    result = CollectorResult(
        collector_name="disk",
        data={"filesystems": []},
        errors=[{"message": "minor issue", "severity": "warning"}],
        duration_ms=12.5,
    )

    assert result.success is True


def test_collector_result_fails_with_an_error_severity_entry():
    result = CollectorResult(
        collector_name="disk",
        data={"filesystems": []},
        errors=[{"message": "df failed", "severity": "error"}],
        duration_ms=12.5,
    )

    assert result.success is False


def test_collector_result_is_immutable():
    result = CollectorResult(
        collector_name="disk", data={}, errors=[], duration_ms=1.0
    )

    with pytest.raises(AttributeError):
        result.duration_ms = 2.0
