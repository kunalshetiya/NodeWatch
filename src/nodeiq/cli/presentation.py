"""Shared terminal-presentation helpers for the CLI and interactive
shell.

Presentation only: no business logic, no decision about what's
noteworthy (that already belongs to `nodeiq.summary`/`nodeiq.report`),
and no ANSI colour codes — output stays readable on any terminal. This
is the one place the startup banner and the question/answer rendering
are built, so `nodeiq ask` and the interactive shell's per-question
output can never drift apart in formatting — both call the exact same
functions.
"""

SEPARATOR = "=" * 70
"""Matches the width/character `nodeiq.report.format_report()` already
uses for its own header — reused here (as a literal, not an import,
since `nodeiq.report` doesn't export it) so the shell's banner and
`format_report()`'s output read as one consistent visual style."""


def render_banner(lines: list) -> str:
    """A boxed banner: a separator, each line, a separator.

    Used only for the interactive shell's startup banner — never
    printed for a single `scan`/`report`/`ask` invocation.
    """
    return "\n".join([SEPARATOR, *lines, SEPARATOR])


def render_qa(question: str, answer: str) -> str:
    """Render one question/answer pair with a clear, consistent shape,
    bounded by the same separator the banner and `format_report()` use
    — so a single answer reads as one clearly-bounded block, especially
    useful in the interactive shell where several exchanges scroll by
    in a row.

    `answer` is never rewritten, reformatted, or trimmed — only the
    surrounding "Question:"/"Answer:" labels and separators are added.
    """
    return f"{SEPARATOR}\nQuestion: {question}\n\nAnswer:\n{answer}\n{SEPARATOR}"
