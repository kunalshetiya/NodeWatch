"""End-to-end integration test for nodeiq.collectors.scheduled_jobs.

Unlike test_scheduled_jobs.py, nothing here is mocked — this calls the
real `collect()`, which reads the real cron files and runs the real
`systemctl` on this machine. That only makes sense on a real Linux
system with systemd (see DECISIONS.md ADR-002), so this test is skipped
everywhere else.
"""

import platform

import pytest

from datetime import datetime, timezone

from nodeiq.collectors import scheduled_jobs
from nodeiq.core.collector import CollectorContext

pytestmark = pytest.mark.skipif(
    platform.system() != "Linux",
    reason="requires a real Linux system with systemd (see DECISIONS.md ADR-002); "
    "run this inside the Multipass Ubuntu VM",
)


def test_collect_produces_a_sane_summary_on_a_real_linux_system():
    context = CollectorContext(scan_start_time=datetime.now(timezone.utc))

    result = scheduled_jobs.collect(context)

    assert result.collector_name == "scheduled_jobs"
    assert result.data["cron_job_count"] == len(result.data["cron_jobs"])
    assert result.data["timer_count"] == len(result.data["systemd_timers"])
    # Ubuntu ships at least a few system cron jobs and systemd timers by default.
    assert result.data["cron_job_count"] > 0
    assert result.data["timer_count"] > 0

    for job in result.data["cron_jobs"]:
        assert job["user"]
        assert job["schedule"]
        assert job["source_file"]

    for timer in result.data["systemd_timers"]:
        assert timer["name"].endswith(".timer")
        # Usually a real "<name>.service", but `systemctl list-timers`
        # can legitimately print "-" for a timer with no associated
        # unit it could resolve (confirmed for real: GitHub Actions'
        # Ubuntu runner has one such timer, even though every timer on
        # the Multipass VM this was originally verified against had a
        # real unit) — both are valid values this collector doesn't
        # guess past, per `_parse_list_timers`'s own "don't parse what
        # isn't robustly parseable" reasoning.
        assert timer["unit"].endswith(".service") or timer["unit"] == "-"
