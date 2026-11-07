# Workflow-Infrastruktur: Definitions + Runs + State

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Workflows als wiederverwendbare Definitionen in der DB speichern (Teil des NixOS State). Workflow-Runs mit Status, Checkpoint und Artefakten tracken. Resume nach Pause/Abbruch ermoeglichen.

**Voraussetzung fuer:** Creator-Agent Workflow, Scheduled Workflows, jeder komplexere Multi-Step Flow.

---

## Aktueller Stand

| Was | Wo | Problem |
|-----|-----|---------|
| Workflow-Definitionen | YAML-Files (`artifacts/workflows/`) | Nicht in DB, nicht im NixOS State |
| Workflow-Ausfuehrung | `WorkflowExecutor` (in-memory) | Kein persistierter State |
| Workflow-Status | `workflow_events` (Event-Log) | Nur Log, kein Status/Resume |
| Workflow-Artefakte | Nirgends | Gehen bei Abbruch verloren |

## Ziel: Drei Ebenen

```
┌─────────────────────────────────────────────────┐
│ WORKFLOW DEFINITIONS (wiederverwendbar)          │
│   "Was kann ausgefuehrt werden?"                 │
│   → workflows Tabelle (Teil des NixOS State)     │
│   → Rollbackable, versioniert                    │
└────────────────────┬────────────────────────────┘
                     │ instanziiert
┌────────────────────▼────────────────────────────┐
│ WORKFLOW RUNS (einzelne Ausfuehrungen)           │
│   "Was laeuft / was ist passiert?"               │
│   → workflow_runs Tabelle                        │
│   → Status, Checkpoint, Artefakte, Kosten        │
│   → Pause/Resume moeglich                        │
└────────────────────┬────────────────────────────┘
                     │ loggt
┌────────────────────▼────────────────────────────┐
│ WORKFLOW EVENTS (Detail-Log)                     │
│   "Was ist in welcher Reihenfolge passiert?"     │
│   → workflow_events Tabelle (schon da)           │
│   → Append-only, fuer Debugging                  │
└─────────────────────────────────────────────────┘
```

---

## Neue Tabellen

### `workflows` — Wiederverwendbare Definitionen

```sql
CREATE TABLE IF NOT EXISTS workflows (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    goal        TEXT,
    version     INTEGER NOT NULL DEFAULT 1,
    steps       TEXT NOT NULL,              -- JSON: Workflow-Steps
    scope       TEXT,                       -- JSON: benoetigte Capabilities
    tags        TEXT,                       -- JSON: Tags fuer Suche
    status      TEXT NOT NULL DEFAULT 'active',  -- active, deprecated, draft
    created_by  TEXT NOT NULL DEFAULT 'system',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT
);
```

**Beispiel-Rows:**
```
id: "news-summary"     | name: "News Summary"      | steps: [...] | status: active
id: "create-agent"     | name: "Create Agent"      | steps: [...] | status: active
id: "email-briefing"   | name: "Email Briefing"    | steps: [...] | status: active
```

**Warum `steps` als JSON bleibt:** Steps sind ein geordnetes Array, keine eigene Entitaet. Sie werden nie einzeln abgefragt. Das ist bewusst (wie `plans.steps`).

### `workflow_runs` — Einzelne Ausfuehrungen

```sql
CREATE TABLE IF NOT EXISTS workflow_runs (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL REFERENCES workflows(id),
    task_id         TEXT REFERENCES tasks(id),
    user_id         TEXT NOT NULL DEFAULT 'default',
    status          TEXT NOT NULL DEFAULT 'running',
        -- running, paused, waiting_input, completed, failed, aborted
    current_step    TEXT,                   -- Step-ID der gerade laeuft
    completed_steps TEXT,                   -- JSON: ["step1", "step2"]
    artifacts       TEXT,                   -- JSON: {"code_hash": "abc", "spec": {...}}
    error           TEXT,                   -- letzter Fehler
    cost            REAL NOT NULL DEFAULT 0.0,
    budget_limit    REAL,                   -- max Kosten (NULL = kein Limit)
    retry_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_workflow_runs_status ON workflow_runs(status);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_user ON workflow_runs(user_id, status);
```

**Status-Flow:**
```
running → paused (Budget/User Pause)
running → waiting_input (braucht User-Entscheidung)
running → completed (alle Steps fertig)
running → failed (Step gescheitert, max Retries erreicht)
paused → running (User sagt "weiter")
paused → aborted (User sagt "abbrechen")
waiting_input → running (User hat geantwortet)
```

---

## NixOS State Integration

`workflows`-Tabelle gehoert in den NixOS State (rollbackable).
`workflow_runs` gehoert NICHT in den State (laufende Arbeit).

### StateManager erweitern

Snapshot:
```python
"workflows": {
    "news-summary": {
        "name": "News Summary",
        "description": "Search and summarize news",
        "version": 1,
        "steps": [...],
        "scope": ["search.web", "search.news"],
        "status": "active"
    },
    "create-agent": {
        "name": "Create Agent",
        "version": 1,
        "steps": [...],
        "status": "active"
    }
}
```

Restore: Workflows aus Snapshot in `workflows`-Tabelle schreiben.

---

## WorkflowRegistry — CRUD fuer Definitions

```python
class WorkflowRegistry:
    """Manages workflow definitions in the database."""

    def register(self, workflow_id, name, steps, description=None, goal=None,
                 scope=None, tags=None, created_by="system") -> None:
        """Register a new workflow definition."""

    def get(self, workflow_id) -> dict | None:
        """Get a workflow definition by ID (with parsed steps)."""

    def list_workflows(self, status=None, tag=None) -> list[dict]:
        """List workflow definitions."""

    def update(self, workflow_id, steps=None, description=None) -> None:
        """Update a workflow (creates new version)."""

    def deprecate(self, workflow_id) -> None:
        """Mark a workflow as deprecated."""

    def import_from_yaml(self, yaml_path) -> str:
        """Import a workflow from YAML file into DB."""

    def export_to_yaml(self, workflow_id) -> str:
        """Export a workflow definition as YAML string."""
```

## WorkflowRunManager — Ausfuehrung + State

```python
class WorkflowRunManager:
    """Manages workflow execution runs with state tracking."""

    def start(self, workflow_id, task_id=None, user_id="default",
              budget_limit=None) -> str:
        """Start a new workflow run. Returns run_id."""

    def get(self, run_id) -> dict | None:
        """Get a run with parsed artifacts and completed_steps."""

    def update_step(self, run_id, step_id, status, artifacts=None,
                    cost=0.0) -> None:
        """Update progress after a step completes."""

    def pause(self, run_id, reason="user") -> None:
        """Pause a running workflow."""

    def resume(self, run_id) -> dict:
        """Resume a paused workflow. Returns the run state for continuation."""

    def complete(self, run_id, result=None) -> None:
        """Mark a run as completed."""

    def fail(self, run_id, error) -> None:
        """Mark a run as failed."""

    def abort(self, run_id) -> None:
        """User-initiated abort. Artifacts are preserved."""

    def list_runs(self, status=None, user_id=None, workflow_id=None) -> list[dict]:
        """List runs, filterable."""

    def get_pending_runs(self, user_id="default") -> list[dict]:
        """Get paused/waiting runs that need attention."""

    def check_budget(self, run_id) -> bool:
        """Check if run has exceeded its budget limit."""
```

---

## Workflow-Artefakte

Artefakte sind JSON in `workflow_runs.artifacts`. Fuer grosse Daten (Code) nutzen wir den Object Store:

```python
artifacts = {
    "spec": {"name": "pdf-agent", "capabilities": [...]},     # klein, inline
    "code_hash": "abc123...",                                   # Object Store Referenz
    "tests_hash": "def456...",                                  # Object Store Referenz
    "prompt_hash": "ghi789...",                                 # Object Store Referenz
    "test_output": "3 passed, 0 failed",                       # klein, inline
    "audit_result": {"approved": true, "findings": []},        # klein, inline
}
```

Bei Resume: Code aus Object Store laden (immutable, immer verfuegbar).

---

## Integration mit bestehendem WorkflowExecutor

Der bestehende `WorkflowExecutor` bleibt — er fuehrt Steps aus. Neu: `WorkflowRunManager` wrappet ihn und persistiert den State:

```python
class ManagedWorkflowExecutor:
    """Wraps WorkflowExecutor with persistent state tracking."""

    def __init__(self, app, run_manager, executor):
        ...

    def execute(self, run_id) -> ExecutionResult:
        """Execute a workflow run with state tracking.

        For each step:
        1. Check if already completed (resume case)
        2. Check budget
        3. Execute step via WorkflowExecutor
        4. Persist result + artifacts
        5. If failed: check retry count
        6. If budget exceeded: pause
        """
```

---

## Mycelos zeigt halbfertige Workflows

Beim Chat-Start checkt Mycelos ob es halbfertige Runs gibt:

```python
# In ChatService oder chat_cmd
pending = app.workflow_run_manager.get_pending_runs(user_id)
if pending:
    for run in pending:
        console.print(
            f"[yellow]Offener Workflow: {run['workflow_name']} "
            f"({run['status']}, Step: {run['current_step']})[/yellow]"
        )
    # LLM erwaehnt das im Greeting
```

---

## Tasks

### Task 1: Schema — workflows + workflow_runs Tabellen

**Files:**
- Modify: `src/mycelos/storage/schema.sql`
- Create: `tests/test_workflow_schema.py`

**Was:**
- `workflows` Tabelle (Definitionen, Teil des NixOS State)
- `workflow_runs` Tabelle (Ausfuehrungen, NICHT im State)
- Index auf status + user_id
- Tests: Insert, FK constraints, Status-Updates

---

### Task 2: WorkflowRegistry — CRUD fuer Definitions

**Files:**
- Create: `src/mycelos/workflows/workflow_registry.py`
- Create: `tests/test_workflow_registry.py`
- Modify: `src/mycelos/app.py` (Property)

**Was:**
- register, get, list, update, deprecate
- import_from_yaml / export_to_yaml
- JSON-Parsing fuer steps, scope, tags
- Tests: CRUD, YAML import/export, Versionierung

---

### Task 3: WorkflowRunManager — Ausfuehrung + State

**Files:**
- Create: `src/mycelos/workflows/run_manager.py`
- Create: `tests/test_workflow_run_manager.py`
- Modify: `src/mycelos/app.py` (Property)

**Was:**
- start, get, update_step, pause, resume, complete, fail, abort
- list_runs, get_pending_runs, check_budget
- Artifacts als JSON (mit Object Store Referenzen)
- Status-Transitions validiert
- Tests: Full lifecycle, Budget-Check, Resume nach Pause

---

### Task 4: StateManager — Workflows in Snapshot

**Files:**
- Modify: `src/mycelos/config/state_manager.py`
- Modify: `tests/test_state_manager.py`

**Was:**
- `_snapshot_workflows()` — liest `workflows` Tabelle
- `restore()` — schreibt Workflows zurueck
- Snapshot-Format: `"workflows": {"news-summary": {...}}`
- Tests: Snapshot enthaelt Workflows, Rollback stellt sie wieder her

---

### Task 5: Bestehende YAML-Workflows importieren + Init

**Files:**
- Modify: `src/mycelos/cli/init_cmd.py` (Built-in Workflows registrieren)
- Create: `artifacts/workflows/create-agent.yaml`
- Create: `tests/test_workflow_import.py`

**Was:**
- Bei `mycelos init`: YAML-Workflows aus `artifacts/workflows/` importieren
- `news-summary.yaml` → DB
- `create-agent.yaml` → DB (fuer den Creator-Agent Workflow)
- Tests: Import, DB hat korrekte Eintraege

---

### Task 6: ManagedWorkflowExecutor + Integration

**Files:**
- Create: `src/mycelos/workflows/managed_executor.py`
- Create: `tests/test_managed_executor.py`

**Was:**
- Wrappet WorkflowExecutor mit State-Tracking
- Skip completed steps bei Resume
- Budget-Check nach jedem Step
- Pause bei Budget-Ueberschreitung
- Retry-Tracking
- Tests: Normal-Flow, Resume, Budget-Pause, Retry

---

### Task 7: Pending-Runs Anzeige + Integration Tests

**Files:**
- Modify: `src/mycelos/chat/service.py` (Pending-Check bei Session-Start)
- Create: `tests/test_workflow_lifecycle.py`

**Was:**
- ChatService checkt `get_pending_runs()` bei neuer Session
- Zeigt offene Workflows als Hinweis
- E2E Test: Workflow starten → pausieren → Resume → abschliessen
- E2E Test: Budget-Limit → Pause → User bestaetigt → weiter

---

## Reihenfolge

```
Task 1 (Schema)
    ↓
Task 2 (WorkflowRegistry) + Task 3 (RunManager) — parallel
    ↓
Task 4 (StateManager)
    ↓
Task 5 (YAML Import + Init)
    ↓
Task 6 (ManagedExecutor)
    ↓
Task 7 (Pending-Anzeige + E2E)
```

## Was danach kommt

Nach diesem Plan ist die Infrastruktur da fuer:
- **Creator-Agent Workflow** (naechster Plan)
- **Scheduled Workflows** (Huey, spaeter)
- **Jeder Custom Workflow** den der User erstellt
