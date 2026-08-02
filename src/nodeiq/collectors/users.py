"""User accounts collector: who has an account on this system, who's
currently logged in, and recent login history.

Answers "who am I" and "what users are on this system" — a gap
identified during real usage: the original 9 collectors covered
processes, services, and infrastructure state, but nothing about user
identity itself. Follows the same `CollectorContext` -> `collect()` ->
`CollectorResult` pattern as every other collector.

Accounts come from reading `/etc/passwd` directly (no command, matching
`permissions.py`'s approach); logged-in sessions and login history come
from running `who` and `last` — there's no `/proc` equivalent for either.
"""

import time
from pathlib import Path

from nodeiq.core.collector import CollectorContext, CollectorResult
from nodeiq.core.errors import error_entry
from nodeiq.core.runner import command_failure_message, run_command

_PASSWD_PATH = Path("/etc/passwd")
_WHO_COMMAND = ["who"]

_MAX_LOGIN_HISTORY_ENTRIES = 20
"""How many recent `last` entries to fetch at most — matches the same
"bounded, not unbounded" pattern as `logs.py`'s `_MAX_ENTRIES` and
`processes.py`'s `_TOP_N`."""

_LAST_COMMAND = ["last", "-n", str(_MAX_LOGIN_HISTORY_ENTRIES)]

_MIN_HUMAN_UID = 1000
"""Debian/Ubuntu convention: UIDs below this belong to system/service
accounts (e.g. `syslog`, `systemd-network`), not a real human login.
Not a universal standard across every Linux distribution, but a
reasonable default for distinguishing "who are the actual people with
access" from the dozens of service accounts every system has."""


def collect(context: CollectorContext) -> CollectorResult:
    """Gather user accounts, who's currently logged in, and recent login
    history.

    Each of the three is an independent data source — if one fails (e.g.
    `last` isn't installed), the others are still collected. See
    PROJECT_RULES.md Section 7 and docs/collector_guidelines.md for why
    partial data always beats no data.
    """
    start_time = time.monotonic()
    data: dict = {}
    errors: list[dict] = []

    try:
        data["users"] = _get_users()
    except ValueError as exc:
        data["users"] = None
        errors.append(error_entry(exc))

    try:
        data["logged_in_sessions"] = _get_logged_in_sessions(context)
    except ValueError as exc:
        data["logged_in_sessions"] = None
        errors.append(error_entry(exc))

    try:
        data["recent_logins"] = _get_recent_logins(context)
    except ValueError as exc:
        data["recent_logins"] = None
        errors.append(error_entry(exc))

    return CollectorResult(
        collector_name="users",
        data=data,
        errors=errors,
        duration_ms=(time.monotonic() - start_time) * 1000,
    )


def _get_users() -> list[dict]:
    """Read `/etc/passwd` and return every account on this system.

    Raises `ValueError` if the file can't be read or a line doesn't
    parse — `/etc/passwd` is a fixed-format system file that's
    essentially guaranteed well-formed on any real Linux system, so a
    malformed line indicates a genuinely broken environment, not a
    normal edge case to skip.
    """
    try:
        raw_text = _PASSWD_PATH.read_text()
    except OSError as exc:
        raise ValueError(f"could not read {_PASSWD_PATH}: {exc}") from exc
    return _parse_passwd(raw_text)


def _parse_passwd(raw_text: str) -> list[dict]:
    """Pure function: `/etc/passwd`'s text in, a list of account dicts
    out. No file I/O — just string parsing, so it can be tested with a
    literal sample string.

    Blank lines and comment lines (starting with `#`) are skipped, not
    treated as malformed — real Ubuntu `/etc/passwd` files rarely have
    either, but macOS's does (a leading `# User Database` header), and
    skipping them costs nothing on the platforms that never produce them.
    """
    users = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        users.append(_parse_passwd_line(line))
    return users


def _parse_passwd_line(line: str) -> dict:
    """Pure function: one `/etc/passwd` line
    (`name:x:uid:gid:comment:home:shell`) in, an account dict out.
    """
    fields = line.split(":")
    if len(fields) < 7:
        raise ValueError(f"could not parse /etc/passwd line: {line!r}")
    name, _password, uid_text, gid_text, _gecos, home, shell = fields[:7]
    try:
        uid = int(uid_text)
        gid = int(gid_text)
    except ValueError as exc:
        raise ValueError(
            f"could not parse uid/gid in /etc/passwd line: {line!r}"
        ) from exc
    return {
        "name": name,
        "uid": uid,
        "gid": gid,
        "home": home,
        "shell": shell,
        "is_system_account": uid < _MIN_HUMAN_UID,
    }


def _get_logged_in_sessions(context: CollectorContext) -> list[dict]:
    """Run `who` and parse it into a list of currently-logged-in
    sessions.

    Raises `ValueError` if the command fails or its output doesn't parse.
    """
    result = run_command(_WHO_COMMAND, timeout=context.default_timeout)
    if not result.succeeded:
        raise ValueError(command_failure_message(_WHO_COMMAND, result))
    return _parse_who_output(result.stdout)


def _parse_who_output(raw_text: str) -> list[dict]:
    """Pure function: `who`'s text in, a list of session dicts out. No
    subprocess calls, no I/O — just string parsing, so it can be tested
    with a literal sample string.
    """
    sessions = []
    for line in raw_text.splitlines():
        if not line.strip():
            continue
        sessions.append(_parse_who_line(line))
    return sessions


def _parse_who_line(line: str) -> dict:
    """Pure function: one `who` line (e.g. `ubuntu pts/0 2026-07-17
    10:02 (192.168.1.5)`) in, a session dict out. The host in
    parentheses is only present for remote logins — `None` otherwise.
    """
    tokens = line.split()
    if len(tokens) < 4:
        raise ValueError(f"could not parse who line: {line!r}")
    name, terminal = tokens[0], tokens[1]
    login_time = f"{tokens[2]} {tokens[3]}"
    host = None
    if len(tokens) > 4 and tokens[4].startswith("(") and tokens[4].endswith(")"):
        host = tokens[4][1:-1]
    return {"name": name, "terminal": terminal, "login_time": login_time, "host": host}


def _get_recent_logins(context: CollectorContext) -> list[dict]:
    """Run `last -n N` and parse it into a list of recent login-history
    entries.

    Raises `ValueError` if the command fails. Malformed or non-login
    individual lines are skipped rather than failing the whole batch
    (see `_parse_last_line`) — `last`'s output shape varies (a session
    still logged in vs. a completed session with a duration vs. a
    reboot marker vs. a trailing "wtmp begins ..." line), and this
    collector only needs the identity fields, not a fully structured
    timeline.
    """
    result = run_command(_LAST_COMMAND, timeout=context.default_timeout)
    if not result.succeeded:
        raise ValueError(command_failure_message(_LAST_COMMAND, result))
    return _parse_last_output(result.stdout)


def _parse_last_output(raw_text: str) -> list[dict]:
    """Pure function: `last -n N`'s text in, a list of login-history
    dicts out. No subprocess calls, no I/O — just string parsing, so it
    can be tested with a literal sample string.
    """
    entries = []
    for line in raw_text.splitlines():
        entry = _parse_last_line(line)
        if entry is not None:
            entries.append(entry)
    return entries


def _parse_last_line(line: str) -> dict | None:
    """Pure function: one `last` line in, a login-history dict out — or
    `None` for a line that isn't a real user login (a blank line, the
    trailing `wtmp begins ...` marker, or a `reboot` pseudo-entry),
    which the caller skips rather than treating as an error.

    Keeps only `name`, `terminal`, and the rest of the line as a raw
    `detail` string — `last`'s date/duration columns vary too much
    (still logged in vs. a completed session with a duration vs. a
    crash marker) to parse into structured fields without guessing.
    """
    tokens = line.split()
    if len(tokens) < 2:
        return None
    name = tokens[0]
    if name in ("wtmp", "reboot"):
        return None
    terminal = tokens[1]
    detail = " ".join(tokens[2:])
    return {"name": name, "terminal": terminal, "detail": detail}
