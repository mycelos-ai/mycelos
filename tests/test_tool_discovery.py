"""Tests for Lazy Tool Discovery."""
from __future__ import annotations

from pathlib import Path

import pytest

from mycelos.storage.database import SQLiteStorage


def test_tool_usage_table_exists(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "d.db")
    storage.initialize()

    rows = storage.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tool_usage'"
    )
    assert len(rows) == 1

    cols = {r["name"] for r in storage.fetchall("PRAGMA table_info(tool_usage)")}
    assert cols >= {"user_id", "agent_id", "tool_name", "call_count", "last_used"}


def test_discover_tools_interception(tmp_path: Path) -> None:
    """Verify that _handle_discover_tools returns loaded status with tool names."""
    from mycelos.tools.categories import TOOL_CATEGORIES, DISCOVERABLE_CATEGORIES

    category = "knowledge_manage"
    assert category in DISCOVERABLE_CATEGORIES

    expected_tools = TOOL_CATEGORIES[category]
    assert len(expected_tools) > 0
    assert "topic_create" in expected_tools


def test_discover_unknown_category_returns_error() -> None:
    """Unknown categories are not in DISCOVERABLE_CATEGORIES."""
    from mycelos.tools.categories import DISCOVERABLE_CATEGORIES
    assert "nonexistent" not in DISCOVERABLE_CATEGORIES


def test_discover_tools_via_chat_service(tmp_path: Path) -> None:
    """Test the _handle_discover_tools method on ChatService directly."""
    from unittest.mock import MagicMock
    from mycelos.chat.service import ChatService

    app = MagicMock()
    svc = ChatService(app)

    # Valid category
    result = svc._handle_discover_tools({"category": "knowledge_manage"}, "sess-1")
    assert result["status"] == "loaded"
    assert "topic_create" in result["tools_loaded"]
    assert "knowledge_manage" in svc._session_extra_tools.get("sess-1", set())

    # Same category again → already_loaded
    result2 = svc._handle_discover_tools({"category": "knowledge_manage"}, "sess-1")
    assert result2["status"] == "already_loaded"

    # Invalid category
    result3 = svc._handle_discover_tools({"category": "nonexistent"}, "sess-1")
    assert "error" in result3

    # Different session gets fresh state
    result4 = svc._handle_discover_tools({"category": "knowledge_manage"}, "sess-2")
    assert result4["status"] == "loaded"


def test_session_extra_tools_isolation(tmp_path: Path) -> None:
    """Each session tracks its own discovered categories."""
    from unittest.mock import MagicMock
    from mycelos.chat.service import ChatService

    app = MagicMock()
    svc = ChatService(app)

    svc._handle_discover_tools({"category": "email"}, "sess-a")
    svc._handle_discover_tools({"category": "workflows"}, "sess-b")

    assert svc._session_extra_tools["sess-a"] == {"email"}
    assert svc._session_extra_tools["sess-b"] == {"workflows"}


def test_tool_usage_upsert(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "d.db")
    storage.initialize()

    storage.execute(
        "INSERT INTO tool_usage (user_id, agent_id, tool_name, call_count, last_used) "
        "VALUES (?, ?, ?, 1, '2026-04-09') "
        "ON CONFLICT (user_id, agent_id, tool_name) "
        "DO UPDATE SET call_count = call_count + 1, last_used = excluded.last_used",
        ("default", "mycelos", "note_write"),
    )
    storage.execute(
        "INSERT INTO tool_usage (user_id, agent_id, tool_name, call_count, last_used) "
        "VALUES (?, ?, ?, 1, '2026-04-09') "
        "ON CONFLICT (user_id, agent_id, tool_name) "
        "DO UPDATE SET call_count = call_count + 1, last_used = excluded.last_used",
        ("default", "mycelos", "note_write"),
    )

    row = storage.fetchone(
        "SELECT call_count FROM tool_usage WHERE user_id=? AND agent_id=? AND tool_name=?",
        ("default", "mycelos", "note_write"),
    )
    assert row["call_count"] == 2
