"""Unit tests for nodeiq.llm.prompt.

build_prompt() is a pure function — every test here is a plain,
deterministic call with a hand-built fixture, no mocking, no I/O,
per docs/prompt_builder_design.md Section 3.
"""

import copy

import pytest

from nodeiq.llm.prompt import build_prompt


def _fake_summary(**overrides) -> dict:
    """A small, hand-built Summary — enough to exercise evidence
    formatting without needing a real snapshot/summarize_snapshot() call.
    """
    summary = {
        "generated_at": "2026-07-16T09:00:00.000000+00:00",
        "snapshot_timestamp": "2026-07-16T08:35:17.525799+00:00",
        "hostname": "test-host",
        "sections": {
            "system": {
                "available": True,
                "status": "healthy",
                "headline": "Ubuntu 24.04.4 LTS, kernel 6.8.0-134-generic",
                "highlights": ["Hostname: test-host"],
                "concerns": [],
                "evidence": {"kernel_version": "6.8.0-134-generic"},
                "errors": [],
            },
        },
        "collection_errors": {},
    }
    summary.update(overrides)
    return summary


# --- Return shape --------------------------------------------------------------


def test_returns_a_plain_dict_with_the_three_expected_keys():
    result = build_prompt("What kernel version is running?", _fake_summary())

    assert isinstance(result, dict)
    assert set(result.keys()) == {"system", "user", "prompt_version"}


def test_never_returns_an_openai_message_object():
    result = build_prompt("anything", _fake_summary())

    assert isinstance(result["system"], str)
    assert isinstance(result["user"], str)
    assert isinstance(result["prompt_version"], str)


# --- Normal question -------------------------------------------------------------


def test_normal_question():
    result = build_prompt("What kernel version is running?", _fake_summary())

    assert "Question: What kernel version is running?" in result["user"]
    assert "6.8.0-134-generic" in result["user"]


# --- Empty question --------------------------------------------------------------


def test_empty_question_does_not_raise():
    result = build_prompt("", _fake_summary())

    assert result["user"].endswith("Question: ")


# --- Empty evidence ----------------------------------------------------------------


def test_empty_evidence_does_not_raise():
    result = build_prompt("Is anything wrong?", {})

    assert "{}" in result["user"]
    assert "Question: Is anything wrong?" in result["user"]


def test_empty_evidence_uses_unknown_freshness_marker():
    result = build_prompt("q", {})

    assert "snapshot taken at unknown" in result["user"]
    assert "summary generated at unknown" in result["user"]


# --- Unsupported evidence_kind -----------------------------------------------------


def test_unsupported_evidence_kind_raises_value_error():
    with pytest.raises(ValueError, match="unsupported evidence_kind"):
        build_prompt("q", _fake_summary(), evidence_kind="snapshot")


def test_unsupported_evidence_kind_message_lists_supported_kinds():
    with pytest.raises(ValueError, match="summary"):
        build_prompt("q", _fake_summary(), evidence_kind="bogus")


def test_supported_evidence_kind_is_accepted_explicitly():
    result = build_prompt("q", _fake_summary(), evidence_kind="summary")

    assert result["prompt_version"] == "v4"


# --- Deterministic output --------------------------------------------------------


def test_deterministic_output_for_identical_input():
    evidence = _fake_summary()

    first = build_prompt("What failed?", evidence)
    second = build_prompt("What failed?", evidence)

    assert first == second


def test_deterministic_across_separate_fixture_instances():
    first = build_prompt("What failed?", _fake_summary())
    second = build_prompt("What failed?", _fake_summary())

    assert first == second


# --- Prompt version ----------------------------------------------------------------


def test_prompt_version_is_present_and_stable():
    result = build_prompt("q", _fake_summary())

    assert result["prompt_version"] == "v4"


def test_prompt_version_does_not_change_with_different_evidence():
    a = build_prompt("q", _fake_summary())
    b = build_prompt("q", _fake_summary(hostname="other-host"))

    assert a["prompt_version"] == b["prompt_version"]


# --- Evidence preserved --------------------------------------------------------------


def test_evidence_values_appear_verbatim_in_user_prompt():
    evidence = _fake_summary(hostname="my-special-host-42")

    result = build_prompt("q", evidence)

    assert "my-special-host-42" in result["user"]


def test_evidence_field_order_is_preserved_not_sorted():
    evidence = {"zebra": 1, "apple": 2, "mango": 3}

    result = build_prompt("q", evidence)

    assert result["user"].index('"zebra"') < result["user"].index('"apple"')
    assert result["user"].index('"apple"') < result["user"].index('"mango"')


def test_nested_evidence_structure_is_preserved():
    evidence = _fake_summary()

    result = build_prompt("q", evidence)

    assert '"kernel_version": "6.8.0-134-generic"' in result["user"]


# --- Question preserved exactly ----------------------------------------------------


def test_question_preserved_exactly_including_punctuation():
    question = "What's wrong?! (urgent) -- please check."

    result = build_prompt(question, _fake_summary())

    assert f"Question: {question}" in result["user"]


def test_multiline_question_preserved_verbatim():
    question = "Is memory usage high?\nAlso, are any services down?"

    result = build_prompt(question, _fake_summary())

    assert f"Question: {question}" in result["user"]


# --- Unicode handling ----------------------------------------------------------------


def test_unicode_question_preserved_and_not_escaped():
    question = "Est-ce que le service échoue à cause d'un problème de mémoire? 日本語"

    result = build_prompt(question, _fake_summary())

    assert f"Question: {question}" in result["user"]
    assert "\\u" not in result["user"]


def test_unicode_evidence_preserved_and_not_escaped():
    evidence = _fake_summary(hostname="Serveur-café-☕")

    result = build_prompt("q", evidence)

    assert "Serveur-café-☕" in result["user"]
    assert "\\u" not in result["user"]


# --- Prompt contains required guardrails --------------------------------------------


@pytest.mark.parametrize(
    "expected_substring",
    [
        "EVIDENCE BOUNDARY",
        "What you may conclude",
        "What you must never conclude",
        "root cause",
        "recommendation or remediation",
        "According to the evidence",
        "does not establish a cause",
        "does not contain enough information",
        "Conflicting evidence",
        "Historical logs vs. current state",
        "Unsupported questions",
        "Absence from a list the evidence presents as complete",
        "false or unsupported premise",
        "not live access to log files on disk",
        "Interpreting the question before answering",
        "Correct obvious terminology mistakes",
        "Recognize synonyms and loose phrasing",
        "it never supplies, substitutes, or estimates an actual fact",
        "Terminology interpretation",
        "Clarification needed",
    ],
)
def test_system_prompt_contains_guardrail(expected_substring):
    result = build_prompt("q", _fake_summary())

    assert expected_substring in result["system"]


def test_system_prompt_is_identical_regardless_of_question_or_evidence():
    a = build_prompt("question one", _fake_summary())
    b = build_prompt("a completely different question", _fake_summary(hostname="other"))

    assert a["system"] == b["system"]


# --- No mutation of input dict -----------------------------------------------------


def test_evidence_dict_is_not_mutated():
    evidence = _fake_summary()
    before = copy.deepcopy(evidence)

    build_prompt("q", evidence)

    assert evidence == before


def test_nested_evidence_values_are_not_mutated():
    evidence = {"sections": {"system": {"concerns": ["a", "b"]}}}
    before = copy.deepcopy(evidence)

    build_prompt("q", evidence)

    assert evidence == before
    assert evidence["sections"]["system"]["concerns"] == ["a", "b"]


def test_returned_user_prompt_is_independent_of_further_evidence_mutation():
    evidence = {"hostname": "original-host"}

    result = build_prompt("q", evidence)
    evidence["hostname"] = "mutated-after-the-fact"

    assert "original-host" in result["user"]
    assert "mutated-after-the-fact" not in result["user"]


# --- Prompt size protection (Phase 7B hardening) ------------------------------------


def test_small_evidence_is_not_truncated():
    evidence = {"hostname": "test-host"}
    result = build_prompt("q", evidence)

    assert "truncated" not in result["user"]


def test_huge_evidence_is_truncated_with_a_visible_marker():
    from nodeiq.llm import prompt as prompt_module

    huge_evidence = {"padding": "x" * (prompt_module._MAX_EVIDENCE_JSON_CHARS + 5_000)}
    result = build_prompt("q", huge_evidence)

    assert "evidence truncated" in result["user"]
    assert "characters omitted for prompt-size safety" in result["user"]


def test_truncated_evidence_stays_at_or_under_the_configured_limit_plus_marker():
    from nodeiq.llm import prompt as prompt_module

    huge_evidence = {"padding": "x" * (prompt_module._MAX_EVIDENCE_JSON_CHARS * 3)}
    result = build_prompt("q", huge_evidence)

    # The raw JSON portion itself must never exceed the configured cap,
    # even though the visible marker appended after it adds more text.
    evidence_block = result["user"].split("\n\n")[0]
    json_only = evidence_block.split("\n... [")[0]
    assert len(json_only) <= prompt_module._MAX_EVIDENCE_JSON_CHARS + len(
        "Evidence (snapshot taken at unknown, summary generated at unknown):\n"
    )


def test_small_question_is_not_truncated():
    result = build_prompt("a normal, short question", {})
    assert "question truncated" not in result["user"]


def test_huge_question_is_truncated_with_a_visible_marker():
    from nodeiq.llm import prompt as prompt_module

    huge_question = "why? " * (prompt_module._MAX_QUESTION_CHARS // 4)
    result = build_prompt(huge_question, {})

    assert "question truncated" in result["user"]
    assert "characters omitted for prompt-size safety" in result["user"]


def test_truncation_never_raises_for_pathological_input():
    from nodeiq.llm import prompt as prompt_module

    huge_evidence = {"a": "y" * 1_000_000}
    huge_question = "z" * 1_000_000

    result = build_prompt(huge_question, huge_evidence)

    assert isinstance(result["user"], str)
