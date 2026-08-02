"""NodeIQ — a CLI that answers natural-language questions about a Linux
server using real system data, collected into a JSON snapshot and
interpreted by an LLM. See CONTEXT.md for the full architecture.
"""

__version__ = "0.1.0"
"""NodeIQ's own version — recorded in every snapshot's `metadata.
nodeiq_version` field (see docs/snapshot_schema.md Section 2 and
docs/coordinator.md), so a snapshot always says which version of NodeIQ
produced it, even if the schema or collectors change later.
"""
