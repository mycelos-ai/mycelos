# Lazy Tool Discovery

**Status:** Draft
**Date:** 2026-04-09
**Scope:** Context-optimized tool loading for chat agents. Reduces tool-schema token overhead from ~4K (all tools) to ~800 (core + adaptive basis-set), with on-demand discovery for specialized categories. No changes to permissions, MCP, or workflow-agent whitelists.

## Goal

Mycelos loads 30+ tool schemas into every LLM turn, costing ~4K tokens regardless of whether the user talks about knowledge, workflows, or just wants to chat. On local models with 8K–32K context, this eats 15–50% of the window before any conversation even starts.

Lazy Tool Discovery solves this by loading only a small, adaptive set of tools per session and letting the LLM discover additional tool categories on demand — in the same turn, transparently.

## Non-Goals

- Changing the permission system (`ToolPermission.OPEN/STANDARD/PRIVILEGED/BUILDER/SYSTEM`)
- Affecting MCP connector tools (already loaded per-connector)
- Affecting WorkflowAgent allowed_tools whitelists (fixed per workflow definition)
- Affecting the Builder agent (always gets full tool access)
- User-facing configuration of tool categories

## Architecture

### Session-Start Flow

1. Gateway resolves the model for this agent → computes a token budget for tool schemas
2. `core` category tools are always loaded (~400 tokens, not counted against budget)
3. Remaining budget is filled with the agent+user's most-used tools, ranked by `call_count` from the `tool_usage` table
4. New agents or new users with no usage data get a default set: `knowledge_read` + `knowledge_write`
5. The resulting tool list is passed to the LLM as the available tools for this turn

### Budget Calculation

```
budget = min(context_window * 0.05, 4096)
```

5% of the model's context window, capped at 4096 tokens. Examples:

| Model | Context | Budget | Rough capacity |
|---|---|---|---|
| Llama 3 8B (8K) | 8192 | 409 | core + ~2 extra tools |
| Llama 3 70B (32K) | 32768 | 1638 | core + ~8 extra tools |
| Haiku (200K) | 200000 | 4096 | core + full basis set |
| Sonnet (200K) | 200000 | 4096 | core + full basis set |

Token count per tool schema is estimated by `len(json.dumps(schema)) // 4` (conservative approximation). The budget calculation runs once at session start, not per turn.

### Mid-Session Discovery

1. The LLM calls `discover_tools(category="knowledge_manage")`
2. The gateway intercepts the tool call **before** returning to the user
3. The gateway loads the category's tool schemas and adds them to the current tool list
4. The LLM call is retried with the expanded tool list — same messages, same context, but more tools available
5. The LLM now sees the new tools and can call them in this same turn
6. From the user's perspective: one turn, no visible delay

The discovered tools remain available for the rest of the session (they are added to a `session_extra_tools` set). They do not persist across sessions — the next session rebuilds from the basis set.

### Permission Integration

Discovery respects the existing permission system. When an agent discovers a category:

```python
discovered_tools = [
    tool for tool in category_tools
    if tool.permission in agent_permissions
]
```

A custom agent with `{OPEN, STANDARD}` permissions that discovers `knowledge_manage` only gets the OPEN and STANDARD tools from that category — not PRIVILEGED ones. The permission filter is the same one `get_tools_for()` already applies.

### Agents That Skip Discovery

- **Builder agent**: always gets full tool access (all permissions). No discovery needed — loads everything.
- **WorkflowAgent**: uses an explicit `allowed_tools` whitelist per workflow definition. Discovery is not available during workflow execution.
- **Knowledge Organizer**: internal system handler, calls Python methods directly. No tool system involved.

Only conversational agents (Mycelos, custom persona agents) use discovery.

## Data Model

New table for usage tracking:

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

Updated on every tool call via a lightweight `UPSERT`:

```sql
INSERT INTO tool_usage (user_id, agent_id, tool_name, call_count, last_used)
VALUES (?, ?, ?, 1, ?)
ON CONFLICT (user_id, agent_id, tool_name)
DO UPDATE SET call_count = call_count + 1, last_used = excluded.last_used;
```

This runs after the tool executes (not before), so it never blocks the call. The query is a single indexed write — negligible overhead.

## Tool Categories

| Category | Loading | Tools |
|---|---|---|
| `core` | Always | `discover_tools`, `handoff`, `memory_read`, `memory_write`, `workflow_list` |
| `knowledge_read` | Basis-set candidate | `note_search`, `note_read`, `note_list`, `topic_list` |
| `knowledge_write` | Basis-set candidate | `note_write`, `note_update`, `note_done`, `note_remind`, `note_move` |
| `knowledge_manage` | Discovery | `topic_create`, `topic_rename`, `topic_merge`, `note_archive`, `note_delete`, `knowledge_stats` |
| `workflows` | Discovery | `workflow_create`, `workflow_run`, `workflow_status` |
| `connectors` | Discovery | `connector_setup`, `connector_list`, `credential_store` |
| `system` | Discovery | `config_list`, `config_rollback`, `agent_create` |
| `email` | Discovery | `email_send`, `email_search`, `email_read` |

**Rule: max 8 tools per category.** When a category grows beyond 8, split it. This keeps discovery payloads bounded and prevents context bloat from a single discover call.

Categories are defined in a `TOOL_CATEGORIES` dict in `tools/registry.py`. Each tool is assigned to exactly one category. A tool's category is independent of its permission level — both filters apply.

## `discover_tools` Tool Schema

```json
{
  "type": "function",
  "function": {
    "name": "discover_tools",
    "description": "Load additional tools for a specific task category. Use when the user requests an action you don't currently have a tool for. The tools become available immediately.",
    "parameters": {
      "type": "object",
      "properties": {
        "category": {
          "type": "string",
          "enum": ["knowledge_manage", "workflows", "connectors", "system", "email"],
          "description": "Tool category to load. Only categories not already loaded are useful."
        }
      },
      "required": ["category"]
    }
  }
}
```

Note: `core`, `knowledge_read`, and `knowledge_write` are not in the enum because they are loaded at session start (either always or via basis set). Discovering an already-loaded category is a no-op.

## System Prompt Changes

The MycelosHandler system prompt gets a new `## Tools` section:

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

This replaces any existing tool-listing instructions in the system prompt. The LLM no longer needs a hardcoded list of available tools in the prompt — it sees them in the tool definitions directly.

## Implementation Scope

### Files to create

- `src/mycelos/tools/categories.py` — `TOOL_CATEGORIES` dict, `get_basis_set(user_id, agent_id, budget)`, `get_category_tools(category, agent_permissions)`

### Files to modify

- `src/mycelos/storage/schema.sql` — add `tool_usage` table
- `src/mycelos/storage/database.py` — add `tool_usage` to migration list
- `src/mycelos/tools/registry.py` — add category assignment per tool, modify `get_tools_for()` to use budget + basis set, add `discover_category()` method, add usage tracking in `execute()`
- `src/mycelos/tools/knowledge.py` — add `category="knowledge_read"` etc. to each `register()` call
- `src/mycelos/tools/workflow.py` — add category assignments
- `src/mycelos/tools/connector.py` — add category assignments
- `src/mycelos/tools/system.py` — add category assignments
- `src/mycelos/tools/email.py` — add category assignments
- `src/mycelos/tools/memory.py` — add category assignments
- `src/mycelos/agents/handlers/mycelos_handler.py` — update system prompt
- `src/mycelos/chat/service.py` — intercept `discover_tools` calls, retry the LLM turn with expanded tools, track `session_extra_tools`

### Files NOT modified

- `src/mycelos/workflows/agent.py` — WorkflowAgent keeps its `allowed_tools` whitelist
- `src/mycelos/agents/handlers/builder_handler.py` — Builder keeps full access
- MCP connector loading — unchanged

## Testing Strategy

### Unit tests

- `budget_for_model(context_window)` returns correct budgets for edge cases (tiny model, huge model, 0)
- `get_basis_set()` with empty `tool_usage` table returns the default set
- `get_basis_set()` with populated usage data returns top-N by call_count within budget
- `get_category_tools()` filters by permission correctly
- Category assignment: every registered tool has exactly one category
- No category exceeds 8 tools

### Integration tests

- Full discovery flow: LLM calls `discover_tools`, retry happens, LLM calls discovered tool in same turn
- Usage tracking: tool call increments counter in DB
- Permission boundary: custom agent discovers a category but only sees OPEN+STANDARD tools

### What we don't test

- Actual token counts (approximation is good enough, exact tokenizer is overkill)
- LLM behavior (whether the LLM actually calls discover_tools when appropriate — that's a prompt quality issue, not a code test)

## Constitution Compliance

- **Audit Everything**: `discover_tools` calls are logged as audit events (`tool.discovered`, details: category + tools loaded). Tool usage tracking is a DB write, not an audit event (too noisy).
- **Security Fail-Closed**: if discovery fails (unknown category, permission denied), the LLM gets an error response and continues with existing tools. No tools are added.
- **Capability Scoping**: discovery respects agent permissions. A custom agent cannot discover tools above its permission level.
- **English Everywhere**: all code, categories, tool names, and comments in English.

## Risks and Mitigations

- **LLM doesn't call discover_tools when it should** → The system prompt instructs it clearly. If the LLM fails to discover, the user can also ask explicitly ("lade die Knowledge-Management-Tools"). Worst case: the user gets "I don't have a tool for that" and learns to ask for discovery.
- **LLM calls discover_tools too eagerly** → The prompt says "only discover when the user's request clearly needs tools you don't have". The retry cost is one extra LLM call — annoying but not destructive.
- **Category grows beyond 8 tools** → The spec says to split. A startup warning logs categories that exceed the limit.
- **Basis set thrashes** (tools enter and leave the set frequently) → call_count is cumulative, not rolling. Stable usage patterns produce stable basis sets. A tool needs sustained usage to enter the set.
- **New user cold start** → Default set (`knowledge_read` + `knowledge_write`) covers the most common first-session needs.
