"""Unit tests for nodeiq.llm.client.

The OpenAI SDK is never called for real here — every test installs a
fake `openai.OpenAI` constructor via monkeypatch, per PROJECT_RULES.md
Section 11 ("tests must not depend on the real state of the machine").
`httpx.Request`/`httpx.Response` are used only to construct real SDK
exception *instances* (the SDK requires them), never to make a real
network call.
"""

import types

import httpx
import openai
import pytest

from nodeiq.llm import client
from nodeiq.llm.exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMConnectionError,
    LLMError,
    LLMRateLimitError,
    LLMResponseError,
    LLMServerError,
    LLMTimeoutError,
)

_FAKE_PROMPT = {"system": "SYSTEM TEXT", "user": "USER TEXT", "prompt_version": "v1"}


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _http_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code, request=_request())


def _timeout_error() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=_request())


def _connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=_request())


def _rate_limit_error() -> openai.RateLimitError:
    return openai.RateLimitError("rate limited", response=_http_response(429), body=None)


def _server_error() -> openai.InternalServerError:
    return openai.InternalServerError("server error", response=_http_response(500), body=None)


def _auth_error() -> openai.AuthenticationError:
    return openai.AuthenticationError("invalid api key", response=_http_response(401), body=None)


def _bad_request_error() -> openai.BadRequestError:
    return openai.BadRequestError("bad request", response=_http_response(400), body=None)


def _chat_response(content):
    message = types.SimpleNamespace(content=content)
    choice = types.SimpleNamespace(message=message)
    return types.SimpleNamespace(choices=[choice])


def _response_with_no_choices():
    return types.SimpleNamespace(choices=[])


def _response_with_no_message():
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=None)])


class _FakeCompletions:
    """Records every call and delegates to a test-supplied behavior
    function, so a test can raise, return, or count attempts."""

    def __init__(self, behavior):
        self._behavior = behavior
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._behavior(len(self.calls), kwargs)


class _FakeOpenAIClient:
    def __init__(self, completions, **kwargs):
        self.chat = types.SimpleNamespace(completions=completions)
        self.init_kwargs = kwargs


def _install_fake_openai(monkeypatch, behavior, api_key="sk-test-key-do-not-leak"):
    """Wire up a fake `openai.OpenAI` constructor, a real API key in the
    environment, a no-op `.env` loader, and no real sleep delays.
    Returns the `_FakeCompletions` instance so a test can inspect calls.
    """
    completions = _FakeCompletions(behavior)

    def fake_constructor(**kwargs):
        return _FakeOpenAIClient(completions, **kwargs)

    monkeypatch.setattr(client.openai, "OpenAI", fake_constructor)
    monkeypatch.setattr(client, "load_dotenv", lambda: None)
    monkeypatch.setattr(client.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("OPENAI_API_KEY", api_key)
    return completions


# --- Successful request --------------------------------------------------------------


def test_normal_question_returns_assistant_text(monkeypatch):
    _install_fake_openai(monkeypatch, lambda n, kw: _chat_response("nginx has not failed."))

    result = client.ask_openai(_FAKE_PROMPT)

    assert result == "nginx has not failed."


def test_only_one_request_made_on_success(monkeypatch):
    completions = _install_fake_openai(monkeypatch, lambda n, kw: _chat_response("ok"))

    client.ask_openai(_FAKE_PROMPT)

    assert len(completions.calls) == 1


# --- Missing API key -----------------------------------------------------------------


def test_missing_api_key_raises_configuration_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(client, "load_dotenv", lambda: None)

    with pytest.raises(LLMConfigurationError, match="OPENAI_API_KEY is not configured"):
        client.ask_openai(_FAKE_PROMPT)


def test_empty_string_api_key_is_treated_as_missing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr(client, "load_dotenv", lambda: None)

    with pytest.raises(LLMConfigurationError):
        client.ask_openai(_FAKE_PROMPT)


# --- Authentication failure ------------------------------------------------------------


def test_authentication_failure_translates_to_llm_authentication_error(monkeypatch):
    def behavior(n, kw):
        raise _auth_error()

    _install_fake_openai(monkeypatch, behavior, api_key="sk-super-secret-should-not-leak")

    with pytest.raises(LLMAuthenticationError):
        client.ask_openai(_FAKE_PROMPT)


def test_authentication_failure_is_not_retried(monkeypatch):
    def behavior(n, kw):
        raise _auth_error()

    completions = _install_fake_openai(monkeypatch, behavior)

    with pytest.raises(LLMAuthenticationError):
        client.ask_openai(_FAKE_PROMPT)

    assert len(completions.calls) == 1


def test_authentication_error_message_never_contains_the_api_key(monkeypatch):
    def behavior(n, kw):
        raise _auth_error()

    _install_fake_openai(monkeypatch, behavior, api_key="sk-super-secret-should-not-leak")

    with pytest.raises(LLMAuthenticationError) as exc_info:
        client.ask_openai(_FAKE_PROMPT)

    assert "sk-super-secret-should-not-leak" not in str(exc_info.value)


# --- Timeout ---------------------------------------------------------------------------


def test_timeout_exhausts_retries_and_raises_llm_timeout_error(monkeypatch):
    def behavior(n, kw):
        raise _timeout_error()

    completions = _install_fake_openai(monkeypatch, behavior)

    with pytest.raises(LLMTimeoutError):
        client.ask_openai(_FAKE_PROMPT)

    assert len(completions.calls) == client._MAX_ATTEMPTS


def test_timeout_message_does_not_leak_sdk_details(monkeypatch):
    def behavior(n, kw):
        raise _timeout_error()

    _install_fake_openai(monkeypatch, behavior)

    with pytest.raises(LLMTimeoutError) as exc_info:
        client.ask_openai(_FAKE_PROMPT)

    assert "httpx" not in str(exc_info.value)


# --- Retry behavior ----------------------------------------------------------------------


def test_retries_transient_connection_failure_then_succeeds(monkeypatch):
    def behavior(n, kw):
        if n < client._MAX_ATTEMPTS:
            raise _connection_error()
        return _chat_response("recovered answer")

    completions = _install_fake_openai(monkeypatch, behavior)

    result = client.ask_openai(_FAKE_PROMPT)

    assert result == "recovered answer"
    assert len(completions.calls) == client._MAX_ATTEMPTS


def test_retry_sleeps_between_attempts(monkeypatch):
    sleep_calls = []

    def behavior(n, kw):
        if n < 2:
            raise _connection_error()
        return _chat_response("ok")

    _install_fake_openai(monkeypatch, behavior)
    monkeypatch.setattr(client.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    client.ask_openai(_FAKE_PROMPT)

    assert sleep_calls == [client._RETRY_BACKOFF_SECONDS]


def test_rate_limit_exhausts_retries_and_raises_llm_rate_limit_error(monkeypatch):
    def behavior(n, kw):
        raise _rate_limit_error()

    completions = _install_fake_openai(monkeypatch, behavior)

    with pytest.raises(LLMRateLimitError):
        client.ask_openai(_FAKE_PROMPT)

    assert len(completions.calls) == client._MAX_ATTEMPTS


def test_server_error_exhausts_retries_and_raises_llm_server_error(monkeypatch):
    def behavior(n, kw):
        raise _server_error()

    completions = _install_fake_openai(monkeypatch, behavior)

    with pytest.raises(LLMServerError):
        client.ask_openai(_FAKE_PROMPT)

    assert len(completions.calls) == client._MAX_ATTEMPTS


def test_bad_request_error_is_not_retried_and_raises_generic_llm_error(monkeypatch):
    def behavior(n, kw):
        raise _bad_request_error()

    completions = _install_fake_openai(monkeypatch, behavior)

    with pytest.raises(LLMError):
        client.ask_openai(_FAKE_PROMPT)

    assert len(completions.calls) == 1


# --- Malformed / empty response --------------------------------------------------------


def test_response_with_no_choices_raises_llm_response_error(monkeypatch):
    _install_fake_openai(monkeypatch, lambda n, kw: _response_with_no_choices())

    with pytest.raises(LLMResponseError):
        client.ask_openai(_FAKE_PROMPT)


def test_response_with_missing_message_raises_llm_response_error(monkeypatch):
    _install_fake_openai(monkeypatch, lambda n, kw: _response_with_no_message())

    with pytest.raises(LLMResponseError):
        client.ask_openai(_FAKE_PROMPT)


def test_response_with_empty_string_content_raises_llm_response_error(monkeypatch):
    _install_fake_openai(monkeypatch, lambda n, kw: _chat_response(""))

    with pytest.raises(LLMResponseError):
        client.ask_openai(_FAKE_PROMPT)


def test_response_with_whitespace_only_content_raises_llm_response_error(monkeypatch):
    _install_fake_openai(monkeypatch, lambda n, kw: _chat_response("   \n  "))

    with pytest.raises(LLMResponseError):
        client.ask_openai(_FAKE_PROMPT)


def test_malformed_response_is_not_retried(monkeypatch):
    completions = _install_fake_openai(monkeypatch, lambda n, kw: _response_with_no_choices())

    with pytest.raises(LLMResponseError):
        client.ask_openai(_FAKE_PROMPT)

    assert len(completions.calls) == 1


# --- Response extraction ----------------------------------------------------------------


def test_response_extraction_returns_content_verbatim(monkeypatch):
    _install_fake_openai(
        monkeypatch, lambda n, kw: _chat_response("Exactly this text, unmodified.")
    )

    result = client.ask_openai(_FAKE_PROMPT)

    assert result == "Exactly this text, unmodified."


# --- Deterministic defaults --------------------------------------------------------------


def test_default_temperature_and_model_are_used(monkeypatch):
    completions = _install_fake_openai(monkeypatch, lambda n, kw: _chat_response("ok"))

    client.ask_openai(_FAKE_PROMPT)

    assert completions.calls[0]["temperature"] == 0.0
    assert completions.calls[0]["model"] == client._DEFAULT_MODEL


def test_custom_temperature_is_passed_through(monkeypatch):
    completions = _install_fake_openai(monkeypatch, lambda n, kw: _chat_response("ok"))

    client.ask_openai(_FAKE_PROMPT, temperature=0.7)

    assert completions.calls[0]["temperature"] == 0.7


def test_request_timeout_is_applied(monkeypatch):
    completions = _install_fake_openai(monkeypatch, lambda n, kw: _chat_response("ok"))

    client.ask_openai(_FAKE_PROMPT)

    assert completions.calls[0]["timeout"] == client._REQUEST_TIMEOUT_SECONDS


def test_response_token_cap_is_applied(monkeypatch):
    completions = _install_fake_openai(monkeypatch, lambda n, kw: _chat_response("ok"))

    client.ask_openai(_FAKE_PROMPT)

    assert completions.calls[0]["max_completion_tokens"] == client._MAX_RESPONSE_TOKENS


# --- Prompt Builder output consumed verbatim, never rebuilt ----------------------------


def test_prompt_is_consumed_verbatim_not_rebuilt(monkeypatch):
    completions = _install_fake_openai(monkeypatch, lambda n, kw: _chat_response("ok"))

    client.ask_openai(_FAKE_PROMPT)

    assert completions.calls[0]["messages"] == [
        {"role": "system", "content": "SYSTEM TEXT"},
        {"role": "user", "content": "USER TEXT"},
    ]


def test_prompt_version_field_is_ignored_not_sent_to_openai(monkeypatch):
    completions = _install_fake_openai(monkeypatch, lambda n, kw: _chat_response("ok"))

    client.ask_openai(_FAKE_PROMPT)

    sent_text = str(completions.calls[0]["messages"])
    assert "v1" not in sent_text


# --- Security: the API key never appears anywhere it shouldn't -------------------------


@pytest.mark.parametrize(
    "make_error",
    [_auth_error, _timeout_error, _connection_error, _rate_limit_error, _server_error, _bad_request_error],
)
def test_api_key_never_appears_in_any_raised_exception(monkeypatch, make_error):
    secret_key = "sk-do-not-leak-this-value-anywhere"

    def behavior(n, kw):
        raise make_error()

    _install_fake_openai(monkeypatch, behavior, api_key=secret_key)

    with pytest.raises(LLMError) as exc_info:
        client.ask_openai(_FAKE_PROMPT)

    assert secret_key not in str(exc_info.value)
