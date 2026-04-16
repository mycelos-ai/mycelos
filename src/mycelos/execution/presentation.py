"""Presentation Layer — truncates and formats agent output for LLM consumption.

Different data types get different strategies:
- Plain text: head + tail
- JSON: schema-preserving (arrays shortened, deep objects collapsed)
- CSV: header + sample rows + stats
- Binary: metadata only (size, format)
- Code/tracebacks: preserve errors, strip info lines
"""

from __future__ import annotations

import json
from dataclasses import dataclass

DEFAULT_TOKEN_BUDGET = 4000


@dataclass(frozen=True)
class PresentationResult:
    """Result of presenting agent output."""

    content: str
    overflow_path: str | None = None
    was_truncated: bool = False
    original_size: int = 0
    content_type: str = "text"


class PresentationLayer:
    """Formats raw execution output for LLM context windows.

    Each content type has a dedicated truncation strategy that preserves
    the most useful information within the token budget.

    Args:
        token_budget: Approximate token limit. Characters are estimated
            at 4 chars per token.
    """

    def __init__(self, token_budget: int = DEFAULT_TOKEN_BUDGET) -> None:
        self.token_budget = token_budget

    def present(
        self,
        content: str | bytes,
        content_type: str = "auto",
    ) -> PresentationResult:
        """Format content for LLM consumption within token budget.

        Args:
            content: Raw output from execution (text or bytes).
            content_type: One of "auto", "text", "json", "csv",
                "traceback", or "binary". When "auto", the type
                is detected from the content.

        Returns:
            PresentationResult with formatted content and metadata.
        """
        if isinstance(content, bytes):
            return self._present_binary(content)

        if content_type == "auto":
            content_type = self._detect_type(content)

        original_size = len(content)

        if content_type == "json":
            return self._present_json(content, original_size)
        elif content_type == "csv":
            return self._present_csv(content, original_size)
        elif content_type == "traceback":
            return self._present_traceback(content, original_size)
        else:
            return self._present_text(content, original_size)

    # ------------------------------------------------------------------
    # Type detection
    # ------------------------------------------------------------------

    def _detect_type(self, content: str) -> str:
        """Auto-detect content type from the raw string."""
        stripped = content.strip()

        if stripped.startswith(("{", "[")):
            try:
                json.loads(stripped)
                return "json"
            except (json.JSONDecodeError, ValueError):
                pass

        if "Traceback (most recent call last)" in content:
            return "traceback"

        lines = content.split("\n")
        if len(lines) >= 3 and "," in lines[0]:
            return "csv"

        return "text"

    # ------------------------------------------------------------------
    # Strategy: plain text — head + tail
    # ------------------------------------------------------------------

    def _present_text(
        self, content: str, original_size: int
    ) -> PresentationResult:
        char_budget = self.token_budget * 4
        if len(content) <= char_budget:
            return PresentationResult(
                content=content,
                original_size=original_size,
                content_type="text",
            )

        head_chars = int(char_budget * 0.8)
        tail_chars = int(char_budget * 0.2)
        head = content[:head_chars]
        tail = content[-tail_chars:]
        omitted = len(content) - head_chars - tail_chars
        truncated = (
            f"{head}\n\n"
            f"--- [{omitted} characters omitted] ---\n\n"
            f"{tail}"
        )
        return PresentationResult(
            content=truncated,
            was_truncated=True,
            original_size=original_size,
            content_type="text",
        )

    # ------------------------------------------------------------------
    # Strategy: JSON — schema-preserving truncation
    # ------------------------------------------------------------------

    def _present_json(
        self, content: str, original_size: int
    ) -> PresentationResult:
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return self._present_text(content, original_size)

        truncated_data = self._truncate_json_value(data, depth=0, max_depth=3)
        was_truncated = truncated_data != data
        result = json.dumps(truncated_data, indent=2, ensure_ascii=False)

        if len(result) > self.token_budget * 4:
            result = result[: self.token_budget * 4] + "\n... [JSON truncated]"
            was_truncated = True

        return PresentationResult(
            content=result,
            was_truncated=was_truncated,
            original_size=original_size,
            content_type="json",
        )

    def _truncate_json_value(
        self, value: object, depth: int, max_depth: int
    ) -> object:
        """Recursively truncate a JSON value, collapsing beyond max_depth."""
        if depth > max_depth:
            if isinstance(value, dict):
                return {"...": f"{len(value)} keys"}
            elif isinstance(value, list):
                return [f"... ({len(value)} items)"]
            elif isinstance(value, str) and len(value) > 200:
                return value[:200] + "... [truncated]"
            return value

        if isinstance(value, dict):
            return {
                k: self._truncate_json_value(v, depth + 1, max_depth)
                for k, v in value.items()
            }
        elif isinstance(value, list):
            if len(value) <= 5:
                return [
                    self._truncate_json_value(v, depth + 1, max_depth)
                    for v in value
                ]
            head = [
                self._truncate_json_value(v, depth + 1, max_depth)
                for v in value[:3]
            ]
            tail = [
                self._truncate_json_value(v, depth + 1, max_depth)
                for v in value[-2:]
            ]
            return head + [f"... ({len(value) - 5} more items)"] + tail
        elif isinstance(value, str) and len(value) > 200:
            return value[:200] + "... [truncated]"
        return value

    # ------------------------------------------------------------------
    # Strategy: CSV — header + sample rows + stats
    # ------------------------------------------------------------------

    def _present_csv(
        self, content: str, original_size: int
    ) -> PresentationResult:
        lines = content.strip().split("\n")
        if len(lines) <= 15:
            return PresentationResult(
                content=content,
                original_size=original_size,
                content_type="csv",
            )

        header = lines[0]
        first_rows = "\n".join(lines[1:11])
        last_rows = "\n".join(lines[-5:])
        col_count = len(header.split(","))
        omitted_count = len(lines) - 16
        truncated = (
            f"{header}\n{first_rows}\n\n"
            f"--- [{omitted_count} rows omitted, "
            f"{len(lines)} total, {col_count} columns] ---\n\n"
            f"{last_rows}"
        )
        return PresentationResult(
            content=truncated,
            was_truncated=True,
            original_size=original_size,
            content_type="csv",
        )

    # ------------------------------------------------------------------
    # Strategy: tracebacks — preserve the error, trim middle frames
    # ------------------------------------------------------------------

    def _present_traceback(
        self, content: str, original_size: int
    ) -> PresentationResult:
        char_budget = self.token_budget * 4
        if len(content) <= char_budget:
            return PresentationResult(
                content=content,
                original_size=original_size,
                content_type="traceback",
            )

        return PresentationResult(
            content=content[-char_budget:],
            was_truncated=True,
            original_size=original_size,
            content_type="traceback",
        )

    # ------------------------------------------------------------------
    # Strategy: binary — metadata only
    # ------------------------------------------------------------------

    def _present_binary(self, content: bytes) -> PresentationResult:
        return PresentationResult(
            content=f"[Binary data: {len(content)} bytes]",
            was_truncated=True,
            original_size=len(content),
            content_type="binary",
        )
