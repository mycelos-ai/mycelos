# Permission UI — Design Specification

**Date:** 2026-03-25
**Status:** DRAFT
**Scope:** Interactive permission prompts across Terminal, Telegram, Web — agent-scoped

---

## 1. Overview

When an agent needs a permission the user hasn't granted, the system shows
an interactive prompt. The user chooses from 5 agent-scoped options. Each
channel (Terminal, Telegram, Web) renders the prompt natively.

### Core Principle

Permissions are **agent-scoped by default**. Granting Creator-Agent access
to `/Downloads` does not grant other agents the same access. The user can
explicitly choose "all agents" if desired.

### What This Replaces

Current: plain text `Y/A/N/!` prompt with no agent context.
New: Rich formatted prompt showing who is asking, what they need, and why —
with 5 granular scope options rendered natively per channel.

---

## 2. Permission Options

Every permission prompt offers exactly 5 options:

| # | Option | Scope | PolicyEngine Entry |
|---|--------|-------|-------------------|
| 1 | Allow for {agent} (this session) | Session-scoped, gone on restart | None (in-memory set) |
| 2 | Allow for {agent} (always) | Permanent, only this agent | `(user, agent_id, resource, "always")` |
| 3 | Allow for all agents (always) | Permanent, any agent | `(user, None, resource, "always")` |
| 4 | Deny | One-time block | None |
| 5 | Never allow for {agent} | Permanent deny for this agent | `(user, agent_id, resource, "never")` |

Default: Option 1 (session-scoped, least privilege).

---

## 3. Prompt Content

Every channel shows these fields:

```
Agent:   Creator-Agent
Tool:    filesystem_read
Target:  /Users/stefan/Downloads
Reason:  "Need read access to check for PDF files"
```

All fields come from the existing `PermissionRequired` exception:
- `tool` → Tool name
- `target` → Resource path/identifier
- `reason` → Human-readable explanation
- Agent name comes from the ChatService context (which agent is currently
  active: "mycelos", "creator", or a registered agent's name)

---

## 4. Terminal UI

Rich-formatted panel with numbered options. No new dependencies — uses
existing `rich` library via `system_response_event`.

All display strings use `t()` from `mycelos.i18n` (Constitution Rule 7).
Keys: `permission.title`, `permission.option.allow_session`,
`permission.option.allow_always`, `permission.option.allow_all`,
`permission.option.deny`, `permission.option.never`, `permission.choose`.

```
╭─ Permission Required ──────────────────────────╮
│                                                 │
│  [Creator-Agent] filesystem_read                │
│  /Users/stefan/Downloads                        │
│  "Need read access to check for PDF files"      │
│                                                 │
│  1. Allow for Creator (this session)            │
│  2. Allow for Creator (always)                  │
│  3. Allow for all agents (always)               │
│  4. Deny                                        │
│  5. Never allow for Creator                     │
│                                                 │
╰─────────────────────────────────────────────────╯
Choose [1-5, default=1]:
```

### Input Handling

The `_handle_permission_response()` method accepts:
- `1` or `Y` or `yes` or `ja` or empty → Option 1 (session)
- `2` or `A` or `always` or `immer` → Option 2 (agent always)
- `3` → Option 3 (all agents always)
- `4` or `N` or `no` or `nein` → Option 4 (deny)
- `5` or `!` or `never` or `nie` → Option 5 (never)

Old shortcuts `Y/A/N/!` remain for backward compatibility.

---

## 5. Telegram UI

Inline keyboard buttons below the permission message. Uses aiogram's
`InlineKeyboardMarkup` with `InlineKeyboardButton` and callback data.

### Message

```
⚠️ Permission Required

Creator-Agent → filesystem_read
/Users/stefan/Downloads
"Need read access to check for PDF files"
```

### Buttons (3 rows)

```
[Allow (session)] [Allow (always)]
[All agents]      [Deny]
[Never for Creator]
```

### Callback Data

Each button carries a callback identifier:
- `perm:allow_session:{permission_id}`
- `perm:allow_always:{permission_id}`
- `perm:allow_all:{permission_id}`
- `perm:deny:{permission_id}`
- `perm:never:{permission_id}`

The `permission_id` is generated via `uuid.uuid4().hex[:12]` — short enough
for callback data, unique enough to prevent collisions.

The pending dict stores `{permission_id: {permission, telegram_user_id, ...}}`.
The `telegram_user_id` is recorded so the callback handler can verify the
responding user matches the requesting user.

### Callback Handler

```python
@dp.callback_query(F.data.startswith("perm:"))
async def handle_permission_callback(callback: types.CallbackQuery):
    # 1. Parse callback data → extract decision + permission_id
    # 2. Look up pending permission by permission_id
    # 3. SECURITY: verify callback.from_user.id == stored telegram_user_id
    #    If mismatch → answer "Not your permission prompt" and return
    # 4. Call grant_permission() with the resolved decision
    # 5. Edit original message to show result, remove keyboard
    # 6. Delete pending entry
```

### Security: Callback Authentication

The handler MUST verify that the Telegram user clicking the button is the
same user who triggered the permission prompt. Without this check, any
Telegram user could approve another user's permission request.

### Timeout

If no callback arrives within 5 minutes, the pending entry is deleted.
On next chat message, if `_pending_permission` is stale (expired), it is
cleared and the user sees "Permission request expired."

After handling, the original message is edited to show the decision
(e.g., "Allowed for Creator (this session)") and buttons are removed.

---

## 6. Web Frontend UI

SSE event `permission_prompt` triggers a widget in the chat. The widget
replaces the current bare widget event with a styled permission card.

### Widget Event Data

```json
{
  "type": "permission_prompt",
  "permission_id": "abc123",
  "agent": "Creator-Agent",
  "tool": "filesystem_read",
  "target": "/Users/stefan/Downloads",
  "reason": "Need read access to check for PDF files",
  "options": [
    {"value": "1", "label": "Allow for Creator (this session)"},
    {"value": "2", "label": "Allow for Creator (always)"},
    {"value": "3", "label": "Allow for all agents (always)"},
    {"value": "4", "label": "Deny"},
    {"value": "5", "label": "Never allow for Creator"}
  ]
}
```

### Rendering

```html
<div class="permission-card">
  <div class="permission-header">⚠️ Permission Required</div>
  <div class="permission-info">
    <strong>Creator-Agent</strong> → filesystem_read<br>
    <code>/Users/stefan/Downloads</code><br>
    <em>"Need read access to check for PDF files"</em>
  </div>
  <div class="permission-options">
    <label><input type="radio" name="perm" value="1" checked>
      Allow for Creator (this session)</label>
    <label><input type="radio" name="perm" value="2">
      Allow for Creator (always)</label>
    <label><input type="radio" name="perm" value="3">
      Allow for all agents (always)</label>
    <label><input type="radio" name="perm" value="4">
      Deny</label>
    <label><input type="radio" name="perm" value="5">
      Never allow for Creator</label>
  </div>
  <button onclick="submitPermission(this)">Confirm</button>
</div>
```

The confirm button sends a structured message to `/api/chat`:
`PERM:{permission_id}:{value}` (e.g., `PERM:abc123def456:2`).

The `_handle_permission_response()` in ChatService detects the `PERM:` prefix,
extracts `permission_id` and value, verifies the `permission_id` matches the
pending permission, and processes the decision. This prevents ambiguity with
regular chat messages like "1" or "2".

Terminal input (bare `1-5` or `Y/A/N/!`) continues to work because
`_handle_permission_response()` checks for `PERM:` prefix first, then falls
back to the legacy matching.

---

## 7. Implementation Changes

### 7.1 `security/permissions.py`

Extend `grant_permission()` to handle the new "all agents" scope.

**Important:** `"allow_all_always"` is an internal routing string used only
in `_handle_permission_response()`. It is NEVER passed to `set_policy()`.
The function translates it to `decision="always"` with `agent_id=None`:

```python
# New decision: "allow_all_always" → agent_id=None, decision="always"
if decision == "allow_all_always":
    # agent_id=None means all agents in PolicyEngine
    app.policy_engine.set_policy("default", None, permission.tool, "always")
    # Trigger config generation (permanent policy = declarative state)
    app.config.apply_from_state()
    app.audit.log("permission.allow_all", details={
        "tool": permission.tool, "action": permission.action,
        "agent_id": None,
    })
```

Similarly, the existing `"always_allow"` path must also trigger a config
generation, since permanent policy changes are declarative state (Constitution
Rule 2):

```python
if decision == "always_allow":
    app.policy_engine.set_policy("default", agent_id, permission.tool, "always")
    app.config.apply_from_state()  # NEW: config generation
    ...

if decision == "never_allow":
    app.policy_engine.set_policy("default", agent_id, permission.tool, "never")
    app.config.apply_from_state()  # NEW: config generation
    ...
```

### 7.2 `chat/service.py`

**Permission prompt creation** (where PermissionRequired is caught):
- Format prompt text with agent name, tool, target, reason, 5 numbered options
- Include `permission_id` in widget data for Telegram callback resolution
- Store `permission_id` in `_pending_permission` dict

**`_handle_permission_response()`**:
- Accept `1-5` in addition to existing `Y/A/N/!`
- Map `3` → `"allow_all_always"` (new decision type)
- Map `2` → `"always_allow"` (existing, agent-scoped)
- Map `1` → `"allow_session"` (existing)

### 7.3 `channels/telegram.py`

- Add `InlineKeyboardMarkup` import
- New function `_build_permission_keyboard(permission_id)` → returns markup
- Modify `handle_message` to detect permission widget events and send
  inline keyboard instead of plain text
- Add `@dp.callback_query(F.data.startswith("perm:"))` handler
- After callback: edit original message, remove keyboard, show decision
- Store pending permissions in module-level dict keyed by `permission_id`

### 7.4 `frontend/out/index.html`

- Add inline script that intercepts `permission_prompt` SSE events
- Render permission card with radio buttons
- Confirm button sends selected value as chat message

### 7.5 `chat/events.py`

No changes needed — the existing `ChatEvent(type="widget", data={...})`
already supports the permission_prompt widget type. The data structure
is enriched with `permission_id` and `options`.

---

## 8. Workflow Permission Clarification

### Interactive Workflows (On-Demand)

When a user says "get me the latest AI news" and the Orchestrator routes
to an existing workflow, the workflow runs **in the foreground**. The user
is present and can respond to permission prompts.

Permission decisions apply at the **agent level**, not the workflow level:
- If the AI-News workflow uses `search-agent` and `http-agent`, each agent's
  permissions are checked independently
- "Allow for search-agent (always)" persists across all workflows that use
  `search-agent`, whether triggered manually or via scheduler
- This is correct because agents have defined capabilities — a `search-agent`
  always does search, regardless of which workflow calls it

### Scheduled Workflows (Background)

Scheduled workflows run without a user present. They cannot show permission
prompts. Instead, they use a `permission_set` defined at schedule creation:

```python
# When scheduling (follows main spec Section 14 schema: {tool: decision}):
schedule_task(
    workflow_id="ai-news-daily",
    permission_set={
        "search.web": "always",
        "http.get": "always",
        "llm.complete": "always",
    }
)
```

The `permission_set` uses the main spec's flat `{tool: decision}` schema,
not per-agent scoping. This is correct for scheduled workflows because the
workflow definition already specifies which agents run which steps — the
permission_set gates the tools, not the agents.

If a scheduled workflow step needs a permission not in the `permission_set`,
it fails with an audit event `workflow.permission_denied`. The user sees
this on the next chat interaction (via proactive notification).

### Capability Tokens

Both modes use Capability Tokens at runtime:
- Interactive: token issued on-demand when user grants permission
- Scheduled: token issued from the frozen `permission_set`
- Background tasks (Huey): token scoped by `BackgroundTaskRunner.dispatch()`

The Permission UI only affects the **interactive** path. Scheduled/background
permissions are managed through `/schedule` commands and `permission_set`
configuration.

---

## 9. Audit Events

| Event | Details |
|-------|---------|
| `permission.allow_session` | tool, target, agent_id, channel |
| `permission.allow_always` | tool, target, agent_id, channel |
| `permission.allow_all` | tool, target, agent_id (None), channel |
| `permission.denied` | tool, target, agent_id, channel |
| `permission.never` | tool, target, agent_id, channel |

All events include the channel (`terminal`, `telegram`, `web`) so we can
track where permissions are being granted.

---

## 10. Path Validation (Already Implemented)

As of today's fix, filesystem permission prompts only appear for paths that
actually exist. Non-existent paths return an error to the LLM without
bothering the user. This prevents confusing prompts like:

```
# BEFORE (bad): asks permission for non-existent path
Permission Required: /Users/stefan/Tante Maria
# AFTER (good): returns error to LLM silently
{"error": "Directory not found: /Users/stefan/Tante Maria"}
```

---

## 11. Testing Strategy

### Unit Tests
- `_handle_permission_response()` accepts `1-5` and old shortcuts
- `grant_permission()` with `"allow_all_always"` creates agent_id=None policy
- Permission prompt text includes agent name, all 5 options
- Path validation: no prompt for non-existent paths (already tested)

### Integration Tests
- Telegram callback handler resolves permission correctly
- Telegram inline keyboard is removed after decision
- Web widget event includes all required fields
- Permission persists: agent-scoped vs all-agents vs session

### Security Tests
- Option 2 (agent always) does NOT grant other agents
- Option 3 (all agents) DOES grant other agents
- Option 5 (never) blocks permanently, survives restart
- Callback spoofing: invalid permission_id rejected
- Audit events logged for all 5 decision types

---

## 12. Scope Boundary

### In Scope
- 5 agent-scoped permission options
- Terminal: Rich panel + numbered selection (i18n)
- Telegram: Inline keyboard buttons + callback handler (user-verified)
- Web: Permission card widget with radio buttons (structured PERM: response)
- Slack: not covered — degrades to text-only `1-5` input (same as current)
- Backward compatibility: Y/A/N/! still work
- `grant_permission()` extended for "all agents" scope
- Audit events with channel tracking
- Workflow permission clarification in spec

### Out of Scope (Future)
- Permission management dashboard (list/revoke all grants)
- Bulk permission grants ("allow this agent everything")
- Time-limited permissions ("allow for 1 hour")
- Permission inheritance (agent groups)
- `permission_set` editor for scheduled workflows
