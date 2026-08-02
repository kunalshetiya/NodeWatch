"""Tier 3 of `ask`'s collector routing: full OpenAI function-calling —
the last resort, only reached when neither `keyword_router` nor
`intent_router` could confidently resolve which collector(s) a question
needs.

Unlike the other two tiers (which only ever return a `list[str]` of
collector names, handing off to the normal evidence-answering pipeline
afterward), this tier produces the **final answer directly** — it's a
genuinely agentic, multi-round conversation: the model requests
evidence via tools, sees the real results, and may request more before
answering. This is real, deliberate asymmetry with tiers 1/2, and it's
why this tier is reserved for last: it's the only one that can adapt
mid-conversation, and the only one expensive enough (potentially
several round-trips) to justify using only when the cheaper tiers
couldn't resolve the question at all.

Every collector is exposed as a callable tool with **no parameters** —
the model can only ever invoke one of these ten fixed, pre-defined
functions (each of which runs the exact same trusted collector code
every other tier already uses), never construct or run an arbitrary
shell command. This is the same safety guarantee the rest of the
project already relies on, extended to a tool-calling interface rather
than loosened by one.

The system prompt reused here is the *exact* evidence-boundary guardrail
text every other answer in this project is governed by
(`nodeiq.llm.prompt._SYSTEM_PROMPT`), with one small addition explaining
the tool-calling mechanism itself — so a tier-3 answer is held to
precisely the same "never hallucinate, never invent a cause, say so
when evidence is insufficient" standard as a tier-1 or tier-2 answer.
"""

import json

from nodeiq.core.coordinator import run_selected_collectors
from nodeiq.llm.client import send_chat_request
from nodeiq.llm.exceptions import LLMResponseError
from nodeiq.llm.prompt import _SYSTEM_PROMPT
from nodeiq.summary import summarize_snapshot

_MAX_TOOL_CALL_ROUNDS = 3
"""One initial (forced) tool-call round plus up to two more rounds of
the model adaptively requesting further evidence — mirrors
`llm/client.py`'s own `_MAX_ATTEMPTS` retry-cap pattern, applied here to
bound how long a single question's tool-calling conversation can run,
in cost and latency, rather than to a transient-failure retry."""

_TOOL_DESCRIPTIONS = {
    "system": "Get this machine's hostname, OS, kernel version, architecture, and uptime.",
    "cpu_memory": "Get current CPU usage, memory usage, swap usage, and load average.",
    "processes": (
        "Get process counts (including zombie/blocked processes) and the top "
        "processes by memory and CPU usage."
    ),
    "disk": "Get disk space and inode usage for every mounted filesystem.",
    "services": (
        "Get systemd service status: which services are running, failed, or restarting."
    ),
    "scheduled_jobs": "Get cron jobs and systemd timers configured on this system.",
    "permissions": (
        "Get ownership and permission info for a small set of security-sensitive "
        "paths (/etc/passwd, /etc/shadow, /etc/ssh, /var/log)."
    ),
    "network": (
        "Get network interfaces, the default route, listening ports, and firewall status."
    ),
    "logs": "Get recent warning and error log entries from the systemd journal.",
    "users": (
        "Get user accounts on this system, who is currently logged in, and recent "
        "login history."
    ),
}
"""One tool per collector, no parameters — each tool's only job is
"gather this specific category of evidence right now," matching exactly
what `run_selected_collectors([name])` already does."""

_TOOL_CALLING_PREAMBLE = """

You do not yet have any evidence — you must call one or more of the \
tools available to you to gather it before answering. You may call \
more than one tool at once if the question needs more than one category \
of evidence. After seeing the results, you may call additional tools if \
you discover you need more, up to a small limit — after that, you must \
answer using whatever evidence you have gathered, applying the same \
"insufficient evidence" rule above to anything you still don't have \
data for."""

_FINAL_ROUND_REMINDER = (
    "You must answer now, in plain text, using only the evidence already "
    "gathered above — no more tools are available this round."
)


def answer(question: str) -> str:
    """Resolve which collector(s) this question needs via OpenAI
    function-calling, gather their evidence live as the model requests
    it, and return the final answer.

    Raises `LLMResponseError` if no usable text answer was produced
    within `_MAX_TOOL_CALL_ROUNDS` — the same "a caller acting on a
    malformed answer is worse than a clear failure" principle
    `llm/client.py`'s own `_extract_answer()` already applies.
    """
    tools = _build_tools()
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT + _TOOL_CALLING_PREAMBLE},
        {"role": "user", "content": f"Question: {question}"},
    ]

    for round_number in range(1, _MAX_TOOL_CALL_ROUNDS + 1):
        is_final_round = round_number == _MAX_TOOL_CALL_ROUNDS
        if is_final_round:
            messages.append({"role": "system", "content": _FINAL_ROUND_REMINDER})
            response = send_chat_request(messages, temperature=0.0)
        else:
            tool_choice = "required" if round_number == 1 else "auto"
            response = send_chat_request(
                messages, temperature=0.0, tools=tools, tool_choice=tool_choice
            )

        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None)

        if not tool_calls:
            content = message.content
            if not content or not content.strip():
                raise LLMResponseError("OpenAI returned an empty response.")
            return content

        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [tc.model_dump() for tc in tool_calls],
            }
        )
        _append_tool_results(messages, tool_calls)

    raise LLMResponseError(
        f"Could not resolve an answer within {_MAX_TOOL_CALL_ROUNDS} tool-call round(s)."
    )


def _build_tools() -> list:
    """The OpenAI function-calling `tools` list — one entry per
    collector, each with an empty parameter schema, since every
    collector's `collect()` already takes no question-specific input."""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
        for name, description in _TOOL_DESCRIPTIONS.items()
    ]


def _append_tool_results(messages: list, tool_calls: list) -> None:
    """Run whichever collector(s) this round's tool calls named, and
    append one `"tool"`-role message per `tool_call_id` — OpenAI
    requires a response for every tool call in the preceding assistant
    message, so an unrecognized name (which shouldn't happen, since the
    API itself only ever offers the names in `_build_tools()`) still
    gets a response rather than breaking the conversation.
    """
    requested_names = sorted({tc.function.name for tc in tool_calls})
    valid_names = [name for name in requested_names if name in _TOOL_DESCRIPTIONS]

    sections = {}
    if valid_names:
        snapshot = run_selected_collectors(valid_names)
        sections = summarize_snapshot(snapshot)["sections"]

    for tool_call in tool_calls:
        section_evidence = sections.get(
            tool_call.function.name, {"available": False, "evidence": {}}
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(section_evidence),
            }
        )
