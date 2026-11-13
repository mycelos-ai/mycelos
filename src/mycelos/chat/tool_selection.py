"""Tool selection for the chat agent.

Which tools the Mycelos chat agent sees per session, the routing and
system-agent constants used by the agent router, and the partitioning
of tool calls into parallel batches. Split out of ``chat/service.py``.
"""

from __future__ import annotations

from typing import Any

from mycelos.tools.registry import ToolPermission, ToolRegistry

# Hard-coded context window for the chat agent — Anthropic's standard
# prompt budget. Used both as the LLM's context window and as the basis
# for attachment-budget calculations.
_CHAT_CONTEXT_WINDOW = 200_000

# Fraction of the context window reserved for attachments. The remainder
# is for system prompt, conversation history, tool schemas, and reply.
_ATTACHMENT_BUDGET_FRACTION = 0.85

# Backwards-compatible alias: tools are now defined in mycelos.tools.*
# but many tests and modules import CHAT_AGENT_TOOLS from here.

# Builder-only tools — only loaded when Builder agent is active.
_BUILDER_ONLY_TOOLS = {
    "create_workflow",        # 550 tokens — building workflows is Builder's job
}

# Connector-gated tools — in-process tools that depend on a connector
# being active. Currently empty: email migrated to MCP (served by
# @n24q02m/better-email-mcp) and its tools are discovered automatically
# via the MCP manager, not hard-coded here.
_CONNECTOR_GATED_TOOLS: dict[str, set[str]] = {}


def _get_chat_agent_tools(
    app: Any = None,
    channel: str = "api",
    user_id: str = "default",
    agent_id: str = "mycelos",
    session_extras: set[str] | None = None,
) -> list[dict]:
    """Return the chat agent tool list from the ToolRegistry.

    When *app* has a storage backend, uses budget-aware session loading
    (``get_tools_for_session``) which sends core + adaptive basis-set.
    Falls back to the full ``get_tools_for`` when storage is unavailable.

    *session_extras*: category names discovered mid-session via
    ``discover_tools``.  Tools from these categories are merged in on
    top of the basis-set.

    Excludes:
    - Dynamic tools (connector_tools, connector_call, github_api) injected at runtime
    - handoff (added dynamically by handlers)
    - Builder-only tools (create_workflow) -- saves ~550 tokens per call
    - Connector-gated tools (email_*) -- only included if the connector is active
    """
    _STATIC_TOOL_NAMES = {
        "search_web", "search_news", "http_get",
        "memory_read", "memory_write",
        "filesystem_read", "filesystem_write", "filesystem_list",
        "search_mcp_servers", "system_status", "workflow_info",
        "create_schedule", "run_workflow",
        "note_write", "note_read", "note_search", "note_list",
        "note_update", "note_link",
        "note_done", "note_remind", "note_move",
        "file_analyze", "file_manage",
        # Email tools removed — handled by @n24q02m/better-email-mcp
        # (registered as an MCP connector). Agents reach them via
        # connector_call, which is already in this allow list.
        "session_set", "session_list",
    }

    # Determine which connector-gated tools to include
    active_connector_ids: set[str] = set()
    if app is not None:
        try:
            for c in app.connector_registry.list_connectors(status="active"):
                active_connector_ids.add(c.get("id", ""))
        except Exception:
            pass

    excluded_gated: set[str] = set()
    for connector_id, gated_tools in _CONNECTOR_GATED_TOOLS.items():
        if connector_id not in active_connector_ids:
            excluded_gated |= gated_tools

    # Budget-aware loading when storage is available
    storage = getattr(app, "storage", None) if app else None
    if storage:
        all_tools = ToolRegistry.get_tools_for_session(
            agent_type="mycelos",
            user_id=user_id,
            agent_id=agent_id,
            storage=storage,
            context_window=_CHAT_CONTEXT_WINDOW,
        )
    else:
        all_tools = ToolRegistry.get_tools_for("mycelos")

    # Merge in discovered session extras
    if session_extras and storage:
        from mycelos.tools.registry import _AGENT_PERMISSIONS
        allowed = _AGENT_PERMISSIONS.get("mycelos", {ToolPermission.OPEN})
        existing_names = {t.get("function", {}).get("name") for t in all_tools}
        for name, entry in ToolRegistry._tools.items():
            cat = entry.get("category")
            if cat in session_extras and entry["permission"] in allowed:
                if name not in existing_names:
                    all_tools.append(entry["schema"])
                    existing_names.add(name)

    # When using session-aware loading, trust the category system for
    # which tools to include.  The _STATIC_TOOL_NAMES allowlist is only
    # needed for the legacy get_tools_for() path (no storage).
    from mycelos.tools.categories import _TOOL_TO_CATEGORY
    use_category_trust = bool(storage)

    result = []
    for t in all_tools:
        name = t["function"]["name"]
        # discover_tools is injected by get_tools_for_session, always allow it
        if name == "discover_tools":
            result.append(t)
            continue
        # Session-aware: allow any tool that has a category assignment
        # (the budget system already filtered appropriately).
        # Legacy path: restrict to the static allowlist.
        if use_category_trust:
            if name not in _STATIC_TOOL_NAMES and name not in _TOOL_TO_CATEGORY:
                continue
        else:
            if name not in _STATIC_TOOL_NAMES:
                continue
        if name in _BUILDER_ONLY_TOOLS:
            continue
        if name in excluded_gated:
            continue
        # The return path is only for routed (non-mycelos) agents —
        # ChatService._get_session_tools appends it per-agent.
        if name == "return_to_mycelos":
            continue
        result.append(t)
    return result


class _ChatAgentToolsProxy:
    """Lazy proxy so `CHAT_AGENT_TOOLS` works as a module-level list.

    Supports iteration, indexing, and len() — delegates to the registry.
    """

    def __iter__(self):
        return iter(_get_chat_agent_tools())

    def __len__(self):
        return len(_get_chat_agent_tools())

    def __getitem__(self, idx):
        return _get_chat_agent_tools()[idx]

    def __contains__(self, item):
        return item in _get_chat_agent_tools()

    def __add__(self, other):
        return _get_chat_agent_tools() + list(other)

    def __radd__(self, other):
        return list(other) + _get_chat_agent_tools()

    def __repr__(self):
        return repr(_get_chat_agent_tools())


CHAT_AGENT_TOOLS = _ChatAgentToolsProxy()

# Actions the LLM is allowed to propose via request_action
_ALLOWED_ACTION_PREFIXES = [
    "mount add", "mount revoke",
    "connector setup", "connector add",
    "schedule add",
    "memory set",
    "pip install",
    "agent grant", "agent revoke",
]

# Actions that are blocked (destructive)
_BLOCKED_ACTION_PREFIXES = [
    "config rollback",
    "memory clear",
    "connector remove",
    "schedule delete",
    "workflow delete",
]

# Tools that control agent routing — always available to routed agents
# regardless of their allowed_tools scoping, so a specialist can always
# hand the conversation back to Mycelos.
_ROUTING_CONTROL_TOOLS = {"handoff", "return_to_mycelos"}

# Agent IDs that are exempt from the persona tool-allowlist check —
# system agents have their own handlers and tool scoping.
_SYSTEM_AGENT_IDS = frozenset({
    "mycelos", "builder", "doctor", "creator", "planner",
    "workflow-agent", "evaluator-agent", "auditor-agent",
    "knowledge-organizer", "model-updater",
})

# Maximum tool-call rounds to prevent infinite loops
MAX_TOOL_ROUNDS = 5

# Maximum concurrent tool executions
MAX_TOOL_CONCURRENCY = 8


def _partition_tool_calls(tool_calls: list[dict]) -> list[list[dict]]:
    """Partition tool calls into batches for parallel execution.

    Consecutive concurrent-safe tools go into the same batch.
    Non-safe tools get their own single-item batch.
    This enables parallel execution of read-only tools while
    preserving order for write tools.

    Example:
        [search_web, http_get, note_write, note_read, note_search]
        → [[search_web, http_get], [note_write], [note_read, note_search]]
    """
    from mycelos.tools.registry import ToolRegistry

    batches: list[list[dict]] = []
    current_batch: list[dict] = []

    for tc in tool_calls:
        name = tc["function"]["name"]
        if ToolRegistry.is_concurrent_safe(name):
            current_batch.append(tc)
        else:
            if current_batch:
                batches.append(current_batch)
                current_batch = []
            batches.append([tc])

    if current_batch:
        batches.append(current_batch)

    return batches
