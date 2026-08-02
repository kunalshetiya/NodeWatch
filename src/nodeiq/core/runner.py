"""Safe execution of external commands.

Every collector needs to run a Linux command (`df`, `systemctl`,
`journalctl`, ...) and read back what it printed. `run_command` is the one
place in NodeIQ that actually does this, so every collector shares the same
safety guarantees instead of each reimplementing them slightly differently.

See LEARNING_NOTES.md for a beginner-friendly explanation of `subprocess`,
why `shell=True` is avoided, stdout vs. stderr, exit codes, and timeouts.
"""

import subprocess
import time

from nodeiq.core.exceptions import InvalidCommandError
from nodeiq.core.result import CommandResult

DEFAULT_TIMEOUT_SECONDS = 10.0


def run_command(
    command: list[str], timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> CommandResult:
    """Run an external command and capture everything about what happened.

    `command` must be a list of strings, e.g. `["df", "-h"]` — never a
    single string. This is what lets `subprocess` run the program directly
    (`shell=False`, the default) instead of through a shell, which avoids
    an entire class of shell-injection bugs: there is no shell here to
    misinterpret spaces, quotes, or special characters in a value that
    might contain them.

    Output is decoded as text with `errors="replace"`, so a command that
    happens to print a byte sequence that isn't valid UTF-8 (rare, but
    possible with real system logs) never crashes this function either —
    the invalid bytes are replaced rather than raising.

    This function never raises an exception for anything that can go wrong
    while actually *running* the command — a missing program, a timeout, or
    any other OS-level failure is reported back as a `CommandResult` with
    `error` or `timed_out` set, not as a raised exception. This is what
    lets a collector call this function without needing its own
    try/except for every possible way a subprocess can fail. The only
    exception this function *does* raise is `InvalidCommandError`, and only
    for a programmer mistake (passing something that isn't a valid command)
    rather than anything the operating system did.

    Args:
        command: The program and its arguments, as separate list items.
        timeout: Maximum seconds to let the command run before it's killed
            and treated as timed out.

    Returns:
        A `CommandResult` describing exactly what happened.
    """
    if not isinstance(command, list) or not command or not all(
        isinstance(part, str) for part in command
    ):
        raise InvalidCommandError(
            f"command must be a non-empty list of strings, got: {command!r}"
        )

    start_time = time.monotonic()

    try:
        completed = subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=command,
            returncode=None,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            duration_seconds=time.monotonic() - start_time,
            timed_out=True,
        )
    except FileNotFoundError as exc:
        return CommandResult(
            command=command,
            returncode=None,
            stdout="",
            stderr="",
            duration_seconds=time.monotonic() - start_time,
            error=f"command not found: {exc}",
        )
    except OSError as exc:
        # Covers anything else the operating system can raise while trying
        # to launch a process (e.g. permission denied) — this function's
        # whole purpose is to guarantee a bad command never crashes NodeIQ.
        return CommandResult(
            command=command,
            returncode=None,
            stdout="",
            stderr="",
            duration_seconds=time.monotonic() - start_time,
            error=f"failed to run command: {exc}",
        )

    return CommandResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_seconds=time.monotonic() - start_time,
    )


def command_failure_message(command: list[str], result: CommandResult) -> str:
    """Build a human-readable "X failed: Y" message from a failed
    `CommandResult`.

    Every collector that runs a command follows the same
    `if not result.succeeded: raise ValueError(...)` shape, and by
    Collector Sprint 2 six collectors had independently built an
    identical message this way — this is the single, shared home for
    that message, extracted from that evidence (see DECISIONS.md
    ADR-012's "three or more collectors" threshold). Callers still do
    their own `if`/`raise` explicitly; this only builds the message text.
    """
    return f"{' '.join(command)} failed: {result.error or result.stderr.strip()}"
