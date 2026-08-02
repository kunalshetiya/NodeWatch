"""Translate an `answer_question()` exception into one clean,
user-facing message.

Shared by `nodeiq.cli.main._cmd_ask` (the single-shot `ask` subcommand)
and `nodeiq.cli.shell` (the interactive shell's per-question handling)
so the two never drift apart in wording — this is the one place that
translation happens. Kept in its own module (rather than in `main.py`
or `shell.py`) specifically so neither of those two modules needs to
import the other: `shell.py` calls `nodeiq.llm.ask.answer_question()`
directly, exactly like `main.py` does, and both import this module for
the shared error text.
"""

from nodeiq.core.exceptions import SnapshotError
from nodeiq.llm.exceptions import LLMError


def format_ask_error(exc: Exception) -> str:
    """Return the message `ask` (in either form) should print for
    `exc` — never a raw Python exception string, never a traceback.
    """
    if isinstance(exc, SnapshotError):
        return f"No snapshot found: {exc}\n\nRun:\n\n    nodeiq scan\n\nand try again."
    if isinstance(exc, LLMError):
        return f"Could not get an answer: {exc}"
    return f"Could not complete ask: {exc}"
