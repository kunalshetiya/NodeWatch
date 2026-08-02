"""Report Formatter: turns a Summary into a human-readable terminal report.

This module is presentation only. It performs no summarization (see
nodeiq.summary) and no data collection (see nodeiq.core.coordinator) —
it only converts an already-computed Summary dict into plain text.
`format_report()` is a pure function: the same Summary always produces
the same report string, since every field it reads (`status`,
`headline`, `highlights`, `concerns`) was already made deterministic one
layer down, in nodeiq.summary. This module adds no new judgment of its
own — see docs/report_formatter.md for the full design rationale.

Typical usage:

    from nodeiq.summary import summarize_snapshot
    from nodeiq.report import format_report

    summary = summarize_snapshot(snapshot)
    print(format_report(summary))
"""

_SECTION_TITLES = {
    "cpu_memory": "CPU & Memory",
}
"""Display titles for section keys whose default `.title()`-cased form
would read poorly (just the one acronym among the 9 v1 sections). Any
section not listed here falls back to `<name>.replace("_", " ").title()`
— so a future section works without needing an entry here."""


def format_report(summary: dict) -> str:
    """Render a Summary (see nodeiq.summary.summarize_snapshot) as a
    plain-text report for a terminal.

    Never prints raw JSON or a section's full `evidence` dict — only the
    already human-readable `headline`, `highlights`, and `concerns`
    fields each summarizer produced. A missing `sections` entry, or a
    missing/empty section, is rendered as a plain, clean line rather
    than raising or printing an empty placeholder.
    """
    lines = [
        "=" * 70,
        "NodeIQ Report",
        f"Host: {summary.get('hostname') or 'unknown host'}",
        f"Snapshot: {summary.get('snapshot_timestamp') or 'unknown'}",
        "=" * 70,
    ]

    sections = summary.get("sections") or {}
    for section_name, section in sections.items():
        lines.append("")
        lines.extend(_format_section(section_name, section or {}))

    return "\n".join(lines) + "\n"


def _format_section(section_name: str, section: dict) -> list:
    """Render one SectionSummary as a list of lines: a title/status
    heading, the headline, each highlight, and — only if any exist —
    a "Concerns:" block.
    """
    title = _SECTION_TITLES.get(section_name, section_name.replace("_", " ").title())
    status = (section.get("status") or "unknown").upper()

    lines = [f"{title} [{status}]"]

    headline = section.get("headline")
    if headline:
        lines.append(headline)

    for highlight in section.get("highlights") or []:
        lines.append(f"  - {highlight}")

    concerns = section.get("concerns") or []
    if concerns:
        lines.append("  Concerns:")
        for concern in concerns:
            lines.append(f"    - {concern}")

    return lines
