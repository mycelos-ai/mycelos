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

Two shapes get explicit treatment, because both were found leaking:

* **Grouped identifiers.** An IBAN, a card number, a tax number and a VAT id
  are written in groups — ``DE89 3704 0044 0532 0130 00``. Matching only the
  solid token catches the form nobody writes. We match across separators.
* **Unbalanced quotes.** A message truncated upstream can open a quote and
  never close it. We cannot know where the payload ends, so an unmatched
  opening quote consumes the rest of the message.

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

# Structured identifiers are written for humans, not for regexes. An IBAN,
# a card number, a tax number and a VAT id all appear grouped — "DE89 3704
# 0044 0532 0130 00", "4111-1111-1111-1111", "DE 123 456 789". Matching only
# the solid form catches the shape nobody actually writes.
#
# So we match the *group* rather than the token: a leading country/prefix
# marker, or a digit group, followed by more groups across spaces, hyphens
# or slashes. Order matters — the grouped forms run before the solid-token
# rules, because once a group is replaced there is nothing left to leak.
_GROUP_SEPARATOR = r"[ \-–—.]"
_DATA_TOKEN_PATTERNS: list[tuple[str, str]] = [
    # IBAN in any separator style: two letters, two digits, then groups.
    # Covers "DE89370400440532013000", "DE89 3704 0044 0532 0130 00",
    # "DE89-3704-...", and mixed separators.
    (
        rf"\b[A-Z]{{2}}\d{{2}}(?:{_GROUP_SEPARATOR}?[A-Za-z0-9]{{2,6}}){{2,}}\b",
        "<id>",
    ),
    # Any sequence of digit groups whose digits together are long enough to
    # be an account, a card, a tax number or an amount with separators.
    # "4111 1111 1111 1111", "123/456/78901", "12 345 678".
    (rf"\b\d{{2,6}}(?:{_GROUP_SEPARATOR}\d{{2,6}}){{1,}}\b", "<number>"),
    # A country/registry prefix followed by grouped digits: "DE 123 456 789".
    (rf"\b[A-Z]{{2}}{_GROUP_SEPARATOR}\d{{2,6}}(?:{_GROUP_SEPARATOR}\d{{2,6}})+\b", "<id>"),
    # Any run of 5+ digits, and any mixed alphanumeric token of 12+ chars.
    (r"\b\d{5,}\b", "<number>"),
    (r"\b(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{12,}\b", "<id>"),
]

# Quote characters that open a span an exception may have wrapped a payload
# in. ASCII pairs plus the German and French typographic pairs, which appear
# in any message built from German text.
_QUOTE_PAIRS: list[tuple[str, str]] = [
    ('"', '"'),
    ("`", "`"),
    ("„", "“"),  # „…“ German
    ("“", "”"),  # “…” English
    ("‚", "‘"),  # ‚…‘ German single
    ("‘", "’"),  # ‘…’ English single
    ("«", "»"),  # «…» French
    ("›", "‹"),  # ›…‹ German guillemets
    ("‹", "›"),
]

# Constructs that carry content rather than describe a failure. Each is
# replaced wholesale — we never keep "part of" a path or a quoted payload.
_CONTENT_PATTERNS: list[tuple[str, str]] = [
    # Absolute and home-relative paths, with or without a drive letter.
    (r"(?:[A-Za-z]:)?[\\/~][\w.\-~]*(?:[\\/][\w.\-]+)+/?", "<path>"),
    # Bare filenames with a content-bearing extension.
    (r"\b[\w.\-]+\.(?:md|txt|json|ya?ml|csv|eml|html?|pdf|sqlite3?|db|log)\b", "<file>"),
    # Anything that looks like an address or a URL.
    (r"\b[\w.\-+]+@[\w.\-]+\.\w+\b", "<address>"),
    (r"\bhttps?://\S+", "<url>"),
]

# Traceback shapes, in case a message was built from one. These run before
# the quoted-span pass: a frame quotes its path, and we want it named as a
# frame rather than reduced to an anonymous <value>.
_TRACEBACK_PATTERNS: list[tuple[str, str]] = [
    (r"File \"[^\"]*\", line \d+", "<frame>"),
    (r"Traceback \(most recent call last\):.*", "<traceback>"),
]

# What remains after redaction must look like prose. Anything else is dropped
# rather than guessed at: a cause of just the exception type is honest, a cause
# carrying an unrecognised fragment of user data is not.
_SAFE_MESSAGE = re.compile(r"^[\w\s.,:;()\[\]/#%+*=<>!?'\"-]*$")

# An apostrophe inside a word is possessive or elided prose ("Anna's",
# "don't"), not a quote. Only an apostrophe that opens at a word boundary
# can be a quoted span, so a single quote is treated as an opening delimiter
# only when it is not preceded by a word character.
_OPENING_APOSTROPHE = re.compile(r"(?<!\w)'")


def _redact_quoted_spans(text: str) -> str:
    """Replace every quoted span, closed or not, with ``<value>``.

    An exception embeds its payload inside quotes. A *closed* span is easy.
    The dangerous case is the unbalanced one — a message truncated upstream,
    or one that opens a quote around a payload and ends. We cannot know where
    such a payload stops, so an unmatched opening quote consumes the rest of
    the text. Keeping the tail would keep the payload.

    Args:
        text: Text that may contain quoted spans in any supported style.

    Returns:
        The text with every quoted span replaced.
    """
    openers: dict[str, str] = {}
    for opening, closing in _QUOTE_PAIRS:
        openers.setdefault(opening, closing)

    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        closing = openers.get(char)
        if closing is None and char == "'" and _OPENING_APOSTROPHE.match(text, index):
            closing = "'"
        if closing is None:
            out.append(char)
            index += 1
            continue
        end = text.find(closing, index + 1)
        out.append("<value>")
        if end == -1:
            # Unbalanced: the payload runs to the end of the message.
            break
        index = end + 1
    return "".join(out)


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

    # Traceback frames quote a path, so they are recognised before the quote
    # pass turns that quoted path into <value>.
    for pattern, replacement in _TRACEBACK_PATTERNS:
        result = re.sub(pattern, replacement, result, flags=re.DOTALL)

    # Quoted spans next: an unbalanced quote takes the rest of the message,
    # so nothing after it needs redacting anyway.
    result = _redact_quoted_spans(result)

    # A run of digit groups joined by slashes — "123/456/78901" — is a tax
    # or registry number, not a path. The path pattern would claim the
    # separators and leave the leading group standing, so this runs first.
    result = re.sub(r"\b\d{2,6}(?:/\d{2,6}){1,}\b", "<number>", result)

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
