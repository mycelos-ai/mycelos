"""ToolRegistry — central registry for all Mycelos tools.

Each tool has a schema (OpenAI function calling format), an execute function,
and a permission level that controls which agents can use it.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger("mycelos.tools.registry")


class ToolPermission(Enum):
    """Permission levels for tool access.

    OPEN: All agents (read-only info tools).
    STANDARD: Mycelos + Builder (search, notes, web).
    PRIVILEGED: Mycelos always, Builder per-request (filesystem, connectors).
    BUILDER: Builder only (create_workflow, create_schedule, create_agent).
    SYSTEM: Internal tools (handoff) — available to all agents.
    """

    OPEN = "open"
    STANDARD = "standard"
    PRIVILEGED = "privileged"
    BUILDER = "builder"
    SYSTEM = "system"


# Which permissions each agent type can access
_AGENT_PERMISSIONS: dict[str, set[ToolPermission]] = {
    "mycelos": {
        ToolPermission.OPEN,
        ToolPermission.STANDARD,
        ToolPermission.PRIVILEGED,
        ToolPermission.BUILDER,
        ToolPermission.SYSTEM,
    },
    "builder": {
        ToolPermission.OPEN,
        ToolPermission.STANDARD,
        ToolPermission.PRIVILEGED,
        ToolPermission.BUILDER,
        ToolPermission.SYSTEM,
    },
    "custom": {
        ToolPermission.OPEN,
        ToolPermission.SYSTEM,
    },
}


class ToolRegistry:
    """Central registry for all Mycelos tools.

    Tools are registered via module-level register() functions called
    during initialization. The registry is a class-level singleton.
    Thread-safe: initialization guarded by lock.
    """

    _tools: dict[str, dict[str, Any]] = {}
    _initialized: bool = False
    _lock = __import__("threading").Lock()

    @classmethod
    def register(
        cls,
        name: str,
        schema: dict,
        execute_fn: Callable[[dict, dict], Any],
        permission: ToolPermission = ToolPermission.STANDARD,
        concurrent_safe: bool = False,
        category: str | None = None,
    ) -> None:
        """Register a tool with its schema, execute function, and permission level.

        concurrent_safe: If True, this tool can run in parallel with other
        concurrent-safe tools (e.g., read-only lookups). Write tools should
        leave this False (the default).

        category: Lazy-discovery category (e.g., "core", "knowledge_read").
        Used by the tool-budget system to decide which tools to send per turn.
        """
        cls._tools[name] = {
            "schema": schema,
            "execute": execute_fn,
            "permission": permission,
            "concurrent_safe": concurrent_safe,
            "category": category,
        }

    @classmethod
    def is_concurrent_safe(cls, name: str) -> bool:
        """Check if a tool can safely run in parallel with others."""
        cls._ensure_initialized()
        entry = cls._tools.get(name)
        return entry.get("concurrent_safe", False) if entry else False

    @classmethod
    def get_tools_for(cls, agent_type: str) -> list[dict]:
        """Get tool schemas for an agent type: 'mycelos', 'builder', 'custom'.

        Returns a list of OpenAI function calling format tool definitions
        filtered by the agent's permission level.
        """
        cls._ensure_initialized()
        allowed = _AGENT_PERMISSIONS.get(agent_type, {ToolPermission.OPEN})
        return [
            entry["schema"]
            for entry in cls._tools.values()
            if entry["permission"] in allowed
        ]

    @classmethod
    def execute(cls, name: str, args: dict, context: dict) -> Any:
        """Execute a tool by name.

        The context dict must contain:
            app: App instance
            user_id: current user ID
            session_id: current session ID
            agent_id: which agent is calling

        Permission checks are done at this level based on the calling agent.
        The outer _execute_tool() in ChatService handles policy checks and
        response sanitization.
        """
        cls._ensure_initialized()
        entry = cls._tools.get(name)
        if not entry:
            return {"error": f"Unknown tool: {name}"}

        agent_id = context.get("agent_id", "mycelos")
        agent_type = _resolve_agent_type(agent_id)
        allowed = _AGENT_PERMISSIONS.get(agent_type, {ToolPermission.OPEN})

        if entry["permission"] not in allowed:
            logger.warning(
                "Agent %s (%s) denied access to tool %s (requires %s)",
                agent_id, agent_type, name, entry["permission"].value,
            )
            return {"error": f"Tool '{name}' is not available for agent '{agent_id}'."}

        result = entry["execute"](args, context)

        # Track usage for lazy-discovery basis-set adaptation
        app = context.get("app")
        if app and hasattr(app, "storage"):
            cls.track_usage(
                user_id=context.get("user_id", "default"),
                agent_id=context.get("agent_id", "mycelos"),
                tool_name=name,
                storage=app.storage,
            )

        return result

    @classmethod
    def track_usage(cls, user_id: str, agent_id: str, tool_name: str, storage: Any) -> None:
        """Increment the usage counter for a tool. Non-blocking, best-effort."""
        try:
            from datetime import datetime, timezone
            now = datetime.now(tz=timezone.utc).isoformat()
            storage.execute(
                "INSERT INTO tool_usage (user_id, agent_id, tool_name, call_count, last_used) "
                "VALUES (?, ?, ?, 1, ?) "
                "ON CONFLICT (user_id, agent_id, tool_name) "
                "DO UPDATE SET call_count = call_count + 1, last_used = excluded.last_used",
                (user_id, agent_id, tool_name, now),
            )
        except Exception:
            pass  # Never block on tracking failure

    @classmethod
    def get_schema(cls, name: str) -> dict | None:
        """Get the JSON schema for a tool, or None if not found."""
        cls._ensure_initialized()
        entry = cls._tools.get(name)
        return entry["schema"] if entry else None

    @classmethod
    def get_all_tool_names(cls) -> list[str]:
        """Return all registered tool names."""
        cls._ensure_initialized()
        return list(cls._tools.keys())

    @classmethod
    def get_tools_by_category(cls) -> dict[str, list[str]]:
        """Return tool names grouped by their registered category.

        Built dynamically from the registry — always in sync with
        actual registrations, unlike the static TOOL_CATEGORIES dict.
        """
        cls._ensure_initialized()
        categories: dict[str, list[str]] = {}
        for name, entry in cls._tools.items():
            cat = entry.get("category") or "uncategorized"
            categories.setdefault(cat, []).append(name)
        return categories

    @classmethod
    def _ensure_initialized(cls) -> None:
        """Lazily initialize the registry by loading all tool modules."""
        if cls._initialized:
            return
        with cls._lock:
            if cls._initialized:  # double-check after acquiring lock
                return
            cls._initialized = True

        from mycelos.tools import agent as _agent
        from mycelos.tools import connector as _connector
        from mycelos.tools import filesystem as _filesystem
        from mycelos.tools import knowledge as _knowledge
        from mycelos.tools import memory as _memory
        from mycelos.tools import system as _system
        from mycelos.tools import web as _web
        from mycelos.tools import workflow as _workflow
        from mycelos.tools import session as _session
        from mycelos.tools import ui_widgets as _ui_widgets

        _web.register(cls)
        _memory.register(cls)
        _filesystem.register(cls)
        _knowledge.register(cls)
        _workflow.register(cls)
        _connector.register(cls)
        _system.register(cls)
        _agent.register(cls)
        # email_* in-process tools removed — replaced by the
        # @n24q02m/better-email-mcp MCP server, registered as a recipe.
        _session.register(cls)
        _ui_widgets.register(cls)

    @classmethod
    def get_tools_for_session(
        cls,
        agent_type: str,
        user_id: str,
        agent_id: str,
        storage: Any,
        context_window: int = 200000,
    ) -> list[dict]:
        """Get tools for a session: core + adaptive basis-set within budget.

        This replaces get_tools_for() for conversational agents.
        Builder and workflow agents should continue using get_tools_for().
        """
        from mycelos.tools.categories import (
            DISCOVER_TOOLS_SCHEMA,
            budget_for_model,
            get_basis_set,
        )

        cls._ensure_initialized()
        allowed = _AGENT_PERMISSIONS.get(agent_type, {ToolPermission.OPEN})

        # 1. Core tools (always loaded)
        core_schemas: list[dict] = []
        core_names: set[str] = set()
        for name, entry in cls._tools.items():
            if entry.get("category") == "core" and entry["permission"] in allowed:
                core_schemas.append(entry["schema"])
                core_names.add(name)

        # Always include discover_tools schema
        if not any(
            s.get("function", {}).get("name") == "discover_tools"
            for s in core_schemas
        ):
            core_schemas.append(DISCOVER_TOOLS_SCHEMA)

        # 2. Adaptive basis-set within budget
        budget = budget_for_model(context_window)
        try:
            usage_rows = storage.fetchall(
                "SELECT tool_name, call_count FROM tool_usage "
                "WHERE user_id=? AND agent_id=? ORDER BY call_count DESC",
                (user_id, agent_id),
            )
        except Exception:
            usage_rows = []

        all_schemas = {
            name: entry["schema"]
            for name, entry in cls._tools.items()
            if entry["permission"] in allowed and name not in core_names
        }
        basis_names = get_basis_set(
            usage_rows=usage_rows,
            budget=budget,
            all_schemas=all_schemas,
        )

        basis_schemas: list[dict] = []
        for name in basis_names:
            if name in core_names:
                continue
            entry = cls._tools.get(name)
            if entry and entry["permission"] in allowed:
                basis_schemas.append(entry["schema"])

        return core_schemas + basis_schemas

    @classmethod
    def reset(cls) -> None:
        """Reset the registry (for testing)."""
        cls._tools.clear()
        cls._initialized = False


_SYSTEM_AGENT_IDS = frozenset({
    "mycelos", "workflow-runner", "builder", "auditor", "evaluator",
})

# Prefixes that also count as system agents (e.g. "workflow-agent:run-123")
_SYSTEM_AGENT_PREFIXES = ("workflow-agent:",)


def _resolve_agent_type(agent_id: str) -> str:
    """Map an agent_id to its type for permission checks."""
    if agent_id == "mycelos" or agent_id == "workflow-runner":
        return "mycelos"
    if agent_id == "builder":
        return "builder"
    if agent_id in _SYSTEM_AGENT_IDS:
        return "mycelos"
    if any(agent_id.startswith(p) for p in _SYSTEM_AGENT_PREFIXES):
        return "mycelos"
    return "custom"
