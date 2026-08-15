"""Turn an exception into a cause a human can act on — and nothing else.

Constitution Rule 1 applies to the ``workflow_runs.error`` column: it is
user-facing text, not a traceback. An exception raised by an ingest or a tool
routinely carries the very thing that must never be stored — the note content
that failed to parse, the absolute path it was read from, the rule text that
did not match.

The rule here is deliberately strict, because the input is untrusted: an
exception message is built from the data that failed. We keep the exception
type, which is always safe and is the part that actually tells a reader where
to look, and we admit the message only after removing every construct that can
carry content: paths, filenames, quoted spans, addresses, URLs, traceback
frames, credentials, and data-shaped tokens such as long numbers and ids.

**Known limit.** Redaction cannot classify free prose. A message that embeds
content as ordinary unquoted words, with no path and no data-shaped token, is
not distinguishable from a description of a failure and will survive. The
mitigations are the short length cap and the fact that the callers in this
package build their own cause text for the paths they control; only exceptions
raised by third-party code reach this function unshaped. Treat that as a
reason to keep causes short at the raise site, not as a reason to trust this
with a payload.

Want the traceback? ``logger.exception`` it. It never goes in the column.
"""

from __future__ import annotations

import re

# Longest cause we store. A cause is a short phrase, not a report. A long
# message is nearly always a message built around a payload.
MAX_CAUSE_LENGTH = 120

# Token shapes that carry data rather than describe a failure. A number long
# enough to be an amount, an IBAN, an account or an id is not a cause, and a
# very long unbroken token is a payload however it was quoted.
_DATA_TOKEN_PATTERNS: list[tuple[str, str]] = [
    # Any run of 5+ digits, and any mixed alphanumeric token of 12+ chars.
    (r"\b[A-Z]{2}\d{2}[A-Za-z0-9]{8,}\b", "<id>"),   # IBAN-shaped
    (r"\b\d{5,}\b", "<number>"),
    (r"\b(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{12,}\b", "<id>"),
]

# Constructs that carry content rather than describe a failure. Each is
# replaced wholesale — we never keep "part of" a path or a quoted payload.
_CONTENT_PATTERNS: list[tuple[str, str]] = [
    # Absolute and home-relative paths, with or without a drive letter.
    (r"(?:[A-Za-z]:)?[\\/~][\w.\-~]*(?:[\\/][\w.\-]+)+/?", "<path>"),
    # Bare filenames with a content-bearing extension.
    (r"\b[\w.\-]+\.(?:md|txt|json|ya?ml|csv|eml|html?|pdf|sqlite3?|db|log)\b", "<file>"),
    # Quoted spans: the conventional place an exception embeds the payload.
    (r"\"[^\"]*\"", "<value>"),
    (r"'[^']*'", "<value>"),
    (r"`[^`]*`", "<value>"),
    # Anything that looks like an address or a URL.
    (r"\b[\w.\-+]+@[\w.\-]+\.\w+\b", "<address>"),
    (r"\bhttps?://\S+", "<url>"),
    # Traceback frames, in case a message was built from one.
    (r"File \"[^\"]*\", line \d+", "<frame>"),
    (r"Traceback \(most recent call last\):.*", "<traceback>"),
]

# What remains after redaction must look like prose. Anything else is dropped
# rather than guessed at: a cause of just the exception type is honest, a cause
# carrying an unrecognised fragment of user data is not.
_SAFE_MESSAGE = re.compile(r"^[\w\s.,:;()\[\]/#%+*=<>!?'\"-]*$")


def sanitize_cause_text(text: str, max_length: int = MAX_CAUSE_LENGTH) -> str:
    """Strip content-bearing constructs from a free-text cause.

    Args:
        text: Raw text, possibly built from the data that failed.
        max_length: Truncation limit for the result.

    Returns:
        Text safe to store in ``workflow_runs.error``. May be empty.
    """
    result = text.strip()
    if not result:
        return ""

    # Credentials first — the sanitizer owns that pattern set, we do not
    # duplicate it here.
    try:
        from mycelos.security.sanitizer import ResponseSanitizer

        result = ResponseSanitizer().sanitize_text(result)
    except Exception:  # pragma: no cover - sanitizer must never break recording
        pass

    for pattern, replacement in _CONTENT_PATTERNS:
        result = re.sub(pattern, replacement, result, flags=re.DOTALL)

    for pattern, replacement in _DATA_TOKEN_PATTERNS:
        result = re.sub(pattern, replacement, result)

    # Collapse whitespace so a multi-line message becomes one sentence.
    result = " ".join(result.split())

    if not _SAFE_MESSAGE.match(result):
        return ""

    # Truncate at a word boundary rather than mid-token: a cut token can
    # still be a readable fragment of the content that failed.
    if len(result) > max_length:
        head = result[:max_length]
        cut = head.rfind(" ")
        result = (head[:cut] if cut > 0 else head).rstrip() + " …"
    return result


def describe_exception(exc: BaseException) -> str:
    """Build the stored cause for an exception.

    The exception type always survives; the message survives only what
    :func:`sanitize_cause_text` permits. The result never contains a
    traceback, a file path, or the content that failed to parse.

    Args:
        exc: The exception that ended the run.

    Returns:
        A short cause, e.g. ``"RuntimeError: provider unreachable"``.
    """
    exc_type = type(exc).__name__
    message = sanitize_cause_text(str(exc))
    if not message:
        return exc_type
    return f"{exc_type}: {message}"
