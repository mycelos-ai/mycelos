"""Tests for conversation validator — tool_use/tool_result pairing repair."""

from mycelos.chat.conversation_validator import validate_conversation


def test_valid_conversation_unchanged():
    """A valid conversation passes through unchanged."""
    conv = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    result = validate_conversation(conv)
    assert len(result) == 3
    assert result[0]["role"] == "system"
    assert result[2]["content"] == "Hi there!"


def test_tool_use_with_result_passes():
    """Matched tool_use + tool_result pair passes through."""
    conv = [
        {"role": "user", "content": "Search for news"},
        {"role": "assistant", "tool_calls": [
            {"id": "tc_1", "function": {"name": "search_web", "arguments": "{}"}}
        ], "content": ""},
        {"role": "tool", "tool_call_id": "tc_1", "content": "Results here"},
        {"role": "assistant", "content": "I found some results."},
    ]
    result = validate_conversation(conv)
    assert len(result) == 4
    assert result[1]["tool_calls"][0]["id"] == "tc_1"
    assert result[2]["role"] == "tool"


def test_orphaned_tool_use_gets_synthetic_result():
    """tool_use without tool_result → synthetic error result injected."""
    conv = [
        {"role": "user", "content": "List files"},
        {"role": "assistant", "tool_calls": [
            {"id": "tc_orphan", "function": {"name": "filesystem_list", "arguments": "{}"}}
        ], "content": "Let me check."},
        # NO tool_result follows!
        {"role": "user", "content": "I granted permission"},
    ]
    result = validate_conversation(conv)

    # Should have: user, assistant(tool_calls), tool(synthetic), user
    assert len(result) == 4
    assert result[1]["tool_calls"][0]["id"] == "tc_orphan"
    assert result[2]["role"] == "tool"
    assert result[2]["tool_call_id"] == "tc_orphan"
    assert "interrupted" in result[2]["content"].lower() or "error" in result[2]["content"].lower()
    assert result[3]["role"] == "user"


def test_multiple_tool_calls_partial_results():
    """Two tool_calls, only one has a result → synthetic result for the other."""
    conv = [
        {"role": "user", "content": "Do both"},
        {"role": "assistant", "tool_calls": [
            {"id": "tc_a", "function": {"name": "search_web", "arguments": "{}"}},
            {"id": "tc_b", "function": {"name": "filesystem_list", "arguments": "{}"}},
        ], "content": ""},
        {"role": "tool", "tool_call_id": "tc_a", "content": "Search results"},
        # tc_b has NO result
        {"role": "assistant", "content": "Done."},
    ]
    result = validate_conversation(conv)

    # Find tool results
    tool_results = [m for m in result if m.get("role") == "tool"]
    assert len(tool_results) == 2
    ids = {tr["tool_call_id"] for tr in tool_results}
    assert "tc_a" in ids
    assert "tc_b" in ids

    # tc_b should have synthetic error
    tc_b_result = next(tr for tr in tool_results if tr["tool_call_id"] == "tc_b")
    assert "interrupted" in tc_b_result["content"].lower() or "error" in tc_b_result["content"].lower()


def test_duplicate_tool_result_dropped():
    """Duplicate tool_result for same ID → second one dropped."""
    conv = [
        {"role": "user", "content": "Search"},
        {"role": "assistant", "tool_calls": [
            {"id": "tc_dup", "function": {"name": "search_web", "arguments": "{}"}}
        ], "content": ""},
        {"role": "tool", "tool_call_id": "tc_dup", "content": "First result"},
        {"role": "tool", "tool_call_id": "tc_dup", "content": "Duplicate result"},
    ]
    result = validate_conversation(conv)
    tool_results = [m for m in result if m.get("role") == "tool"]
    assert len(tool_results) == 1
    assert tool_results[0]["content"] == "First result"


def test_orphaned_tool_result_dropped():
    """tool_result without any matching tool_use → dropped."""
    conv = [
        {"role": "user", "content": "Hello"},
        {"role": "tool", "tool_call_id": "tc_ghost", "content": "I don't belong here"},
        {"role": "assistant", "content": "Hi!"},
    ]
    result = validate_conversation(conv)
    assert len(result) == 2
    assert result[0]["role"] == "user"
    assert result[1]["role"] == "assistant"


def test_empty_assistant_gets_fallback():
    """Empty assistant message → gets fallback content."""
    conv = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": ""},
    ]
    result = validate_conversation(conv)
    assert result[1]["content"] == "[No response generated]"


def test_consecutive_user_messages_merged():
    """Two consecutive user messages → merged."""
    conv = [
        {"role": "user", "content": "Part 1"},
        {"role": "user", "content": "Part 2"},
        {"role": "assistant", "content": "Got it."},
    ]
    result = validate_conversation(conv)
    assert len(result) == 2
    assert "Part 1" in result[0]["content"]
    assert "Part 2" in result[0]["content"]


def test_permission_flow_recovery():
    """Simulates the exact permission flow that causes the Anthropic error.

    1. User asks to list files
    2. LLM calls filesystem_list
    3. Permission flow interrupts — no tool_result
    4. User grants permission
    5. Validator must synthesize the missing tool_result
    """
    conv = [
        {"role": "system", "content": "You are Mycelos."},
        {"role": "user", "content": "List my documents"},
        {"role": "assistant", "tool_calls": [
            {"id": "toolu_017NC7m8eTZqF3FsybctPK9k",
             "function": {"name": "filesystem_list",
                         "arguments": '{"path": "/Users/stefan/Documents"}'}}
        ], "content": "Let me check your documents."},
        # Permission flow interrupted here — no tool_result!
        {"role": "user", "content": "2"},  # User chose "allow always"
    ]
    result = validate_conversation(conv)

    # Must have: system, user, assistant(tool_calls), tool(synthetic), user
    assert len(result) == 5
    assert result[0]["role"] == "system"
    assert result[1]["role"] == "user"
    assert result[2]["role"] == "assistant"
    assert result[2]["tool_calls"][0]["id"] == "toolu_017NC7m8eTZqF3FsybctPK9k"
    assert result[3]["role"] == "tool"
    assert result[3]["tool_call_id"] == "toolu_017NC7m8eTZqF3FsybctPK9k"
    assert result[4]["role"] == "user"

    # The synthetic tool_result should explain the interruption
    assert "interrupted" in result[3]["content"].lower() or "error" in result[3]["content"].lower()


def test_system_messages_stay_at_start():
    """System messages are always placed at the beginning."""
    conv = [
        {"role": "user", "content": "Hello"},
        {"role": "system", "content": "Late system message"},
        {"role": "assistant", "content": "Hi!"},
    ]
    result = validate_conversation(conv)
    assert result[0]["role"] == "system"
