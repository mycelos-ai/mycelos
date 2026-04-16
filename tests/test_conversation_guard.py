"""Tests for conversation validation and tool result guard."""

import json
import pytest

from mycelos.chat.tool_result_guard import ToolResultGuard, validate_tool_calls
from mycelos.chat.conversation_validator import validate_conversation


class TestToolResultGuard:
    def test_track_and_record(self):
        guard = ToolResultGuard()
        guard.track_tool_calls([
            {"id": "call_1", "function": {"name": "search_web", "arguments": "{}"}},
            {"id": "call_2", "function": {"name": "http_get", "arguments": "{}"}},
        ])
        assert guard.has_pending
        guard.record_tool_result("call_1")
        assert guard.has_pending
        guard.record_tool_result("call_2")
        assert not guard.has_pending

    def test_flush_creates_synthetic_results(self):
        guard = ToolResultGuard()
        guard.track_tool_calls([
            {"id": "call_1", "function": {"name": "search_web", "arguments": "{}"}},
        ])
        synthetic = guard.flush_pending()
        assert len(synthetic) == 1
        assert synthetic[0]["role"] == "tool"
        assert synthetic[0]["tool_call_id"] == "call_1"
        assert "error" in synthetic[0]["content"]
        assert not guard.has_pending

    def test_flush_empty_when_all_recorded(self):
        guard = ToolResultGuard()
        guard.track_tool_calls([
            {"id": "call_1", "function": {"name": "test", "arguments": "{}"}},
        ])
        guard.record_tool_result("call_1")
        assert guard.flush_pending() == []


class TestValidateToolCalls:
    def test_valid_calls_preserved(self):
        calls = [{"id": "c1", "function": {"name": "search", "arguments": "{}"}}]
        assert validate_tool_calls(calls) == calls

    def test_missing_id_dropped(self):
        calls = [{"function": {"name": "search", "arguments": "{}"}}]
        assert validate_tool_calls(calls) is None

    def test_missing_name_dropped(self):
        calls = [{"id": "c1", "function": {"arguments": "{}"}}]
        assert validate_tool_calls(calls) is None

    def test_none_input(self):
        assert validate_tool_calls(None) is None

    def test_empty_list(self):
        assert validate_tool_calls([]) is None

    def test_mixed_valid_invalid(self):
        calls = [
            {"id": "c1", "function": {"name": "good", "arguments": "{}"}},
            {"function": {"name": "bad"}},  # no id
            {"id": "c3", "function": {"name": "also_good", "arguments": "{}"}},
        ]
        result = validate_tool_calls(calls)
        assert len(result) == 2
        assert result[0]["id"] == "c1"
        assert result[1]["id"] == "c3"


class TestConversationValidator:
    def test_empty_conversation(self):
        assert validate_conversation([]) == []

    def test_normal_conversation_unchanged(self):
        conv = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        result = validate_conversation(conv)
        assert len(result) == 3

    def test_consecutive_user_messages_merged(self):
        conv = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "First"},
            {"role": "user", "content": "Second"},
        ]
        result = validate_conversation(conv)
        assert len(result) == 2  # system + merged user
        assert "First" in result[1]["content"]
        assert "Second" in result[1]["content"]

    def test_empty_assistant_gets_fallback(self):
        conv = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": ""},
        ]
        result = validate_conversation(conv)
        assert result[1]["content"] == "[No response generated]"

    def test_assistant_with_tool_calls_kept(self):
        conv = [
            {"role": "user", "content": "Search for X"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "search"}}
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "results"},
        ]
        result = validate_conversation(conv)
        # Assistant with tool_calls (even empty content) is valid
        assert result[1].get("tool_calls") is not None

    def test_orphaned_tool_result_dropped(self):
        conv = [
            {"role": "user", "content": "Hello"},
            {"role": "tool", "tool_call_id": "orphan", "content": "result"},
            {"role": "assistant", "content": "Hi"},
        ]
        result = validate_conversation(conv)
        # Orphaned tool result should be dropped
        assert not any(m.get("role") == "tool" for m in result)

    def test_dangling_tool_use_gets_synthetic_result(self):
        conv = [
            {"role": "user", "content": "Do something"},
            {"role": "assistant", "content": "Let me try", "tool_calls": [
                {"id": "c1", "function": {"name": "search"}},
                {"id": "c2", "function": {"name": "http_get"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "result1"},
            # c2 has no matching tool_result → gets synthesized
            {"role": "user", "content": "Continue"},
        ]
        result = validate_conversation(conv)
        # Both tool_calls remain, c2 gets a synthetic error result
        tool_results = [m for m in result if m.get("role") == "tool"]
        assert len(tool_results) == 2
        c2_result = [m for m in tool_results if m.get("tool_call_id") == "c2"][0]
        assert "interrupted" in c2_result["content"].lower() or "error" in c2_result["content"].lower()

    def test_system_messages_at_start(self):
        conv = [
            {"role": "user", "content": "Hello"},
            {"role": "system", "content": "Extra context"},
            {"role": "assistant", "content": "Hi"},
        ]
        result = validate_conversation(conv)
        # System messages should be at the start
        assert result[0]["role"] == "system"
