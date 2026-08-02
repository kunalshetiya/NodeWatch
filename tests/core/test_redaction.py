"""Unit tests for nodeiq.core.redaction."""

from nodeiq.core.redaction import redact_secrets

# --- API keys / tokens --------------------------------------------------------------


def test_redacts_openai_style_api_key():
    result = redact_secrets("OPENAI_API_KEY=sk-proj-abcdef123")
    assert result == "OPENAI_API_KEY=[REDACTED]"
    assert "sk-proj-abcdef123" not in result


def test_redacts_generic_api_key_assignment():
    result = redact_secrets("api_key=abcdef1234567890")
    assert result == "api_key=[REDACTED]"


def test_redacts_apikey_with_no_delimiter():
    result = redact_secrets("apikey=xyz789")
    assert result == "apikey=[REDACTED]"


def test_redacts_token_assignment():
    result = redact_secrets("AUTH_TOKEN=abc.def.ghi")
    assert "abc.def.ghi" not in result
    assert "AUTH_TOKEN=[REDACTED]" in result


def test_redacts_bearer_token():
    result = redact_secrets("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
    assert "Bearer [REDACTED]" in result


# --- Passwords -----------------------------------------------------------------------


def test_redacts_password_assignment():
    result = redact_secrets("password=mysecret")
    assert result == "password=[REDACTED]"


def test_redacts_password_with_colon_separator():
    result = redact_secrets("db_password: hunter2")
    assert result == "db_password: [REDACTED]"


def test_redacts_quoted_password():
    result = redact_secrets('password="mysecret123"')
    assert "mysecret123" not in result
    assert "REDACTED" in result


def test_redacts_passwd_spelling():
    result = redact_secrets("passwd=letmein")
    assert result == "passwd=[REDACTED]"


# --- Credentials / secrets -----------------------------------------------------------


def test_redacts_credential_assignment():
    result = redact_secrets("db_credential=admin:s3cr3t")
    assert "s3cr3t" not in result
    assert "REDACTED" in result


def test_redacts_secret_assignment():
    result = redact_secrets("client_secret=abc123def456")
    assert result == "client_secret=[REDACTED]"


# --- Private keys ----------------------------------------------------------------------


def test_redacts_pem_private_key_block():
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA1c7...redacted-body...\n"
        "-----END RSA PRIVATE KEY-----"
    )
    result = redact_secrets(f"config dump: {pem}")
    assert "MIIEpAIBAAKCAQEA1c7" not in result
    assert "[REDACTED]" in result


def test_redacts_generic_private_key_block():
    pem = "-----BEGIN PRIVATE KEY-----\nabc123\n-----END PRIVATE KEY-----"
    result = redact_secrets(pem)
    assert "abc123" not in result


# --- No false positives (explicit "no false assumptions" requirement) -----------------


def test_does_not_redact_unrelated_word_containing_key_substring():
    result = redact_secrets("monkey=true")
    assert result == "monkey=true"


def test_does_not_redact_ordinary_assignments():
    result = redact_secrets("disk_usage=95")
    assert result == "disk_usage=95"


def test_does_not_redact_plain_prose_mentioning_credentials():
    result = redact_secrets("credentials.json path is /etc/app/credentials.json")
    assert "REDACTED" not in result


def test_does_not_redact_normal_log_message():
    result = redact_secrets("nginx.service: Main process exited, code=exited, status=1/FAILURE")
    assert "REDACTED" not in result
    assert result == "nginx.service: Main process exited, code=exited, status=1/FAILURE"


# --- Malformed / edge-case input ---------------------------------------------------------


def test_empty_string_returned_unchanged():
    assert redact_secrets("") == ""


def test_none_returned_unchanged():
    assert redact_secrets(None) is None


def test_whitespace_only_returned_unchanged():
    assert redact_secrets("   ") == "   "


def test_multiple_secrets_in_one_message_all_redacted():
    result = redact_secrets("OPENAI_API_KEY=sk-abc123 password=hunter2")
    assert "sk-abc123" not in result
    assert "hunter2" not in result
    assert result.count("[REDACTED]") == 2


def test_redaction_is_deterministic():
    text = "OPENAI_API_KEY=sk-abc123"
    assert redact_secrets(text) == redact_secrets(text)


def test_does_not_mutate_or_error_on_non_ascii_text():
    result = redact_secrets("café password=café123")
    assert "café123" not in result
    assert "café" in result
