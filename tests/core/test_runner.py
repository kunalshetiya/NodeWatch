"""Tests for nodeiq.core.runner.run_command.

Rather than depending on Linux-only commands like `df` or `systemctl`
(which may not exist on the machine running these tests — see
DECISIONS.md ADR-002 for why development happens in a Multipass VM, but
tests themselves should still run anywhere Python does), these tests use
the currently-running Python interpreter itself as the "external command."
This makes exit code, timing, and output fully predictable without
depending on any particular operating system or installed tool.
"""

import sys

import pytest

from nodeiq.core.exceptions import InvalidCommandError
from nodeiq.core.result import CommandResult
from nodeiq.core.runner import command_failure_message, run_command


def test_run_command_success():
    """A command that exits cleanly reports success and its stdout."""
    result = run_command([sys.executable, "-c", "print('hello')"])

    assert result.succeeded is True
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"
    assert result.stderr == ""
    assert result.timed_out is False
    assert result.error is None
    assert result.duration_seconds >= 0


def test_run_command_captures_stderr_separately():
    """stdout and stderr are captured independently, never mixed together."""
    script = "import sys; print('out'); print('err', file=sys.stderr)"
    result = run_command([sys.executable, "-c", script])

    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"


def test_run_command_non_zero_exit_code():
    """A non-zero exit code is reported, not treated as a crash."""
    result = run_command([sys.executable, "-c", "import sys; sys.exit(7)"])

    assert result.succeeded is False
    assert result.returncode == 7
    assert result.timed_out is False
    assert result.error is None


def test_run_command_timeout():
    """A command that runs past its timeout is killed and marked timed out."""
    script = "import time; time.sleep(5)"
    result = run_command([sys.executable, "-c", script], timeout=0.2)

    assert result.succeeded is False
    assert result.timed_out is True
    assert result.returncode is None


def test_run_command_rejects_a_non_list_command():
    """Passing a string instead of a list is a programmer error, not a
    runtime failure, so it raises immediately rather than being absorbed
    into a CommandResult. This is also what keeps run_command honest about
    never needing shell=True."""
    with pytest.raises(InvalidCommandError):
        run_command("df -h")


# --- command_failure_message ---------------------------------------------------
#
# Extracted during the Phase 3.7 refactoring sprint from eleven identical
# per-collector message-building call sites — see DECISIONS.md ADR-012.


def test_command_failure_message_prefers_the_error_field():
    result = CommandResult(
        command=["df", "-h"],
        returncode=None,
        stdout="",
        stderr="",
        duration_seconds=0.01,
        error="command not found",
    )

    assert command_failure_message(["df", "-h"], result) == "df -h failed: command not found"


def test_command_failure_message_falls_back_to_stripped_stderr():
    result = CommandResult(
        command=["df", "-h"],
        returncode=1,
        stdout="",
        stderr="  permission denied  \n",
        duration_seconds=0.01,
    )

    assert command_failure_message(["df", "-h"], result) == "df -h failed: permission denied"
