"""Unit tests for nodeiq.llm.ask.

answer_question() has two paths: an explicit `snapshot_path` (loads that
exact file, no routing at all — unchanged from before this module's
tiered-routing rewrite) and the default on-demand path (resolves via
keyword_router -> intent_router -> tool_calling_router, collecting only
the resolved collector(s) live, escalating to the next tier if an
answer comes back insufficient). Every external seam
(`run_selected_collectors`, the three routers, `ask_openai`) is mocked
directly, per PROJECT_RULES.md Section 11 — no real snapshot file, no
real OpenAI call, no real collector execution.
"""

import pytest

from nodeiq.core.exceptions import SnapshotError
from nodeiq.llm import ask as ask_module
from nodeiq.llm.exceptions import LLMConfigurationError

_FAKE_SNAPSHOT = {
    "metadata": {"scan_timestamp": "2026-07-16T09:00:00+00:00", "hostname": "test-host"},
    "collection_errors": {},
    "system": {
        "hostname": "test-host",
        "operating_system": "Ubuntu 24.04.4 LTS",
        "kernel_version": "6.8.0-134-generic",
        "architecture": "aarch64",
        "uptime_seconds": 3600.0,
    },
}

_ON_DEMAND_SNAPSHOT = {
    "metadata": {
        "scan_timestamp": "2026-07-29T09:00:00+00:00",
        "collection_mode": "on_demand",
        "collectors_run": ["disk"],
    },
    "collection_errors": {},
    "disk": {"highest_disk_usage_percent": 68.0, "filesystems": []},
}


def _install_routing(
    monkeypatch,
    *,
    keyword_result=None,
    intent_result=None,
    tool_calling_answer=None,
    answers=None,
    collected_snapshot=None,
):
    """Wire up fake keyword_router/intent_router/tool_calling_router and
    run_selected_collectors/ask_openai. `answers` is a list of answer
    strings returned by successive ask_openai calls (one per
    _collect_and_answer invocation, in order); `tool_calling_answer` is
    what tool_calling_router.answer() itself returns directly.
    """
    calls = {
        "keyword_route": 0,
        "intent_route": 0,
        "tool_calling_answer": 0,
        "collected_names": [],
        "ask_openai_count": 0,
    }
    answer_iter = iter(answers or [])

    def _keyword_route(question):
        calls["keyword_route"] += 1
        return keyword_result

    def _intent_route(question):
        calls["intent_route"] += 1
        return intent_result

    def _tool_calling_answer(question):
        calls["tool_calling_answer"] += 1
        return tool_calling_answer

    monkeypatch.setattr(ask_module.keyword_router, "route", _keyword_route)
    monkeypatch.setattr(ask_module.intent_router, "route", _intent_route)
    monkeypatch.setattr(ask_module.tool_calling_router, "answer", _tool_calling_answer)

    def _run_selected_collectors(names):
        calls["collected_names"].append(list(names))
        return collected_snapshot if collected_snapshot is not None else _ON_DEMAND_SNAPSHOT

    monkeypatch.setattr(ask_module, "run_selected_collectors", _run_selected_collectors)

    def _ask_openai(prompt):
        calls["ask_openai_count"] += 1
        return next(answer_iter)

    monkeypatch.setattr(ask_module, "ask_openai", _ask_openai)

    return calls


# --- Explicit snapshot_path: unchanged behavior --------------------------------------


def test_explicit_snapshot_path_loads_that_exact_file_no_routing(monkeypatch):
    calls = {"load_path": None, "routers_called": False}

    def _load_snapshot(path):
        calls["load_path"] = path
        return _FAKE_SNAPSHOT

    def _keyword_route(question):
        calls["routers_called"] = True
        return None

    monkeypatch.setattr(ask_module, "load_snapshot", _load_snapshot)
    monkeypatch.setattr(ask_module, "ask_openai", lambda prompt: "the answer")
    monkeypatch.setattr(ask_module.keyword_router, "route", _keyword_route)

    result = ask_module.answer_question("What OS is this?", snapshot_path="snapshots/specific.json")

    assert calls["load_path"] == "snapshots/specific.json"
    assert calls["routers_called"] is False
    assert result["answer"] == "the answer"
    assert result["snapshot_metadata"] == _FAKE_SNAPSHOT["metadata"]


def test_explicit_snapshot_path_propagates_snapshot_error(monkeypatch):
    def _raise(path):
        raise SnapshotError("snapshot bad.json is not valid JSON")

    monkeypatch.setattr(ask_module, "load_snapshot", _raise)

    with pytest.raises(SnapshotError, match="not valid JSON"):
        ask_module.answer_question("What OS is this?", snapshot_path="bad.json")


def test_explicit_snapshot_path_propagates_llm_errors(monkeypatch):
    monkeypatch.setattr(ask_module, "load_snapshot", lambda path: _FAKE_SNAPSHOT)

    def _raise(prompt):
        raise LLMConfigurationError("OPENAI_API_KEY is not configured.")

    monkeypatch.setattr(ask_module, "ask_openai", _raise)

    with pytest.raises(LLMConfigurationError):
        ask_module.answer_question("What OS is this?", snapshot_path="snapshots/x.json")


# --- Default path: tier 1 (keyword) resolves cleanly ---------------------------------


def test_keyword_tier_resolves_and_answers_directly(monkeypatch):
    calls = _install_routing(
        monkeypatch,
        keyword_result=["disk"],
        answers=["According to the evidence, disk usage is 68%."],
    )

    result = ask_module.answer_question("is disk space running low")

    assert result["answer"] == "According to the evidence, disk usage is 68%."
    assert calls["collected_names"] == [["disk"]]
    assert calls["intent_route"] == 0
    assert calls["tool_calling_answer"] == 0


def test_keyword_tier_snapshot_metadata_is_returned(monkeypatch):
    _install_routing(monkeypatch, keyword_result=["disk"], answers=["some answer"])

    result = ask_module.answer_question("is disk space running low")

    assert result["snapshot_metadata"]["collection_mode"] == "on_demand"
    assert result["snapshot_metadata"]["collectors_run"] == ["disk"]


# --- Default path: falls through to tier 2 (intent) when tier 1 can't resolve --------


def test_falls_through_to_intent_tier_when_keyword_tier_returns_none(monkeypatch):
    calls = _install_routing(
        monkeypatch,
        keyword_result=None,
        intent_result=["cpu_memory", "processes"],
        answers=["a broader answer"],
    )

    result = ask_module.answer_question("why is my system slow")

    assert result["answer"] == "a broader answer"
    assert calls["keyword_route"] == 1
    assert calls["intent_route"] == 1
    assert calls["collected_names"] == [["cpu_memory", "processes"]]


# --- Default path: falls through to tier 3 (tool-calling) when tiers 1+2 fail --------


def test_falls_through_to_tool_calling_when_neither_router_resolves(monkeypatch):
    calls = _install_routing(
        monkeypatch,
        keyword_result=None,
        intent_result=None,
        tool_calling_answer="the tool-calling answer",
    )

    result = ask_module.answer_question("some genuinely ambiguous question")

    assert result["answer"] == "the tool-calling answer"
    assert calls["tool_calling_answer"] == 1
    assert calls["collected_names"] == []  # tool_calling_router does its own collection internally


# --- Escalation: an insufficient answer tries a broader tier --------------------------


def test_escalates_from_keyword_to_intent_tier_on_insufficient_answer(monkeypatch):
    calls = _install_routing(
        monkeypatch,
        keyword_result=["disk"],
        intent_result=["disk", "services"],
        answers=[
            "The evidence does not contain enough information to determine that.",
            "According to the broader evidence, nginx is not running.",
        ],
    )

    result = ask_module.answer_question("is nginx running and is disk full")

    assert result["answer"] == "According to the broader evidence, nginx is not running."
    assert calls["intent_route"] == 1
    assert calls["collected_names"] == [["disk"], ["disk", "services"]]


def test_escalates_all_the_way_to_tool_calling_when_first_two_tiers_are_insufficient(
    monkeypatch,
):
    calls = _install_routing(
        monkeypatch,
        keyword_result=["disk"],
        intent_result=["disk", "network"],
        tool_calling_answer="finally, a real answer",
        answers=[
            "The evidence does not contain enough information to determine that.",
            "The evidence does not contain enough information to determine that either.",
        ],
    )

    result = ask_module.answer_question("a very ambiguous question")

    assert result["answer"] == "finally, a real answer"
    assert calls["tool_calling_answer"] == 1


def test_falls_back_to_every_collector_when_even_tool_calling_is_insufficient(monkeypatch):
    calls = _install_routing(
        monkeypatch,
        keyword_result=None,
        intent_result=None,
        tool_calling_answer="The evidence does not contain enough information to determine that.",
        answers=["final answer from the full sweep"],
    )

    result = ask_module.answer_question("a question nothing can resolve well")

    assert result["answer"] == "final answer from the full sweep"
    # The final fallback collects every registered collector.
    assert len(calls["collected_names"][0]) == 10


def test_does_not_escalate_when_the_first_answer_is_sufficient(monkeypatch):
    calls = _install_routing(
        monkeypatch,
        keyword_result=["disk"],
        answers=["According to the evidence, disk usage is fine."],
    )

    ask_module.answer_question("is disk space running low")

    assert calls["intent_route"] == 0
    assert calls["tool_calling_answer"] == 0
    assert calls["ask_openai_count"] == 1


# --- _is_insufficient() ---------------------------------------------------------------


def test_is_insufficient_matches_the_standardized_phrasing():
    assert ask_module._is_insufficient(
        "The evidence does not contain enough information to determine X."
    )


def test_is_insufficient_is_case_insensitive():
    assert ask_module._is_insufficient("The Evidence Does Not Contain Enough Information.")


def test_is_insufficient_matches_real_model_paraphrasing_variations():
    # Found via live-model testing: a multi-topic answer used "provide"
    # instead of the exact prescribed "contain" — the marker set must
    # catch this too, not just the one literal phrase.
    assert ask_module._is_insufficient(
        "In summary, the evidence does not provide enough information to "
        "determine the status of disk space or any failed services."
    )


def test_is_insufficient_false_for_a_normal_answer():
    assert not ask_module._is_insufficient("According to the evidence, disk usage is 68%.")
