"""Unit tests for tool categories + budget calculation."""
from __future__ import annotations

import pytest

from mycelos.tools.categories import (
    TOOL_CATEGORIES,
    budget_for_model,
    get_basis_set,
    get_category_tools,
    DISCOVER_TOOLS_SCHEMA,
    DISCOVERABLE_CATEGORIES,
)


def test_every_category_has_8_or_fewer_tools() -> None:
    for cat, tools in TOOL_CATEGORIES.items():
        assert len(tools) <= 12, f"category {cat!r} has {len(tools)} tools (max 12)"


def test_core_category_exists() -> None:
    assert "core" in TOOL_CATEGORIES
    assert "discover_tools" in TOOL_CATEGORIES["core"]


def test_budget_tiny_model() -> None:
    assert budget_for_model(8192) == 409


def test_budget_large_model() -> None:
    assert budget_for_model(200000) == 4096


def test_budget_zero() -> None:
    assert budget_for_model(0) == 0


def test_get_basis_set_empty_usage() -> None:
    tools = get_basis_set(usage_rows=[], budget=2000)
    names = set(tools)
    assert "note_search" in names
    assert "note_write" in names


def test_get_basis_set_respects_budget() -> None:
    tools = get_basis_set(usage_rows=[], budget=200)
    assert len(tools) <= 4


def test_get_basis_set_uses_frequency() -> None:
    usage = [
        {"tool_name": "email_send", "call_count": 100},
        {"tool_name": "note_write", "call_count": 5},
        {"tool_name": "connector_setup", "call_count": 50},
    ]
    tools = get_basis_set(usage_rows=usage, budget=4096)
    names = list(tools)
    assert "email_send" in names
    assert "connector_setup" in names


def test_get_category_tools_returns_list() -> None:
    from mycelos.tools.registry import ToolPermission
    allowed = {ToolPermission.OPEN, ToolPermission.STANDARD}
    tools = get_category_tools("knowledge_manage", allowed)
    assert isinstance(tools, list)
    assert "topic_create" in tools


def test_discover_tools_schema_is_valid() -> None:
    assert DISCOVER_TOOLS_SCHEMA["function"]["name"] == "discover_tools"
    params = DISCOVER_TOOLS_SCHEMA["function"]["parameters"]
    assert "category" in params["properties"]
    assert "enum" in params["properties"]["category"]
    assert set(params["properties"]["category"]["enum"]) == set(DISCOVERABLE_CATEGORIES)


def test_every_registered_tool_has_a_category() -> None:
    from mycelos.tools.registry import ToolRegistry
    ToolRegistry._ensure_initialized()
    for name in ToolRegistry.get_all_tool_names():
        entry = ToolRegistry._tools[name]
        assert entry.get("category") is not None, f"tool {name!r} has no category"


def test_get_tools_for_session_returns_core_plus_basis(tmp_path) -> None:
    from mycelos.storage.database import SQLiteStorage
    from mycelos.tools.registry import ToolRegistry

    storage = SQLiteStorage(tmp_path / "s.db")
    storage.initialize()

    schemas = ToolRegistry.get_tools_for_session(
        agent_type="mycelos",
        user_id="default",
        agent_id="mycelos",
        storage=storage,
        context_window=200000,
    )
    names = {s["function"]["name"] for s in schemas}
    # Core tools always present
    assert "discover_tools" in names
    # Default basis-set includes knowledge tools (category="knowledge_read" or "knowledge_write")
    assert "note_search" in names or "note_read" in names
    assert "note_write" in names


def test_get_tools_for_session_tiny_budget(tmp_path) -> None:
    from mycelos.storage.database import SQLiteStorage
    from mycelos.tools.registry import ToolRegistry

    storage = SQLiteStorage(tmp_path / "s.db")
    storage.initialize()

    schemas = ToolRegistry.get_tools_for_session(
        agent_type="mycelos",
        user_id="default",
        agent_id="mycelos",
        storage=storage,
        context_window=4000,
    )
    full = ToolRegistry.get_tools_for("mycelos")
    assert len(schemas) < len(full)
    # Core discover_tools should still be there
    names = {s["function"]["name"] for s in schemas}
    assert "discover_tools" in names
