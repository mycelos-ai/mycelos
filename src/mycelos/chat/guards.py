"""Security helpers for the chat service.

Recursive credential sanitizing of tool results and validation of
memory writes proposed by the LLM. Split out of ``chat/service.py``.
"""

from __future__ import annotations

import re as _re
from typing import Any as _Any


def _sanitize_dict(sanitizer: _Any, d: dict) -> dict:
    """Recursively sanitize all string values in a dict."""
    result = {}
    for k, v in d.items():
        if isinstance(v, str):
            result[k] = sanitizer.sanitize_text(v)
        elif isinstance(v, dict):
            result[k] = _sanitize_dict(sanitizer, v)
        elif isinstance(v, list):
            result[k] = [
                _sanitize_dict(sanitizer, item) if isinstance(item, dict)
                else sanitizer.sanitize_text(item) if isinstance(item, str)
                else item
                for item in v
            ]
        else:
            result[k] = v
    return result

# H-03: Patterns that indicate prompt injection attempts in memory values
_INJECTION_PATTERNS = [
    _re.compile(r"ignore\s+(all\s+)?previous", _re.IGNORECASE),
    _re.compile(r"you\s+must\s+(always|never)", _re.IGNORECASE),
    _re.compile(r"(system|assistant)\s*:\s*", _re.IGNORECASE),
    _re.compile(r"<\s*(system|instruction|prompt)", _re.IGNORECASE),
    _re.compile(r"from\s+now\s+on", _re.IGNORECASE),
    _re.compile(r"override\s+(all|any|previous)", _re.IGNORECASE),
    _re.compile(r"forget\s+(everything|all|previous)", _re.IGNORECASE),
    _re.compile(r"new\s+instructions?\s*:", _re.IGNORECASE),
]

# Allowed categories for memory_write (LLM can only write user-scoped entries)
_ALLOWED_MEMORY_CATEGORIES = {"preference", "decision", "context", "fact"}

# Max length for memory values (prevents bloat + reduces injection surface)
_MAX_MEMORY_VALUE_LENGTH = 500


def _validate_memory_write(category: str, key: str, value: str) -> str | None:
    """Validate a memory_write request from the LLM.

    Returns an error message if invalid, None if OK.

    Security layers:
    1. Category must be in allowlist (forces user.* key prefix)
    2. Key must be alphanumeric + dots/underscores (no path traversal)
    3. Value max length enforced
    4. Value checked against injection patterns
    """
    # Layer 1: Category allowlist
    if category not in _ALLOWED_MEMORY_CATEGORIES:
        return f"Invalid category '{category}'. Allowed: {', '.join(sorted(_ALLOWED_MEMORY_CATEGORIES))}"

    # Layer 2: Key format validation (no path traversal, no dots that escape user.* prefix)
    if not _re.match(r"^[a-zA-Z0-9_][a-zA-Z0-9_.]{0,100}$", key):
        return "Invalid key format. Use alphanumeric characters, dots, and underscores."

    # Layer 3: Value length
    if len(value) > _MAX_MEMORY_VALUE_LENGTH:
        return f"Value too long ({len(value)} chars). Maximum is {_MAX_MEMORY_VALUE_LENGTH}."

    # Layer 4: Injection pattern detection
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(value):
            return "Value contains suspicious content and was blocked."

    return None

