"""Tier 2 of `ask`'s collector routing: one small, cheap, bounded LLM
classification call — deliberately NOT the full answering call, and NOT
agentic tool-calling.

Only reached when `keyword_router` couldn't confidently resolve a
question to exactly one category (a vague or multi-topic question, e.g.
"why is my system slow"). This tier asks the model one narrow question —
"which of these 10 categories are relevant?" — and nothing else: no
evidence, no answer, no safety guardrails about hallucination (there's
nothing to hallucinate a fact about here, only a classification). This
keeps it far cheaper than `tool_calling_router`, which is a full
multi-round agentic conversation.

Reuses `nodeiq.llm.client.ask_openai()` for the actual request (the same
retry/timeout/error-translation machinery every other OpenAI call in
this project already gets) — only the prompt content is different.
"""

from nodeiq.llm.client import ask_openai

_VALID_CATEGORIES = frozenset(
    {
        "system",
        "cpu_memory",
        "processes",
        "disk",
        "services",
        "scheduled_jobs",
        "permissions",
        "network",
        "logs",
        "users",
    }
)

_INTENT_SYSTEM_PROMPT = """\
You are a routing classifier for NodeIQ, a Linux diagnostics tool. Given \
an operator's question about a Linux server, decide which of the \
following evidence categories are needed to answer it — nothing else.

Categories: system, cpu_memory, processes, disk, services, \
scheduled_jobs, permissions, network, logs, users

Respond with ONLY a comma-separated list of category names from the \
list above that are relevant to the question — no explanation, no \
punctuation beyond the commas. If the question does not relate to any \
of these categories at all, respond with exactly: none\
"""

_PROMPT_VERSION = "intent-v1"


def route(question: str) -> list | None:
    """Ask the model which categories (if any) this question needs.

    Returns a sorted list of one or more valid collector names, or
    `None` if the model said "none" or returned nothing usable — in
    either case the caller should fall through to
    `tool_calling_router`. Any `LLMError` (missing API key, timeout,
    etc.) is not caught here — it propagates to the caller exactly like
    every other OpenAI call in this project, since `tool_calling_router`
    would hit the identical failure immediately after if this tier
    silently swallowed it.
    """
    prompt = {
        "system": _INTENT_SYSTEM_PROMPT,
        "user": f"Question: {question}",
        "prompt_version": _PROMPT_VERSION,
    }
    response = ask_openai(prompt, temperature=0.0)
    return _parse_response(response)


def _parse_response(response: str) -> list | None:
    """Pure function: the model's raw text in, a sorted list of valid
    category names out — or `None` if it said "none" or nothing
    resolvable. Silently drops anything that isn't one of the 10 known
    category names (e.g. a hallucinated or malformed entry) rather than
    raising — a partially-useful classification still beats none.
    """
    cleaned = response.strip().lower()
    if not cleaned or cleaned == "none":
        return None
    names = {name.strip() for name in cleaned.split(",")}
    valid = sorted(names & _VALID_CATEGORIES)
    return valid or None
