"""NodeIQ's interactive shell: a REPL entered by running `nodeiq` with
no subcommand.

This module adds no new business logic. Every question typed at the
`NodeIQ>` prompt is answered by calling the exact same
`nodeiq.llm.ask.answer_question()` the `ask` subcommand already calls
— there is no second orchestration path and no second place a prompt
is built. Errors are translated with the exact same
`nodeiq.cli.ask_errors.format_ask_error()` `ask` uses. The only things
genuinely new here are the platform-detection entry gate and startup
banner (this is the "front door" a first-time user sees, so it's the
one place NodeIQ explains what it does and doesn't support) and the
REPL loop itself: reading lines, recognizing `help`/`exit`/`quit`/
`clear`, and otherwise treating a line as a question.
"""

import sys

from nodeiq.cli.ask_errors import format_ask_error
from nodeiq.cli.freshness import check_snapshot_freshness
from nodeiq.cli.platform_info import detect_platform
from nodeiq.cli.presentation import render_banner, render_qa
from nodeiq.llm.ask import answer_question

_PROMPT = "NodeIQ> "
_EXIT_COMMANDS = {"exit", "quit"}
_CLEAR_SEQUENCE = "\033[2J\033[H"

_HELP_TEXT = (
    "Type a question in plain English, for example:\n"
    "  Why is memory usage high?\n"
    "  Analyze disk usage\n"
    "  Show failed services\n\n"
    "Commands:\n"
    "  help   Show this message\n"
    "  clear  Clear the screen\n"
    "  exit   Leave NodeIQ (quit works too)"
)


def run_shell() -> int:
    """Enter the interactive shell and return an exit code — same
    convention every `_cmd_*` handler in `nodeiq.cli.main` follows.

    On a non-Linux platform, prints a friendly explanation and returns
    without ever entering the read loop — NodeIQ v1 never attempts
    Linux diagnostics on a platform it doesn't support.
    """
    platform_info = detect_platform()

    if not platform_info["is_linux"]:
        print(_unsupported_platform_message(platform_info))
        return 0

    print(_startup_banner(platform_info))

    while True:
        try:
            line = input(_PROMPT)
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        question = line.strip()
        if not question:
            continue

        lowered = question.lower()
        if lowered in _EXIT_COMMANDS:
            return 0
        if lowered == "help":
            print(_HELP_TEXT)
            continue
        if lowered == "clear":
            print(_CLEAR_SEQUENCE, end="")
            continue

        try:
            _handle_question(question)
        except KeyboardInterrupt:
            # A question in flight (e.g. waiting on OpenAI) can also be
            # interrupted — this must exit as cleanly as interrupting
            # at the prompt itself does, never a raw traceback.
            print()
            return 0


def _handle_question(question: str) -> None:
    """Answer one question via the existing `ask` pipeline and render
    it — or print a clean error and let the loop continue, since one
    failed question must never end the whole session.
    """
    try:
        result = answer_question(question)
    except Exception as exc:
        print(format_ask_error(exc), file=sys.stderr)
        return

    freshness_warning = check_snapshot_freshness(result["snapshot_metadata"])
    if freshness_warning:
        print(freshness_warning, file=sys.stderr)

    print()
    print(render_qa(question, result["answer"]))
    print()


def _startup_banner(platform_info: dict) -> str:
    return render_banner(
        [
            "NodeIQ v1",
            "AI Linux Diagnostics Assistant",
            f"{platform_info['description']} detected",
            "",
            "Type 'help' for commands.",
            "Type 'exit' to quit.",
        ]
    )


def _unsupported_platform_message(platform_info: dict) -> str:
    return (
        "Detected platform:\n\n"
        f"{platform_info['description']}\n"
        f"Architecture: {platform_info['machine']}\n\n"
        "NodeIQ v1 currently supports Linux systems only.\n\n"
        "This platform is outside the current project scope.\n"
        "Support for macOS and Windows is planned for future versions."
    )
