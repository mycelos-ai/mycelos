# Agent Handoff & Lifecycle — Design Specification

**Date:** 2026-03-26
**Status:** DRAFT
**Scope:** Unified agent routing, handoff protocol, agent lifecycle states

---

## 1. Overview

Every conversation has exactly one active agent at any time. The active
agent is stored in the database. Handoff is a first-class operation —
agents transfer conversations to each other with a reason. No hardcoded
routing, no if-else chains.

### Core Principles

1. **One active agent per session** — stored in DB, not memory
2. **Uniform interface** — every agent implements the same `handle()` method
3. **Handoff as a tool** — agents decide when to hand off via their prompt
4. **Agent lifecycle** — agents have states: proposed → developing → auditing → active → paused → deprecated
5. **User can always interrupt** — abort, pause, or switch back to Mycelos

---

## 2. Session Active Agent

### Database Change

Add `active_agent_id` column to the session tracking:

```sql
-- In sessions or conversations table (or as a new lookup)
ALTER TABLE ... ADD COLUMN active_agent_id TEXT NOT NULL DEFAULT 'mycelos';
```

Or if sessions are file-based (JSONL), store in memory_entries:

```python
# Per-session active agent tracking
app.memory.set(user_id, "session", f"active_agent.{session_id}", "mycelos")
```

### Lookup

```python
def _get_active_agent(self, session_id: str) -> str:
    """Get the active agent for this session. Default: mycelos."""
    # Fast path: in-memory cache
    if session_id in self._active_agents:
        return self._active_agents[session_id]
    # DB lookup
    stored = self._app.storage.fetchone(
        "SELECT active_agent_id FROM session_agents WHERE session_id = ?",
        (session_id,),
    )
    agent_id = (stored or {}).get("active_agent_id", "mycelos")
    self._active_agents[session_id] = agent_id
    return agent_id
```

---

## 3. Unified Agent Interface

Every user-facing agent implements the same interface:

```python
class AgentHandler(Protocol):
    """Interface for user-facing agents."""

    @property
    def agent_id(self) -> str: ...

    @property
    def display_name(self) -> str: ...

    def handle(self, message: str, session_id: str, user_id: str,
               conversation: list[dict]) -> list[ChatEvent]: ...

    def get_system_prompt(self) -> str: ...

    def get_tools(self) -> list[dict]: ...
```

### Built-in Agent Handlers

| Agent | user_facing | Purpose |
|-------|-------------|---------|
| `mycelos` | true | Default chat agent — conversation, tools, note-taking |
| `creator` | true | Builds new agents — interview, code gen, testing |
| `planner` | true | Plans complex tasks — workflow matching, step creation |
| `auditor` | false | Reviews agent code — security, quality (not user-facing) |
| `evaluator` | false | Scores agent output quality (not user-facing) |

### Agent Registry: user_facing Flag

```sql
-- Already in agents table, add:
ALTER TABLE agents ADD COLUMN user_facing INTEGER NOT NULL DEFAULT 0;
```

System agents registered at init:

```python
SYSTEM_AGENTS = [
    {"id": "mycelos", "name": "Mycelos", "user_facing": True,
     "description": "Your personal AI assistant"},
    {"id": "creator", "name": "Creator-Agent", "user_facing": True,
     "description": "Builds and tests new agents"},
    {"id": "planner", "name": "Planner-Agent", "user_facing": True,
     "description": "Plans complex tasks and finds workflows"},
    {"id": "auditor", "name": "Auditor-Agent", "user_facing": False,
     "description": "Reviews agent code for security"},
    {"id": "evaluator", "name": "Evaluator-Agent", "user_facing": False,
     "description": "Evaluates agent output quality"},
]
```

---

## 4. Handoff Protocol

### Handoff Tool

Every user-facing agent has a `handoff` tool in its tool list:

```json
{
  "name": "handoff",
  "description": "Transfer this conversation to another agent. Use when the user's request is better handled by a specialist.",
  "parameters": {
    "target_agent": {
      "type": "string",
      "description": "ID of the agent to hand off to",
      "enum": ["mycelos", "creator", "planner"]
    },
    "reason": {
      "type": "string",
      "description": "Why you're handing off (shown to the user)"
    },
    "context": {
      "type": "string",
      "description": "Brief summary of what the user wants (for the target agent)"
    }
  }
}
```

The `enum` is populated dynamically from user-facing agents in the registry.

### Handoff Execution

```python
def _execute_handoff(self, session_id, target_agent_id, reason, context):
    """Execute agent handoff — update DB, show transition to user."""

    # 1. Validate target is user-facing
    agent = self._app.agent_registry.get(target_agent_id)
    if not agent or not agent.get("user_facing"):
        return {"error": f"Agent '{target_agent_id}' is not available for conversation"}

    # 2. Update active agent in DB
    self._app.storage.execute(
        "INSERT OR REPLACE INTO session_agents (session_id, active_agent_id) VALUES (?, ?)",
        (session_id, target_agent_id),
    )
    self._active_agents[session_id] = target_agent_id

    # 3. Audit
    self._app.audit.log("agent.handoff", details={
        "from": self._get_previous_agent(session_id),
        "to": target_agent_id,
        "reason": reason,
        "session_id": session_id,
    })

    # 4. Return handoff event
    return {
        "status": "handoff",
        "target_agent": target_agent_id,
        "reason": reason,
    }
```

### What the User Sees

```
You: "I want to build an agent that reads CSV files"

Mycelos: "That's a job for the Creator! Let me hand you over."
  → [Handoff: Creator-Agent]
  → Reason: "User wants to build a new agent for CSV processing"

Creator-Agent: "Great! Let's design your CSV agent together.
  What kind of CSV files will it process?"
```

### Handoff Back

When the Creator is done (or user says "cancel"):

```
Creator-Agent: "Your CSV-Reader agent is built and tested! ✓
  Handing you back to Mycelos."
  → [Handoff: Mycelos]
  → Reason: "Agent creation completed"

Mycelos: "Welcome back! Your CSV-Reader agent is ready.
  Want to try it out?"
```

---

## 5. Message Routing (No If-Else)

```python
def handle_message(self, message, session_id, user_id):
    """Route message to the active agent."""

    # Proactive checks (gamification, KB context, task reminders)
    events = self._proactive_checks(message, session_id, user_id)

    # Get active agent
    active_agent_id = self._get_active_agent(session_id)

    # Get the handler for this agent
    handler = self._agent_handlers.get(active_agent_id)
    if not handler:
        # Unknown agent — fall back to mycelos
        handler = self._agent_handlers["mycelos"]
        active_agent_id = "mycelos"

    # Show which agent is responding
    events.append(agent_event(handler.display_name))

    # Let the agent handle the message
    agent_events = handler.handle(message, session_id, user_id, conversation)
    events.extend(agent_events)

    # Check if a handoff happened (agent called the handoff tool)
    # If so, the next message will go to the new agent automatically

    return events
```

---

## 6. Agent Lifecycle States

Agents (especially user-created ones) go through a lifecycle:

```
proposed → developing → auditing → active → paused → deprecated
```

### States

| State | Meaning | User Can Talk To? |
|-------|---------|-------------------|
| `proposed` | Idea captured, not yet built | No |
| `developing` | Creator-Agent is building it | No (Creator handles it) |
| `auditing` | Auditor is reviewing code | No |
| `active` | Ready to use | Yes (if user_facing) |
| `paused` | User paused development | No |
| `deprecated` | No longer used | No |

### Creator → Auditor → Active Flow

```
1. User: "Build me a CSV agent"
   → Handoff to Creator-Agent
   → Agent record created: status="developing"

2. Creator finishes code + tests
   → Creator calls internal tool: submit_for_audit(agent_id)
   → Agent status: "developing" → "auditing"
   → Auditor-Agent reviews (not user-facing, runs in background)

3. Auditor approves
   → Agent status: "auditing" → "active"
   → Agent is now usable
   → Notification to user: "Your CSV-Reader is ready!"

4. Auditor rejects
   → Agent status stays "auditing" with audit notes
   → Creator can fix and resubmit
   → Or user is asked: "The auditor found issues. Fix or abort?"
```

### Interruption & Resume

If the user interrupts during Creator flow:

```
User: "Lass mal, das war eine blöde Idee"
  → Creator calls: handoff(target="mycelos", reason="User cancelled")
  → Agent status: "developing" → "deprecated" (or deleted)

User: "Das ist mir gerade zu teuer, machen wir später weiter"
  → Creator calls: handoff(target="mycelos", reason="User paused")
  → Agent status: "developing" → "paused"
  → Next session: "Du hast einen Agent in Entwicklung: CSV-Reader. Weitermachen?"
```

### Listing Agent States

```
/agent list
  ✓ csv-reader (active) — Reads and summarizes CSV files
  ⏸ email-processor (paused) — Development paused by user
  🔍 invoice-scanner (auditing) — Security review in progress
  📝 report-generator (developing) — Creator is building...
```

---

## 7. Prompts

### Mycelos (Chat-Agent) System Prompt — Handoff Rules

```
You are Mycelos, the user's personal AI assistant.

## When to Hand Off

You have a `handoff` tool. Use it when:

- User wants to BUILD or CREATE a new agent → handoff to "creator"
  Example: "Build me an agent that...", "Create an automation for..."

- User wants a COMPLEX multi-step task that needs planning → handoff to "planner"
  Example: "Research and summarize...", "Analyze my repos and..."

- User asks about an agent that's in development → handoff to "creator"
  Example: "How's my CSV agent coming along?"

## When NOT to Hand Off

Stay in the conversation when:
- Simple questions, greetings, chat
- Note-taking, task management (use your tools)
- Searching, web lookup (use your tools)
- File analysis (use your tools)
- Anything you can handle with your tools

## Agents in Development

{paused_agents_context}

If there are paused or in-development agents, mention them naturally:
"By the way, you have a paused agent 'csv-reader'. Want to continue?"
```

### Creator-Agent System Prompt

```
You are the Creator-Agent. You design and build new agents through
structured conversation.

## Your Process
1. Understand what the user wants (ask clarifying questions)
2. Design the agent (capabilities, inputs, outputs)
3. Generate Gherkin scenarios for acceptance testing
4. Get user confirmation
5. Build the agent (code generation, testing)
6. Submit for security audit
7. Hand back to Mycelos when done

## Handoff Rules

- When you're DONE building → handoff(target="mycelos", reason="Agent created")
- When the user says CANCEL → handoff(target="mycelos", reason="User cancelled")
- When the user says PAUSE → handoff(target="mycelos", reason="User paused")
- When the user asks something UNRELATED → handoff(target="mycelos",
  reason="User has a different question")

## Available Tools
- handoff: transfer conversation
- note_write: save design decisions to KB
- (code generation tools when building)
```

### Planner-Agent System Prompt

```
You are the Planner-Agent. You decompose complex requests into
executable plans using existing workflows and agents.

## Your Process
1. Understand the user's goal
2. Search for matching workflows (you receive the list)
3. If a workflow matches → explain and execute it
4. If no match → create an ad-hoc plan with steps
5. If new capabilities needed → suggest creating an agent

## Handoff Rules

- When the user needs a NEW AGENT → handoff(target="creator",
  reason="Need to build a new agent")
- When you're DONE with the plan → handoff(target="mycelos",
  reason="Plan executed")
- When the user asks something SIMPLE → handoff(target="mycelos",
  reason="Simple request, no planning needed")
```

---

## 8. Schema Changes

### New Table: session_agents

```sql
CREATE TABLE IF NOT EXISTS session_agents (
    session_id      TEXT PRIMARY KEY,
    active_agent_id TEXT NOT NULL DEFAULT 'mycelos',
    handoff_reason  TEXT,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
```

### Agent Table: New Columns

```sql
ALTER TABLE agents ADD COLUMN user_facing INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agents ADD COLUMN system_prompt TEXT;
```

---

## 9. Paused/In-Progress Agent Context

At the start of each session, the system checks for agents that are not
in a terminal state:

```python
def _get_pending_agents_context(self, user_id):
    """Build context about agents in non-terminal states."""
    agents = self._app.agent_registry.list_agents()
    pending = []
    for a in agents:
        if a["status"] in ("developing", "paused", "auditing"):
            pending.append(f"- {a['name']} ({a['status']}): {a.get('description', '')}")
    if pending:
        return "Agents in progress:\n" + "\n".join(pending)
    return ""
```

This is injected into the Mycelos system prompt so it can proactively
mention paused agents: "You have a paused agent 'csv-reader'. Continue?"

---

## 10. Audit Flow

### When Creator Finishes

```python
def _submit_for_audit(self, agent_id):
    """Submit an agent for security audit."""
    self._app.agent_registry.set_status(agent_id, "auditing")
    # Dispatch background audit task
    self._app.task_runner.dispatch(
        "agent_audit",
        {"agent_id": agent_id},
        agent_id="auditor",
    )
```

### Auditor Reviews (Background)

The Auditor-Agent runs in the background (not user-facing):
1. Reads the agent's code, tests, and Gherkin scenarios
2. Checks for security violations (credential access, sandbox escape, etc.)
3. Checks Constitution compliance
4. Returns: APPROVED or REJECTED with reasons

```python
# On approval:
app.agent_registry.set_status(agent_id, "active")
app.audit.log("agent.audit.approved", details={...})
# Notify user via proactive notification

# On rejection:
app.audit.log("agent.audit.rejected", details={...})
# Keep status "auditing", add audit_notes to agent record
# Notify user: "Auditor found issues with your CSV agent"
```

---

## 11. Testing Strategy

### Unit Tests

- Handoff tool: validates target is user-facing, updates DB
- Active agent lookup: default mycelos, updates on handoff
- Agent lifecycle: proposed → developing → auditing → active
- Paused agent context: shows in system prompt

### Integration Tests (Real LLM)

- "Build an agent" → Creator handoff happens
- Creator interview → handoff back to Mycelos when done
- "Cancel" during Creator → handoff back, agent deprecated
- "Pause" → agent paused, shown in next session
- Planner handoff → workflow executed → handoff back

---

## 12. Scope Boundary

### In Scope

- `session_agents` table with active_agent_id
- `user_facing` flag on agents table
- `handoff` tool for all user-facing agents
- Unified `handle()` routing (no if-else)
- Agent lifecycle states (proposed → ... → active)
- Creator → Auditor → Active flow
- Pause/resume agent development
- Proactive "you have paused agents" context
- System prompts with handoff rules for Mycelos, Creator, Planner

---

## 13. Agent-Specific Tools

### Built-in Agent Tools

Each agent has its own tool set, defined by its role:

| Agent | Tools |
|-------|-------|
| Mycelos | note_write, note_read, search_web, http_get, file_analyze, etc. |
| Creator | generate_code, run_tests, submit_for_audit, handoff |
| Planner | search_workflows, create_plan, execute_workflow, handoff |
| Custom Agent | tools defined by the Creator during build |

### Custom Agent Tools

When the Creator builds a new agent, it generates:

1. **Tool implementations** — Python functions in `~/.mycelos/agents/<name>/tools.py`
2. **Tool schemas** — JSON Schema for LLM function calling
3. **Tests** — Unit tests for each tool

```python
# ~/.mycelos/agents/csv-reader/tools.py

def read_csv(args: dict) -> dict:
    """Read and parse a CSV file.

    Args:
        path: Path to the CSV file
        max_rows: Maximum rows to read (default: 100)

    Returns:
        {"headers": [...], "rows": [...], "row_count": N}
    """
    import csv
    from pathlib import Path

    path = Path(args["path"])
    max_rows = args.get("max_rows", 100)

    with open(path, newline="", errors="replace") as f:
        reader = csv.DictReader(f)
        rows = []
        for i, row in enumerate(reader):
            if i >= max_rows:
                break
            rows.append(dict(row))

    return {
        "headers": list(rows[0].keys()) if rows else [],
        "rows": rows,
        "row_count": len(rows),
    }
```

### Tool Schema Template

The Creator generates a JSON schema for each tool:

```json
{
  "name": "read_csv",
  "description": "Read and parse a CSV file",
  "parameters": {
    "type": "object",
    "properties": {
      "path": {"type": "string", "description": "Path to the CSV file"},
      "max_rows": {"type": "integer", "description": "Max rows (default: 100)"}
    },
    "required": ["path"]
  }
}
```

### Creator System Prompt: Tool Guidelines

```
When generating tools for an agent, follow this template:

1. Each tool is a Python function: def tool_name(args: dict) -> dict
2. Docstring describes what the tool does (shown to the LLM)
3. Args come as a dict matching the JSON schema
4. Return a dict with the result
5. Handle errors gracefully: return {"error": "message"} not raise
6. No global state — each call is independent
7. File access: use Path from args, check mount access
8. Network access: use http_get/http_post tools, not raw requests
9. Keep tools focused: one responsibility per tool
10. Include type hints in docstring for clarity
```

### Loading Custom Tools at Runtime

Custom agent tools are loaded via `importlib` — no restart needed:

```python
def _load_agent_tools(self, agent_id: str) -> list[dict]:
    """Load custom tools for an agent from ~/.mycelos/agents/<id>/tools.py"""
    import importlib.util

    tools_path = self._app.data_dir / "agents" / agent_id / "tools.py"
    if not tools_path.exists():
        return []

    spec = importlib.util.spec_from_file_location(f"agent_{agent_id}_tools", tools_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Load schema
    schema_path = tools_path.parent / "tools_schema.json"
    if schema_path.exists():
        schemas = json.loads(schema_path.read_text())
    else:
        schemas = []

    # Register tools
    tools = []
    for schema in schemas:
        handler = getattr(module, schema["name"], None)
        if handler:
            tools.append({
                "schema": schema,
                "handler": handler,
            })

    return tools
```

### Security: Tool Sandbox

Custom tools run with restrictions:
- File access: only within mounted directories (MountRegistry check)
- Network access: only via SecurityProxy (http_get/http_post)
- No direct credential access
- No `os.system`, `subprocess`, `eval`, or `exec`
- The Auditor checks for these violations before approval

---

## 14. Model Switching on Handoff

Each agent can have its own preferred model:

```python
def _get_model_for_agent(self, agent_id: str) -> str:
    """Get the LLM model assigned to this agent."""
    models = self._app.model_registry.resolve_models(agent_id, "execution")
    if models:
        return models[0]
    # Fallback to system default
    config = self._app.config.get_active_config() or {}
    return config.get("default_model", "anthropic/claude-sonnet-4-6")
```

On handoff, the model switches automatically:

```python
# In agent.handle():
model = self._get_model_for_agent(self.agent_id)
response = self._app.llm.complete(conversation, model=model, tools=tools)
```

Default model assignments:

| Agent | Default Model | Why |
|-------|--------------|-----|
| Mycelos | Sonnet | Good balance of speed + quality |
| Creator | Opus | Needs best code generation |
| Planner | Haiku | Fast planning, cheap |
| Custom | Haiku | Cost-efficient for simple tasks |

---

## 15. MCP Tool Requests

Creator and Planner can request MCP tools for the agents they build:

```python
# Creator prompt includes:
"If the agent needs external services, you can request MCP tools:
- Use `connector_tools` to discover available MCP tools
- Use `connector_call` to test them
- Include required connectors in the agent's capabilities list

Example: If building an agent that reads GitHub issues, request:
  capabilities: ['github.read']
  required_connectors: ['github']"
```

The agent's capability list determines which MCP tools it can access.
This is already enforced by the CapabilityTokenManager.

---

## 16. Custom Agents as MCP Servers

### Architecture

Custom agents built by the Creator run as **MCP server subprocesses**.
This gives them:
- Process isolation (crash doesn't kill the Gateway)
- Standard protocol (MCP — same as GitHub, Filesystem connectors)
- Tool discovery (the Gateway discovers agent tools via MCP)
- Consistent with existing connector architecture

```
┌───────────────────────────────────────────────────┐
│ Gateway                                            │
│                                                    │
│  ┌─────────────────────────────────────┐           │
│  │ CSV-Reader Agent (MCP Subprocess)   │           │
│  │  Own tools:                         │           │
│  │   - read_csv (local logic)          │           │
│  │   - summarize_data (local logic)    │           │
│  │  Proxy tools (via Gateway):         │           │
│  │   - gmail.send ──────────────────────┼──→ SecurityProxy → Gmail MCP
│  │   - http.get ────────────────────────┼──→ SecurityProxy → SSRF filter
│  └─────────────────────────────────────┘           │
│                                                    │
│  CapabilityTokenManager                            │
│   └─ CSV-Reader: filesystem.read, gmail.send       │
│                                                    │
│  PolicyEngine                                      │
│   └─ gmail.send: "always" for csv-reader           │
└───────────────────────────────────────────────────┘
```

### How It Works

**1. Agent Creation (Creator builds it)**

The Creator generates:
- `~/.mycelos/agents/csv-reader/server.py` — MCP server implementation
- `~/.mycelos/agents/csv-reader/tools.py` — Tool implementations
- `~/.mycelos/agents/csv-reader/tools_schema.json` — Tool schemas
- `~/.mycelos/agents/csv-reader/tests/` — Test files

The MCP server template:

```python
# ~/.mycelos/agents/csv-reader/server.py
"""CSV-Reader Agent — MCP server for reading and summarizing CSV files."""

from mcp.server import Server
from mcp.types import Tool, TextContent

from tools import read_csv, summarize_data

server = Server("csv-reader")

@server.list_tools()
async def list_tools():
    return [
        Tool(name="read_csv", description="Read a CSV file",
             inputSchema={...}),
        Tool(name="summarize_data", description="Summarize CSV data",
             inputSchema={...}),
    ]

@server.call_tool()
async def call_tool(name, arguments):
    if name == "read_csv":
        result = read_csv(arguments)
    elif name == "summarize_data":
        result = summarize_data(arguments)
    else:
        return [TextContent(text=f"Unknown tool: {name}")]
    return [TextContent(text=json.dumps(result))]

if __name__ == "__main__":
    import asyncio
    from mcp.server.stdio import stdio_server
    asyncio.run(server.run(stdio_server()))
```

**2. Agent Start (on first use or Gateway boot)**

When the agent is needed, the Gateway starts it as a subprocess:

```python
# Same pattern as MCP connectors:
process = subprocess.Popen(
    [sys.executable, str(agent_path / "server.py")],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    env=sandbox_env,  # Stripped env — no credentials, no proxy socket
)
```

The agent subprocess has:
- NO access to `MYCELOS_MASTER_KEY`
- NO access to SecurityProxy socket
- NO access to `ANTHROPIC_API_KEY` or any credential
- Only: `PATH`, `HOME`, `MYCELOS_AGENT_ID`, `MYCELOS_AGENT_DIR`

**3. Proxy Tools (calling external services)**

When the agent needs an external tool (e.g., `gmail.send`), it cannot
call it directly. Instead, the Gateway provides proxy tools.

The Gateway intercepts tool calls from the agent and routes them:

```python
# Gateway receives MCP tool call from agent subprocess
tool_call = await mcp_session.call_tool("gmail.send", args)

# Gateway checks:
# 1. Does csv-reader have "gmail.send" capability? → CapabilityTokenManager
# 2. Is the policy "always"? → PolicyEngine
# 3. If yes → route through SecurityProxy → Gmail MCP
# 4. Return result to agent subprocess
```

This means the agent's MCP server only implements its OWN tools.
External tools are transparent proxies — the agent calls them as if
they were local, but the Gateway intercepts and routes them securely.

**4. Tool Manifest**

Each agent has a manifest that declares what it needs:

```yaml
# ~/.mycelos/agents/csv-reader/manifest.yaml
name: csv-reader
description: Reads and summarizes CSV files
version: 1
user_facing: false
capabilities:
  own:
    - csv.read
    - csv.summarize
  required:
    - filesystem.read
    - gmail.send      # optional, for sending summaries
model: haiku          # default model tier
```

The `required` capabilities are registered when the agent is activated.
The Gateway checks these before starting the agent.

### Security Guarantees

| Attack | Defense |
|--------|---------|
| Agent tries to read credentials | Subprocess env is stripped — no keys |
| Agent tries to call SecurityProxy | No socket path in env |
| Agent tries to access unmounted paths | Gateway checks MountRegistry before proxying filesystem calls |
| Agent calls external API directly | No network access — stripped env, no proxy |
| Agent calls unauthorized MCP tool | Gateway checks CapabilityTokenManager before proxying |
| Malicious code in agent | Auditor reviews before activation; LocalSandbox restricts |

### Lifecycle with MCP

```
1. Creator builds agent → generates server.py + tools.py + manifest.yaml
2. Auditor reviews code → checks for prohibited patterns
3. Agent activated → registered in DB with capabilities
4. First use → Gateway starts MCP subprocess
5. User interacts → Gateway forwards tool calls
6. External tools → Gateway proxies through SecurityProxy
7. Agent idle → subprocess killed after timeout
8. Agent deprecated → subprocess stopped, files kept for reference
```

---

## 17. Scope Boundary (Updated)

### In Scope

- `session_agents` table with active_agent_id
- `user_facing` flag on agents table
- `handoff` tool for all user-facing agents
- Unified `handle()` routing (no if-else)
- Agent lifecycle states (proposed → ... → active)
- Creator → Auditor → Active flow
- Pause/resume agent development
- Agent-specific tool loading (importlib)
- Tool generation guidelines in Creator prompt
- Model switching on handoff
- MCP tool requests by Creator/Planner
- Custom tool security audit

### Out of Scope (Future)

- Agent-to-agent direct communication (without user)
- Multi-agent collaboration on one task
- Agent marketplace/sharing
- Docker-based tool sandbox (LocalSandbox for now)
