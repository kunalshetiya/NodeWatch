"""NodeIQ's command-line interface.

Implements `docs/cli_design.md` exactly: `scan`, `report`, and `ask` —
plus, per Phase 7A, an interactive shell entered by running `nodeiq`
with no subcommand at all. This module is a thin orchestration layer
only — it parses arguments and calls already-existing functions from
`nodeiq.core.coordinator`, `nodeiq.core.snapshot`, `nodeiq.summary`,
`nodeiq.report`, and `nodeiq.llm.ask` in the order each command's
design specifies. It computes no status, formats no evidence, builds no
prompt, and makes no decision about what's noteworthy — every one of
those already belongs to a lower layer. `ask`'s entire pipeline (load a
snapshot, summarize it, build a prompt, call OpenAI) is one call to
`nodeiq.llm.ask.answer_question()` — this module never orchestrates
those four steps itself. Running `nodeiq` with no subcommand dispatches
to `nodeiq.cli.shell.run_shell()`, which reuses this exact same
`answer_question()` pipeline for every question typed at its prompt —
there is no second `ask` implementation anywhere in this project.

Reachable two ways (see docs/cli_design.md Section 3):

    python -m nodeiq [command]      # via src/nodeiq/__main__.py
    nodeiq [command]                # via the console-script entry point
                                     # in pyproject.toml, after `pip install -e .`
"""

import argparse
import sys
from pathlib import Path

from nodeiq import __version__ as _NODEIQ_VERSION
from nodeiq.cli.ask_errors import format_ask_error
from nodeiq.cli.freshness import check_snapshot_freshness
from nodeiq.cli.presentation import render_qa
from nodeiq.cli.shell import run_shell
from nodeiq.core.coordinator import run_scan
from nodeiq.core.exceptions import SnapshotError
from nodeiq.core.snapshot import load_latest_snapshot, load_snapshot, save_snapshot
from nodeiq.llm.ask import answer_question
from nodeiq.llm.exceptions import LLMError
from nodeiq.report import format_report
from nodeiq.summary import summarize_snapshot

_EXAMPLES_EPILOG = """\
examples:
  nodeiq                              enter the interactive shell
  nodeiq scan                         collect a fresh snapshot
  nodeiq report                       show a report for the latest snapshot
  nodeiq report --section disk        show only the disk section
  nodeiq ask what service failed      ask a question -- no quotes needed
                                       (auto-scans first if no snapshot exists yet)
"""

# --- Exit codes (docs/cli_design.md Section 5, "Exit Code Summary") -------------

EXIT_OK = 0
EXIT_NO_SNAPSHOT = 1
EXIT_INVALID_ARGS = 2
EXIT_LLM_UNAVAILABLE = 3
EXIT_INTERNAL_FAILURE = 4


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level `nodeiq` parser and its three subcommands,
    exactly as designed in `docs/cli_design.md` Section 3.
    """
    parser = argparse.ArgumentParser(
        prog="nodeiq",
        description="Answer natural-language operational questions about "
        "a Linux server using real system data. Run with no command to "
        "enter the interactive shell.",
        epilog=_EXAMPLES_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"NodeIQ {_NODEIQ_VERSION}"
    )
    subparsers = parser.add_subparsers(dest="command", required=False)

    subparsers.add_parser("scan", help="Collect a fresh snapshot of system state.")

    report_parser = subparsers.add_parser(
        "report", help="Print a human-readable report from a snapshot."
    )
    report_parser.add_argument(
        "--section", help="Only print this one section (e.g. disk)."
    )
    report_source = report_parser.add_mutually_exclusive_group()
    report_source.add_argument(
        "--snapshot", help="Load this snapshot file instead of the latest."
    )
    report_source.add_argument(
        "--fresh", action="store_true", help="Run a new scan first, then report on it."
    )

    ask_parser = subparsers.add_parser(
        "ask", help="Ask a natural-language question about the machine."
    )
    ask_parser.add_argument(
        "question",
        nargs="+",
        help="The question to ask — no quotes needed (e.g. nodeiq ask what failed).",
    )
    ask_parser.add_argument(
        "--snapshot", help="Answer using this snapshot file instead of the latest."
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the requested command's handler.

    Returns an exit code rather than calling `sys.exit()` itself, so it's
    directly testable and so the console-script entry point
    (`pyproject.toml`) can do `sys.exit(main())` on top of it.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        return run_shell()
    if args.command == "scan":
        return _cmd_scan()
    if args.command == "report":
        return _cmd_report(args)
    return _cmd_ask(args)


def _cmd_scan() -> int:
    """`nodeiq scan`: `run_scan()` -> `save_snapshot()` -> print a
    confirmation. See docs/cli_design.md Section 4.1.
    """
    try:
        snapshot = run_scan()
        saved_path = save_snapshot(snapshot)
    except Exception as exc:
        print(f"Could not complete scan: {exc}", file=sys.stderr)
        return EXIT_INTERNAL_FAILURE

    print(_scan_confirmation(snapshot, saved_path))
    return EXIT_OK


def _cmd_report(args: argparse.Namespace) -> int:
    """`nodeiq report`: load a snapshot (default: latest; `--fresh`: scan
    first; `--snapshot PATH`: a specific file), summarize it, optionally
    narrow to one `--section`, format it, and print it. See
    docs/cli_design.md Section 4.2.
    """
    try:
        if args.fresh:
            snapshot = run_scan()
            saved_path = save_snapshot(snapshot)
            print(_scan_confirmation(snapshot, saved_path))
            print()
        elif args.snapshot:
            snapshot = load_snapshot(args.snapshot)
        else:
            snapshot = load_latest_snapshot()
    except SnapshotError as exc:
        print(f"No snapshot available: {exc}", file=sys.stderr)
        return EXIT_NO_SNAPSHOT
    except Exception as exc:
        print(f"Could not complete report: {exc}", file=sys.stderr)
        return EXIT_INTERNAL_FAILURE

    if not args.fresh:
        freshness_warning = check_snapshot_freshness(snapshot.get("metadata"))
        if freshness_warning:
            print(freshness_warning, file=sys.stderr)

    summary = summarize_snapshot(snapshot)

    if args.section:
        filtered = _select_section(summary, args.section)
        if filtered is None:
            available = ", ".join(sorted(summary["sections"]))
            print(
                f"Unknown section '{args.section}'. Available sections: {available}",
                file=sys.stderr,
            )
            return EXIT_INVALID_ARGS
        summary = filtered

    print(format_report(summary))
    return EXIT_OK


def _cmd_ask(args: argparse.Namespace) -> int:
    """`nodeiq ask`: load a snapshot (default: latest, auto-scanning
    first if none exists yet; `--snapshot PATH`: a specific file),
    summarize it, build a prompt, and answer via OpenAI — the entire
    pipeline is one call to `nodeiq.llm.ask.answer_question()`. See
    docs/cli_design.md Section 4.3 and docs/prompt_builder_design.md.

    `question` is `nargs="+"` (one or more words) so a question reads
    naturally without shell quoting — `nodeiq ask what failed` and
    `nodeiq ask "what failed"` both work identically.
    """
    question = " ".join(args.question)

    try:
        result = answer_question(question, snapshot_path=args.snapshot)
    except SnapshotError as exc:
        print(format_ask_error(exc), file=sys.stderr)
        return EXIT_NO_SNAPSHOT
    except LLMError as exc:
        print(format_ask_error(exc), file=sys.stderr)
        return EXIT_LLM_UNAVAILABLE
    except Exception as exc:
        print(format_ask_error(exc), file=sys.stderr)
        return EXIT_INTERNAL_FAILURE

    freshness_warning = check_snapshot_freshness(result["snapshot_metadata"])
    if freshness_warning:
        print(freshness_warning, file=sys.stderr)
    print(render_qa(question, result["answer"]))
    return EXIT_OK


def _scan_confirmation(snapshot: dict, saved_path: Path) -> str:
    """The two-line confirmation `scan` prints, and `report --fresh`
    prints before its report: how many collectors succeeded (from the
    snapshot's own `metadata.collector_count` and `collection_errors` —
    no new computation), and where the snapshot was saved.
    """
    metadata = snapshot.get("metadata") or {}
    collector_count = metadata.get("collector_count")
    collection_errors = snapshot.get("collection_errors") or {}
    error_count = len(collection_errors)

    if collector_count is None:
        summary_line = "Scan complete."
    else:
        succeeded = collector_count - error_count
        if error_count:
            summary_line = (
                f"Scan complete: {succeeded}/{collector_count} collectors succeeded "
                f"({error_count} error(s) — see snapshot for details)."
            )
        else:
            summary_line = f"Scan complete: {succeeded}/{collector_count} collectors succeeded."

    return f"{summary_line}\nSnapshot saved to: {saved_path}"


def _select_section(summary: dict, section_name: str) -> dict | None:
    """Return a copy of `summary` narrowed to just `section_name`, or
    `None` if it isn't one of the sections this Summary actually has.

    Deliberately plain dict filtering, not a new parameter on
    `summarize_snapshot()`/`format_report()` — both already work
    correctly on an arbitrary subset of sections (docs/cli_design.md
    Section 4.2, docs/report_formatter.md).
    """
    if section_name not in summary["sections"]:
        return None
    return {**summary, "sections": {section_name: summary["sections"][section_name]}}
