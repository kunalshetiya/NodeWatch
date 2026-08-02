"""Redacts secret-shaped text before it enters a snapshot.

Used by `collectors/logs.py` on each journal entry's `message` field —
the one place free-form, human-authored text (an application's own log
line) enters a snapshot. Nothing here ever touches the real system log
itself (`journalctl`'s own output on disk is never modified); this only
sanitizes NodeIQ's own collected *copy* of that text, before it can ever
reach a Summary, a report, or an LLM prompt (`CONTEXT.md` Section 4).

Deliberately simple and deterministic: fixed patterns, no heuristics
that could vary run to run, no attempt at exhaustive secret-scanning.
Three pattern families, matching this phase's explicit examples:

1. `<name>=<value>` / `<name>: <value>` assignments where `<name>`
   looks like a secret (a whole `_`/`.`/`-`-delimited segment matching
   "key", "token", "password", "secret", "credential", etc.) —
   `OPENAI_API_KEY=sk-proj-abc123` -> `OPENAI_API_KEY=[REDACTED]`.
2. `Bearer <token>` HTTP authorization headers.
3. PEM private key blocks (`-----BEGIN ... PRIVATE KEY-----` ...
   `-----END ... PRIVATE KEY-----`).

Whole-segment matching (not substring matching) is deliberate: a naive
"contains 'key'" check would also redact `monkey=true` or
`disk_usage=95`'s neighbors — false positives this phase's own "ensure
no false assumptions are introduced" instruction calls out directly.
"""

import re

REDACTED_PLACEHOLDER = "[REDACTED]"

_SECRET_NAME_SEGMENTS = frozenset(
    {
        "key",
        "apikey",
        "token",
        "tokens",
        "secret",
        "secrets",
        "password",
        "passwd",
        "credential",
        "credentials",
    }
)
"""A name is treated as secret-shaped if any one of its `_`/`.`/`-`
delimited segments, lowercased, is exactly one of these — not merely
*contains* one as a substring (see module docstring)."""

_ASSIGNMENT_PATTERN = re.compile(
    r"""(?P<key>[A-Za-z][A-Za-z0-9]*(?:[_.\-][A-Za-z0-9]+)*)
        (?P<sep>\s*[:=]\s*)
        (?P<quote>['"]?)
        (?P<value>[^\s'"]+)
        (?P=quote)""",
    re.VERBOSE,
)

_BEARER_TOKEN_PATTERN = re.compile(r"\bBearer\s+\S+", re.IGNORECASE)

_PRIVATE_KEY_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)


def redact_secrets(text: str) -> str:
    """Return `text` with secret-shaped substrings replaced by
    `[REDACTED]`. Pure function: same input always produces the same
    output. Returns `text` unchanged (including `None`/empty) if
    nothing secret-shaped is found.
    """
    if not text:
        return text
    text = _PRIVATE_KEY_BLOCK_PATTERN.sub(REDACTED_PLACEHOLDER, text)
    text = _BEARER_TOKEN_PATTERN.sub(f"Bearer {REDACTED_PLACEHOLDER}", text)
    text = _ASSIGNMENT_PATTERN.sub(_redact_assignment_if_secret_shaped, text)
    return text


def _redact_assignment_if_secret_shaped(match: re.Match) -> str:
    key = match.group("key")
    if not _looks_like_a_secret_name(key):
        return match.group(0)
    return f"{key}{match.group('sep')}{REDACTED_PLACEHOLDER}"


def _looks_like_a_secret_name(identifier: str) -> bool:
    """Pure function: `True` if any whole `_`/`.`/`-`-delimited segment
    of `identifier`, lowercased, is a known secret-shaped name.
    """
    segments = re.split(r"[_.\-]", identifier.lower())
    return any(segment in _SECRET_NAME_SEGMENTS for segment in segments)
