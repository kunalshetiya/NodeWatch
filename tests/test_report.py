"""Unit tests for nodeiq.report.

format_report() consumes a Summary (the output shape of
nodeiq.summary.summarize_snapshot), not a raw snapshot — so fixtures
here are hand-built Summary dicts, independent of nodeiq.summary itself.
"""

from nodeiq.report import format_report


def _section(**overrides) -> dict:
    """A healthy, fully-populated SectionSummary, overridden per test."""
    section = {
        "available": True,
        "status": "healthy",
        "headline": "Example headline",
        "highlights": ["Example highlight"],
        "concerns": [],
        "evidence": {"example_field": 42},
        "errors": [],
    }
    section.update(overrides)
    return section


def _summary(**section_overrides) -> dict:
    """A minimal Summary with one section, `system`, by default."""
    sections = {"system": _section()}
    sections.update(section_overrides)
    return {
        "generated_at": "2026-07-15T09:00:00.000000+00:00",
        "snapshot_timestamp": "2026-07-15T08:35:17.525799+00:00",
        "hostname": "main-cattle",
        "sections": sections,
        "collection_errors": {},
    }


def test_report_includes_hostname_and_snapshot_timestamp():
    report = format_report(_summary())
    assert "main-cattle" in report
    assert "2026-07-15T08:35:17.525799+00:00" in report


def test_report_returns_a_string():
    assert isinstance(format_report(_summary()), str)


def test_section_headline_appears():
    report = format_report(_summary(system=_section(headline="Ubuntu 24.04, up 5d 3h")))
    assert "Ubuntu 24.04, up 5d 3h" in report


def test_section_status_appears_uppercase():
    report = format_report(_summary(system=_section(status="warning")))
    assert "[WARNING]" in report


def test_all_four_status_values_render():
    for status in ("healthy", "warning", "critical", "unknown"):
        report = format_report(_summary(system=_section(status=status)))
        assert f"[{status.upper()}]" in report


def test_section_title_is_human_readable():
    report = format_report(_summary())
    assert "System" in report


def test_cpu_memory_gets_a_special_display_title():
    report = format_report(_summary(cpu_memory=_section(headline="Memory 26.6% used")))
    assert "CPU & Memory" in report
    assert "cpu_memory" not in report


def test_unknown_section_name_falls_back_to_title_case():
    report = format_report(_summary(scheduled_jobs=_section(headline="8 cron job(s)")))
    assert "Scheduled Jobs" in report


def test_highlights_are_listed():
    report = format_report(
        _summary(system=_section(highlights=["Hostname: main-cattle", "Architecture: aarch64"]))
    )
    assert "Hostname: main-cattle" in report
    assert "Architecture: aarch64" in report


def test_multiple_sections_all_appear():
    report = format_report(
        _summary(
            system=_section(headline="System headline"),
            disk=_section(headline="Disk headline"),
        )
    )
    assert "System headline" in report
    assert "Disk headline" in report


def test_sections_render_in_summary_order():
    summary = _summary(
        system=_section(headline="System headline"),
        disk=_section(headline="Disk headline"),
    )
    report = format_report(summary)
    assert report.index("System headline") < report.index("Disk headline")


# --- Concerns: shown only when present -------------------------------------------


def test_concerns_are_listed_when_present():
    report = format_report(
        _summary(disk=_section(concerns=["Highest filesystem usage at 92.0% (warning threshold: 85%)"]))
    )
    assert "Concerns:" in report
    assert "Highest filesystem usage at 92.0% (warning threshold: 85%)" in report


def test_concerns_heading_absent_when_no_concerns():
    report = format_report(_summary(system=_section(concerns=[])))
    assert "Concerns:" not in report


def test_multiple_concerns_all_listed():
    report = format_report(
        _summary(services=_section(concerns=["1 service failed: nginx", "2 services restarting: foo, bar"]))
    )
    assert "1 service failed: nginx" in report
    assert "2 services restarting: foo, bar" in report


# --- Empty highlights/concerns display cleanly ------------------------------------


def test_empty_highlights_produce_no_stray_bullets():
    report = format_report(_summary(system=_section(highlights=[])))
    assert "  - " not in report.split("\n")[0]
    # No bullet line should be an empty/placeholder artifact.
    for line in report.split("\n"):
        assert line.strip() != "-"


def test_empty_highlights_and_concerns_leave_only_title_and_headline():
    summary = _summary(system=_section(headline="Just a headline", highlights=[], concerns=[]))
    report = format_report(summary)
    assert "Just a headline" in report
    assert "Concerns:" not in report


# --- Missing sections / missing data handled gracefully ---------------------------


def test_missing_sections_key_does_not_crash():
    summary = {"hostname": "main-cattle", "snapshot_timestamp": "2026-07-15T08:35:17+00:00"}
    report = format_report(summary)
    assert "NodeIQ Report" in report
    assert "main-cattle" in report


def test_empty_sections_dict_does_not_crash():
    report = format_report(_summary(**{}) | {"sections": {}})
    assert "NodeIQ Report" in report


def test_none_section_value_handled_gracefully():
    summary = _summary()
    summary["sections"]["disk"] = None
    report = format_report(summary)
    assert "Disk" in report
    assert "[UNKNOWN]" in report


def test_unavailable_section_shape_renders_cleanly():
    unavailable = {
        "available": False,
        "status": "unknown",
        "headline": "No data available for this section.",
        "highlights": [],
        "concerns": [],
        "evidence": {},
        "errors": [{"message": "boom", "severity": "error", "exception_type": "ValueError"}],
    }
    report = format_report(_summary(disk=unavailable))
    assert "No data available for this section." in report
    assert "[UNKNOWN]" in report


def test_missing_hostname_and_timestamp_use_placeholders():
    summary = {"sections": {}}
    report = format_report(summary)
    assert "unknown host" in report
    assert "unknown" in report


def test_section_missing_headline_omits_headline_line_without_crashing():
    report = format_report(_summary(system=_section(headline=None)))
    lines = [line for line in report.split("\n") if line.strip()]
    assert any("System" in line for line in lines)


# --- Determinism -------------------------------------------------------------------


def test_format_report_is_deterministic():
    summary = _summary(
        disk=_section(status="warning", concerns=["Highest filesystem usage at 92.0%"]),
    )
    first = format_report(summary)
    second = format_report(summary)
    assert first == second


def test_format_report_does_not_mutate_input():
    import copy

    summary = _summary(disk=_section(highlights=["a"], concerns=["b"]))
    before = copy.deepcopy(summary)
    format_report(summary)
    assert summary == before


# --- No raw JSON leaked -------------------------------------------------------------


def test_no_raw_json_braces_in_output():
    report = format_report(_summary(system=_section(evidence={"secret_internal_field": 123})))
    assert "{" not in report
    assert "}" not in report


def test_evidence_fields_are_not_duplicated_in_output():
    report = format_report(
        _summary(system=_section(evidence={"internal_only_marker": "should-not-appear"}))
    )
    assert "internal_only_marker" not in report
    assert "should-not-appear" not in report


def test_errors_field_is_not_dumped_as_raw_dicts():
    section = _section(errors=[{"message": "boom", "severity": "error", "exception_type": "ValueError"}])
    report = format_report(_summary(system=section))
    assert "exception_type" not in report
    assert "severity" not in report


def test_full_nine_section_summary_formats_without_error():
    section_names = [
        "system",
        "cpu_memory",
        "processes",
        "disk",
        "services",
        "scheduled_jobs",
        "permissions",
        "network",
        "logs",
    ]
    overrides = {name: _section(headline=f"{name} headline") for name in section_names}
    report = format_report(_summary(**overrides))
    for name in section_names:
        assert f"{name} headline" in report
