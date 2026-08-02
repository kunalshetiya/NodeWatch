"""CLI entry points and command wiring (Phase 5).

See docs/cli_design.md for the full design this package implements: the
CLI is a thin orchestration layer that parses arguments and calls
already-existing functions from `nodeiq.core` / `nodeiq.summary` /
`nodeiq.report` — it contains no business logic of its own.
"""
