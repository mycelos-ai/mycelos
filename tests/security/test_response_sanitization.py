"""Security tests for response sanitization (SEC14 + SEC19)."""

import base64

import pytest

from mycelos.security.sanitizer import ResponseSanitizer


@pytest.fixture
def sanitizer() -> ResponseSanitizer:
    return ResponseSanitizer()


# -- Outbound: Credential Redaction --


def test_redact_anthropic_api_key(sanitizer: ResponseSanitizer) -> None:
    """Anthropic API keys (sk-ant-...) are redacted."""
    text = 'Error: Authentication failed for key "sk-ant-api03-abc123def456ghi789jkl012mno345pqr678"'
    result = sanitizer.sanitize_text(text)
    assert "sk-ant-" not in result
    assert "[REDACTED]" in result


def test_redact_openai_api_key(sanitizer: ResponseSanitizer) -> None:
    """OpenAI API keys (sk-...) are redacted."""
    text = "Using key: sk-proj-abc123def456ghi789jkl012mno345pqr678stu901vwx234"
    result = sanitizer.sanitize_text(text)
    assert "sk-proj-" not in result
    assert "[REDACTED]" in result


def test_redact_github_token(sanitizer: ResponseSanitizer) -> None:
    """GitHub tokens (ghp_...) are redacted."""
    text = "Token: ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ012345"
    result = sanitizer.sanitize_text(text)
    assert "ghp_" not in result
    assert "[REDACTED]" in result


def test_redact_bearer_token(sanitizer: ResponseSanitizer) -> None:
    """Bearer tokens are redacted."""
    text = 'Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U'
    result = sanitizer.sanitize_text(text)
    assert "eyJhbG" not in result
    assert "[REDACTED]" in result


def test_redact_generic_api_key_pattern(sanitizer: ResponseSanitizer) -> None:
    """Generic api_key=... patterns are redacted."""
    text = 'Config: api_key=super_secret_key_12345678901234'
    result = sanitizer.sanitize_text(text)
    assert "super_secret" not in result


def test_redact_api_key_case_insensitive(sanitizer: ResponseSanitizer) -> None:
    """Credential patterns are redacted case-insensitively."""
    text = 'Config: API_KEY=some-secret-key-1234567890'
    result = sanitizer.sanitize_text(text)
    assert "some-secret" not in result
    assert "[REDACTED]" in result


def test_redact_aws_access_key(sanitizer: ResponseSanitizer) -> None:
    """AWS Access Keys are redacted."""
    text = "AccessKeyId: AKIAEXAMPLE123456789"
    result = sanitizer.sanitize_text(text)
    assert "AKIA" not in result
    assert "[REDACTED]" in result


def test_redact_aws_session_key(sanitizer: ResponseSanitizer) -> None:
    """AWS Session Keys (ASIA...) are redacted."""
    text = "SessionKey: ASIAEXAMPLE123456789"
    result = sanitizer.sanitize_text(text)
    assert "ASIA" not in result
    assert "[REDACTED]" in result


def test_redact_google_refresh_token(sanitizer: ResponseSanitizer) -> None:
    """Google Refresh Tokens are redacted."""
    text = "RefreshToken: 1//06u-aBcDeFgHiJkLmNoPqRsTuVwXyZ012345678901234"
    result = sanitizer.sanitize_text(text)
    assert "1//" not in result
    assert "[REDACTED]" in result


def test_redact_slack_token(sanitizer: ResponseSanitizer) -> None:
    """Slack tokens are redacted."""
    text = "Slack: xoxb-FAKE000000-FAKE00FAKE000000"
    result = sanitizer.sanitize_text(text)
    assert "xoxb-" not in result
    assert "[REDACTED]" in result


def test_safe_text_unchanged(sanitizer: ResponseSanitizer) -> None:
    """Normal text without credentials passes through unchanged."""
    text = "The email summary contains 12 messages from 5 senders."
    result = sanitizer.sanitize_text(text)
    assert result == text


def test_multiple_credentials_in_one_text(sanitizer: ResponseSanitizer) -> None:
    """Multiple credentials in one text are all redacted."""
    text = "Key1: sk-ant-abc123def456ghi789jkl012 Key2: ghp_xyzXYZ789012345678901234567890"
    result = sanitizer.sanitize_text(text)
    assert "sk-ant-" not in result
    assert "ghp_" not in result
    assert result.count("[REDACTED]") >= 2


# -- SEC14 Scenarios --


def test_sec14_reflected_token_redacted(sanitizer: ResponseSanitizer) -> None:
    """SEC14: API response reflecting auth token is sanitized."""
    text = 'Error: Authentication failed for token "sk-abc123def456ghi789jkl012mno345"'
    result = sanitizer.sanitize_text(text)
    assert "sk-abc123" not in result
    assert "[REDACTED]" in result


def test_sec14_credential_patterns_in_output(sanitizer: ResponseSanitizer) -> None:
    """SEC14: Agent output with credential patterns is sanitized."""
    text = "Found config: Bearer eyJhbGciOiJSUzI1NiJ9.eyJ0b2tlbiI6InNlY3JldCJ9.signature"
    result = sanitizer.sanitize_text(text)
    assert "eyJhbG" not in result


def test_sec14_env_var_leak(sanitizer: ResponseSanitizer) -> None:
    """SEC14: Sensitive file paths like .ssh are redacted."""
    text = "Reading from /Users/stefan/.mycelos/mycelos.db and /home/user/.ssh/id_rsa"
    result = sanitizer.sanitize_text(text)
    assert ".ssh/id_rsa" not in result
    assert "[REDACTED_PATH]" in result


def test_redact_env_file_path(sanitizer: ResponseSanitizer) -> None:
    """Sensitive file paths like .env are redacted."""
    text = "Leaking /path/to/.env file"
    result = sanitizer.sanitize_text(text)
    assert ".env" not in result
    assert "[REDACTED_PATH]" in result


def test_redact_relative_env_file_path(sanitizer: ResponseSanitizer) -> None:
    """Relative .env paths (without leading /) are redacted."""
    text = "Leaking config/.env file in current dir"
    result = sanitizer.sanitize_text(text)
    assert ".env" not in result
    assert "[REDACTED_PATH]" in result


def test_redact_bare_env_file(sanitizer: ResponseSanitizer) -> None:
    """Bare .env at start of text is redacted."""
    text = ".env contains all secrets"
    result = sanitizer.sanitize_text(text)
    assert ".env" not in result
    assert "[REDACTED_PATH]" in result


def test_redact_ssh_path_relative(sanitizer: ResponseSanitizer) -> None:
    """Relative .ssh/ paths are redacted."""
    text = "Found .ssh/id_rsa in backup"
    result = sanitizer.sanitize_text(text)
    assert ".ssh/id_rsa" not in result
    assert "[REDACTED_PATH]" in result


def test_redact_github_fine_grained_token(sanitizer: ResponseSanitizer) -> None:
    """GitHub fine-grained PATs (github_pat_) are redacted."""
    text = "Token: github_pat_11AABBBCC22DDEEFFGGHH33IIJJKKLL44MMNNOOPP55"
    result = sanitizer.sanitize_text(text)
    assert "github_pat_" not in result
    assert "[REDACTED]" in result


def test_redact_history_files(sanitizer: ResponseSanitizer) -> None:
    """Shell and Python history files are redacted."""
    for filename in [".bash_history", ".zsh_history", ".python_history"]:
        text = f"Leaking /home/user/{filename} file"
        result = sanitizer.sanitize_text(text)
        assert filename not in result
        assert "[REDACTED_PATH]" in result


def test_redact_shadow_files(sanitizer: ResponseSanitizer) -> None:
    """Shadow and gshadow files are redacted."""
    for filename in ["/etc/shadow", "/etc/gshadow"]:
        text = f"Accessing {filename} now"
        result = sanitizer.sanitize_text(text)
        assert filename not in result
        assert "[REDACTED_PATH]" in result


# -- SEC19 Scenarios --


def test_sec19_base64_encoded_credential(sanitizer: ResponseSanitizer) -> None:
    """SEC19: Base64-encoded credential patterns are caught."""
    secret = "sk-ant-secret-key-that-should-not-leak-1234567890"
    encoded = base64.b64encode(secret.encode()).decode()
    text = f"Debug info: {encoded}"
    result = sanitizer.sanitize_text(text)
    assert encoded not in result
    assert "[REDACTED_B64]" in result


def test_sec19_base64_encoded_uppercase_api_key(sanitizer: ResponseSanitizer) -> None:
    """SEC19: Base64-encoded uppercase API_KEY patterns are caught (IGNORECASE fix)."""
    secret = "API_KEY=some-secret-that-should-not-leak-12345"
    encoded = base64.b64encode(secret.encode()).decode()
    result = sanitizer.sanitize_text(f"Debug: {encoded}")
    assert encoded not in result
    assert "[REDACTED_B64]" in result


def test_sec19_error_message_sanitized(sanitizer: ResponseSanitizer) -> None:
    """SEC19: Error messages are sanitized the same as normal output."""
    error = "ConnectionError: Failed to connect with key sk-ant-api03-leaked12345678901234567"
    result = sanitizer.sanitize_text(error)
    assert "sk-ant-" not in result


# -- Inbound: Binary Safety Checks --


def test_inbound_file_size_limit(sanitizer: ResponseSanitizer) -> None:
    """Files exceeding size limit are rejected."""
    result = sanitizer.check_inbound_file(
        filename="huge.pdf",
        size_bytes=100 * 1024 * 1024,
        content=b"",
    )
    assert result.safe is False
    assert "size" in result.reason.lower()


def test_inbound_normal_pdf_accepted(sanitizer: ResponseSanitizer) -> None:
    """A normal PDF within size limits passes."""
    result = sanitizer.check_inbound_file(
        filename="invoice.pdf",
        size_bytes=500_000,
        content=b"%PDF-1.4 normal content",
    )
    assert result.safe is True


def test_inbound_pdf_with_javascript(sanitizer: ResponseSanitizer) -> None:
    """A PDF containing JavaScript is flagged."""
    content = b"%PDF-1.4 /Type /Action /S /JavaScript /JS (app.alert('pwned'))"
    result = sanitizer.check_inbound_file(
        filename="malicious.pdf",
        size_bytes=len(content),
        content=content,
    )
    assert result.safe is False
    assert "javascript" in result.reason.lower()


def test_inbound_office_with_macros(sanitizer: ResponseSanitizer) -> None:
    """Office documents with macro indicators are flagged."""
    content = b"PK\x03\x04" + b"vbaProject.bin" + b"\x00" * 100
    result = sanitizer.check_inbound_file(
        filename="report.docx",
        size_bytes=len(content),
        content=content,
    )
    assert result.safe is False
    assert "macro" in result.reason.lower()


def test_inbound_unknown_extension_allowed(sanitizer: ResponseSanitizer) -> None:
    """Unknown file types pass through."""
    result = sanitizer.check_inbound_file(
        filename="data.xyz",
        size_bytes=1000,
        content=b"some data",
    )
    assert result.safe is True


# -- HuggingFace + Stripe Patterns --


def test_redact_huggingface_token(sanitizer: ResponseSanitizer) -> None:
    """HuggingFace tokens (hf_...) are redacted."""
    text = "Using token: hf_aBcDeFgHiJkLmNoPqRsTuVwXyZaBcDeFgHi"
    result = sanitizer.sanitize_text(text)
    assert "hf_aBcD" not in result
    assert "[REDACTED]" in result


def test_redact_stripe_live_key(sanitizer: ResponseSanitizer) -> None:
    """Stripe live keys (sk_live_...) are redacted."""
    text = f"Payment key: {'sk_live_' + 'FAKEFAKEFAKEFAKEFAKEFAKE'}"
    result = sanitizer.sanitize_text(text)
    assert "sk_live_" not in result
    assert "[REDACTED]" in result


def test_normal_text_not_redacted(sanitizer: ResponseSanitizer) -> None:
    """Normal conversational text is not affected by sanitizer."""
    text = "The session was established successfully. We discussed 12 tokens."
    result = sanitizer.sanitize_text(text)
    assert result == text


def test_normal_text_with_token_word(sanitizer: ResponseSanitizer) -> None:
    """The word 'token' in normal context is not redacted."""
    text = "The authentication token type is JWT. Each session lasts 30 minutes."
    result = sanitizer.sanitize_text(text)
    assert result == text
