"""Ask orchestration: the one place that resolves which collector(s) a
question needs, gathers their evidence, and composes the Summary
Engine, the Prompt Builder, and the OpenAI client into a single answer.

Collection is now **on-demand and tiered**, not a full, always-run
9/10-collector sweep read from a saved snapshot file. Three routing
tiers are tried in order, each only reached if the previous one
couldn't confidently resolve which collector(s) the question needs:

    nodeiq.routing.keyword_router   (free, instant, deterministic)
    nodeiq.routing.intent_router    (one small, cheap LLM classification call)
    nodeiq.routing.tool_calling_router  (full OpenAI function-calling, last resort)

Whichever tier resolves, the relevant collector(s) run **live, right
then** (`nodeiq.core.coordinator.run_selected_collectors()`) — never
from a stale, previously-saved snapshot. This is a deliberate trade:
every `ask` now does real collection work instead of reading a file, in
exchange for evidence that's always current as of the moment asked, at
a fraction of the cost of running every collector for every question.

If an answer comes back explicitly insufficient (the model's own
standardized "the evidence does not contain enough information..."
phrasing — see `nodeiq.llm.prompt._SYSTEM_PROMPT`), that's treated as a
signal the routing tier may have resolved too narrowly, not as the final
word: the question is retried against the next, broader tier, up to a
last-resort full sweep of every collector. This guards against a tier
picking the wrong collector(s) silently — the user never sees "no
information" without the pipeline first trying a wider net.

`nodeiq scan`/`nodeiq report` are unaffected by any of this — they still
run the full sweep and persist it to `snapshots/`, exactly as before.
Passing an explicit `snapshot_path` to `answer_question()` (the CLI's
`ask --snapshot PATH`) also bypasses all routing entirely and answers
from that exact file, unchanged from previous behavior — routing only
applies to the default, no-explicit-snapshot case.

Typical usage (this is exactly what `nodeiq ask` does):

    from nodeiq.llm.ask import answer_question

    result = answer_question("What service failed?")
    print(result["answer"])
"""

from pathlib import Path

from nodeiq.core.coordinator import _COLLECTOR_BY_NAME, run_selected_collectors
from nodeiq.core.snapshot import load_snapshot
from nodeiq.llm.client import ask_openai
from nodeiq.llm.prompt import build_prompt
from nodeiq.routing import intent_router, keyword_router, tool_calling_router
from nodeiq.summary import summarize_snapshot

_INSUFFICIENT_EVIDENCE_MARKERS = (
    "does not contain enough information",
    "does not provide enough information",
    "not enough information to determine",
    "insufficient information",
    "does not contain sufficient information",
)
"""Substring markers for the system prompt's standardized insufficiency
phrasing (`nodeiq.llm.prompt._SYSTEM_PROMPT`'s "How to phrase
uncertainty" section prescribes "does not contain enough information")
— used only to decide whether to escalate to a broader collector-routing
tier, never to change what's shown to the user. Several close
paraphrasings are checked, not just the one exact prescribed phrase:
real-model testing found the model doesn't always reproduce that exact
wording verbatim (e.g. "does not *provide* enough information" for a
multi-topic answer) even while following the underlying rule — a single
exact-string check silently missed those and skipped escalation when it
should have triggered. Still an imperfect heuristic, just a less
brittle one."""


def answer_question(question: str, snapshot_path: Path | str | None = None) -> dict:
    """Answer one natural-language question about the machine.

    If `snapshot_path` is given, that exact file is loaded and used as
    the sole evidence source — no routing, no on-demand collection, no
    escalation; a missing/malformed file at that path is a real error,
    exactly as before.

    Otherwise, resolves which collector(s) the question needs via the
    three-tier router (see module docstring), collects only those, live,
    and answers. If that answer is explicitly insufficient, escalates to
    the next tier and tries again, up to a final full-collector sweep.

    Returns `{"answer": <str>, "snapshot_metadata": <dict>}` — `answer`
    exactly as `ask_openai()`/`tool_calling_router.answer()` returned it,
    unchanged; `snapshot_metadata` is whichever collection attempt's own
    `metadata` dict actually produced the returned answer (see
    `docs/snapshot_schema.md`), including `collection_mode`/
    `collectors_run` for on-demand collection.

    Raises whatever the functions it calls already raise —
    `nodeiq.core.exceptions.SnapshotError` for a missing/malformed
    snapshot at an explicit `snapshot_path`, or one of
    `nodeiq.llm.exceptions`' `LLMError` subclasses for anything that
    goes wrong talking to OpenAI. Translating any exception into a
    clean, user-facing message and an exit code is the CLI's job
    (`nodeiq.cli.main._cmd_ask`), exactly as before.
    """
    if snapshot_path:
        snapshot = load_snapshot(snapshot_path)
        summary = summarize_snapshot(snapshot)
        prompt = build_prompt(question, summary)
        answer = ask_openai(prompt)
        return {"answer": answer, "snapshot_metadata": snapshot.get("metadata") or {}}

    return _answer_via_routing(question)


def _answer_via_routing(question: str) -> dict:
    """The on-demand, tiered-routing path — resolve, collect, answer,
    and escalate to a broader tier if the answer comes back
    insufficient. See module docstring for the full tier order.
    """
    names = keyword_router.route(question)
    if names:
        result = _collect_and_answer(question, names)
        if not _is_insufficient(result["answer"]):
            return result

    names = intent_router.route(question)
    if names:
        result = _collect_and_answer(question, names)
        if not _is_insufficient(result["answer"]):
            return result

    tool_calling_answer = tool_calling_router.answer(question)
    if not _is_insufficient(tool_calling_answer):
        return {"answer": tool_calling_answer, "snapshot_metadata": {}}

    # Last resort: every collector this system has, before giving up on
    # finding a broader answer than what's already been tried.
    return _collect_and_answer(question, sorted(_COLLECTOR_BY_NAME))


def _collect_and_answer(question: str, names: list) -> dict:
    """Run exactly the given collector(s) live, summarize, build a
    prompt, and answer — the on-demand counterpart to loading a saved
    snapshot. Shared by every tier's resolution and the final
    full-sweep fallback, so this composition exists in exactly one
    place.
    """
    snapshot = run_selected_collectors(names)
    summary = summarize_snapshot(snapshot)
    prompt = build_prompt(question, summary)
    answer = ask_openai(prompt)
    return {"answer": answer, "snapshot_metadata": snapshot.get("metadata") or {}}


def _is_insufficient(answer: str) -> bool:
    """Pure function: does `answer` use the system prompt's own
    standardized insufficiency phrasing (or a close paraphrasing of
    it)? See `_INSUFFICIENT_EVIDENCE_MARKERS`'s docstring for why
    several markers are checked, not just one exact phrase."""
    lowered = answer.lower()
    return any(marker in lowered for marker in _INSUFFICIENT_EVIDENCE_MARKERS)
