"""Conversation Validator — ensures LLM API message format requirements.

Anthropic API rules:
1. Roles must alternate: user → assistant → user → assistant
2. Every tool_use block must have a matching tool_result IMMEDIATELY after
3. No empty assistant messages (must have content or tool_calls)
4. System messages only at the start

Inspired by OpenClaw's session-transcript-repair.ts — synthesizes missing
tool_result blocks instead of just stripping orphaned tool_calls.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("mycelos.chat")


def validate_conversation(conversation: list[dict]) -> list[dict]:
    """Validate and repair a conversation for Anthropic API compliance.

    Fixes:
    - Synthesizes missing tool_result blocks for orphaned tool_use calls
    - Ensures tool_results immediately follow their tool_use assistant message
    - Removes orphaned tool_result messages (no preceding tool_use)
    - Removes duplicate tool_results for the same call ID
    - Adds fallback content to empty assistant messages
    - Merges consecutive messages with the same role
    - Ensures system messages are only at the start

    Args:
        conversation: List of message dicts with 'role' and 'content'.

    Returns:
        Cleaned conversation list. Never modifies the input list.
    """
    if not conversation:
        return []

    # Phase 1: Separate system messages
    system_msgs: list[dict] = []
    other_msgs: list[dict] = []
    for msg in conversation:
        if msg.get("role") == "system":
            system_msgs.append(msg)
        else:
            other_msgs.append(msg)

    # Phase 2: Repair tool_use / tool_result pairing
    repaired = _repair_tool_pairing(other_msgs)

    # Phase 3: Ensure assistant messages have content
    for msg in repaired:
        if msg.get("role") == "assistant":
            if not msg.get("content") and not msg.get("tool_calls"):
                msg["content"] = "[No response generated]"

    # Phase 4: Merge consecutive same-role messages (except tool)
    merged = _merge_consecutive(repaired)

    return system_msgs + merged


def _repair_tool_pairing(messages: list[dict]) -> list[dict]:
    """Ensure every tool_use has a matching tool_result immediately after.

    Strategy (inspired by OpenClaw):
    1. Walk through messages sequentially
    2. When we see an assistant message with tool_calls, collect all call IDs
    3. Consume the following tool_result messages that match
    4. For any unmatched call IDs, synthesize a tool_result with an error
    5. Drop orphaned tool_results (no matching tool_use)
    """
    result: list[dict] = []
    # Track all tool_use IDs we've seen
    all_use_ids: set[str] = set()
    # Track all tool_result IDs we've seen (for duplicate detection)
    seen_result_ids: set[str] = set()

    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role", "")

        if role == "assistant" and msg.get("tool_calls"):
            # Append the assistant message (copy)
            msg_copy = dict(msg)
            result.append(msg_copy)

            # Collect expected tool_call IDs
            expected_ids: dict[str, str] = {}  # id → tool name
            for tc in (msg_copy.get("tool_calls") or []):
                tc_id = tc.get("id", "")
                tc_name = tc.get("function", {}).get("name", "unknown")
                if tc_id:
                    expected_ids[tc_id] = tc_name
                    all_use_ids.add(tc_id)

            # Consume following tool_result messages that match
            matched_ids: set[str] = set()
            j = i + 1
            while j < len(messages):
                next_msg = messages[j]
                if next_msg.get("role") != "tool":
                    break
                tc_id = next_msg.get("tool_call_id", "")
                if tc_id in expected_ids and tc_id not in seen_result_ids:
                    result.append(dict(next_msg))
                    matched_ids.add(tc_id)
                    seen_result_ids.add(tc_id)
                elif tc_id in seen_result_ids:
                    # Duplicate — drop it
                    logger.debug("Dropping duplicate tool_result: %s", tc_id[:12])
                else:
                    # Orphaned tool_result — keep for now, will be cleaned later
                    result.append(dict(next_msg))
                j += 1

            # Synthesize missing tool_results
            for tc_id, tc_name in expected_ids.items():
                if tc_id not in matched_ids:
                    logger.warning(
                        "Synthesizing missing tool_result for %s (%s)",
                        tc_id[:12], tc_name,
                    )
                    result.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": json.dumps({
                            "error": f"Tool '{tc_name}' was interrupted (permission flow or error). "
                                     "The result is not available. Continue with what you know.",
                        }),
                    })
                    seen_result_ids.add(tc_id)

            i = j  # Skip past consumed tool_results
            continue

        elif role == "tool":
            tc_id = msg.get("tool_call_id", "")
            if tc_id not in all_use_ids:
                # Orphaned tool_result — no matching tool_use seen
                logger.debug("Dropping orphaned tool_result: %s", tc_id[:12])
                i += 1
                continue
            if tc_id in seen_result_ids:
                # Duplicate
                logger.debug("Dropping duplicate tool_result: %s", tc_id[:12])
                i += 1
                continue
            result.append(dict(msg))
            seen_result_ids.add(tc_id)
            i += 1
            continue

        else:
            result.append(dict(msg))
            i += 1

    return result


def _merge_consecutive(messages: list[dict]) -> list[dict]:
    """Merge consecutive messages with the same role (except tool)."""
    merged: list[dict] = []
    for msg in messages:
        role = msg.get("role", "")

        if role == "tool":
            merged.append(msg)
            continue

        if merged and merged[-1].get("role") == role and role in ("user", "assistant"):
            prev = merged[-1]
            prev_content = prev.get("content") or ""
            curr_content = msg.get("content") or ""
            if prev_content and curr_content:
                prev["content"] = prev_content + "\n\n" + curr_content
            elif curr_content:
                prev["content"] = curr_content
            if role == "assistant":
                prev_tc = prev.get("tool_calls") or []
                curr_tc = msg.get("tool_calls") or []
                if curr_tc:
                    prev["tool_calls"] = prev_tc + curr_tc
            logger.debug("Merged consecutive %s messages", role)
            continue

        merged.append(msg)

    return merged
