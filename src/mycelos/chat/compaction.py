"""Conversation Compaction — summarize old messages to free context space.

Inspired by Claude Code's auto-compact system:
- Triggers at ~80% of context window usage
- Summarizes messages above the compact boundary via a cheap model (Haiku)
- Preserves the system prompt and recent messages
- Circuit breaker after 3 consecutive failures
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("mycelos.chat.compaction")

# Approximate token limit for common models
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-haiku-4-5": 200_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
}
DEFAULT_CONTEXT_WINDOW = 200_000

# Trigger compaction when usage exceeds this fraction
COMPACT_THRESHOLD = 0.75

# Reserve tokens for the response
RESPONSE_RESERVE = 8_000

# Keep at least this many recent messages after compaction
MIN_RECENT_MESSAGES = 6

# Circuit breaker: stop trying after this many consecutive failures
MAX_COMPACT_FAILURES = 3

# Compaction summary prompt
COMPACT_PROMPT = """\
Summarize the following conversation history concisely.
Keep all important facts, decisions, tool results, and user preferences.
Omit pleasantries and redundant back-and-forth.
Output a single paragraph or short bullet list — max 500 words."""


def estimate_tokens(messages: list[dict]) -> int:
    """Estimate token count for a message list (~4 chars per token)."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += len(json.dumps(block)) // 4
        # Tool calls add ~100 tokens overhead each
        if msg.get("tool_calls"):
            total += len(msg["tool_calls"]) * 100
    return total


def needs_compaction(messages: list[dict], model: str = "") -> bool:
    """Check if a conversation needs compaction based on token usage."""
    window = MODEL_CONTEXT_WINDOWS.get(model.split("/")[-1] if "/" in model else model, DEFAULT_CONTEXT_WINDOW)
    threshold = int(window * COMPACT_THRESHOLD) - RESPONSE_RESERVE
    current = estimate_tokens(messages)
    return current > threshold


def compact_conversation(
    messages: list[dict],
    llm: Any,
    model: str = "",
    summary_model: str | None = None,
) -> list[dict]:
    """Compact a conversation by summarizing old messages.

    Keeps: system prompt (index 0) + recent messages.
    Summarizes: everything in between.

    Args:
        summary_model: optional model override. When None, the caller's LLM
            broker picks a default — but prefer passing the registry's
            cheapest model so this respects user configuration.

    Returns the compacted message list, or the original if compaction fails.
    """
    if len(messages) <= MIN_RECENT_MESSAGES + 2:
        return messages  # Too short to compact

    # Split: system prompt | old messages | recent messages
    system_msg = messages[0] if messages and messages[0].get("role") == "system" else None
    start = 1 if system_msg else 0

    # Find the compact boundary — keep the last MIN_RECENT_MESSAGES
    boundary = max(start, len(messages) - MIN_RECENT_MESSAGES)

    old_messages = messages[start:boundary]
    recent_messages = messages[boundary:]

    if not old_messages:
        return messages  # Nothing to compact

    old_tokens = estimate_tokens(old_messages)
    if old_tokens < 2000:
        return messages  # Not worth compacting

    logger.info(
        "Compacting conversation: %d messages (%d old + %d recent), ~%d tokens in old section",
        len(messages), len(old_messages), len(recent_messages), old_tokens,
    )

    # Build the summary request
    old_text = _format_messages_for_summary(old_messages)

    try:
        response = llm.complete(
            [
                {"role": "system", "content": COMPACT_PROMPT},
                {"role": "user", "content": old_text},
            ],
            model=summary_model,
        )
        summary = response.content.strip()
    except Exception as e:
        logger.warning("Compaction failed: %s", e)
        return messages  # Return original on failure

    if not summary:
        return messages

    # Build compacted conversation
    compacted: list[dict] = []
    if system_msg:
        compacted.append(system_msg)

    # Insert compact boundary marker
    compacted.append({
        "role": "user",
        "content": "[Earlier conversation summarized]",
    })
    compacted.append({
        "role": "assistant",
        "content": f"**Conversation summary:**\n{summary}",
    })

    # Add recent messages
    compacted.extend(recent_messages)

    new_tokens = estimate_tokens(compacted)
    saved = old_tokens - estimate_tokens(compacted[1:3])  # tokens saved
    logger.info(
        "Compaction complete: %d → %d messages, ~%d tokens saved",
        len(messages), len(compacted), saved,
    )

    return compacted


def _format_messages_for_summary(messages: list[dict]) -> str:
    """Format messages into readable text for the summarization prompt."""
    lines = []
    for msg in messages:
        role = msg.get("role", "unknown").upper()
        content = msg.get("content", "")

        if isinstance(content, str) and content:
            # Truncate very long messages
            if len(content) > 1000:
                content = content[:1000] + "...[truncated]"
            lines.append(f"[{role}]: {content}")
        elif msg.get("tool_calls"):
            names = [tc.get("function", {}).get("name", "?") for tc in msg["tool_calls"]]
            lines.append(f"[{role}]: Called tools: {', '.join(names)}")
        elif role == "TOOL":
            tool_id = msg.get("tool_call_id", "")
            if isinstance(content, str) and len(content) > 500:
                content = content[:500] + "...[truncated]"
            lines.append(f"[TOOL RESULT {tool_id}]: {content}")

    return "\n".join(lines)
