# Scheduler: Huey mit SqliteHuey fuer Cron-Jobs und Background-Tasks

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wiederkehrende Workflows ausfuehren (taeglich News, stuendlich Emails), Session Summaries im Hintergrund erstellen, und stale Sessions erkennen. Huey laeuft als Thread im Gateway-Prozess.

---

## Warum Huey?

| Alternative | Problem |
|-------------|---------|
| Celery | Braucht Redis/RabbitMQ |
| Dramatiq | Braucht Redis |
| ARQ | Braucht Redis |
| APScheduler | In-Process, kein Worker |
| **Huey** | **SqliteHuey — unsere DB reicht** |

Huey mit SqliteHuey nutzt SQLite als Task-Queue. Kein externer Service noetig. Passt perfekt in unseren Stack.

---

## Architektur

```
mycelos serve (FastAPI + uvicorn)
    │
    ├── HTTP Routes (/api/chat, /api/health, ...)
    │
    └── Huey Consumer (Thread)
        ├── Scheduled Tasks (Cron)
        │   ├── run_scheduled_workflows()    — alle X Minuten
        │   ├── session_summary()            — alle 5 Min
        │   └── cleanup_stale_sessions()     — alle 30 Min
        │
        └── On-Demand Tasks (Queue)
            ├── create_agent_background()    — Creator Pipeline
            └── execute_workflow_background() — Langlaeufer
```

Huey Consumer laeuft als Daemon-Thread im Gateway-Prozess. Beim `mycelos serve` Start wird der Consumer gestartet.

---

## Neue DB-Tabelle: `scheduled_tasks`

```sql
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id          TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflows(id),
    user_id     TEXT NOT NULL DEFAULT 'default',
    schedule    TEXT NOT NULL,      -- Cron-Expression: "0 8 * * *" (taeglich 8 Uhr)
    inputs      TEXT,               -- JSON: {"query": "AI news"}
    status      TEXT NOT NULL DEFAULT 'active',  -- active, paused, disabled
    last_run    TEXT,               -- ISO timestamp
    next_run    TEXT,               -- ISO timestamp
    run_count   INTEGER NOT NULL DEFAULT 0,
    budget_per_run REAL,            -- Max Kosten pro Ausfuehrung
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
```

Teil des NixOS State (rollbackable).

---

## Huey Setup

```python
# src/mycelos/scheduler/huey_app.py
from huey import SqliteHuey
from pathlib import Path

def create_huey(data_dir: Path) -> SqliteHuey:
    """Create the Huey task queue backed by SQLite."""
    return SqliteHuey(
        name="mycelos",
        filename=str(data_dir / "huey.db"),
    )
```

---

## Scheduled Jobs

### 1. Workflow Scheduler — fuehrt geplante Workflows aus

```python
@huey.periodic_task(crontab(minute="*/5"))
def check_scheduled_workflows():
    """Every 5 min: check for due scheduled workflows and execute them."""
    now = datetime.now()
    due = storage.fetchall(
        "SELECT * FROM scheduled_tasks WHERE status = 'active' AND next_run <= ?",
        (now.isoformat(),),
    )
    for task in due:
        workflow = workflow_registry.get(task["workflow_id"])
        if workflow:
            inputs = json.loads(task["inputs"]) if task["inputs"] else {}
            # Execute via ManagedWorkflowExecutor
            executor.start_and_execute(
                workflow, inputs=inputs,
                budget_limit=task.get("budget_per_run"),
            )
            # Update last_run + next_run
            update_schedule(task["id"], now)
```

### 2. Session Summary — extrahiert Memory aus beendeten Sessions

```python
@huey.periodic_task(crontab(minute="*/5"))
def process_session_summaries():
    """Every 5 min: find stale sessions and create summaries."""
    sessions = session_store.list_sessions()
    for s in sessions:
        if is_stale(s, minutes=30) and not has_summary(s["session_id"]):
            messages = session_store.load_messages(s["session_id"])
            if len(messages) > 2:
                summary = extract_memory(messages, llm)
                save_to_memory(summary)
```

### 3. Cleanup — alte Sessions + abgelaufene Tokens

```python
@huey.periodic_task(crontab(minute="0", hour="3"))  # 3 AM taeglich
def daily_cleanup():
    """Daily: clean up expired tokens, old sessions, stale locks."""
    # Expire old capability tokens
    # Archive sessions older than 30 days
    # Clean up orphaned workflow runs
```

---

## On-Demand Background Tasks

### Creator Pipeline im Hintergrund

```python
@huey.task()
def create_agent_async(spec_dict: dict, user_id: str):
    """Run Creator Pipeline as background task."""
    spec = AgentSpec(**spec_dict)
    pipeline = CreatorPipeline(app)
    result = pipeline.run(spec)
    # Notify user via session/inbox
    if result.success:
        notify_user(user_id, f"Agent '{spec.name}' wurde erstellt!")
```

### Langlaeufer-Workflows

```python
@huey.task()
def execute_workflow_async(workflow_id: str, inputs: dict, user_id: str):
    """Run a workflow as background task."""
    workflow = workflow_registry.get(workflow_id)
    executor = ManagedWorkflowExecutor(run_manager, runner)
    result = executor.start_and_execute(workflow, inputs=inputs)
    notify_user(user_id, f"Workflow '{workflow_id}' abgeschlossen.")
```

---

## Gateway Integration

```python
# In gateway/server.py — create_app():
def create_app(data_dir, debug=False):
    api = FastAPI(...)
    mycelos = App(data_dir)

    # Start Huey consumer as background thread
    from mycelos.scheduler.huey_app import create_huey, start_consumer
    huey = create_huey(data_dir)
    consumer_thread = start_consumer(huey, mycelos)

    api.state.huey = huey
    api.state.mycelos = mycelos
    ...
```

```python
# Start consumer in thread
def start_consumer(huey, app):
    from huey.consumer import Consumer
    import threading

    consumer = Consumer(huey, workers=2, periodic=True)

    thread = threading.Thread(target=consumer.run, daemon=True)
    thread.start()
    return thread
```

---

## CLI: `mycelos schedule`

```bash
mycelos schedule list                          # Zeige alle geplanten Tasks
mycelos schedule add news-summary --cron "0 8 * * *" --input '{"query":"AI"}'
mycelos schedule pause <id>                    # Pausieren
mycelos schedule resume <id>                   # Fortsetzen
mycelos schedule delete <id>                   # Loeschen
mycelos schedule run <id>                      # Sofort ausfuehren (test)
```

---

## Tasks

### Task 1: Dependencies + Huey Setup

**Files:**
- Modify: `pyproject.toml` (add huey)
- Create: `src/mycelos/scheduler/__init__.py`
- Create: `src/mycelos/scheduler/huey_app.py`
- Create: `tests/test_huey_setup.py`

**Was:**
- `pip install huey`
- `create_huey(data_dir) -> SqliteHuey`
- Basic test: Huey-Instanz erstellen, Task registrieren

---

### Task 2: scheduled_tasks Schema + ScheduleManager

**Files:**
- Modify: `src/mycelos/storage/schema.sql`
- Create: `src/mycelos/scheduler/schedule_manager.py`
- Modify: `src/mycelos/config/state_manager.py` (scheduled_tasks in Snapshot)
- Create: `tests/test_schedule_manager.py`

**Was:**
- `scheduled_tasks` Tabelle
- CRUD: add, get, list, pause, resume, delete, update_next_run
- Cron-Expression Parsing (naechster Ausfuehrungszeitpunkt)
- StateManager: scheduled_tasks in Snapshot + Restore

---

### Task 3: Workflow Scheduler Job

**Files:**
- Create: `src/mycelos/scheduler/jobs.py`
- Create: `tests/test_scheduler_jobs.py`

**Was:**
- `check_scheduled_workflows()` — periodic, alle 5 Min
- Liest due Tasks aus DB
- Fuehrt via ManagedWorkflowExecutor aus
- Updated last_run + next_run

---

### Task 4: Session Summary Job

**Files:**
- Create: `src/mycelos/scheduler/session_summary.py`
- Create: `tests/test_session_summary.py`

**Was:**
- `process_session_summaries()` — periodic, alle 5 Min
- Erkennt stale Sessions (>30 Min ohne Nachricht)
- LLM-Call (Haiku) extrahiert Memory-Eintraege
- Schreibt in memory_entries

---

### Task 5: Gateway Integration + Consumer Thread

**Files:**
- Modify: `src/mycelos/gateway/server.py`
- Modify: `src/mycelos/cli/serve_cmd.py`
- Create: `tests/test_scheduler_integration.py`

**Was:**
- Gateway startet Huey Consumer als Daemon-Thread
- `--no-scheduler` Flag zum Deaktivieren
- Health-Endpoint zeigt Scheduler-Status
- Graceful Shutdown

---

### Task 6: Schedule CLI + E2E Tests

**Files:**
- Create: `src/mycelos/cli/schedule_cmd.py`
- Modify: `src/mycelos/cli/main.py`
- Create: `tests/test_schedule_cmd.py`
- Create: `tests/test_scheduler_e2e.py`

**Was:**
- `mycelos schedule list/add/pause/resume/delete/run`
- E2E: Schedule erstellen → ausfuehren → Ergebnis pruefen

---

## Reihenfolge

```
Task 1 (Huey Setup)
    ↓
Task 2 (Schema + Manager)
    ↓
Task 3 (Workflow Job) + Task 4 (Session Summary) — parallel
    ↓
Task 5 (Gateway Integration)
    ↓
Task 6 (CLI + E2E)
```
