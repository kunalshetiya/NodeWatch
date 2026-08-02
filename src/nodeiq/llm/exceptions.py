"""Project-specific exceptions for the OpenAI client.

Mirrors `nodeiq.core.exceptions`'s existing pattern (`SnapshotError`
alongside `NodeIQError`): callers of `nodeiq.llm.client.ask_openai`
catch one of these, never an OpenAI SDK exception directly. Every one
of `client.py`'s error-handling requirements maps onto a distinct
subclass here, so a caller can distinguish "the key is missing" from
"OpenAI is down" from "the response didn't make sense" without needing
to know anything about the OpenAI SDK's own exception hierarchy.
"""

from nodeiq.core.exceptions import NodeIQError


class LLMError(NodeIQError):
    """Base class for every error `nodeiq.llm.client` can raise.

    Never raised directly — callers that only care "did talking to the
    LLM fail at all" can catch this one class, exactly like
    `NodeIQError` itself.
    """


class LLMConfigurationError(LLMError):
    """Raised when `OPENAI_API_KEY` is missing or empty.

    A configuration problem, not a request failure — there was never a
    request to retry, so this is never subject to `client.py`'s retry
    logic.
    """


class LLMAuthenticationError(LLMError):
    """Raised when OpenAI rejects the configured API key.

    Not retried: retrying the same rejected key would never succeed.
    """


class LLMTimeoutError(LLMError):
    """Raised when every attempt at a request timed out."""


class LLMConnectionError(LLMError):
    """Raised when every attempt at a request failed to connect."""


class LLMRateLimitError(LLMError):
    """Raised when OpenAI is still rate-limiting the request after every
    retry attempt.
    """


class LLMServerError(LLMError):
    """Raised when OpenAI reports a server-side error on every attempt."""


class LLMResponseError(LLMError):
    """Raised when a request succeeded but the response was empty or
    didn't have the shape a chat completion is expected to have.

    Not retried: an empty/malformed response is a response validation
    problem, not a transient failure the same request is expected to
    self-correct on a retry.
    """
