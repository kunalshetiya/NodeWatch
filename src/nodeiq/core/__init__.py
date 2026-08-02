"""Core execution infrastructure shared by every collector.

This package has no knowledge of any specific Linux command or snapshot
section — it only provides the reusable building blocks (safe subprocess
execution, a result type, project-specific exceptions, the collector
contract types, and the future scan coordinator) that every collector in
`nodeiq.collectors` is built on top of.
"""
