# Background Execution System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable long-running tasks (Creator Pipeline, Agent Execution) to run in the background without blocking the user.

**Architecture:** Huey (SqliteHuey) dispatches tasks to worker threads. A BackgroundTaskRunner manages lifecycle, cost, and notifications. Step-level progress is tracked in DB. Users are notified via chat or Telegram.

**Tech Stack:** Huey (SqliteHuey), SQLite WAL, existing SecurityLayer (CapabilityTokens, PolicyEngine)

**Spec:** `docs/superpowers/specs/2026-03-24-background-execution-design.md`

---

## File Structure

### New Files
- `src/mycelos/tasks/background_runner.py` — BackgroundTaskRunner implementation
- `src/mycelos/tasks/pipeline_task.py` — Creator Pipeline as Huey task
- `tests/test_background_runner.py` — Unit + integration tests
- `tests/e2e/test_background_pipeline.py` — E2E test for dispatch → complete → notify

### Modified Files
- `src/mycelos/storage/schema.sql` — Add 3 tables (users, background_tasks, background_task_steps)
- `src/mycelos/storage/database.py` — Auto-migration for new tables
- `src/mycelos/protocols.py` — BackgroundTaskRunner Protocol
- `src/mycelos/app.py` — Wire BackgroundTaskRunner into App container
- `src/mycelos/gateway/server.py` — Register tasks with Huey, attach runner to api.state
- `src/mycelos/scheduler/jobs.py` — Add stale task sweeper periodic job
- `src/mycelos/chat/service.py` — Dispatch pipeline in background, proactive notification check
- `src/mycelos/chat/slash_commands.py` — /bg command (list, cancel, approve, detail)

---

## Task 1: Schema + Auto-Migration

**Files:**
- Modify: `src/mycelos/storage/schema.sql`
- Modify: `src/mycelos/storage/database.py`

- [ ] **Step 1: Add users table to schema.sql**

```sql
-- Users (runtime data, NOT NixOS State)
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
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

- [ ] **Step 2: Add background_tasks table to schema.sql**

```sql
-- Background Tasks (transient execution state, NOT NixOS State)
CREATE TABLE IF NOT EXISTS background_tasks (
    id                  TEXT PRIMARY KEY,
    task_type           TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    payload             TEXT NOT NULL,
    result              TEXT,
    error               TEXT,
    session_id          TEXT,
    agent_id            TEXT,
    user_id             TEXT NOT NULL DEFAULT 'default',
    capability_token_id TEXT,
    cost                REAL NOT NULL DEFAULT 0.0,
    cost_limit          REAL,
    current_step        TEXT,
    total_steps         INTEGER DEFAULT 0,
    notify_policy       TEXT NOT NULL DEFAULT 'on_completion',
    notify_channels     TEXT DEFAULT 'auto',
    notified_at         TEXT,
    waiting_reason      TEXT,
    timeout_seconds     INTEGER DEFAULT 600,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at          TEXT,
    completed_at        TEXT
);
```

- [ ] **Step 3: Add background_task_steps table to schema.sql**

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

- [ ] **Step 4: Update _ensure_schema in database.py**

Add `"background_tasks"` to the table check list so existing DBs get the new tables:

```python
for check_table in ("llm_usage", "channels", "background_tasks", "users"):
```

- [ ] **Step 5: Run existing tests to verify migration doesn't break anything**

Run: `pytest tests/test_storage.py tests/test_config.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/storage/schema.sql src/mycelos/storage/database.py
git commit -m "schema: add users, background_tasks, background_task_steps tables"
```

---

## Task 2: Protocol + BackgroundTaskRunner

**Files:**
- Modify: `src/mycelos/protocols.py`
- Create: `src/mycelos/tasks/background_runner.py`
- Create: `tests/test_background_runner.py`

- [ ] **Step 1: Write failing tests for BackgroundTaskRunner**

```python
# tests/test_background_runner.py
"""Tests for BackgroundTaskRunner — dispatch, status, lifecycle."""

import os, tempfile, json
from pathlib import Path
import pytest
from mycelos.app import App
from mycelos.tasks.background_runner import BackgroundTaskRunner

@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-bg"
        a = App(Path(tmp))
        a.initialize()
        yield a

@pytest.fixture
def runner(app):
    return BackgroundTaskRunner(app)

class TestDispatch:
    def test_dispatch_returns_task_id(self, runner):
        task_id = runner.dispatch("creator_pipeline", {"name": "test"})
        assert task_id is not None
        assert len(task_id) > 8

    def test_dispatch_creates_db_record(self, runner, app):
        task_id = runner.dispatch("creator_pipeline", {"name": "test"})
        status = runner.get_status(task_id)
        assert status["status"] == "pending"
        assert status["task_type"] == "creator_pipeline"

    def test_dispatch_with_cost_limit(self, runner):
        task_id = runner.dispatch("creator_pipeline", {}, cost_limit=5.0)
        status = runner.get_status(task_id)
        assert status["cost_limit"] == 5.0

class TestLifecycle:
    def test_get_tasks_for_user(self, runner):
        runner.dispatch("creator_pipeline", {"n": "1"}, user_id="stefan")
        runner.dispatch("creator_pipeline", {"n": "2"}, user_id="stefan")
        tasks = runner.get_tasks_for_user("stefan")
        assert len(tasks) == 2

    def test_cancel_task(self, runner):
        task_id = runner.dispatch("creator_pipeline", {})
        assert runner.cancel(task_id)
        status = runner.get_status(task_id)
        assert status["status"] == "cancelled"

    def test_update_step(self, runner):
        task_id = runner.dispatch("creator_pipeline", {})
        runner.start_task(task_id, total_steps=3)
        runner.update_step(task_id, "gherkin", "running")
        status = runner.get_status(task_id)
        assert status["current_step"] == "gherkin"

class TestNotification:
    def test_completed_unnotified(self, runner):
        task_id = runner.dispatch("creator_pipeline", {}, user_id="u1")
        runner.start_task(task_id, total_steps=1)
        runner.complete_task(task_id, result={"success": True})
        unnotified = runner.get_completed_unnotified("u1")
        assert len(unnotified) == 1

    def test_mark_notified(self, runner):
        task_id = runner.dispatch("creator_pipeline", {}, user_id="u1")
        runner.start_task(task_id, total_steps=1)
        runner.complete_task(task_id, result={"success": True})
        runner.mark_notified(task_id)
        unnotified = runner.get_completed_unnotified("u1")
        assert len(unnotified) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_background_runner.py -v`
Expected: ImportError (module doesn't exist yet)

- [ ] **Step 3: Add Protocol to protocols.py**

```python
class BackgroundTaskRunner(Protocol):
    def dispatch(self, task_type: str, payload: dict, **kwargs) -> str: ...
    def get_status(self, task_id: str) -> dict: ...
    def get_tasks_for_user(self, user_id: str, status: str | None = None) -> list[dict]: ...
    def get_completed_unnotified(self, user_id: str) -> list[dict]: ...
    def mark_notified(self, task_id: str) -> None: ...
    def cancel(self, task_id: str) -> bool: ...
    def approve_waiting(self, task_id: str) -> bool: ...
    def start_task(self, task_id: str, total_steps: int) -> None: ...
    def update_step(self, task_id: str, step_name: str, status: str, cost: float = 0) -> None: ...
    def complete_task(self, task_id: str, result: dict | None = None, error: str | None = None) -> None: ...
    def fail_task(self, task_id: str, error: str) -> None: ...
```

- [ ] **Step 4: Implement BackgroundTaskRunner**

Create `src/mycelos/tasks/background_runner.py` with all methods.
Each method does simple SQLite operations on background_tasks table.
`dispatch()` creates the record + step records.
`update_step()` updates both the step record and the task's current_step.
`complete_task()` sets status=completed, completed_at, result.
`fail_task()` sets status=failed, error.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_background_runner.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/protocols.py src/mycelos/tasks/background_runner.py tests/test_background_runner.py
git commit -m "feat: BackgroundTaskRunner — dispatch, lifecycle, notification tracking"
```

---

## Task 3: Creator Pipeline as Background Task

**Files:**
- Create: `src/mycelos/tasks/pipeline_task.py`
- Modify: `src/mycelos/chat/service.py`
- Modify: `src/mycelos/gateway/server.py`
- Modify: `src/mycelos/app.py`

- [ ] **Step 1: Wire BackgroundTaskRunner into App**

In `app.py`, add lazy property:
```python
@property
def task_runner(self) -> BackgroundTaskRunner:
    if self._task_runner is None:
        from mycelos.tasks.background_runner import BackgroundTaskRunner as _BTR
        self._task_runner = _BTR(self)
    return self._task_runner
```

- [ ] **Step 2: Create pipeline_task.py**

Wraps CreatorPipeline.run() with step tracking via BackgroundTaskRunner:

```python
def run_creator_pipeline_bg(app, task_id, spec_dict):
    """Run Creator Pipeline with step-level tracking."""
    runner = app.task_runner
    spec = AgentSpec(**spec_dict)

    runner.start_task(task_id, total_steps=7)

    # Step 1: Feasibility
    runner.update_step(task_id, "feasibility", "running")
    effort = classify_effort(spec)
    runner.update_step(task_id, "feasibility", "completed")

    # Step 2: Gherkin
    runner.update_step(task_id, "gherkin", "running")
    gherkin = generate_gherkin(spec, app.llm)
    runner.update_step(task_id, "gherkin", "completed", cost=...)

    # ... etc for each step

    runner.complete_task(task_id, result={...})
```

- [ ] **Step 3: Register as Huey task in gateway server.py**

```python
# After Huey consumer start:
from mycelos.tasks.pipeline_task import register_pipeline_tasks
register_pipeline_tasks(huey, mycelos)
```

- [ ] **Step 4: Update ChatService to dispatch instead of block**

In `_handle_interview_message`, when `result.confirmed`:
```python
task_id = self._app.task_runner.dispatch(
    task_type="creator_pipeline",
    payload=result.spec.to_dict(),
    session_id=session_id,
    agent_id="creator",
)
events.append(text_event(f"Building your agent in the background (Task {task_id[:8]})..."))
```

- [ ] **Step 5: Add proactive notification check in handle_message**

At the start of `handle_message()`:
```python
completed = self._app.task_runner.get_completed_unnotified(user_id)
for task in completed:
    result = json.loads(task.get("result", "{}"))
    msg = result.get("summary", "Task completed.")
    events.append(system_response_event(f"[Background] {msg}"))
    self._app.task_runner.mark_notified(task["id"])
```

- [ ] **Step 6: Run all tests**

Run: `pytest tests/ --tb=short`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/tasks/pipeline_task.py src/mycelos/chat/service.py src/mycelos/gateway/server.py src/mycelos/app.py
git commit -m "feat: Creator Pipeline runs in background via Huey"
```

---

## Task 4: /bg Slash Command

**Files:**
- Modify: `src/mycelos/chat/slash_commands.py`

- [ ] **Step 1: Add /bg handler**

```python
"bg": _handle_bg,
```

Subcommands: list (default), cancel, approve, detail.

- [ ] **Step 2: Implement /bg list**

Shows: status emoji, task name, step progress, cost, time.

- [ ] **Step 3: Implement /bg cancel, approve, detail**

- [ ] **Step 4: Test via existing slash command test pattern**

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/chat/slash_commands.py
git commit -m "feat: /bg command — list, cancel, approve, detail"
```

---

## Task 5: Stale Task Sweeper + Cost Control

**Files:**
- Modify: `src/mycelos/scheduler/jobs.py`
- Modify: `src/mycelos/chat/service.py`

- [ ] **Step 1: Add stale task sweeper to periodic jobs**

```python
@huey.periodic_task(crontab(minute="*/5"))
def sweep_stale_bg_tasks():
    """Cancel background tasks that exceeded their timeout."""
```

- [ ] **Step 2: Add daily cost check at chat startup**

In `get_system_prompt()` or `handle_message()`, check today's total cost
and inject a warning if threshold exceeded.

- [ ] **Step 3: Test sweeper and cost check**

- [ ] **Step 4: Commit**

```bash
git add src/mycelos/scheduler/jobs.py src/mycelos/chat/service.py
git commit -m "feat: stale task sweeper + daily cost warnings"
```

---

## Task 6: Final Integration Test

**Files:**
- Create: `tests/e2e/test_background_pipeline.py`

- [ ] **Step 1: Write E2E test**

Test the full flow: dispatch creator pipeline → steps run → completes → notification shown.
Uses mocked LLM (not live) for speed.

- [ ] **Step 2: Run all tests**

Run: `pytest tests/ --tb=short`
Expected: All pass, including new E2E test

- [ ] **Step 3: Update CHANGELOG.md**

- [ ] **Step 4: Final commit + push**

```bash
git add -A
git commit -m "feat: Background Execution System — Creator Pipeline runs async"
git push origin main
```
