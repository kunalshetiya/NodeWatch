"""Tier 1 of `ask`'s collector routing: a fixed, deterministic keyword ->
collector mapping.

Free (no LLM call at all) and instant — this is deliberately the first,
cheapest thing tried, and handles the majority of real operator
questions ("what's the cpu usage", "any failed services", "who's logged
in") without ever reaching the more expensive tiers in
`nodeiq.routing.intent_router`/`nodeiq.routing.tool_calling_router`.

`route()` only ever "succeeds" on an unambiguous single-category match —
zero matches (no recognizable keyword at all) or multiple matches (a
question that plausibly spans more than one collector, e.g. "why is my
system slow") both return `None`, deliberately escalating to the next
tier rather than guessing. This is a firm rule, not a fuzzy heuristic:
a genuinely ambiguous or multi-topic question should never be answered
from only one arbitrarily-chosen collector.
"""

import re

_KEYWORD_MAP = {
    "system": {"hostname", "uptime", "kernel", "os", "operating", "architecture"},
    "cpu_memory": {"cpu", "load", "memory", "ram", "swap", "sluggish"},
    "processes": {"process", "processes", "consuming", "pid"},
    "disk": {"disk", "inode", "inodes", "partition", "storage", "filesystem"},
    "services": {"service", "services", "daemon"},
    "scheduled_jobs": {"cron", "crontab", "timer", "timers", "scheduled", "schedule"},
    "permissions": {"permission", "permissions", "owner", "writable", "chmod"},
    "network": {
        "port", "ports", "firewall", "network", "interface", "interfaces",
        "connection", "ufw", "iptables", "nftables",
    },
    "logs": {"log", "logs", "journal", "warning", "warnings"},
    "users": {"user", "users", "who", "login", "logins", "logged", "whoami"},
}
"""One entry per collector. Deliberately plain word sets, not regex or
fuzzy/stemmed matching — the same "simple, deterministic parsing over
clever heuristics" style already used throughout every collector's own
text parsing. Extend by adding words here, not by changing the matching
logic below."""

_WORD_PATTERN = re.compile(r"[a-z0-9]+")


def route(question: str) -> list | None:
    """Return `[collector_name]` if exactly one category's keywords
    appear in `question`, or `None` if zero or more than one category
    matched — both cases mean this tier could not confidently resolve
    the question alone, and the caller should fall through to
    `intent_router`.
    """
    words = _tokenize(question)
    matched = sorted(name for name, keywords in _KEYWORD_MAP.items() if words & keywords)
    if len(matched) == 1:
        return matched
    return None


def _tokenize(question: str) -> set:
    """Pure function: a question in, the set of its lowercase words out
    — whole-word tokens only, so a keyword like "cron" never matches
    inside an unrelated longer word."""
    return set(_WORD_PATTERN.findall(question.lower()))
