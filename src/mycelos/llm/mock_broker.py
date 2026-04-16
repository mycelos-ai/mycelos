"""MockLLMBroker — deterministic LLM broker for testing.

Registers pattern -> response pairs. Matches the last user message
against patterns. Returns deterministic responses without API calls.
"""

from __future__ import annotations

import re

from mycelos.llm.broker import LLMResponse


class MockLLMBroker:
    """Deterministic LLM broker for testing agent behavior."""

    def __init__(self) -> None:
        self._responses: list[tuple[re.Pattern[str], LLMResponse]] = []
        self._default = LLMResponse(
            content="I don't have enough context to answer.",
            total_tokens=10,
            model="mock",
        )
        self.call_log: list[dict[str, object]] = []

    def on_message(
        self,
        pattern: str,
        response: str,
        tool_calls: list[dict[str, object]] | None = None,
    ) -> MockLLMBroker:
        """Register: when user message matches pattern, return response."""
        self._responses.append((
            re.compile(pattern, re.IGNORECASE),
            LLMResponse(
                content=response,
                total_tokens=50,
                model="mock",
                tool_calls=tool_calls,
            ),
        ))
        return self

    def complete(
        self,
        messages: list[dict[str, object]],
        model: str | None = None,
        tools: list[dict[str, object]] | None = None,
        stream: bool = False,
    ) -> LLMResponse:
        """Match last user message against patterns, return response."""
        self.call_log.append({
            "messages": messages,
            "model": model,
            "tools": tools,
        })
        last_user = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        for pattern, response in self._responses:
            if pattern.search(str(last_user)):
                return response
        return self._default

    def count_tokens(
        self,
        messages: list[dict[str, object]],
        model: str | None = None,
    ) -> int:
        """Approximate token count (1 token ~ 4 chars)."""
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        return max(1, total_chars // 4)
