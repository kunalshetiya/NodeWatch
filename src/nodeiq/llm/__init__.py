"""LLM integration (Phase 6).

See docs/prompt_builder_design.md for the full design this package
implements. `nodeiq.llm.prompt` is the only module so far — it builds
prompts and nothing else: no OpenAI client, no network calls, and no
dependency on the CLI, the collectors, or the coordinator.
"""
