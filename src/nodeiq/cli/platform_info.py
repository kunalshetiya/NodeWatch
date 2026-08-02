"""Platform detection for the interactive shell's startup banner and
entry gate.

This is CLI-layer UX infrastructure, not a collector: it never runs as
part of a scan, never produces snapshot data, and is never registered
with `nodeiq.core.coordinator`. It is deliberately independent of
`nodeiq.collectors.system` (which reads `/etc/os-release` for a
different reason — populating one snapshot field, under that
collector's own error-tracking contract) — this module exists purely
to answer one narrow, presentation-only question before the
interactive shell decides whether to continue: "is this a Linux
machine, and if so, which distribution?"
"""

import platform
from pathlib import Path

_OS_RELEASE_PATH = Path("/etc/os-release")


def detect_platform() -> dict:
    """Return `{"system": ..., "machine": ..., "description": ..., "is_linux": ...}`.

    `system` and `machine` are Python's own `platform.system()`/
    `platform.machine()` values (e.g. `"Linux"`/`"aarch64"`).
    `description` is a human-readable label: a Linux distribution's
    `PRETTY_NAME` (e.g. `"Ubuntu 24.04.4 LTS"`) when available, a
    macOS/Windows version string otherwise, or the bare `system` name
    as a last resort.
    """
    system = platform.system()
    machine = platform.machine()

    if system == "Linux":
        description = _linux_description() or "Linux"
    elif system == "Darwin":
        description = f"macOS {platform.mac_ver()[0]}"
    elif system == "Windows":
        description = f"Windows {platform.release()}"
    else:
        description = system

    return {
        "system": system,
        "machine": machine,
        "description": description,
        "is_linux": system == "Linux",
    }


def _linux_description() -> str | None:
    """Read `/etc/os-release` and return its `PRETTY_NAME`, or `None` if
    the file is missing, unreadable, or doesn't have one.
    """
    try:
        raw_text = _OS_RELEASE_PATH.read_text()
    except OSError:
        return None
    return _parse_pretty_name(raw_text)


def _parse_pretty_name(raw_text: str) -> str | None:
    """Pure function: `/etc/os-release`'s text in, its `PRETTY_NAME`
    value out (or `None` if absent) — no file I/O, so it's directly
    testable with a literal sample string.
    """
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key == "PRETTY_NAME":
            return value.strip().strip('"').strip("'")
    return None
