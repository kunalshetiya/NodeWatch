"""Unit tests for nodeiq.routing.intent_router.

ask_openai is mocked throughout — no real API call, no dependency on
the real machine or a configured API key, per PROJECT_RULES.md
Section 11.
"""

import pytest

from nodeiq.routing import intent_router


# --- _parse_response ---------------------------------------------------------------


def test_parse_response_returns_a_single_category():
    assert intent_router._parse_response("disk") == ["disk"]


def test_parse_response_returns_multiple_categories_sorted():
    assert intent_router._parse_response("processes, disk, cpu_memory") == [
        "cpu_memory",
        "disk",
        "processes",
    ]


def test_parse_response_is_case_insensitive():
    assert intent_router._parse_response("DISK") == ["disk"]


def test_parse_response_returns_none_for_the_literal_none_response():
    assert intent_router._parse_response("none") is None


def test_parse_response_returns_none_for_empty_response():
    assert intent_router._parse_response("") is None
    assert intent_router._parse_response("   ") is None


def test_parse_response_drops_hallucinated_category_names():
    assert intent_router._parse_response("disk, made_up_category") == ["disk"]


def test_parse_response_returns_none_when_everything_is_invalid():
    assert intent_router._parse_response("made_up_category, another_fake_one") is None


def test_parse_response_handles_extra_whitespace():
    assert intent_router._parse_response("  disk ,  cpu_memory  ") == [
        "cpu_memory",
        "disk",
    ]


# --- route() ------------------------------------------------------------------------


def test_route_calls_ask_openai_and_parses_the_result(monkeypatch):
    captured = {}

    def fake_ask_openai(prompt, temperature=0.0):
        captured["prompt"] = prompt
        captured["temperature"] = temperature
        return "cpu_memory, processes"

    monkeypatch.setattr(intent_router, "ask_openai", fake_ask_openai)

    result = intent_router.route("why is my system slow")

    assert result == ["cpu_memory", "processes"]
    assert "why is my system slow" in captured["prompt"]["user"]
    assert captured["temperature"] == 0.0


def test_route_returns_none_when_model_says_none(monkeypatch):
    monkeypatch.setattr(intent_router, "ask_openai", lambda prompt, temperature=0.0: "none")

    assert intent_router.route("what is the weather today") is None


def test_route_propagates_llm_errors_rather_than_swallowing_them(monkeypatch):
    from nodeiq.llm.exceptions import LLMTimeoutError

    def fake_ask_openai(prompt, temperature=0.0):
        raise LLMTimeoutError("timed out")

    monkeypatch.setattr(intent_router, "ask_openai", fake_ask_openai)

    with pytest.raises(LLMTimeoutError):
        intent_router.route("why is my system slow")


def test_route_prompt_is_independent_of_the_answering_system_prompt(monkeypatch):
    # The intent classification prompt must never be confused with (or
    # accidentally reuse) the full evidence-answering system prompt —
    # they serve completely different purposes.
    captured = {}

    def fake_ask_openai(prompt, temperature=0.0):
        captured["prompt"] = prompt
        return "disk"

    monkeypatch.setattr(intent_router, "ask_openai", fake_ask_openai)
    intent_router.route("is disk space running low")

    assert "EVIDENCE BOUNDARY" not in captured["prompt"]["system"]
    assert "routing classifier" in captured["prompt"]["system"]
