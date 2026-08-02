"""The OpenAI client: the one module in NodeIQ allowed to talk to OpenAI.

Implements Phase 6C exactly: a pure communication layer over the
official OpenAI Python SDK. This module knows how to authenticate, send
a prompt, retry transient failures, time out, and translate every SDK
failure into one of `nodeiq.llm.exceptions`' project-specific
exceptions. It knows nothing about Linux, snapshots, collectors, the
coordinator, the Summary Engine, the Report Formatter, or CLI argument
parsing — it consumes exactly the `Prompt` shape
`nodeiq.llm.prompt.build_prompt()` already returns
(`{"system": ..., "user": ..., "prompt_version": ...}`) and never
rebuilds, modifies, or re-engineers it.

Architecture (unchanged from docs/prompt_builder_design.md):

    Prompt Builder  ->  OpenAI Client  ->  Assistant response

`OPENAI_API_KEY` is read **only** in this module — nowhere else in
NodeIQ reads that environment variable. It is never logged, never
printed, never included in an exception message, and never serialized
into a snapshot, summary, report, or prompt.

Typical usage:

    from nodeiq.llm.prompt import build_prompt
    from nodeiq.llm.client import ask_openai

    prompt = build_prompt(question, evidence)
    answer = ask_openai(prompt)
"""

import os
import time

import openai
from dotenv import load_dotenv

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

_DEFAULT_MODEL = "gpt-4o-mini"
"""Chosen as NodeIQ v1's one, non-configurable-at-runtime default model
(per this phase's explicit "no runtime model selection" requirement).
`ask` asks short, evidence-grounded technical questions about a single
snapshot — not open-ended, long-form, or highly creative tasks — so a
smaller, fast, inexpensive model is a better fit than a frontier
flagship model: lower latency (a better CLI experience), lower per-call
cost (important for a tool meant to be run repeatedly during normal
operations work), and more than enough reasoning ability for
"restate/compare facts already given," which is all this system's
guardrails (docs/prompt_builder_design.md Section 10) ever ask the
model to do. If a future need for stronger reasoning is demonstrated,
this is the one constant to change.
"""

_DEFAULT_TEMPERATURE = 0.0
"""Deterministic by default: `ask` answers technical questions from
fixed evidence, where a consistent, literal answer is more valuable
than creative variation. Configurable per call via `ask_openai`'s
`temperature` parameter — unlike the model, this is designed to be
runtime-adjustable, per this phase's requirements.
"""

_REQUEST_TIMEOUT_SECONDS = 30.0
"""Applied per request (including each retry) — a hung request must
never hang `nodeiq ask` indefinitely, the same guarantee
`nodeiq.core.runner` already gives every collector's subprocess calls.
"""

_MAX_RESPONSE_TOKENS = 1000
"""Bounds the model's own response size (Phase 7B hardening: "model
responses are handled safely") — generous enough for a thorough,
multi-sentence answer to any question this system is designed to
answer (`docs/prompt_builder_design.md` Section 10's guardrails never
call for a long-form response), while guaranteeing no single request
can produce an unbounded amount of text."""

_MAX_ATTEMPTS = 3
"""One initial attempt plus up to two retries — applied only to the
transient failure categories in `_RETRYABLE_EXCEPTIONS` below."""

_RETRY_BACKOFF_SECONDS = 1.0
"""A short, fixed pause between retry attempts. Kept as a fixed
constant, not exponential backoff, since 3 total attempts over a few
seconds is already enough to ride out the transient failures this
module handles, without adding a more complex backoff scheme that
nothing has yet shown a need for."""

_RETRYABLE_EXCEPTIONS = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)
"""SDK exceptions worth retrying: a timeout, a connection failure, a
rate limit, or a temporary (5xx) server error — every one of these is
plausibly transient. `openai.APITimeoutError` is itself a subclass of
`openai.APIConnectionError`; listing both is harmless (Python's
`except` matches the first applicable type either way) and keeps this
tuple self-documenting about exactly which failures are considered
retryable, rather than relying on inheritance a reader would have to
already know."""


def ask_openai(prompt: dict, *, temperature: float = _DEFAULT_TEMPERATURE) -> str:
    """Send a Prompt Builder `Prompt` to OpenAI and return the assistant's
    answer text.

    `prompt` must be exactly the shape `nodeiq.llm.prompt.build_prompt()`
    returns (`{"system": ..., "user": ..., "prompt_version": ...}`) —
    this function reads `prompt["system"]`/`prompt["user"]` and nothing
    else; it never rebuilds, edits, or adds to the prompt text itself.

    Raises one of `nodeiq.llm.exceptions`' `LLMError` subclasses for
    every failure category this module handles (missing/invalid API
    key, authentication failure, timeout, connection failure, rate
    limiting, a server error, or an empty/malformed response) — never
    an OpenAI SDK exception, and never a raw Python exception whose
    message might contain SDK/API implementation detail.
    """
    messages = [
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": prompt["user"]},
    ]
    response = send_chat_request(messages, temperature=temperature)
    return _extract_answer(response)


def send_chat_request(
    messages: list,
    *,
    temperature: float = _DEFAULT_TEMPERATURE,
    tools: list | None = None,
    tool_choice: str | dict | None = None,
):
    """The lower-level primitive `ask_openai()` is built on, also used
    directly by `nodeiq.routing.tool_calling_router` (tier 3's
    multi-round tool-calling conversation needs the *raw* response,
    including any `tool_calls`, not just a plain text answer — and needs
    to grow the `messages` list across rounds, not just one fixed
    system+user pair).

    Every retry/timeout/error-translation guarantee `ask_openai()` has
    always had lives in exactly this one place — both callers share it,
    never duplicate it. Raises the same `LLMError` subclasses as
    `ask_openai()`; returns the raw SDK response object (callers extract
    whatever shape they need from it).
    """
    api_key = _load_api_key()
    client = openai.OpenAI(api_key=api_key)

    kwargs = {
        "model": _DEFAULT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "timeout": _REQUEST_TIMEOUT_SECONDS,
        "max_completion_tokens": _MAX_RESPONSE_TOKENS,
    }
    if tools is not None:
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return client.chat.completions.create(**kwargs)
        except openai.AuthenticationError:
            raise LLMAuthenticationError(
                "OpenAI rejected the configured API key."
            ) from None
        except _RETRYABLE_EXCEPTIONS as exc:
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_SECONDS)
                continue
            raise _translate_exhausted_retry(exc) from None
        except openai.APIStatusError as exc:
            raise LLMError(
                f"OpenAI rejected the request (HTTP {exc.status_code})."
            ) from None
        except openai.OpenAIError:
            raise LLMError("OpenAI request failed for an unexpected reason.") from None


def _load_api_key() -> str:
    """Read `OPENAI_API_KEY` — the only place in NodeIQ this environment
    variable is read.

    `load_dotenv()` loads a project-root `.env` file into the process
    environment if one exists (for local development, per
    `DECISIONS.md` ADR-008) without overriding a variable already set —
    so an explicitly exported `OPENAI_API_KEY` always wins over a
    `.env` file's value. Raises `LLMConfigurationError` with a clear,
    actionable message if no key is configured either way. The key
    itself never appears in that message, in any log, or anywhere else
    this function touches.
    """
    load_dotenv()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise LLMConfigurationError(
            "OPENAI_API_KEY is not configured. Create a .env file or "
            "export the environment variable."
        )
    return api_key


def _translate_exhausted_retry(exc: Exception) -> LLMError:
    """After `_MAX_ATTEMPTS` retryable failures, translate the last one
    into the matching `LLMError` subclass. Never includes `str(exc)` —
    only a fixed, safe, project-authored message — since an SDK
    exception's text could otherwise carry request/response detail this
    module must not expose to callers.
    """
    if isinstance(exc, openai.APITimeoutError):
        return LLMTimeoutError(
            f"The request to OpenAI timed out after {_MAX_ATTEMPTS} attempt(s)."
        )
    if isinstance(exc, openai.APIConnectionError):
        return LLMConnectionError(
            f"Could not connect to OpenAI after {_MAX_ATTEMPTS} attempt(s)."
        )
    if isinstance(exc, openai.RateLimitError):
        return LLMRateLimitError(
            f"OpenAI rate-limited this request after {_MAX_ATTEMPTS} attempt(s)."
        )
    return LLMServerError(
        f"OpenAI reported a server error after {_MAX_ATTEMPTS} attempt(s)."
    )


def _extract_answer(response) -> str:
    """Validate and extract the assistant's text from a chat completion
    response. Raises `LLMResponseError` for anything short of "a
    non-empty answer was returned" — no partial/best-effort extraction,
    since a caller acting on a malformed answer is worse than a clear
    failure.
    """
    choices = getattr(response, "choices", None)
    if not choices:
        raise LLMResponseError("OpenAI returned a response with no choices.")

    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None) if message is not None else None
    if not content or not content.strip():
        raise LLMResponseError("OpenAI returned an empty response.")

    return content
