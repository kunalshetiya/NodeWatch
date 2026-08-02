"""Tiered collector routing for `nodeiq ask`.

Replaces "always run all 10 collectors" with selective, on-demand
collection: three tiers, tried in order, each one only reached if the
previous one couldn't confidently resolve which collector(s) a question
actually needs — `keyword_router` (free, deterministic), `intent_router`
(one small, cheap LLM classification call), and `tool_calling_router`
(full OpenAI function-calling, last resort). See
`nodeiq.llm.ask.answer_question()` for how these are actually chained
together, and the project's own design memory for the full rationale.
"""
