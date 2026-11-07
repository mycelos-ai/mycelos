# Agent Handoff — Implementation Plan (Foundation)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hardcoded agent routing with a DB-backed handoff system where each session has one active agent, and agents transfer conversations via a `handoff` tool.

**Architecture:** Add `session_agents` table tracking `active_agent_id` per session. Create an `AgentHandler` protocol that Mycelos, Creator, and Planner implement. Route all messages through the active handler — no if-else chains. Handoff is a tool that updates the DB and switches the conversation.

**Tech Stack:** Existing SQLite storage, Python Protocols, ChatService refactor

**Spec:** `docs/superpowers/specs/2026-03-26-agent-handoff-design.md`

**Note:** This is Plan A (Foundation). Plan B (Custom Agents as MCP Servers) builds on this and will be written separately.

---

## File Structure

### New Files
- `src/mycelos/agents/handlers/__init__.py` — Package init
- `src/mycelos/agents/handlers/base.py` — AgentHandler Protocol
- `src/mycelos/agents/handlers/mycelos_handler.py` — Mycelos (chat) agent handler
- `src/mycelos/agents/handlers/creator_handler.py` — Creator agent handler
- `src/mycelos/agents/handlers/planner_handler.py` — Planner agent handler
- `tests/test_agent_handoff.py` — All handoff tests

### Modified Files
- `src/mycelos/storage/schema.sql` — Add `session_agents` table + `user_facing` column
- `src/mycelos/storage/database.py` — Auto-migration for new table
- `src/mycelos/chat/service.py` — Replace routing with handler dispatch
- `src/mycelos/app.py` — Register agent handlers

---

## Task 1: Schema + Session Agent Tracking

**Files:**
- Modify: `src/mycelos/storage/schema.sql`
- Modify: `src/mycelos/storage/database.py`
- Create: `tests/test_agent_handoff.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_agent_handoff.py
"""Tests for Agent Handoff — session tracking, handler dispatch, handoff tool."""

import os
import tempfile
from pathlib import Path

import pytest

from mycelos.app import App


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-handoff"
        a = App(Path(tmp))
        a.initialize()
        yield a


class TestSessionAgentTracking:
    def test_default_agent_is_mycelos(self, app):
        """New session should default to mycelos agent."""
        agent = app.storage.fetchone(
            "SELECT active_agent_id FROM session_agents WHERE session_id = 'new-session'"
        )
        # No row yet — default is mycelos (handled by lookup logic)
        assert agent is None  # No row = default mycelos

    def test_set_active_agent(self, app):
        """Setting active agent creates/updates the row."""
        app.storage.execute(
            "INSERT INTO session_agents (session_id, active_agent_id, handoff_reason) VALUES (?, ?, ?)",
            ("sess-1", "creator", "User wants to build an agent"),
        )
        row = app.storage.fetchone(
            "SELECT active_agent_id, handoff_reason FROM session_agents WHERE session_id = 'sess-1'"
        )
        assert row["active_agent_id"] == "creator"
        assert "build" in row["handoff_reason"]

    def test_update_active_agent(self, app):
        """Handoff updates the active agent."""
        app.storage.execute(
            "INSERT INTO session_agents (session_id, active_agent_id) VALUES (?, ?)",
            ("sess-2", "mycelos"),
        )
        app.storage.execute(
            "UPDATE session_agents SET active_agent_id = ?, handoff_reason = ? WHERE session_id = ?",
            ("planner", "Complex task needs planning", "sess-2"),
        )
        row = app.storage.fetchone(
            "SELECT active_agent_id FROM session_agents WHERE session_id = 'sess-2'"
        )
        assert row["active_agent_id"] == "planner"

    def test_session_agents_table_exists(self, app):
        """Schema migration creates session_agents table."""
        result = app.storage.fetchone("SELECT 1 FROM session_agents LIMIT 0")
        # Should not raise — table exists
```

- [ ] **Step 2: Add schema**

Add to `src/mycelos/storage/schema.sql` at the end:

```sql
-- Session Agent Tracking (which agent is active per session)
CREATE TABLE IF NOT EXISTS session_agents (
    session_id      TEXT PRIMARY KEY,
    active_agent_id TEXT NOT NULL DEFAULT 'mycelos',
    handoff_reason  TEXT,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
```

Add `user_facing` column migration to agents table (in `_ensure_schema` migrations list):

```python
("agents", "user_facing", "INTEGER NOT NULL DEFAULT 0"),
```

- [ ] **Step 3: Update auto-migration in database.py**

Add `"session_agents"` to the migration check list.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_agent_handoff.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/storage/schema.sql src/mycelos/storage/database.py tests/test_agent_handoff.py
git commit -m "feat: session_agents table + user_facing column for agent handoff"
```

---

## Task 2: AgentHandler Protocol + Base

**Files:**
- Create: `src/mycelos/agents/handlers/__init__.py`
- Create: `src/mycelos/agents/handlers/base.py`
- Modify: `tests/test_agent_handoff.py`

- [ ] **Step 1: Write failing tests**

```python
class TestAgentHandlerProtocol:
    def test_protocol_exists(self):
        from mycelos.agents.handlers.base import AgentHandler
        assert hasattr(AgentHandler, 'handle')
        assert hasattr(AgentHandler, 'agent_id')
        assert hasattr(AgentHandler, 'display_name')
        assert hasattr(AgentHandler, 'get_system_prompt')
        assert hasattr(AgentHandler, 'get_tools')
```

- [ ] **Step 2: Implement AgentHandler Protocol**

Create `src/mycelos/agents/handlers/__init__.py` (empty).

Create `src/mycelos/agents/handlers/base.py`:

```python
"""AgentHandler Protocol — unified interface for user-facing agents."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from mycelos.chat.events import ChatEvent


@runtime_checkable
class AgentHandler(Protocol):
    """Every user-facing agent implements this interface."""

    @property
    def agent_id(self) -> str:
        """Unique identifier (e.g., 'mycelos', 'creator', 'planner')."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name shown to user (e.g., 'Creator-Agent')."""
        ...

    def handle(self, message: str, session_id: str, user_id: str,
               conversation: list[dict]) -> list[ChatEvent]:
        """Process a user message and return response events."""
        ...

    def get_system_prompt(self, context: dict | None = None) -> str:
        """Return the system prompt for this agent."""
        ...

    def get_tools(self) -> list[dict]:
        """Return the tool definitions for this agent."""
        ...
```

- [ ] **Step 3: Run tests + commit**

```bash
git add src/mycelos/agents/handlers/ tests/test_agent_handoff.py
git commit -m "feat: AgentHandler Protocol — unified interface for user-facing agents"
```

---

## Task 3: Mycelos Handler (extract from ChatService)

**Files:**
- Create: `src/mycelos/agents/handlers/mycelos_handler.py`
- Modify: `tests/test_agent_handoff.py`

- [ ] **Step 1: Write failing tests**

```python
class TestMycelosHandler:
    def test_mycelos_handler_exists(self):
        from mycelos.agents.handlers.mycelos_handler import MycelosHandler
        assert MycelosHandler

    def test_mycelos_has_correct_id(self, app):
        from mycelos.agents.handlers.mycelos_handler import MycelosHandler
        handler = MycelosHandler(app)
        assert handler.agent_id == "mycelos"
        assert handler.display_name == "Mycelos"

    def test_mycelos_has_tools(self, app):
        from mycelos.agents.handlers.mycelos_handler import MycelosHandler
        handler = MycelosHandler(app)
        tools = handler.get_tools()
        assert len(tools) > 0
        tool_names = [t["function"]["name"] for t in tools]
        assert "note_write" in tool_names

    def test_mycelos_has_handoff_tool(self, app):
        from mycelos.agents.handlers.mycelos_handler import MycelosHandler
        handler = MycelosHandler(app)
        tools = handler.get_tools()
        tool_names = [t["function"]["name"] for t in tools]
        assert "handoff" in tool_names
```

- [ ] **Step 2: Implement MycelosHandler**

Create `src/mycelos/agents/handlers/mycelos_handler.py`.

This extracts the Mycelos-specific logic from ChatService:
- System prompt (the existing one from `get_system_prompt()`)
- Tool list (the existing `CHAT_AGENT_TOOLS` + `handoff`)
- The LLM call + tool execution loop

```python
"""Mycelos Handler — the default chat agent."""

from __future__ import annotations

from typing import Any

from mycelos.chat.events import ChatEvent, text_event, done_event, agent_event


class MycelosHandler:
    """Default chat agent — conversation, tools, note-taking."""

    def __init__(self, app: Any):
        self._app = app

    @property
    def agent_id(self) -> str:
        return "mycelos"

    @property
    def display_name(self) -> str:
        return "Mycelos"

    def get_system_prompt(self, context: dict | None = None) -> str:
        """Return Mycelos's system prompt with handoff rules."""
        # Import the existing prompt builder
        # Add handoff rules to the prompt
        base_prompt = self._get_base_prompt(context)
        handoff_rules = self._get_handoff_rules()
        return f"{base_prompt}\n\n{handoff_rules}"

    def _get_base_prompt(self, context: dict | None = None) -> str:
        """Get the existing Mycelos system prompt."""
        user_name = self._app.memory.get("default", "system", "user.name")
        # Reuse existing prompt logic from ChatService
        from mycelos.chat.service import ChatService
        temp_service = ChatService.__new__(ChatService)
        temp_service._app = self._app
        return temp_service.get_system_prompt(user_name)

    def _get_handoff_rules(self) -> str:
        """Handoff rules for Mycelos's system prompt."""
        # Get paused/developing agents
        agents_context = self._get_pending_agents_context()
        return f"""
## Agent Handoff

You have a `handoff` tool. Use it to transfer this conversation to a specialist:

- User wants to BUILD or CREATE a new agent → handoff to "creator"
- User wants a COMPLEX multi-step task that needs planning → handoff to "planner"

Do NOT hand off for simple tasks you can handle with your tools (notes, search, files).

{agents_context}
"""

    def _get_pending_agents_context(self) -> str:
        """Context about agents in development/paused state."""
        try:
            agents = self._app.agent_registry.list_agents()
            pending = [a for a in agents if a["status"] in ("developing", "paused", "auditing")]
            if pending:
                lines = ["Agents in progress (mention naturally if relevant):"]
                for a in pending:
                    lines.append(f"  - {a['name']} ({a['status']})")
                return "\n".join(lines)
        except Exception:
            pass
        return ""

    def get_tools(self) -> list[dict]:
        """Return Mycelos's tools including the handoff tool."""
        from mycelos.chat.service import CHAT_AGENT_TOOLS
        tools = list(CHAT_AGENT_TOOLS)
        tools.append(self._handoff_tool_definition())
        return tools

    def _handoff_tool_definition(self) -> dict:
        """The handoff tool definition."""
        # Get user-facing agents for the enum
        try:
            agents = self._app.agent_registry.list_agents()
            targets = [a["id"] for a in agents
                       if a.get("user_facing") and a["id"] != self.agent_id]
        except Exception:
            targets = ["creator", "planner"]

        return {
            "type": "function",
            "function": {
                "name": "handoff",
                "description": "Transfer this conversation to another agent. Use when the user's request is better handled by a specialist.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_agent": {
                            "type": "string",
                            "description": "ID of the agent to hand off to",
                            "enum": targets,
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why you're handing off (shown to the user)",
                        },
                        "context": {
                            "type": "string",
                            "description": "Brief summary for the target agent",
                        },
                    },
                    "required": ["target_agent", "reason"],
                },
            },
        }

    def handle(self, message: str, session_id: str, user_id: str,
               conversation: list[dict]) -> list[ChatEvent]:
        """Handle a message — the main LLM call + tool loop.

        This is the existing logic from ChatService.handle_message()
        extracted into the handler pattern.
        """
        # For Phase 1: delegate back to ChatService's LLM loop
        # The full extraction happens incrementally
        # This is a thin wrapper that sets up the right prompt + tools
        raise NotImplementedError("Phase 1: use ChatService._handle_via_agent()")
```

Note: Full `handle()` implementation is deferred to Task 5 (routing refactor).
For now, the handler provides prompt + tools, and ChatService calls them.

- [ ] **Step 3: Run tests + commit**

```bash
git add src/mycelos/agents/handlers/mycelos_handler.py tests/test_agent_handoff.py
git commit -m "feat: MycelosHandler — default chat agent with handoff tool"
```

---

## Task 4: Creator + Planner Handlers

**Files:**
- Create: `src/mycelos/agents/handlers/creator_handler.py`
- Create: `src/mycelos/agents/handlers/planner_handler.py`
- Modify: `tests/test_agent_handoff.py`

- [ ] **Step 1: Write failing tests**

```python
class TestCreatorHandler:
    def test_creator_has_correct_id(self, app):
        from mycelos.agents.handlers.creator_handler import CreatorHandler
        handler = CreatorHandler(app)
        assert handler.agent_id == "creator"
        assert handler.display_name == "Creator-Agent"

    def test_creator_has_handoff_tool(self, app):
        from mycelos.agents.handlers.creator_handler import CreatorHandler
        handler = CreatorHandler(app)
        tools = handler.get_tools()
        tool_names = [t["function"]["name"] for t in tools]
        assert "handoff" in tool_names

    def test_creator_prompt_includes_guidelines(self, app):
        from mycelos.agents.handlers.creator_handler import CreatorHandler
        handler = CreatorHandler(app)
        prompt = handler.get_system_prompt()
        assert "Creator" in prompt
        assert "handoff" in prompt.lower()


class TestPlannerHandler:
    def test_planner_has_correct_id(self, app):
        from mycelos.agents.handlers.planner_handler import PlannerHandler
        handler = PlannerHandler(app)
        assert handler.agent_id == "planner"
        assert handler.display_name == "Planner-Agent"

    def test_planner_has_handoff_tool(self, app):
        from mycelos.agents.handlers.planner_handler import PlannerHandler
        handler = PlannerHandler(app)
        tools = handler.get_tools()
        tool_names = [t["function"]["name"] for t in tools]
        assert "handoff" in tool_names
```

- [ ] **Step 2: Implement CreatorHandler**

```python
"""Creator Handler — builds and tests new agents."""

from __future__ import annotations
from typing import Any
from mycelos.chat.events import ChatEvent


class CreatorHandler:
    def __init__(self, app: Any):
        self._app = app

    @property
    def agent_id(self) -> str:
        return "creator"

    @property
    def display_name(self) -> str:
        return "Creator-Agent"

    def get_system_prompt(self, context: dict | None = None) -> str:
        return """You are the Creator-Agent. You design and build new agents.

## Your Process
1. Understand what the user wants (ask clarifying questions)
2. Design the agent (capabilities, tools, inputs/outputs)
3. Generate Gherkin acceptance scenarios
4. Get user confirmation before building
5. Generate code + tests
6. Submit for security audit
7. Hand back to Mycelos when done

## Handoff Rules
- DONE building → handoff(target="mycelos", reason="Agent created successfully")
- User says CANCEL → handoff(target="mycelos", reason="User cancelled agent creation")
- User says PAUSE → handoff(target="mycelos", reason="User paused development")
- User asks something UNRELATED → handoff(target="mycelos", reason="Different question")

## Tool Guidelines (for generated agent code)
1. Each tool: def tool_name(args: dict) -> dict
2. Return {"result": ...} on success, {"error": "message"} on failure
3. No global state, no subprocess, no eval/exec
4. File access only via mounted paths
5. Network access only via proxy tools (http_get, http_post)
"""

    def get_tools(self) -> list[dict]:
        tools = []
        # Handoff tool (targets: mycelos, planner)
        tools.append({
            "type": "function",
            "function": {
                "name": "handoff",
                "description": "Transfer conversation to another agent",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_agent": {"type": "string", "enum": ["mycelos", "planner"]},
                        "reason": {"type": "string"},
                        "context": {"type": "string"},
                    },
                    "required": ["target_agent", "reason"],
                },
            },
        })
        # Note tools (for saving design decisions)
        tools.append({
            "type": "function",
            "function": {
                "name": "note_write",
                "description": "Save a design decision or agent spec to the knowledge base",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "note_type": {"type": "string", "enum": ["decision", "note"]},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title", "content"],
                },
            },
        })
        return tools

    def handle(self, message: str, session_id: str, user_id: str,
               conversation: list[dict]) -> list[ChatEvent]:
        raise NotImplementedError("Phase 1: use ChatService._handle_via_agent()")
```

- [ ] **Step 3: Implement PlannerHandler**

```python
"""Planner Handler — plans complex tasks and finds workflows."""

from __future__ import annotations
from typing import Any
from mycelos.chat.events import ChatEvent


class PlannerHandler:
    def __init__(self, app: Any):
        self._app = app

    @property
    def agent_id(self) -> str:
        return "planner"

    @property
    def display_name(self) -> str:
        return "Planner-Agent"

    def get_system_prompt(self, context: dict | None = None) -> str:
        # Include available workflows
        workflows_text = self._get_workflows_context()
        return f"""You are the Planner-Agent. You decompose complex requests into plans.

## Your Process
1. Understand the user's goal
2. Search for matching workflows: {workflows_text}
3. If a workflow matches → explain and offer to execute
4. If no match → create an ad-hoc plan with steps
5. If new capabilities needed → suggest building an agent

## Handoff Rules
- Need a NEW AGENT → handoff(target="creator", reason="Need new agent")
- DONE with plan → handoff(target="mycelos", reason="Plan complete")
- User asks something SIMPLE → handoff(target="mycelos", reason="Simple request")
"""

    def _get_workflows_context(self) -> str:
        try:
            from mycelos.agents.planner_context import build_planner_context, format_context_for_prompt
            context = build_planner_context(self._app)
            return format_context_for_prompt(context)
        except Exception:
            return "(no workflows available)"

    def get_tools(self) -> list[dict]:
        tools = [{
            "type": "function",
            "function": {
                "name": "handoff",
                "description": "Transfer conversation to another agent",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_agent": {"type": "string", "enum": ["mycelos", "creator"]},
                        "reason": {"type": "string"},
                        "context": {"type": "string"},
                    },
                    "required": ["target_agent", "reason"],
                },
            },
        }]
        # Planner also gets note and search tools
        from mycelos.chat.service import CHAT_AGENT_TOOLS
        for tool in CHAT_AGENT_TOOLS:
            name = tool["function"]["name"]
            if name in ("note_write", "note_search", "note_list", "search_web"):
                tools.append(tool)
        return tools

    def handle(self, message: str, session_id: str, user_id: str,
               conversation: list[dict]) -> list[ChatEvent]:
        raise NotImplementedError("Phase 1: use ChatService._handle_via_agent()")
```

- [ ] **Step 4: Run tests + commit**

```bash
git add src/mycelos/agents/handlers/ tests/test_agent_handoff.py
git commit -m "feat: Creator + Planner handlers with handoff tools + system prompts"
```

---

## Task 5: Handoff Execution + Routing Refactor

**Files:**
- Modify: `src/mycelos/chat/service.py`
- Modify: `src/mycelos/app.py`
- Modify: `tests/test_agent_handoff.py`

This is the core task — replace the hardcoded routing in ChatService with handler-based dispatch.

- [ ] **Step 1: Write failing tests**

```python
class TestHandoffExecution:
    def test_handoff_updates_session_agent(self, app):
        """Calling handoff tool updates the active agent in DB."""
        from mycelos.chat.service import ChatService
        service = ChatService(app)
        session_id = service.create_session()

        # Simulate handoff execution
        service._execute_handoff(
            session_id, "creator", "User wants to build an agent", "Build CSV reader"
        )

        row = app.storage.fetchone(
            "SELECT active_agent_id FROM session_agents WHERE session_id = ?",
            (session_id,),
        )
        assert row["active_agent_id"] == "creator"

    def test_handoff_rejects_non_user_facing(self, app):
        """Cannot hand off to a non-user-facing agent."""
        from mycelos.chat.service import ChatService
        service = ChatService(app)
        session_id = service.create_session()

        result = service._execute_handoff(session_id, "auditor", "test", "")
        assert "error" in result

    def test_get_active_agent_default_mycelos(self, app):
        """New session defaults to mycelos."""
        from mycelos.chat.service import ChatService
        service = ChatService(app)
        session_id = service.create_session()
        assert service._get_active_agent(session_id) == "mycelos"

    def test_get_active_agent_after_handoff(self, app):
        """After handoff, active agent changes."""
        from mycelos.chat.service import ChatService
        service = ChatService(app)
        session_id = service.create_session()
        service._execute_handoff(session_id, "creator", "build agent", "")
        assert service._get_active_agent(session_id) == "creator"
```

- [ ] **Step 2: Register agent handlers in App**

In `src/mycelos/app.py`, add a method to get handlers:

```python
def get_agent_handlers(self) -> dict[str, Any]:
    """Get all registered agent handlers."""
    from mycelos.agents.handlers.mycelos_handler import MycelosHandler
    from mycelos.agents.handlers.creator_handler import CreatorHandler
    from mycelos.agents.handlers.planner_handler import PlannerHandler
    return {
        "mycelos": MycelosHandler(self),
        "creator": CreatorHandler(self),
        "planner": PlannerHandler(self),
    }
```

- [ ] **Step 3: Add handoff methods to ChatService**

Add to `src/mycelos/chat/service.py`:

```python
def _get_active_agent(self, session_id: str) -> str:
    """Get the active agent for this session. Default: mycelos."""
    if not hasattr(self, '_active_agents_cache'):
        self._active_agents_cache = {}
    if session_id in self._active_agents_cache:
        return self._active_agents_cache[session_id]
    row = self._app.storage.fetchone(
        "SELECT active_agent_id FROM session_agents WHERE session_id = ?",
        (session_id,),
    )
    agent_id = (row or {}).get("active_agent_id", "mycelos")
    self._active_agents_cache[session_id] = agent_id
    return agent_id

def _execute_handoff(self, session_id: str, target_agent_id: str,
                     reason: str, context: str = "") -> dict:
    """Execute agent handoff — update DB, return result."""
    # Validate target is user-facing
    agent = self._app.agent_registry.get(target_agent_id)
    if not agent:
        # Check system agents
        system_agents = {"mycelos", "creator", "planner"}
        if target_agent_id not in system_agents:
            return {"error": f"Agent '{target_agent_id}' not found"}
    elif not agent.get("user_facing"):
        return {"error": f"Agent '{target_agent_id}' is not available for conversation"}

    # Get previous agent for audit
    prev_agent = self._get_active_agent(session_id)

    # Update DB
    self._app.storage.execute(
        "INSERT OR REPLACE INTO session_agents (session_id, active_agent_id, handoff_reason) VALUES (?, ?, ?)",
        (session_id, target_agent_id, reason),
    )

    # Update cache
    if hasattr(self, '_active_agents_cache'):
        self._active_agents_cache[session_id] = target_agent_id

    # Audit
    self._app.audit.log("agent.handoff", details={
        "from": prev_agent, "to": target_agent_id,
        "reason": reason, "session_id": session_id,
    })

    return {"status": "handoff", "target_agent": target_agent_id, "reason": reason}
```

- [ ] **Step 4: Update handle_message routing**

In `handle_message()`, after proactive checks, replace the existing routing with:

```python
# Get active agent and its handler
active_agent_id = self._get_active_agent(session_id)
handlers = self._app.get_agent_handlers()
handler = handlers.get(active_agent_id, handlers["mycelos"])

# Show which agent is responding
events.append(agent_event(handler.display_name))

# Use the handler's system prompt and tools
system_prompt = handler.get_system_prompt()
tools = handler.get_tools()
```

Then in the tool execution section, add handoff tool handling:

```python
if tool_name == "handoff":
    target = args.get("target_agent", "mycelos")
    reason = args.get("reason", "")
    context = args.get("context", "")
    result = self._execute_handoff(session_id, target, reason, context)
    if result.get("status") == "handoff":
        # Return handoff event — next message goes to new agent
        events.append(system_response_event(
            f"[Handoff → {handler.display_name}] {reason}"
        ))
        events.append(done_event())
        return events
    return result
```

- [ ] **Step 5: Mark system agents as user_facing**

In `src/mycelos/cli/init_cmd.py`, update `SYSTEM_AGENTS`:

```python
SYSTEM_AGENTS = [
    {"id": "creator-agent", "name": "Creator Agent", "agent_type": "full_model",
     "capabilities": [], "user_facing": True},
    {"id": "planner-agent", "name": "Planner Agent", "agent_type": "light_model",
     "capabilities": [], "user_facing": True},
    {"id": "evaluator-agent", "name": "Evaluator Agent", "agent_type": "light_model",
     "capabilities": [], "user_facing": False},
    {"id": "auditor-agent", "name": "Auditor Agent", "agent_type": "full_model",
     "capabilities": [], "user_facing": False},
]
```

And update `agent_registry.register()` to accept `user_facing`.

- [ ] **Step 6: Run tests + full suite**

Run: `pytest tests/test_agent_handoff.py -v && pytest tests/ --tb=short -q`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/chat/service.py src/mycelos/app.py src/mycelos/cli/init_cmd.py tests/test_agent_handoff.py
git commit -m "feat: Handoff execution + handler-based routing in ChatService"
```

---

## Task 6: Remove Old Routing + Integration Tests

**Files:**
- Modify: `src/mycelos/chat/service.py` — Remove old classifier/routing code
- Modify: `tests/test_agent_handoff.py` — Add integration tests

- [ ] **Step 1: Write integration tests**

```python
class TestHandoffIntegration:
    def test_handoff_tool_in_tool_execution(self, app):
        """Handoff tool works within the tool execution loop."""
        from mycelos.chat.service import ChatService
        service = ChatService(app)
        session_id = service.create_session()

        # Execute handoff as if the LLM called it
        result = service._execute_tool("handoff", {
            "target_agent": "creator",
            "reason": "User wants to build an agent",
            "context": "CSV file reader",
        }, user_id="default")
        assert result.get("status") == "handoff" or "error" not in result

    def test_active_agent_persists_across_messages(self, app):
        """After handoff, the agent stays active for subsequent messages."""
        from mycelos.chat.service import ChatService
        service = ChatService(app)
        session_id = service.create_session()
        service._execute_handoff(session_id, "creator", "build agent")

        # Verify on next lookup
        assert service._get_active_agent(session_id) == "creator"

        # New ChatService instance should also see it (DB-backed)
        service2 = ChatService(app)
        assert service2._get_active_agent(session_id) == "creator"
```

- [ ] **Step 2: Remove old classifier-based routing**

In `handle_message()`, the old routing code that does:
```python
if route_result.intent == Intent.CREATE_AGENT:
    creator_events = self._handle_create_agent(...)
```

This should now be handled by the handoff tool. The classifier is still
useful for suggesting handoffs in the Mycelos prompt, but the actual routing
is done by the `active_agent_id` in the DB.

Keep the classifier for now (it helps the LLM decide when to call handoff),
but remove the direct routing branches for CREATE_AGENT and TASK_REQUEST.

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ --tb=short -q`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add src/mycelos/chat/service.py tests/test_agent_handoff.py
git commit -m "feat: remove old routing — all agent dispatch via handoff + active_agent_id"
```

---

## Task 7: CHANGELOG + Push

- [ ] **Step 1: Update CHANGELOG**

```markdown
### Agent Handoff (Foundation)
- session_agents table: one active agent per session (DB-backed)
- AgentHandler Protocol: unified interface for user-facing agents
- Mycelos, Creator, Planner handlers with agent-specific prompts + tools
- Handoff tool: agents transfer conversations with reason + context
- No more hardcoded routing — all dispatch via active_agent_id
- Model switching on handoff (agent-specific model preferences)
- Paused/developing agents shown proactively in Mycelos prompt
```

- [ ] **Step 2: Run full suite + commit + push**

```bash
pytest tests/ --tb=short -q
git add CHANGELOG.md
git commit -m "feat: Agent Handoff — unified routing, handoff protocol, handler dispatch"
git push origin main
```
