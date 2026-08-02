"""Unit tests for nodeiq.cli.presentation."""

from nodeiq.cli.presentation import SEPARATOR, render_banner, render_qa


def test_separator_matches_report_formatters_own_width():
    # nodeiq.report.format_report() uses "=" * 70 for its own header —
    # this asserts the shell/CLI's separator stays visually consistent
    # with it, without importing nodeiq.report (a presentation-only,
    # no-new-coupling check).
    assert SEPARATOR == "=" * 70


def test_render_banner_wraps_lines_with_separators():
    result = render_banner(["line one", "line two"])

    lines = result.split("\n")
    assert lines[0] == SEPARATOR
    assert lines[-1] == SEPARATOR
    assert "line one" in result
    assert "line two" in result


def test_render_banner_preserves_line_order():
    result = render_banner(["first", "second", "third"])

    assert result.index("first") < result.index("second") < result.index("third")


def test_render_banner_handles_empty_lines_list():
    result = render_banner([])
    assert result == f"{SEPARATOR}\n{SEPARATOR}"


def test_render_qa_includes_question_and_answer():
    result = render_qa("What failed?", "Nothing has failed.")

    assert "Question: What failed?" in result
    assert "Answer:" in result
    assert "Nothing has failed." in result


def test_render_qa_preserves_answer_wording_exactly():
    answer = "  Exactly this — punctuation, em dashes, and odd spacing.  "
    result = render_qa("q", answer)

    assert answer in result


def test_render_qa_question_appears_before_answer():
    result = render_qa("q?", "a.")
    assert result.index("Question:") < result.index("Answer:")


def test_render_qa_multiline_answer_preserved_verbatim():
    answer = "Line one.\nLine two.\nLine three."
    result = render_qa("q", answer)
    assert answer in result


def test_render_qa_is_bounded_by_the_shared_separator():
    result = render_qa("q", "a")
    lines = result.split("\n")

    assert lines[0] == SEPARATOR
    assert lines[-1] == SEPARATOR
