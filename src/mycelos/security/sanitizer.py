"""Response Sanitizer -- bidirectional content sanitization.

Outbound (agent -> user): Strips reflected credentials, tokens, system paths.
Inbound (external -> agent): Checks binaries for known-bad patterns.

Stateless -- no database, no configuration. Pure function pipeline.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass

# -- Outbound: Credential Patterns --

CREDENTIAL_PATTERNS: list[tuple[str, str]] = [
    (r"sk-ant-[a-zA-Z0-9\-_]{20,}", "[REDACTED]"),       # Anthropic
    (r"sk-(?:proj-)?[a-zA-Z0-9]{20,}", "[REDACTED]"),     # OpenAI
    (r"gh[pous]_[a-zA-Z0-9]{20,}", "[REDACTED]"),         # GitHub
    (r"github_pat_[a-zA-Z0-9]{20,}", "[REDACTED]"),      # GitHub Fine-grained PAT
    (r"AKIA[0-9A-Z]{16}", "[REDACTED]"),                   # AWS Access Key
    (r"ASIA[0-9A-Z]{16}", "[REDACTED]"),                   # AWS Session Key
    (r"1//[0-9A-Za-z\-_]{40,}", "[REDACTED]"),             # Google Refresh Token
    (r"xox[baprs]-[a-zA-Z0-9-]{10,}", "[REDACTED]"),      # Slack Token
    (r"AIza[0-9A-Za-z\-_]{35}", "[REDACTED]"),             # Google API Key
    (r"sk-or-v1-[a-zA-Z0-9]{48,}", "[REDACTED]"),         # OpenRouter
    (r"\d{10}:[A-Za-z0-9_-]{35}", "[REDACTED]"),           # Telegram Bot Token
    (r"hf_[a-zA-Z0-9]{34}", "[REDACTED]"),                 # HuggingFace Token
    (r"sk_live_[a-zA-Z0-9]{24,}", "[REDACTED]"),           # Stripe Live Key
    (r"Bearer\s+[a-zA-Z0-9\-_.~+/]+=*", "Bearer [REDACTED]"),
    (r"(?:api[_-]?key|apikey|api_secret|secret_key)\s*[=:]\s*\S{10,}", "[REDACTED]"),
]

SENSITIVE_PATH_PATTERNS: list[tuple[str, str]] = [
    (r"(?:^|[\s/])[^\s]*\.ssh/[^\s]*", "[REDACTED_PATH]"),
    (r"(?:^|[\s/])[^\s]*\.pem\b", "[REDACTED_PATH]"),
    (r"(?:^|[\s/])[^\s]*\.key\b", "[REDACTED_PATH]"),
    (r"(?:^|[\s/])[^\s]*\.env\b", "[REDACTED_PATH]"),
    (r"(?:^|[\s/])[^\s]*\.(?:bash|zsh|python)_history\b", "[REDACTED_PATH]"),
    (r"/etc/(?:shadow|gshadow)\b", "[REDACTED_PATH]"),
]

# -- Inbound: Binary Safety --

MAX_FILE_SIZE = 50 * 1024 * 1024

PDF_DANGEROUS_PATTERNS = [
    rb"/JavaScript",
    rb"/JS\s*\(",
    rb"/Launch",
    rb"/EmbeddedFile",
    rb"/OpenAction.*JavaScript",
]

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
        """Sanitize outbound text -- redact credentials, tokens, sensitive paths."""
        result = text

        for pattern, replacement in CREDENTIAL_PATTERNS:
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

        for pattern, replacement in SENSITIVE_PATH_PATTERNS:
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

        result = self._redact_base64_credentials(result)

        return result

    def _redact_base64_credentials(self, text: str) -> str:
        """Detect and redact base64-encoded strings that decode to credential patterns."""
        b64_pattern = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")

        def check_and_redact(match: re.Match) -> str:  # type: ignore[type-arg]
            original_val = match.group()
            try:
                decoded = base64.b64decode(original_val).decode(
                    "utf-8", errors="ignore"
                )
                for pattern, _ in CREDENTIAL_PATTERNS:
                    if re.search(pattern, decoded, re.IGNORECASE):
                        return "[REDACTED_B64]"
            except Exception:
                return "[REDACTED_B64]"  # Fail-closed: redact if decode fails
            return original_val  # Not a credential — keep original

        return b64_pattern.sub(check_and_redact, text)

    def check_inbound_file(
        self,
        filename: str,
        size_bytes: int,
        content: bytes,
    ) -> InboundCheckResult:
        """Check an incoming file for basic safety issues."""
        if size_bytes > MAX_FILE_SIZE:
            return InboundCheckResult(
                safe=False,
                reason=f"File size {size_bytes} exceeds limit of {MAX_FILE_SIZE} bytes",
            )

        lower_name = filename.lower()
        if lower_name.endswith(".pdf"):
            return self._check_pdf(content)

        if any(
            lower_name.endswith(ext)
            for ext in (".docx", ".xlsx", ".pptx", ".doc", ".xls")
        ):
            return self._check_office(content)

        return InboundCheckResult(safe=True)

    def _check_pdf(self, content: bytes) -> InboundCheckResult:
        """Check PDF for embedded JavaScript or launch actions."""
        for pattern in PDF_DANGEROUS_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                return InboundCheckResult(
                    safe=False,
                    reason="PDF contains potentially dangerous content: JavaScript or launch action detected",
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
