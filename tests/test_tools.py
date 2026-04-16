"""Tests for Tool Registry."""

import pytest

from mycelos.execution.tools import ToolRegistry, ToolDefinition


def test_register_and_call() -> None:
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="math.add",
        description="Add two numbers",
        handler=lambda a, b: a + b,
        required_capability="math",
    ))
    result = registry.call("math.add", {"a": 2, "b": 3})
    assert result == 5


def test_call_unknown_tool() -> None:
    registry = ToolRegistry()
    with pytest.raises(KeyError, match="not found"):
        registry.call("nonexistent", {})


def test_list_tools() -> None:
    registry = ToolRegistry()
    registry.register(ToolDefinition(name="a", description="Tool A", handler=lambda: None, required_capability="cap.a"))
    registry.register(ToolDefinition(name="b", description="Tool B", handler=lambda: None, required_capability="cap.b"))
    tools = registry.list_tools()
    assert len(tools) == 2
    names = {t["name"] for t in tools}
    assert names == {"a", "b"}


def test_get_tool() -> None:
    registry = ToolRegistry()
    registry.register(ToolDefinition(name="x", description="X", handler=lambda: 42, required_capability="cap"))
    tool = registry.get("x")
    assert tool is not None
    assert tool.name == "x"
    assert registry.get("nonexistent") is None
