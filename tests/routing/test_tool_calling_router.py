"""Unit tests for nodeiq.routing.tool_calling_router.

send_chat_request and run_selected_collectors are both mocked — no real
OpenAI call, no dependency on the real machine, per PROJECT_RULES.md
Section 11. Fake response/tool-call objects are plain, hand-built stand-ins
for the OpenAI SDK's pydantic response shapes (`response.choices[0].message`,
`.tool_calls[i].function.name`, etc.) — just enough attribute access for
this module to work with, nothing SDK-specific.
"""

import json

import pytest

from nodeiq.routing import tool_calling_router


class _FakeFunction:
    def __init__(self, name):
        self.name = name


class _FakeToolCall:
    def __init__(self, call_id, name):
        self.id = call_id
        self.function = _FakeFunction(name)

    def model_dump(self):
        return {"id": self.id, "type": "function", "function": {"name": self.function.name, "arguments": "{}"}}


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message):
        self.message = message


class _FakeResponse:
    def __init__(self, message):
        self.choices = [_FakeChoice(message)]


def _text_response(content):
    return _FakeResponse(_FakeMessage(content=content))


def _tool_call_response(*names):
    tool_calls = [_FakeToolCall(f"call_{i}", name) for i, name in enumerate(names)]
    return _FakeResponse(_FakeMessage(content=None, tool_calls=tool_calls))


# --- _build_tools ------------------------------------------------------------------


def test_build_tools_includes_every_collector_with_no_parameters():
    tools = tool_calling_router._build_tools()

    assert len(tools) == 10
    names = {tool["function"]["name"] for tool in tools}
    assert names == set(tool_calling_router._TOOL_DESCRIPTIONS)
    for tool in tools:
        assert tool["function"]["parameters"] == {
            "type": "object",
            "properties": {},
            "required": [],
        }


# --- answer(): normal single-round resolution ---------------------------------------


def test_answer_calls_one_tool_and_returns_the_final_text(monkeypatch):
    responses = iter(
        [
            _tool_call_response("disk"),
            _text_response("According to the evidence, disk usage is 68%."),
        ]
    )
    monkeypatch.setattr(
        tool_calling_router, "send_chat_request", lambda *a, **k: next(responses)
    )
    monkeypatch.setattr(
        tool_calling_router,
        "run_selected_collectors",
        lambda names: {"metadata": {}, "collection_errors": {}, "disk": {}},
    )

    result = tool_calling_router.answer("is disk space running low")

    assert result == "According to the evidence, disk usage is 68%."


def test_answer_forces_a_tool_call_on_the_first_round(monkeypatch):
    captured_kwargs = []

    def fake_send(messages, **kwargs):
        captured_kwargs.append(kwargs)
        if len(captured_kwargs) == 1:
            return _tool_call_response("disk")
        return _text_response("answer")

    monkeypatch.setattr(tool_calling_router, "send_chat_request", fake_send)
    monkeypatch.setattr(
        tool_calling_router, "run_selected_collectors", lambda names: {"metadata": {}, "collection_errors": {}}
    )

    tool_calling_router.answer("some question")

    assert captured_kwargs[0]["tool_choice"] == "required"
    assert captured_kwargs[0]["tools"] is not None


def test_answer_collects_only_the_requested_collectors(monkeypatch):
    captured = {}

    def fake_run_selected(names):
        captured["names"] = names
        return {"metadata": {}, "collection_errors": {}, "disk": {}, "network": {}}

    responses = iter([_tool_call_response("disk", "network"), _text_response("done")])
    monkeypatch.setattr(tool_calling_router, "send_chat_request", lambda *a, **k: next(responses))
    monkeypatch.setattr(tool_calling_router, "run_selected_collectors", fake_run_selected)

    tool_calling_router.answer("is disk full and are there open ports")

    assert captured["names"] == ["disk", "network"]


# --- answer(): multi-round adaptive tool calling ------------------------------------


def test_answer_supports_a_second_round_of_tool_calls(monkeypatch):
    responses = iter(
        [
            _tool_call_response("disk"),
            _tool_call_response("network"),
            _text_response("final answer using both"),
        ]
    )
    monkeypatch.setattr(tool_calling_router, "send_chat_request", lambda *a, **k: next(responses))
    monkeypatch.setattr(
        tool_calling_router,
        "run_selected_collectors",
        lambda names: {"metadata": {}, "collection_errors": {}, names[0]: {}},
    )

    result = tool_calling_router.answer("complex question")

    assert result == "final answer using both"


def test_answer_uses_auto_tool_choice_after_the_first_round(monkeypatch):
    captured_kwargs = []

    def fake_send(messages, **kwargs):
        captured_kwargs.append(kwargs)
        if len(captured_kwargs) == 1:
            return _tool_call_response("disk")
        return _text_response("done")

    monkeypatch.setattr(tool_calling_router, "send_chat_request", fake_send)
    monkeypatch.setattr(
        tool_calling_router, "run_selected_collectors", lambda names: {"metadata": {}, "collection_errors": {}}
    )

    tool_calling_router.answer("question")

    assert captured_kwargs[1]["tool_choice"] == "auto"


# --- answer(): hitting the round cap ------------------------------------------------


def test_answer_forces_a_text_answer_on_the_final_round(monkeypatch):
    call_count = {"n": 0}

    def fake_send(messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < tool_calling_router._MAX_TOOL_CALL_ROUNDS:
            return _tool_call_response("disk")
        # Final round: no tools/tool_choice passed at all.
        assert "tools" not in kwargs
        assert "tool_choice" not in kwargs
        return _text_response("forced final answer")

    monkeypatch.setattr(tool_calling_router, "send_chat_request", fake_send)
    monkeypatch.setattr(
        tool_calling_router, "run_selected_collectors", lambda names: {"metadata": {}, "collection_errors": {}}
    )

    result = tool_calling_router.answer("question")

    assert result == "forced final answer"
    assert call_count["n"] == tool_calling_router._MAX_TOOL_CALL_ROUNDS


def test_answer_raises_if_still_no_text_after_the_round_cap(monkeypatch):
    monkeypatch.setattr(
        tool_calling_router, "send_chat_request", lambda *a, **k: _tool_call_response("disk")
    )
    monkeypatch.setattr(
        tool_calling_router, "run_selected_collectors", lambda names: {"metadata": {}, "collection_errors": {}}
    )

    with pytest.raises(Exception):
        tool_calling_router.answer("question")


# --- answer(): tool-result plumbing --------------------------------------------------


def test_tool_results_are_valid_json_evidence_per_call(monkeypatch):
    appended_tool_messages = []

    responses = iter([_tool_call_response("disk"), _text_response("done")])

    def fake_send(messages, **kwargs):
        for msg in messages:
            if msg.get("role") == "tool":
                appended_tool_messages.append(msg)
        return next(responses)

    monkeypatch.setattr(tool_calling_router, "send_chat_request", fake_send)
    monkeypatch.setattr(
        tool_calling_router,
        "run_selected_collectors",
        lambda names: {"metadata": {}, "collection_errors": {}, "disk": {"highest_disk_usage_percent": 68.0}},
    )

    tool_calling_router.answer("is disk full")

    assert len(appended_tool_messages) == 1
    parsed = json.loads(appended_tool_messages[0]["content"])
    assert parsed["evidence"]["highest_disk_usage_percent"] == 68.0
