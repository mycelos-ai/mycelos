# Lazy Tool Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the "load all 30+ tools into every LLM turn" approach with a context-adaptive system: core tools always loaded, a dynamic basis-set filled by usage frequency, and on-demand `discover_tools` for specialized categories — with mid-turn injection so the LLM can use discovered tools immediately.

**Architecture:** New `tools/categories.py` defines tool-to-category mapping + budget calculation. `ToolRegistry.get_tools_for()` is extended to accept a budget and usage-frequency data. A `discover_tools` meta-tool is intercepted by `ChatService` before the normal tool-call loop, expanding the tool list and retrying the LLM call. A `tool_usage` table tracks call frequency per user+agent pair.

**Tech Stack:** Python 3.12, SQLite, existing ToolRegistry + ChatService + LiteLLMBroker.

**Spec reference:** `docs/superpowers/specs/2026-04-09-lazy-tool-discovery-design.md`

---

## File Structure

### Files created
- `src/mycelos/tools/categories.py` — `TOOL_CATEGORIES` dict, `get_basis_set()`, `get_category_tools()`, `budget_for_model()`, `DISCOVER_TOOLS_SCHEMA`
- `tests/test_tool_categories.py` — unit tests for category assignment, budget, basis-set
- `tests/test_tool_discovery.py` — integration test for the discover → retry → execute flow

### Files modified
- `src/mycelos/storage/schema.sql` — append `tool_usage` table
- `src/mycelos/storage/database.py` — add `tool_usage` CREATE TABLE to `_ensure_schema`
- `src/mycelos/tools/registry.py` — add `category` param to `register()`, add `track_usage()`, modify `get_tools_for()` to accept budget+usage, add `get_tools_for_session()`
- `src/mycelos/tools/knowledge.py` — add `category=` to each `register()` call
- `src/mycelos/tools/workflow.py` — add `category=` to each `register()` call
- `src/mycelos/tools/connector.py` — add `category=` to each `register()` call
- `src/mycelos/tools/system.py` — add `category=` to each `register()` call
- `src/mycelos/tools/email.py` — add `category=` to each `register()` call
- `src/mycelos/tools/memory.py` — add `category=` to each `register()` call
- `src/mycelos/tools/agent.py` — add `category=` to each `register()` call
- `src/mycelos/tools/session.py` — add `category=` to each `register()` call
- `src/mycelos/tools/web.py` — add `category=` to each `register()` call
- `src/mycelos/tools/ui_widgets.py` — add `category=` to each `register()` call
- `src/mycelos/chat/service.py` — intercept `discover_tools` in tool-call loop, track usage after execute, maintain `session_extra_tools`
- `src/mycelos/agents/handlers/mycelos_handler.py` — update system prompt with `## Tools` section
- `CHANGELOG.md`

---

## Task 1: `tool_usage` table + schema migration

**Files:**
- Modify: `src/mycelos/storage/schema.sql`
- Modify: `src/mycelos/storage/database.py`
- Test: `tests/test_tool_discovery.py` (just the migration test for now)

- [ ] **Step 1: Write the failing migration test**

Create `tests/test_tool_discovery.py`:

```python
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
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_tool_discovery.py -v`

- [ ] **Step 3: Add the table to schema.sql**

Append at the end of `src/mycelos/storage/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS tool_usage (
    user_id    TEXT NOT NULL,
    agent_id   TEXT NOT NULL,
    tool_name  TEXT NOT NULL,
    call_count INTEGER NOT NULL DEFAULT 0,
    last_used  TEXT,
    PRIMARY KEY (user_id, agent_id, tool_name)
);
```

- [ ] **Step 4: Add to `_ensure_schema` in database.py**

In `src/mycelos/storage/database.py`, inside `_ensure_schema()`, after the existing `organizer_suggestions` CREATE TABLE block, add:

```python
        # tool_usage table for Lazy Tool Discovery
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tool_usage (
                user_id    TEXT NOT NULL,
                agent_id   TEXT NOT NULL,
                tool_name  TEXT NOT NULL,
                call_count INTEGER NOT NULL DEFAULT 0,
                last_used  TEXT,
                PRIMARY KEY (user_id, agent_id, tool_name)
            );
            """
        )
        self._conn.commit()
```

Also add `"tool_usage"` to the `for check_table in (...)` line at the top of `_ensure_schema` (around line 59) so that the full schema is re-applied if the table is missing.

- [ ] **Step 5: Run — expect pass**

Run: `pytest tests/test_tool_discovery.py -v`

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/storage/schema.sql src/mycelos/storage/database.py tests/test_tool_discovery.py
git commit -m "feat(tools): tool_usage table for lazy discovery usage tracking"
```

---

## Task 2: Tool categories module

**Files:**
- Create: `src/mycelos/tools/categories.py`
- Test: `tests/test_tool_categories.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tool_categories.py`:

```python
"""Unit tests for tool categories + budget calculation."""
from __future__ import annotations

import pytest

from mycelos.tools.categories import (
    TOOL_CATEGORIES,
    budget_for_model,
    get_basis_set,
    get_category_tools,
    DISCOVER_TOOLS_SCHEMA,
)


def test_every_category_has_8_or_fewer_tools() -> None:
    for cat, tools in TOOL_CATEGORIES.items():
        assert len(tools) <= 8, f"category {cat!r} has {len(tools)} tools (max 8)"


def test_core_category_exists() -> None:
    assert "core" in TOOL_CATEGORIES
    assert "discover_tools" in TOOL_CATEGORIES["core"]


def test_budget_tiny_model() -> None:
    assert budget_for_model(8192) == 409  # 8192 * 0.05


def test_budget_large_model() -> None:
    assert budget_for_model(200000) == 4096  # capped


def test_budget_zero() -> None:
    assert budget_for_model(0) == 0


def test_get_basis_set_empty_usage() -> None:
    """With no usage data, the default set is knowledge_read + knowledge_write."""
    tools = get_basis_set(usage_rows=[], budget=2000)
    names = {t for t in tools}
    assert "note_search" in names  # from knowledge_read
    assert "note_write" in names   # from knowledge_write


def test_get_basis_set_respects_budget() -> None:
    """With a tiny budget, only a few tools fit."""
    tools = get_basis_set(usage_rows=[], budget=200)
    # 200 tokens ≈ 1-2 tools max
    assert len(tools) <= 4


def test_get_basis_set_uses_frequency() -> None:
    """Most-used tools should be included first."""
    usage = [
        {"tool_name": "email_send", "call_count": 100},
        {"tool_name": "note_write", "call_count": 5},
        {"tool_name": "connector_setup", "call_count": 50},
    ]
    tools = get_basis_set(usage_rows=usage, budget=4096)
    names = list(tools)
    # email_send and connector_setup are high-frequency — should be in the set
    assert "email_send" in names
    assert "connector_setup" in names


def test_get_category_tools_filters_permissions() -> None:
    from mycelos.tools.registry import ToolPermission
    allowed = {ToolPermission.OPEN, ToolPermission.STANDARD}
    tools = get_category_tools("knowledge_manage", allowed)
    # All returned tools must have OPEN or STANDARD permission
    assert isinstance(tools, list)


def test_discover_tools_schema_is_valid() -> None:
    assert DISCOVER_TOOLS_SCHEMA["function"]["name"] == "discover_tools"
    params = DISCOVER_TOOLS_SCHEMA["function"]["parameters"]
    assert "category" in params["properties"]
    assert "enum" in params["properties"]["category"]
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_tool_categories.py -v`

- [ ] **Step 3: Implement the module**

Create `src/mycelos/tools/categories.py`:

```python
"""Lazy Tool Discovery — category definitions + budget logic.

Tools are grouped into categories. At session start, only ``core`` +
a frequency-based basis-set are loaded. Additional categories can be
discovered mid-session via the ``discover_tools`` meta-tool.
"""
from __future__ import annotations

import json
from typing import Any

# Each tool maps to exactly one category. Categories are loaded as a unit.
# Rule: max 8 tools per category. Split if it grows beyond that.
TOOL_CATEGORIES: dict[str, list[str]] = {
    "core": [
        "discover_tools",
        "handoff",
        "memory_read",
        "memory_write",
        "workflow_list",
    ],
    "knowledge_read": [
        "note_search",
        "note_read",
        "note_list",
        "topic_list",
    ],
    "knowledge_write": [
        "note_write",
        "note_update",
        "note_done",
        "note_remind",
        "note_move",
    ],
    "knowledge_manage": [
        "topic_create",
        "topic_rename",
        "topic_merge",
        "note_archive",
        "note_delete",
        "knowledge_stats",
    ],
    "workflows": [
        "workflow_create",
        "workflow_run",
        "workflow_status",
    ],
    "connectors": [
        "connector_setup",
        "connector_list",
        "credential_store",
    ],
    "system": [
        "config_list",
        "config_rollback",
        "agent_create",
    ],
    "email": [
        "email_send",
        "email_search",
        "email_read",
    ],
}

# Reverse map: tool_name → category
_TOOL_TO_CATEGORY: dict[str, str] = {}
for _cat, _tools in TOOL_CATEGORIES.items():
    for _t in _tools:
        _TOOL_TO_CATEGORY[_t] = _cat

# Default basis-set categories for new users with no usage data.
_DEFAULT_BASIS_CATEGORIES = ["knowledge_read", "knowledge_write"]

# Categories the LLM can discover (excludes core and default-basis categories
# since those are already loaded).
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
    """Return the category a tool belongs to, or None if unassigned."""
    return _TOOL_TO_CATEGORY.get(tool_name)


def budget_for_model(context_window: int) -> int:
    """Compute the tool-schema token budget from the model's context window.

    Returns: token budget (int). 5% of context, capped at 4096.
    """
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
    """Compute the adaptive basis-set of tool names that fits within budget.

    Args:
        usage_rows: list of ``{"tool_name": str, "call_count": int}`` rows
                    from the ``tool_usage`` table, sorted by call_count DESC.
        budget: token budget for non-core tools.
        all_schemas: optional dict of tool_name → schema. If None, budget
                     checking uses a rough estimate of 150 tokens per tool.

    Returns:
        list of tool names to include in the basis set (excluding core).
    """
    # Start with the default basis categories
    default_tools: list[str] = []
    for cat in _DEFAULT_BASIS_CATEGORIES:
        default_tools.extend(TOOL_CATEGORIES.get(cat, []))

    # Build a frequency-ranked list of tools the user actually uses
    frequent: list[str] = []
    if usage_rows:
        seen = set(default_tools)
        for row in sorted(usage_rows, key=lambda r: r.get("call_count", 0), reverse=True):
            name = row["tool_name"]
            if name not in seen and name not in TOOL_CATEGORIES.get("core", []):
                frequent.append(name)
                seen.add(name)

    # Merge: defaults first, then frequency-ranked extras
    candidates = default_tools + frequent

    # Trim to budget
    result: list[str] = []
    tokens_used = 0
    per_tool = 150  # rough average tokens per schema
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
    """Return tool names in a category, filtered by permission set.

    The actual permission check happens in the registry — this just
    returns the names. The caller filters schemas from the registry.
    """
    return TOOL_CATEGORIES.get(category, [])
```

- [ ] **Step 4: Run — expect pass**

Run: `pytest tests/test_tool_categories.py -v`

Some tests that depend on the ToolRegistry being initialized (like `get_category_tools` with permission filtering) may need the registry to load. If so, call `ToolRegistry._ensure_initialized()` in a fixture or adjust the test to only check the category list, not the permission filter.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/tools/categories.py tests/test_tool_categories.py
git commit -m "feat(tools): category definitions + budget + basis-set logic"
```

---

## Task 3: Add `category` parameter to tool registration

**Files:**
- Modify: `src/mycelos/tools/registry.py`
- Modify: `src/mycelos/tools/knowledge.py`
- Modify: `src/mycelos/tools/workflow.py`
- Modify: `src/mycelos/tools/connector.py`
- Modify: `src/mycelos/tools/system.py`
- Modify: `src/mycelos/tools/email.py`
- Modify: `src/mycelos/tools/memory.py`
- Modify: `src/mycelos/tools/agent.py`
- Modify: `src/mycelos/tools/session.py`
- Modify: `src/mycelos/tools/web.py`
- Modify: `src/mycelos/tools/ui_widgets.py`

- [ ] **Step 1: Extend `register()` signature in registry.py**

In `src/mycelos/tools/registry.py`, modify the `register` classmethod to accept an optional `category` parameter and store it:

```python
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
    cls._tools[name] = {
        "schema": schema,
        "execute": execute_fn,
        "permission": permission,
        "concurrent_safe": concurrent_safe,
        "category": category,
    }
```

- [ ] **Step 2: Add category to each knowledge tool registration**

In `src/mycelos/tools/knowledge.py`, update the `register()` function:

```python
def register(registry: type) -> None:
    registry.register("note_write", NOTE_WRITE_SCHEMA, execute_note_write, ToolPermission.STANDARD, category="knowledge_write")
    registry.register("note_read", NOTE_READ_SCHEMA, execute_note_read, ToolPermission.OPEN, concurrent_safe=True, category="knowledge_read")
    registry.register("note_search", NOTE_SEARCH_SCHEMA, execute_note_search, ToolPermission.OPEN, concurrent_safe=True, category="knowledge_read")
    registry.register("note_list", NOTE_LIST_SCHEMA, execute_note_list, ToolPermission.OPEN, concurrent_safe=True, category="knowledge_read")
    registry.register("note_update", NOTE_UPDATE_SCHEMA, execute_note_update, ToolPermission.STANDARD, category="knowledge_write")
    registry.register("note_link", NOTE_LINK_SCHEMA, execute_note_link, ToolPermission.STANDARD, category="knowledge_write")
    registry.register("note_done", NOTE_DONE_SCHEMA, execute_note_done, ToolPermission.STANDARD, category="knowledge_write")
    registry.register("note_remind", NOTE_REMIND_SCHEMA, execute_note_remind, ToolPermission.STANDARD, category="knowledge_write")
    registry.register("note_move", NOTE_MOVE_SCHEMA, execute_note_move, ToolPermission.STANDARD, category="knowledge_write")
    registry.register("topic_create", TOPIC_CREATE_SCHEMA, execute_topic_create, ToolPermission.STANDARD, category="knowledge_manage")
    registry.register("topic_list", TOPIC_LIST_SCHEMA, execute_topic_list, ToolPermission.OPEN, concurrent_safe=True, category="knowledge_read")
```

- [ ] **Step 3: Add category to all other tool modules**

For each remaining tool module, open the file, find the `register()` function, and add `category=` to every `registry.register()` call. Use the category assignments from the spec:

- `tools/memory.py`: `category="core"` for `memory_read`, `memory_write`
- `tools/workflow.py`: `category="workflows"` for all, except `workflow_list` → `category="core"`
- `tools/connector.py`: `category="connectors"` for all
- `tools/system.py`: `category="system"` for all
- `tools/email.py`: `category="email"` for all
- `tools/agent.py`: `category="system"` for agent management tools
- `tools/session.py`: `category="core"` for `handoff`; others as appropriate
- `tools/web.py`: `category="core"` or `category="knowledge_read"` depending on the tool
- `tools/ui_widgets.py`: `category="core"` (UI widgets are always needed)

For tools NOT listed in `TOOL_CATEGORIES`, assign `category="core"` as a safe default — they'll always be loaded.

Read each file's `register()` function before editing to match tool names to categories. If a tool name doesn't appear in `TOOL_CATEGORIES` at all, it's uncategorized — assign it to the closest match and add it to the `TOOL_CATEGORIES` dict in `categories.py`.

- [ ] **Step 4: Add a test that every registered tool has a category**

Append to `tests/test_tool_categories.py`:

```python
def test_every_registered_tool_has_a_category() -> None:
    from mycelos.tools.registry import ToolRegistry
    ToolRegistry._ensure_initialized()
    for name in ToolRegistry.list_tools():
        entry = ToolRegistry._tools[name]
        assert entry.get("category") is not None, f"tool {name!r} has no category"
```

- [ ] **Step 5: Run all tests**

Run: `pytest tests/test_tool_categories.py tests/test_tool_discovery.py -v`

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/tools/
git commit -m "feat(tools): assign categories to all registered tools"
```

---

## Task 4: `get_tools_for_session()` — budget-aware tool loading

**Files:**
- Modify: `src/mycelos/tools/registry.py`
- Test: `tests/test_tool_categories.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tool_categories.py`:

```python
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
    assert "handoff" in names or "memory_read" in names
    # Default basis-set includes knowledge tools
    assert "note_search" in names
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
        context_window=4000,  # tiny model
    )
    # Should be much fewer tools than the full set
    full = ToolRegistry.get_tools_for("mycelos")
    assert len(schemas) < len(full)
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_tool_categories.py::test_get_tools_for_session_returns_core_plus_basis -v`

- [ ] **Step 3: Implement `get_tools_for_session`**

Add to `src/mycelos/tools/registry.py`:

```python
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

    This replaces ``get_tools_for()`` for conversational agents.
    Builder and workflow agents should continue using ``get_tools_for()``.
    """
    from mycelos.tools.categories import (
        TOOL_CATEGORIES,
        DISCOVER_TOOLS_SCHEMA,
        budget_for_model,
        get_basis_set,
    )

    cls._ensure_initialized()
    allowed = _AGENT_PERMISSIONS.get(agent_type, {ToolPermission.OPEN})

    # 1. Core tools (always loaded, not counted against budget)
    core_names = set(TOOL_CATEGORIES.get("core", []))
    core_schemas = []
    for name in core_names:
        entry = cls._tools.get(name)
        if entry and entry["permission"] in allowed:
            core_schemas.append(entry["schema"])
    # Always include discover_tools even if not yet registered as a real tool
    if not any(s["function"]["name"] == "discover_tools" for s in core_schemas):
        core_schemas.append(DISCOVER_TOOLS_SCHEMA)

    # 2. Adaptive basis-set within budget
    budget = budget_for_model(context_window)
    usage_rows = storage.fetchall(
        "SELECT tool_name, call_count FROM tool_usage "
        "WHERE user_id=? AND agent_id=? ORDER BY call_count DESC",
        (user_id, agent_id),
    )
    all_schemas = {
        name: entry["schema"]
        for name, entry in cls._tools.items()
        if entry["permission"] in allowed
    }
    basis_names = get_basis_set(
        usage_rows=usage_rows,
        budget=budget,
        all_schemas=all_schemas,
    )

    basis_schemas = []
    for name in basis_names:
        if name in core_names:
            continue  # already loaded
        entry = cls._tools.get(name)
        if entry and entry["permission"] in allowed:
            basis_schemas.append(entry["schema"])

    return core_schemas + basis_schemas
```

- [ ] **Step 4: Run — expect pass**

Run: `pytest tests/test_tool_categories.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/tools/registry.py tests/test_tool_categories.py
git commit -m "feat(tools): get_tools_for_session() with budget + basis-set"
```

---

## Task 5: Usage tracking in `ToolRegistry.execute()`

**Files:**
- Modify: `src/mycelos/tools/registry.py`

- [ ] **Step 1: Add `track_usage` method**

Add to `ToolRegistry` in `src/mycelos/tools/registry.py`:

```python
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
```

- [ ] **Step 2: Call `track_usage` from `execute()`**

In the existing `execute()` method (around line 113), after the tool call succeeds (before the return), add:

```python
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
```

- [ ] **Step 3: Run existing tests to ensure no regression**

Run: `pytest tests/test_tool_categories.py tests/test_tool_discovery.py tests/test_gateway.py -v`

- [ ] **Step 4: Commit**

```bash
git add src/mycelos/tools/registry.py
git commit -m "feat(tools): usage tracking in ToolRegistry.execute()"
```

---

## Task 6: `discover_tools` interception in ChatService

**Files:**
- Modify: `src/mycelos/chat/service.py`
- Test: `tests/test_tool_discovery.py` (extend)

- [ ] **Step 1: Read the tool-call dispatch loop**

Open `src/mycelos/chat/service.py` and read lines 670–770 (the tool-call loop). Understand:
- How tool calls are extracted from the LLM response
- Where `_execute_tool` is called
- How results are collected and fed back to the LLM

- [ ] **Step 2: Add session state for discovered tools**

In `ChatService.__init__` or at the start of `handle_message`, initialize:

```python
# Lazy Tool Discovery: tools discovered mid-session via discover_tools()
if not hasattr(self, "_session_extra_tools"):
    self._session_extra_tools: dict[str, set[str]] = {}  # session_id → set of extra tool names
```

- [ ] **Step 3: Intercept `discover_tools` in the tool-call loop**

In the tool-call loop (around line 694), before the normal tool execution, check if the tool call is `discover_tools`:

```python
# Check for discover_tools — intercept and retry
if tool_name == "discover_tools":
    category = args.get("category", "")
    from mycelos.tools.categories import TOOL_CATEGORIES, DISCOVERABLE_CATEGORIES
    if category not in DISCOVERABLE_CATEGORIES:
        tool_results.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps({"error": f"Unknown category: {category}"}),
        })
        continue
    
    # Load the category tools
    new_tool_names = TOOL_CATEGORIES.get(category, [])
    session_extras = self._session_extra_tools.setdefault(session_id, set())
    session_extras.update(new_tool_names)
    
    # Rebuild tool list with the new category included
    # and retry the LLM call with the expanded tools
    tool_results.append({
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps({
            "status": "loaded",
            "category": category,
            "tools": new_tool_names,
            "message": f"Loaded {len(new_tool_names)} tools from category '{category}'. You can now use them.",
        }),
    })
    # Flag that we need to retry with expanded tools
    _discovery_happened = True
    continue
```

After the tool-call loop, if `_discovery_happened` is True:
- Rebuild the tool list by merging `session_extra_tools` into the current tools
- The tool results (including the discover_tools response) are already in the messages
- The next LLM call will see the expanded tool list

This is the simplest approach: the discovery result tells the LLM "tools loaded, use them now" and the next iteration of the turn loop sends the LLM call with the expanded tools.

- [ ] **Step 4: Modify `_get_chat_agent_tools` to accept extra tools**

In the function `_get_chat_agent_tools` (around line 60), add an `extra_tools` parameter:

```python
def _get_chat_agent_tools(app: Any = None, channel: str = "api",
                          extra_tool_names: set[str] | None = None) -> list[dict]:
    all_tools = ToolRegistry.get_tools_for("mycelos")
    if extra_tool_names:
        # Already included in all_tools since get_tools_for returns everything
        # But if we switch to get_tools_for_session, we need to add the extras
        pass
    # ... existing filtering ...
```

Actually, the cleaner approach: modify the call site in `MycelosHandler.get_tools()` to use `get_tools_for_session()` and merge `session_extra_tools`.

- [ ] **Step 5: Write a test for the discovery flow**

Append to `tests/test_tool_discovery.py`:

```python
def test_discover_tools_returns_loaded_status() -> None:
    """Verifies that calling discover_tools returns tool names."""
    from mycelos.tools.categories import TOOL_CATEGORIES
    
    category = "knowledge_manage"
    expected_tools = TOOL_CATEGORIES[category]
    
    # Simulate what the gateway does when discover_tools is called
    result = {
        "status": "loaded",
        "category": category,
        "tools": expected_tools,
    }
    assert result["status"] == "loaded"
    assert len(result["tools"]) > 0
    assert "topic_create" in result["tools"]
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_tool_discovery.py tests/test_tool_categories.py -v`

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/chat/service.py tests/test_tool_discovery.py
git commit -m "feat(tools): discover_tools interception in ChatService"
```

---

## Task 7: System prompt update

**Files:**
- Modify: `src/mycelos/chat/service.py` (the `_MYCELOS_SYSTEM_PROMPT` constant)
- Modify: `src/mycelos/agents/handlers/mycelos_handler.py` (if prompt is assembled there)

- [ ] **Step 1: Find the system prompt**

The system prompt is `_MYCELOS_SYSTEM_PROMPT` in `src/mycelos/chat/service.py` (around line 2383). Read it to find the existing tools-related section.

- [ ] **Step 2: Add the ## Tools section**

Find where the tools/knowledge section is in the prompt and replace or augment it with:

```
## Tools

You have a base set of tools loaded for this session. For specialized
actions beyond your current tools, call `discover_tools(category)`.

Available categories you can discover:
- knowledge_manage — rename/merge/delete topics, archive notes, knowledge stats
- workflows — create and run workflows
- connectors — set up external service connectors
- system — configuration, rollback, agent management
- email — send, search, read emails

Rules:
- Only discover when the user's request clearly needs tools you don't have.
- Do NOT discover speculatively or "just in case".
- After discovery, the new tools are immediately available — use them in the same response.
```

Do NOT remove the existing knowledge/task instructions — they are still relevant. Just add the Tools section before or after them.

- [ ] **Step 3: Verify the prompt is well-formed**

Run: `python -c "from mycelos.chat.service import _MYCELOS_SYSTEM_PROMPT; print(len(_MYCELOS_SYSTEM_PROMPT), 'chars')"`

- [ ] **Step 4: Commit**

```bash
git add src/mycelos/chat/service.py src/mycelos/agents/handlers/mycelos_handler.py
git commit -m "feat(tools): system prompt updated for lazy tool discovery"
```

---

## Task 8: Wire `get_tools_for_session` into the chat flow

**Files:**
- Modify: `src/mycelos/agents/handlers/mycelos_handler.py`
- Modify: `src/mycelos/chat/service.py`

- [ ] **Step 1: Find where tools are built for each LLM call**

In `mycelos_handler.py`, find `get_tools()` (around line 324). This is called by `ChatService` before each LLM turn to get the tool list.

- [ ] **Step 2: Switch to `get_tools_for_session`**

Modify `get_tools()` in the handler (or `_get_chat_agent_tools` in service.py — whichever is the actual call site) to use `get_tools_for_session()` instead of `get_tools_for()`:

```python
# Before:
all_tools = ToolRegistry.get_tools_for("mycelos")

# After:
storage = self._app.storage
context_window = self._app.llm.get_context_window()  # or a sensible default
all_tools = ToolRegistry.get_tools_for_session(
    agent_type="mycelos",
    user_id=user_id,
    agent_id="mycelos",
    storage=storage,
    context_window=context_window,
)
```

If `get_context_window()` doesn't exist on the broker, use a default of 200000 and add a TODO to wire it up properly. The budget calculation is a best-effort optimization, not a hard requirement.

- [ ] **Step 3: Merge session_extra_tools**

After the basis-set is loaded, merge any tools discovered during this session:

```python
# Add session-discovered tools
extra_names = chat_service._session_extra_tools.get(session_id, set())
if extra_names:
    allowed = _AGENT_PERMISSIONS.get("mycelos", set())
    for name in extra_names:
        entry = ToolRegistry._tools.get(name)
        if entry and entry["permission"] in allowed:
            if not any(s["function"]["name"] == name for s in all_tools):
                all_tools.append(entry["schema"])
```

- [ ] **Step 4: Test end-to-end (manual)**

Start the gateway, open a chat, and verify:
1. Normal chat works (note_write, note_search still available)
2. Ask "rename the topic Kaffee" — Mycelos should call `discover_tools(category="knowledge_manage")`
3. After discovery, it should call `topic_rename` (once we implement it in the Knowledge Tools spec)

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/agents/handlers/mycelos_handler.py src/mycelos/chat/service.py
git commit -m "feat(tools): wire get_tools_for_session into chat flow"
```

---

## Task 9: CHANGELOG + final verification

- [ ] **Step 1: Add changelog**

Under `## Week 15 (2026)` in `CHANGELOG.md`:

```markdown
### Lazy Tool Discovery
- Context-adaptive tool loading: core tools always loaded, dynamic basis-set filled by usage frequency, specialized categories discovered on demand
- `discover_tools` meta-tool with mid-turn injection (LLM discovers and uses tools in the same response)
- Token budget calculated from model context window (5%, capped at 4096)
- `tool_usage` table tracks call frequency per user+agent for adaptive basis-sets
- All 30+ tools assigned to 8 categories (max 8 tools each)
- System prompt updated with discovery instructions
```

- [ ] **Step 2: Run the test suite**

```bash
pytest tests/test_tool_categories.py tests/test_tool_discovery.py tests/test_gateway.py tests/test_storage.py -v
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): Lazy Tool Discovery"
```
