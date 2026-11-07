# Response Sanitizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a bidirectional Sanitizer that strips credentials from outgoing agent responses AND scans incoming data (API responses, file attachments) for dangerous content.

**Architecture:** The Sanitizer is a stateless function pipeline — text goes in, sanitized text comes out. No database, no state. It uses regex patterns for credential detection and simple heuristics for binary safety checks. It sits in the Security Layer between the MCP connector and the agent.

**Tech Stack:** Python 3.12+, `re` (stdlib), pytest

**Scope:**
- **Outbound:** Redact reflected credentials (API keys, tokens, Bearer headers), system paths, env vars, base64-encoded secrets
- **Inbound:** Check incoming binaries for basic safety (file size limits, known-bad magic bytes, embedded scripts in PDFs/Office docs). NOT a full antivirus — a lightweight first-pass filter.
- **No external dependencies.** Pure stdlib regex + basic binary checks.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/mycelos/security/sanitizer.py` | `ResponseSanitizer` — bidirectional text/binary sanitization |
| `tests/security/test_response_sanitization.py` | SEC14 + SEC19 scenario tests |

---

### Task 1: Implement ResponseSanitizer + tests

**Files:**
- Create: `src/mycelos/security/sanitizer.py`
- Create: `tests/security/test_response_sanitization.py`

- [ ] **Step 1: Write failing tests**

Create `tests/security/test_response_sanitization.py`:

```python
"""Security tests for response sanitization (SEC14 + SEC19)."""

import pytest

from mycelos.security.sanitizer import ResponseSanitizer


@pytest.fixture
def sanitizer() -> ResponseSanitizer:
    return ResponseSanitizer()


# ── Outbound: Credential Redaction ──


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
    """GitHub tokens (ghp_..., gho_..., ghs_...) are redacted."""
    text = "Token: ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ012345"
    result = sanitizer.sanitize_text(text)
    assert "ghp_" not in result
    assert "[REDACTED]" in result


def test_redact_bearer_token(sanitizer: ResponseSanitizer) -> None:
    """Bearer tokens in headers are redacted."""
    text = 'Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U'
    result = sanitizer.sanitize_text(text)
    assert "eyJhbG" not in result
    assert "[REDACTED]" in result


def test_redact_generic_api_key_pattern(sanitizer: ResponseSanitizer) -> None:
    """Generic 'api_key=...' or 'apikey:...' patterns are redacted."""
    text = 'Config: api_key=super_secret_key_12345678901234'
    result = sanitizer.sanitize_text(text)
    assert "super_secret" not in result


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


# ── SEC14 Scenarios ──


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
    """SEC14: System paths that look like home directories are flagged."""
    text = "Reading from /Users/stefan/.mycelos/mycelos.db and /home/user/.ssh/id_rsa"
    result = sanitizer.sanitize_text(text)
    assert ".ssh/id_rsa" not in result
    assert "[REDACTED_PATH]" in result


# ── SEC19 Scenarios ──


def test_sec19_base64_encoded_credential(sanitizer: ResponseSanitizer) -> None:
    """SEC19: Base64-encoded strings that decode to credential patterns are caught."""
    import base64
    secret = "sk-ant-secret-key-that-should-not-leak-1234567890"
    encoded = base64.b64encode(secret.encode()).decode()
    text = f"Debug info: {encoded}"
    result = sanitizer.sanitize_text(text)
    assert encoded not in result
    assert "[REDACTED_B64]" in result


def test_sec19_error_message_sanitized(sanitizer: ResponseSanitizer) -> None:
    """SEC19: Error messages are sanitized the same as normal output."""
    error = "ConnectionError: Failed to connect with key sk-ant-api03-leaked12345678901234567"
    result = sanitizer.sanitize_text(error)
    assert "sk-ant-" not in result


# ── Inbound: Binary Safety Checks ──


def test_inbound_file_size_limit(sanitizer: ResponseSanitizer) -> None:
    """Files exceeding size limit are rejected."""
    result = sanitizer.check_inbound_file(
        filename="huge.pdf",
        size_bytes=100 * 1024 * 1024,  # 100MB
        content=b"",  # content not checked if size exceeds limit
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
    """A PDF containing JavaScript actions is flagged."""
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
    # VBA macro indicator
    content = b"PK\x03\x04" + b"vbaProject.bin" + b"\x00" * 100
    result = sanitizer.check_inbound_file(
        filename="report.docx",
        size_bytes=len(content),
        content=content,
    )
    assert result.safe is False
    assert "macro" in result.reason.lower()


def test_inbound_unknown_extension_allowed(sanitizer: ResponseSanitizer) -> None:
    """Unknown file types pass through (we don't block by extension)."""
    result = sanitizer.check_inbound_file(
        filename="data.xyz",
        size_bytes=1000,
        content=b"some data",
    )
    assert result.safe is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/security/test_response_sanitization.py -v`
Expected: FAIL — ResponseSanitizer doesn't exist

- [ ] **Step 3: Implement ResponseSanitizer**

Create `src/mycelos/security/sanitizer.py`:

```python
"""Response Sanitizer — bidirectional content sanitization.

Outbound (agent → user): Strips reflected credentials, tokens, system paths.
Inbound (external → agent): Checks binaries for known-bad patterns (JS in PDFs, macros in Office).

Stateless — no database, no configuration. Pure function pipeline.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass

# ── Outbound: Credential Patterns ──

# Order matters: more specific patterns first
CREDENTIAL_PATTERNS: list[tuple[str, str]] = [
    # Anthropic API keys
    (r"sk-ant-[a-zA-Z0-9\-_]{20,}", "[REDACTED]"),
    # OpenAI API keys
    (r"sk-(?:proj-)?[a-zA-Z0-9]{20,}", "[REDACTED]"),
    # GitHub tokens
    (r"gh[pous]_[a-zA-Z0-9]{20,}", "[REDACTED]"),
    # Bearer tokens (JWT or opaque)
    (r"Bearer\s+[a-zA-Z0-9\-_.~+/]+=*", "Bearer [REDACTED]"),
    # Generic api_key=... or apikey:... patterns
    (r"(?:api[_-]?key|apikey|api_secret|secret_key)\s*[=:]\s*\S{10,}", "[REDACTED]"),
]

# Sensitive file paths
SENSITIVE_PATH_PATTERNS: list[tuple[str, str]] = [
    # SSH keys
    (r"/[^\s]*\.ssh/[^\s]*", "[REDACTED_PATH]"),
    # Private keys
    (r"/[^\s]*\.pem\b", "[REDACTED_PATH]"),
    (r"/[^\s]*\.key\b", "[REDACTED_PATH]"),
]

# ── Inbound: Binary Safety ──

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB default

# Dangerous patterns in PDFs
PDF_DANGEROUS_PATTERNS = [
    rb"/JavaScript",
    rb"/JS\s*\(",
    rb"/Launch",
    rb"/EmbeddedFile",
    rb"/OpenAction.*JavaScript",
]

# Dangerous patterns in Office documents (ZIP-based)
OFFICE_MACRO_PATTERNS = [
    rb"vbaProject\.bin",
    rb"xl/macrosheets",
    rb"word/vbaData",
]


@dataclass(frozen=True)
class InboundCheckResult:
    """Result of an inbound file safety check."""
    safe: bool
    reason: str = ""


class ResponseSanitizer:
    """Bidirectional content sanitizer for the Security Layer."""

    def sanitize_text(self, text: str) -> str:
        """Sanitize outbound text — redact credentials, tokens, sensitive paths.

        Applied to: agent output, API responses, error messages.
        """
        result = text

        # Redact credential patterns
        for pattern, replacement in CREDENTIAL_PATTERNS:
            result = re.sub(pattern, replacement, result)

        # Redact sensitive paths
        for pattern, replacement in SENSITIVE_PATH_PATTERNS:
            result = re.sub(pattern, replacement, result)

        # Check for base64-encoded credentials
        result = self._redact_base64_credentials(result)

        return result

    def _redact_base64_credentials(self, text: str) -> str:
        """Detect and redact base64-encoded strings that decode to credential patterns."""
        # Find potential base64 strings (40+ chars, base64 alphabet)
        b64_pattern = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")

        def check_and_redact(match: re.Match) -> str:
            try:
                decoded = base64.b64decode(match.group()).decode("utf-8", errors="ignore")
                # Check if decoded content matches any credential pattern
                for pattern, _ in CREDENTIAL_PATTERNS:
                    if re.search(pattern, decoded):
                        return "[REDACTED_B64]"
            except Exception:
                pass
            return match.group()

        return b64_pattern.sub(check_and_redact, text)

    def check_inbound_file(
        self,
        filename: str,
        size_bytes: int,
        content: bytes,
    ) -> InboundCheckResult:
        """Check an incoming file for basic safety issues.

        NOT a full antivirus — a lightweight first-pass filter for:
        - Oversized files
        - PDFs with embedded JavaScript
        - Office documents with VBA macros
        """
        # Size check
        if size_bytes > MAX_FILE_SIZE:
            return InboundCheckResult(
                safe=False,
                reason=f"File size {size_bytes} exceeds limit of {MAX_FILE_SIZE} bytes",
            )

        # PDF safety check
        lower_name = filename.lower()
        if lower_name.endswith(".pdf"):
            return self._check_pdf(content)

        # Office document safety check
        if any(lower_name.endswith(ext) for ext in (".docx", ".xlsx", ".pptx", ".doc", ".xls")):
            return self._check_office(content)

        # Other file types: pass through
        return InboundCheckResult(safe=True)

    def _check_pdf(self, content: bytes) -> InboundCheckResult:
        """Check a PDF for embedded JavaScript or launch actions."""
        for pattern in PDF_DANGEROUS_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                return InboundCheckResult(
                    safe=False,
                    reason=f"PDF contains potentially dangerous content: JavaScript or launch action detected",
                )
        return InboundCheckResult(safe=True)

    def _check_office(self, content: bytes) -> InboundCheckResult:
        """Check Office documents for VBA macro indicators."""
        for pattern in OFFICE_MACRO_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                return InboundCheckResult(
                    safe=False,
                    reason="Office document contains macro indicators (vbaProject.bin detected)",
                )
        return InboundCheckResult(safe=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/security/test_response_sanitization.py -v`
Expected: All ~18 tests PASS

- [ ] **Step 5: Run all tests for regressions**

Run: `pytest tests/test_storage.py tests/test_config.py tests/test_memory.py tests/test_audit.py tests/security/ -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/security/sanitizer.py tests/security/test_response_sanitization.py src/mycelos/../docs/superpowers/specs/2026-03-15-agentos-design.md
git commit -m "feat: implement ResponseSanitizer with outbound credential redaction + inbound binary safety

Outbound: redacts API keys (Anthropic, OpenAI, GitHub), Bearer tokens,
generic api_key patterns, sensitive paths (.ssh, .pem), base64-encoded
credentials. SEC14 + SEC19 scenarios covered.

Inbound: checks PDFs for embedded JavaScript/launch actions, Office docs
for VBA macros, enforces file size limits. Lightweight first-pass filter,
not a full antivirus."
```
