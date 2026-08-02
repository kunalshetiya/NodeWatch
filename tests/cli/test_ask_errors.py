"""Unit tests for nodeiq.cli.ask_errors."""

from nodeiq.cli.ask_errors import format_ask_error
from nodeiq.core.exceptions import SnapshotError
from nodeiq.llm.exceptions import LLMAuthenticationError, LLMConfigurationError


def test_snapshot_error_message_tells_the_user_to_scan():
    message = format_ask_error(SnapshotError("no snapshot files found in snapshots"))

    assert "No snapshot found" in message
    assert "nodeiq scan" in message
    assert "no snapshot files found in snapshots" in message


def test_llm_error_message_is_prefixed_consistently():
    message = format_ask_error(LLMConfigurationError("OPENAI_API_KEY is not configured."))

    assert message == "Could not get an answer: OPENAI_API_KEY is not configured."


def test_llm_authentication_error_uses_same_prefix():
    message = format_ask_error(LLMAuthenticationError("OpenAI rejected the configured API key."))

    assert message.startswith("Could not get an answer:")


def test_generic_exception_gets_a_clean_fallback_message():
    message = format_ask_error(RuntimeError("something unexpected"))

    assert message == "Could not complete ask: something unexpected"


def test_no_message_ever_contains_a_python_traceback_marker():
    for exc in (
        SnapshotError("boom"),
        LLMConfigurationError("boom"),
        RuntimeError("boom"),
    ):
        message = format_ask_error(exc)
        assert "Traceback" not in message
        assert "File \"" not in message
