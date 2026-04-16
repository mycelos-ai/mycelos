"""Tool Registry — registers and dispatches tool calls."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger("mycelos.tools")


@dataclass
class ToolDefinition:
    name: str
    description: str
    handler: Callable[..., Any]
    required_capability: str


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            logger.warning("Tool '%s' already registered — overwriting", tool.name)
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_tools(self) -> list[dict[str, str]]:
        return [
            {"name": t.name, "description": t.description, "capability": t.required_capability}
            for t in self._tools.values()
        ]

    def call(self, name: str, args: dict[str, Any]) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Tool '{name}' not found. Available: {list(self._tools.keys())}")
        return tool.handler(**args)
