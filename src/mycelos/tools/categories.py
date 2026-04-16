"""Lazy Tool Discovery — category definitions + budget logic.

Tools are grouped into categories. At session start, only ``core`` +
a frequency-based basis-set are loaded. Additional categories can be
discovered mid-session via the ``discover_tools`` meta-tool.
"""
from __future__ import annotations

import json
from typing import Any

def _get_tool_categories() -> dict[str, list[str]]:
    """Build category map dynamically from the ToolRegistry.

    Falls back to the ``discover_tools`` virtual entry for core,
    which is handled specially in ChatService (not in the registry).
    """
    from mycelos.tools.registry import ToolRegistry
    cats = ToolRegistry.get_tools_by_category()
    # discover_tools is a virtual tool handled in ChatService,
    # not in the registry — add it to core so the enum stays correct.
    cats.setdefault("core", [])
    if "discover_tools" not in cats["core"]:
        cats["core"].append("discover_tools")
    return cats


# Lazy-initialized cache; refreshed per-process.
_CACHED_CATEGORIES: dict[str, list[str]] | None = None


def _categories() -> dict[str, list[str]]:
    global _CACHED_CATEGORIES
    if _CACHED_CATEGORIES is None:
        _CACHED_CATEGORIES = _get_tool_categories()
    return _CACHED_CATEGORIES


# Keep TOOL_CATEGORIES as a property-like access for existing callers.
# Code that reads TOOL_CATEGORIES directly will get the dynamic version
# on first access via module __getattr__.
def __getattr__(name: str) -> Any:
    if name == "TOOL_CATEGORIES":
        return _categories()
    if name == "_TOOL_TO_CATEGORY":
        cats = _categories()
        mapping: dict[str, str] = {}
        for cat, tools in cats.items():
            for t in tools:
                mapping[t] = cat
        return mapping
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

_DEFAULT_BASIS_CATEGORIES = ["knowledge_read", "knowledge_write"]

DISCOVERABLE_CATEGORIES = [
    "knowledge_manage",
    "workflows",
    "connectors",
    "system",
    "email",
]

DISCOVER_TOOLS_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "discover_tools",
        "description": (
            "Load additional tools for a specific task category. "
            "Use when the user requests an action you don't currently have a tool for. "
            "The tools become available immediately."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": DISCOVERABLE_CATEGORIES,
                    "description": "Tool category to load.",
                },
            },
            "required": ["category"],
        },
    },
}


def tool_category(tool_name: str) -> str | None:
    """Return the category a tool belongs to, or None."""
    cats = _categories()
    for cat, tools in cats.items():
        if tool_name in tools:
            return cat
    return None


def budget_for_model(context_window: int) -> int:
    """Return the tool-token budget (5% of context, capped at 4096)."""
    if context_window <= 0:
        return 0
    return min(int(context_window * 0.05), 4096)


def _estimate_tokens(schemas: list[dict]) -> int:
    """Rough token estimate for a list of tool schemas."""
    return sum(len(json.dumps(s)) // 4 for s in schemas)


def get_basis_set(
    usage_rows: list[dict],
    budget: int,
    all_schemas: dict[str, dict] | None = None,
) -> list[str]:
    """Pick tools for session start: defaults + frequently-used.

    *usage_rows* is a list of ``{"tool_name": str, "call_count": int}``
    dicts, sorted by frequency (highest first).  *budget* is the token
    budget from :func:`budget_for_model`.
    """
    cats = _categories()
    default_tools: list[str] = []
    for cat in _DEFAULT_BASIS_CATEGORIES:
        default_tools.extend(cats.get(cat, []))

    frequent: list[str] = []
    if usage_rows:
        seen = set(default_tools)
        for row in sorted(usage_rows, key=lambda r: r.get("call_count", 0), reverse=True):
            name = row["tool_name"]
            if name not in seen and name not in cats.get("core", []):
                frequent.append(name)
                seen.add(name)

    candidates = default_tools + frequent

    result: list[str] = []
    tokens_used = 0
    per_tool = 150
    for name in candidates:
        if all_schemas and name in all_schemas:
            cost = len(json.dumps(all_schemas[name])) // 4
        else:
            cost = per_tool
        if tokens_used + cost > budget:
            break
        result.append(name)
        tokens_used += cost

    return result


def get_category_tools(
    category: str,
    allowed_permissions: set,
) -> list[str]:
    """Return tool names for *category*, filtered by permission level.

    Currently returns all tools in the category; permission filtering
    will be wired up when tool registration carries permission metadata.
    """
    return _categories().get(category, [])
