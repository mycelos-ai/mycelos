# Background Execution System — Design Specification

**Date:** 2026-03-24
**Status:** REVIEWED (12 issues addressed)
**Scope:** Tag 1: Creator Pipeline + Agent Execution. Generically extensible.

---

## 1. Overview

A generic background task system for long-running operations that should not
block the user. Built on Huey (SqliteHuey) which is already running as a
worker thread in the Gateway.

### Day 1 Use Cases
- Creator Pipeline (build agent: 30-120s, multiple LLM calls)
- Registered Agent Execution (agent performs a task)

### Future Use Cases (architecture supports, not implemented yet)
- Batch operations (scan folder, process multiple files)
- Report generation (weekly summary, cost report)
- Long-running MCP tool calls (large GitHub search, web scraping)

### Core Principle
User gives an order → system dispatches background task → user can keep
chatting → notification when done. The user is never blocked.

---

## 2. Database Schema

### 2.1 Users Table

```sql
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,       -- "default", "telegram:42"
    name        TEXT,
    email       TEXT,
    language    TEXT DEFAULT 'en',
    timezone    TEXT,
    telegram_id INTEGER,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT
);
```

**NixOS State classification:** The `users` table is NOT part of NixOS State.
User records are runtime data — auto-created on first message (Telegram) or
during init (default user). Rolling back config should not delete users.
User preferences that affect system behavior (language, notification defaults)
are stored here, not in memory_entries.

### 2.2 Background Tasks Table

```sql
CREATE TABLE IF NOT EXISTS background_tasks (
    id                  TEXT PRIMARY KEY,
    task_type           TEXT NOT NULL,          -- "creator_pipeline", "agent_execution"
    status              TEXT NOT NULL DEFAULT 'pending',
    payload             TEXT NOT NULL,          -- JSON: task-specific input
    result              TEXT,                   -- JSON: task-specific output
    error               TEXT,                   -- error message if failed
    session_id          TEXT,
    agent_id            TEXT,
    user_id             TEXT NOT NULL DEFAULT 'default',
    capability_token_id TEXT,                   -- scoped token for this task
    cost                REAL NOT NULL DEFAULT 0.0,
    cost_limit          REAL,
    current_step        TEXT,
    total_steps         INTEGER DEFAULT 0,
    notify_policy       TEXT NOT NULL DEFAULT 'on_completion',
    notify_channels     TEXT DEFAULT 'auto',
    notified_at         TEXT,                   -- when user was notified
    waiting_reason      TEXT,
    timeout_seconds     INTEGER DEFAULT 600,    -- max duration (10 min default)
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at          TEXT,
    completed_at        TEXT
);
```

### 2.3 Background Task Steps Table

```sql
CREATE TABLE IF NOT EXISTS background_task_steps (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL REFERENCES background_tasks(id),
    step_number  INTEGER NOT NULL,
    step_name    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    message      TEXT,
    cost         REAL NOT NULL DEFAULT 0.0,
    started_at   TEXT,
    completed_at TEXT
);
```

### 2.4 Schema Notes

- `background_tasks` and `background_task_steps` are NOT part of NixOS State.
  They are transient execution state, like `workflow_runs`.
- `users` is NOT part of NixOS State (runtime data, not config).
- Naming: `/bg` command (not `/tasks`) to avoid collision with existing `tasks` table.
- The existing `tasks` table is for Planner task tracking. `background_tasks` is
  a separate concept for async execution. No FK between them.

---

## 3. Security Integration

### 3.1 Capability Tokens (Critical)

Every background task gets a **scoped Capability Token** on dispatch.
The token defines what the task is allowed to do.

```python
# On dispatch:
token = app.capability_manager.issue(
    agent_id=agent_id,
    scope=task_type_config["required_capabilities"],
    ttl_seconds=timeout_seconds,
    task_id=task_id,
)
# Stored in background_tasks.capability_token_id

# On each step:
validation = app.capability_manager.validate(token_id, current_capability)
if not validation.valid:
    task.fail("Capability denied: " + validation.reason)

# On cancel:
app.capability_manager.revoke(token_id)
```

### 3.2 Policy Engine Check on Dispatch

Before queuing any task, the PolicyEngine is consulted:

```python
decision = app.policy_engine.evaluate(user_id, agent_id, "background_execution")
if decision == "never":
    return {"error": "Background execution blocked by policy."}
if decision == "confirm":
    # For creator_pipeline, the interview confirmation already covers this.
    # For agent_execution, the user must have explicitly triggered it.
    pass
```

### 3.3 Sandbox Enforcement

Agent execution tasks run inside the execution sandbox:
- `LocalSandbox` for Day 1 (acknowledged limitation — not a security boundary)
- `DockerSandbox` when available (Phase 2.4 of core spec)
- Environment: only `MYCELOS_SESSION_TOKEN` + scoped mount paths
- No access to Credential Proxy from within sandbox

### 3.4 Audit Trail

Every background task operation is audited:
- `bg_task.dispatched` — with task_type, agent_id, cost_limit
- `bg_task.started` — with token_id
- `bg_task.step_completed` — per step with cost
- `bg_task.completed` / `bg_task.failed` / `bg_task.cancelled`
- `bg_task.cost_exceeded` — with current cost and limit
- `bg_task.waiting` — with waiting_reason

---

## 4. Task Lifecycle

### 4.1 Status Flow

```
PENDING
  |
  v
RUNNING
  |
  ├── COMPLETED          (success)
  ├── FAILED             (error, crash, timeout)
  ├── WAITING_FOR_USER   (needs input/confirmation)
  ├── COST_EXCEEDED      (budget reached)
  └── CANCELLED          (user cancelled)
```

WAITING_FOR_USER and COST_EXCEEDED can transition back to RUNNING.

### 4.2 Stale Task Detection

A periodic Huey task (every 5 minutes) sweeps for stale tasks:

```python
@huey.periodic_task(crontab(minute="*/5"))
def sweep_stale_tasks():
    stale = find_tasks_where(
        status="running",
        started_at < now() - timeout_seconds,
    )
    for task in stale:
        task.status = "failed"
        task.error = "Task timed out after {timeout_seconds}s"
        revoke_capability_token(task.capability_token_id)
```

### 4.3 WAITING_FOR_USER Timeout

Tasks in WAITING_FOR_USER auto-cancel after 24 hours if no response.
If the user has no active session and no Telegram, the task surfaces
in the proactive check on next chat interaction.

---

## 5. Task Runner Interface

### 5.1 Protocol (in protocols.py)

```python
class BackgroundTaskRunner(Protocol):
    def dispatch(self, task_type: str, payload: dict, **kwargs) -> str: ...
    def get_status(self, task_id: str) -> dict: ...
    def get_tasks_for_user(self, user_id: str, status: str | None = None) -> list[dict]: ...
    def get_completed_unnotified(self, user_id: str) -> list[dict]: ...
    def mark_notified(self, task_id: str) -> None: ...
    def cancel(self, task_id: str) -> bool: ...
    def approve_waiting(self, task_id: str) -> bool: ...
    def update_step(self, task_id: str, step: str, status: str, cost: float = 0) -> None: ...
```

### 5.2 Task Type Registry

```python
TASK_TYPES = {
    "creator_pipeline": {
        "handler": "mycelos.agents.background_pipeline.run_creator_pipeline",
        "default_cost_limit": 2.00,
        "default_notify": "on_completion",
        "default_timeout": 300,  # 5 minutes
        "required_capabilities": ["agent.create", "sandbox.execute"],
        "steps": ["feasibility", "gherkin", "test_generation",
                  "code_generation", "test_execution", "security_audit",
                  "registration"],
    },
    "agent_execution": {
        "handler": "mycelos.agents.background_pipeline.run_agent_task",
        "default_cost_limit": 0.50,
        "default_notify": "on_failure",
        "default_timeout": 120,  # 2 minutes
        "required_capabilities": [],  # dynamic per agent
        "steps": [],  # dynamic
    },
}
```

---

## 6. Notification System

### 6.1 Notification Policies

| Level | When | Default For |
|-------|------|-------------|
| `silent` | Never automatic | Routine jobs |
| `on_failure` | On error or WAITING_FOR_USER | Scheduled workflows, agent execution |
| `on_completion` | On success or failure | Creator Pipeline |
| `on_every_step` | At each step transition | Debug, first-time |

### 6.2 Notification Channels

| Value | Behavior |
|-------|----------|
| `auto` | Active chat → Telegram → next request |
| `telegram` | Always Telegram push |
| `chat` | Only in active chat |
| `telegram,chat` | Both |

### 6.3 Delivery

**In Chat:** Proactive check at start of `handle_message()`.
**Via Telegram:** Push when task completes and user has telegram_id.
**Fallback:** Next chat request shows completed tasks.

---

## 7. Cost Control

### 7.1 Per-Task Budget

On `cost >= cost_limit`:
1. Task → `COST_EXCEEDED`
2. User notified
3. On approve: limit doubled, task continues
4. On deny: task cancelled

### 7.2 Daily Cost Monitoring

On chat startup:
- $5/day: Info message
- $10/day: Warning + workflow suggestion
- $25/day: Alert + cost review suggestion

Thresholds configurable per user.

---

## 8. ChatService Integration

### 8.1 Dispatching (non-blocking)

```python
if result.confirmed and result.spec:
    task_id = self._app.task_runner.dispatch(
        task_type="creator_pipeline",
        payload=result.spec.to_dict(),
        session_id=session_id,
        agent_id="creator",
    )
    events.append(text_event(f"Building agent in background (Task {task_id[:8]})..."))
```

### 8.2 Proactive Notification

```python
# At start of handle_message():
completed = self._app.task_runner.get_completed_unnotified(user_id)
for task in completed:
    events.append(system_response_event(task_result_summary))
    self._app.task_runner.mark_notified(task["id"])
```

---

## 9. CLI Commands

```
/bg                  — List background tasks
/bg cancel <id>      — Cancel a task
/bg approve <id>     — Approve a waiting task
/bg detail <id>      — Show steps with timing and cost
```

---

## 10. Concurrency Note

SQLite WAL mode handles concurrent reads well. Concurrent writes from
Huey workers + Gateway thread may produce occasional `database is locked`
under load. The TaskRunner must implement retry logic with exponential
backoff for write operations. For Day 1 with 1-2 workers, this is
acceptable. For production scale, consider switching to PostgreSQL.

---

## 11. Implementation Plan

### Phase 1: Schema + Protocol + TaskRunner (1.5 hours)
- Add tables to schema.sql (users, background_tasks, background_task_steps)
- Protocol in protocols.py
- BackgroundTaskRunner implementation
- Auto-migration for existing DBs
- Tests: TaskRunner CRUD methods

### Phase 2: Creator Pipeline as Background Task (1.5 hours)
- Refactor _run_creator_pipeline to dispatch via TaskRunner
- Step tracking within pipeline (update_step per phase)
- Capability token issuance and validation
- Result storage in DB
- Tests: dispatch → steps → complete

### Phase 3: Notification + Chat Integration (1 hour)
- Proactive check in handle_message
- Telegram push for completed tasks
- /bg slash command (list, cancel, approve, detail)
- Tests: notification delivery

### Phase 4: Cost Control + Stale Detection (1 hour)
- Per-task cost tracking during pipeline
- COST_EXCEEDED status + user approval flow
- Stale task sweeper (periodic Huey task)
- Daily cost check on chat startup
- Tests: cost limit, timeout, stale detection

Total estimated: ~5 hours
