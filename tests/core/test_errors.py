"""Unit tests for nodeiq.core.errors.error_entry.

Extracted during the Phase 3.7 refactoring sprint from nine identical
per-collector `_error_entry` implementations — see DECISIONS.md ADR-012.
"""

from nodeiq.core.errors import error_entry


def test_error_entry_uses_str_of_the_exception_by_default():
    exc = ValueError("could not read /proc/uptime")

    entry = error_entry(exc)

    assert entry == {
        "message": "could not read /proc/uptime",
        "severity": "error",
        "exception_type": "ValueError",
    }


def test_error_entry_uses_an_explicit_message_when_given():
    exc = OSError("Permission denied")

    entry = error_entry(exc, message="could not check /etc/shadow: Permission denied")

    assert entry["message"] == "could not check /etc/shadow: Permission denied"
    assert entry["exception_type"] == "OSError"


def test_error_entry_severity_is_always_error():
    assert error_entry(ValueError("x"))["severity"] == "error"


def test_error_entry_reports_the_exceptions_own_class_name():
    class _CustomError(Exception):
        pass

    entry = error_entry(_CustomError("boom"))

    assert entry["exception_type"] == "_CustomError"
