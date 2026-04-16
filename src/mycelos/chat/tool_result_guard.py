"""Tool Result Guard — ensures every tool_use has a matching tool_result.

The Anthropic API requires that every assistant message containing tool_use
blocks is immediately followed by tool_result messages. If a tool execution
is interrupted (error, timeout, permission denied), synthetic error results
are injected to maintain conversation consistency.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("mycelos.chat")


class ToolResultGuard:
    """Tracks pending tool calls and synthesizes missing results."""

    def __init__(self):
        self.pending_calls: dict[str, str] = {}  # tool_call_id → tool_name

    def track_tool_calls(self, tool_calls: list[dict]) -> None:
        """Mark tool calls as expecting results."""
        for tc in tool_calls:
            tc_id = tc.get("id") or ""
            tc_name = (tc.get("function") or {}).get("name", "unknown")
            if tc_id:
                self.pending_calls[tc_id] = tc_name

    def record_tool_result(self, tool_call_id: str) -> None:
        """Mark a tool result as received."""
        self.pending_calls.pop(tool_call_id, None)

    @property
    def has_pending(self) -> bool:
        return len(self.pending_calls) > 0

    def flush_pending(self) -> list[dict]:
        """Create synthetic tool results for any unanswered tool calls.

        Returns list of {"role": "tool", "tool_call_id": ..., "content": ...} dicts
        to append to the conversation.
        """
        synthetic = []
        for tool_call_id, tool_name in self.pending_calls.items():
            synthetic.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps({
                    "error": f"Tool '{tool_name}' did not return a result. "
                             "It may have failed or been interrupted."
                }),
            })
            logger.debug("Synthesized missing tool result for %s (%s)", tool_name, tool_call_id[:8])
        self.pending_calls.clear()
        return synthetic


def validate_tool_calls(tool_calls: list[dict] | None) -> list[dict] | None:
    """Validate and clean tool calls from an LLM response.

    Drops tool calls that are missing required fields (id, function.name).
    Returns None if no valid calls remain.
    """
    if not tool_calls:
        return None

    valid = []
    for tc in tool_calls:
        tc_id = tc.get("id")
        func = tc.get("function") or {}
        name = func.get("name")
        if tc_id and name:
            valid.append(tc)
        else:
            logger.warning("Dropped malformed tool call: %s", tc)

    return valid if valid else None
