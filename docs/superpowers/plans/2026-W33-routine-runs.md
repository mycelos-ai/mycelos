# Routine run history (Package 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every routine run — workflow, scheduled task, briefing, source sync — leaves a durable row that says what happened. A run that fails says so, with a cause a human can act on. The inbox's `failed_run` entry, reserved in Package 2 and left without a producer, becomes real and resolvable.

**Architecture:** Extend the existing `workflow_runs` table with a `kind` discriminator and a nullable `workflow_id`, rather than adding a second table. A thin recorder (`scheduler/run_recorder.py`) gives the three non-workflow kinds the same start/finish/fail discipline `WorkflowRunManager` already gives workflows. The three failure holes in `WorkflowAgent` close. The Package 2 inbox read model gains its `failed_run` producer.

**Tech Stack:** Python 3.12, SQLite, FastAPI, Huey, pytest.

**Spec:** `docs/superpowers/specs/2026-W33-routine-runs-design.md`

## Global Constraints

- **One table, four writers.** Do not create a parallel runs table. The UI concept forbids "two mirrored records"; two schemas would drift.
- **A run that ends for any reason leaves a row that says so.** This is the package's invariant. Every task is measured against it.
- **Constitution Rule 1 — the `error` column is user-facing text, not a traceback.** No file paths, no note content, no rule text, no parsed payload. An ingest failure names what failed and why, never what the data contained. This is the single easiest place in the package to leak personal data; every task that writes `error` must be checked for it.
- **Constitution Rule 3 — fail closed.** A failed retry never marks an inbox entry resolved. Package 2 established this pattern; do not regress it.
- **Constitution Rule 2** — `workflow_runs` is a content/ephemeral table: no config generations.
- **Package 2 invariants survive:** consequence entries never collapse; the count covers Class 2 + Class 3 only; every inbox entry is resolvable through an action it advertises (the final Package 2 review found `unclassifiable` entries that no endpoint could clear — do not ship `failed_run` with the same defect).
- **Do NOT build in this package:** the Routines UI, run-now/pause HTTP routes, per-source schedules or pause, cost accounting, the "Routines" rename. Scope was set explicitly (Stefan, W33).
- All code/comments/log messages English. TDD per task. Commit messages English, conventional, NO Co-Authored-By/Generated-with footers. CHANGELOG under `## Week 33 (2026)` (folded into the last task).
- Test invocation: `export PYTHONPATH=<worktree>/src; python -m pytest <target> -v` (prefix every Bash call; env does not persist). SecurityProxy unix-socket PermissionError = sandbox → rerun that one command with the sandbox disabled.
- **Migration trap (cost us a review in Package 2):** `_ensure_schema()` only re-runs `schema.sql` when a check-table is missing. A schema change must also go in the `_MIGRATIONS` list in `database.py`, which is the path that actually runs on an existing database. Read the comment above that loop.

## Verified current state (2026-08-15, survey against `8b5e6c1`)

- `workflow_runs` (`schema.sql:156-176`): has `status`, `error`, `cost`, `budget_limit`, `retry_count`, `user_id`, `session_id`, timestamps. `workflow_id TEXT NOT NULL REFERENCES workflows(id)` — the FK that blocks reuse. No `kind` column.
- `workflow_events` (`schema.sql:182-192`): **dead**. Zero writers, zero readers in `src/`. Only `tests/test_workflow_schema.py:146-161` touches it.
- `WorkflowRunManager` (`workflows/run_manager.py`): `start` (`:56`), transitions (`:366-407`), `mark_notified` (`:359`), `check_budget` (`:233`, **no caller**).
- `WorkflowAgent.execute` (`workflows/agent.py`): `run_manager.start` at `:231`; `fail()` called at **exactly one site**, `:372` (max-rounds). A raised exception propagates out; the row stays `running`.
- `sweep_orphaned_workflow_runs` (`scheduler/jobs.py:131`): stamps `"Orphaned: gateway restarted while workflow was running"`. Called once at registration (`:152`), not periodically.
- `check_scheduled_workflows` (`jobs.py:290-360`): on non-completed status only `logger.warning` (`:338-345`); `mark_executed` runs either way (`:335`, `:360`). `scheduled_tasks` has no outcome column. A workflow with no `plan` is skipped silently (`:319-324`).
- `auto_ingest_check` (`jobs.py:40-89`): builds a summary dict, writes one audit event `knowledge.auto_ingest.run` (`:85`), returns. No run row. `connectors.last_error_at`/`last_error` (`schema.sql:283-285`) is per-connector and overwritten.
- `briefing_tick` (`jobs.py:214`) → `knowledge/briefing.py`; state is the memory key `briefing_last_sent`.
- `INGEST_SOURCES` (`knowledge/connector_ingest.py:336`): `gmail`, `yt-summary`. A Python dict, no table.
- `inbox_model.py:449-461`: documents in-repo why `failed_run` was omitted in Package 2. `INBOX_KINDS` already contains `failed_run` (`inbox_policy.py`). `InboxService._KINDS` (`knowledge/inbox.py`) does NOT — Package 2 added `scope_violation` there; `failed_run` still needs adding if a row is ever stored (see Task 5's note: it may be synthesized instead).
- Package 2's resolvable-entry pattern: `POST /api/inbox/notes/{path:path}/retry` in `gateway/routers/inbox.py`, with `_is_safe_note_path` guarding the `{path:path}` route against percent-encoded traversal.

## File structure

| File | Responsibility | Change |
|---|---|---|
| `src/mycelos/storage/schema.sql` + `storage/database.py` | `kind` column, nullable `workflow_id`, drop `workflow_events` | Modify |
| `src/mycelos/scheduler/run_recorder.py` | Thin start/finish/fail recorder for non-workflow kinds | Create |
| `src/mycelos/workflows/agent.py` + `run_manager.py` | Close the three failure holes | Modify |
| `src/mycelos/scheduler/jobs.py` | Record briefing + source-sync + scheduled-task runs | Modify |
| `src/mycelos/knowledge/inbox_model.py` | `failed_run` entries from run rows | Modify |
| `src/mycelos/gateway/routers/inbox.py` | Resolve action for `failed_run` | Modify |
| `tests/test_routine_runs.py`, `tests/test_run_failure_recording.py`, `tests/test_failed_run_inbox.py` | Tests | Create |

---

### Task 1: Schema — kind, nullable workflow_id, drop the dead table

**Files:**
- Modify: `src/mycelos/storage/schema.sql`
- Modify: `src/mycelos/storage/database.py` (`_MIGRATIONS`)
- Test: `tests/test_routine_runs.py` (create)

**Interfaces produced, used by every later task:**
- `workflow_runs.kind TEXT NOT NULL DEFAULT 'workflow'` — `'workflow' | 'scheduled_task' | 'briefing' | 'source_sync'`
- `workflow_runs.workflow_id` nullable
- `workflow_runs.routine_key TEXT` — identity for kinds with no `workflow_id` (the source name, or `'briefing'`). Needed so the inbox can say *which* routine failed and so runs group per routine.
- `workflow_events` gone

- [ ] **Step 1: Write the failing tests**

Create `tests/test_routine_runs.py`. Reuse the storage fixtures from `tests/test_uncertain_placement.py` (Package 2 built the migration-regression pattern there — read it first).

Cover:
- `kind` and `routine_key` exist on a fresh database; `kind` defaults to `'workflow'`
- a row with `workflow_id=NULL` and `kind='source_sync'` inserts successfully (today the FK rejects it)
- a row with a `workflow_id` that does not exist in `workflows` is still rejected (loosening null must not loosen referential integrity)
- **migration on an existing database**: build a DB with the old shape, insert a run row, reopen, assert the columns exist AND the old row survived with `kind='workflow'`
- `workflow_events` no longer exists
- existing `workflow_runs` reads still work (`tests/test_workflow_schema.py` must keep passing except for its `workflow_events` assertion, which you update)

- [ ] **Step 2: Run tests to verify they fail**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_routine_runs.py -v`

- [ ] **Step 3: Implement**

SQLite cannot drop a NOT NULL constraint with `ALTER TABLE`. The migration must rebuild `workflow_runs`: create the new shape, `INSERT INTO ... SELECT` the old rows with `kind='workflow'`, drop the old, rename, recreate the three indexes (`idx_workflow_runs_status`, `idx_workflow_runs_user`, `idx_workflow_runs_session_id`). **Do it inside a transaction** — a half-migrated runs table on Stefan's live database is the worst outcome in this package.

Add the migration to `_MIGRATIONS` in `database.py`, not only to `schema.sql`. Read the comment above that loop; `schema.sql` alone never reaches an existing database.

Drop `workflow_events` in the same migration. Update `tests/test_workflow_schema.py`, which asserts its columns.

- [ ] **Step 4: Run tests to verify they pass**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_routine_runs.py tests/test_workflow_schema.py tests/test_knowledge_base.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/storage/schema.sql src/mycelos/storage/database.py tests/test_routine_runs.py tests/test_workflow_schema.py
git commit -m "feat(storage): routine kind on workflow_runs, drop dead events table"
```

---

### Task 2: Close the three failure holes

**Files:**
- Modify: `src/mycelos/workflows/agent.py`, `src/mycelos/workflows/run_manager.py`
- Modify: `src/mycelos/scheduler/jobs.py` (the orphan sweep's message; `check_scheduled_workflows`)
- Test: `tests/test_run_failure_recording.py` (create)

**Interfaces:**
- Consumes: Task 1's schema.
- Produces: the invariant "a run that ends for any reason leaves a row that says so", for the workflow kind.

**This is the task that makes the history trustworthy. Without it every later surface under-reports.**

- [ ] **Step 1: Write the failing tests**

Create `tests/test_run_failure_recording.py`. Reuse the workflow fixtures — find them first (`grep -rl "WorkflowAgent" tests/`).

Cover:
- an exception raised inside `execute()` (from the LLM call, and separately from a tool call) leaves the row `status='failed'` with a cause — NOT `running`
- the row's `error` contains no traceback, no file path, and no note content
- the max-rounds path still records as it does today (do not regress it)
- a scheduled task whose workflow fails leaves a durable failure trace, not only a log line
- a workflow with no `plan` no longer vanishes silently (`jobs.py:319-324`) — it records a failed run naming the cause
- the orphan sweep's message distinguishes "we cannot tell" from "the gateway restarted"

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement**

Wrap `execute()`'s body so any exception records the failure and re-raises — callers must still see it (`jobs.py:272-278` audits, chat surfaces it). Recording must not swallow.

The `error` text is a **cause, not a traceback**: the exception type and a short message, sanitized. Read how Package 2's audit payloads handle this and match it. If you must log the traceback, log it — do not store it.

For the orphan sweep: a row found `running` at startup means "we cannot tell what happened", which is honest; "the gateway restarted while it was running" is a guess. Change the stored cause to the honest one.

For `check_scheduled_workflows`: a non-completed status must leave a durable trace. `scheduled_tasks` has no outcome column and this package does not add one — the run row is the trace, so make sure one exists and is marked failed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_run_failure_recording.py tests/test_routine_runs.py -v`
Then the workflow and scheduler suites — find them (`ls tests/ | grep -E "workflow|schedul"`) and run all of them.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/workflows/ src/mycelos/scheduler/jobs.py tests/test_run_failure_recording.py
git commit -m "fix(workflows): record every run that ends, with an honest cause"
```

---

### Task 3: The recorder — briefing and source syncs write runs

**Files:**
- Create: `src/mycelos/scheduler/run_recorder.py`
- Modify: `src/mycelos/scheduler/jobs.py` (`auto_ingest_check`, `briefing_tick`)
- Test: extend `tests/test_routine_runs.py`

**Interfaces:**
- Consumes: Task 1's schema.
- Produces:
  - `RunRecorder(storage)` with `start(kind, routine_key, user_id) -> run_id`, `finish(run_id, counts)`, `fail(run_id, cause)`
  - A run row per source per sync attempt, and per briefing delivery.

- [ ] **Step 1: Write the failing tests**

Cover:
- a successful `yt-summary` sync writes one row: `kind='source_sync'`, `routine_key='yt-summary'`, `status='completed'`, with the item count
- a failing sync writes `status='failed'` with a cause and **no personal data** — construct a failure whose exception message would contain note content, and assert it does not reach the column
- two sources in one tick write two rows, not one (`auto_ingest_check` loops `INGEST_SOURCES`)
- a source that is skipped (disabled) writes no row — a skip is not a run
- the briefing writes a row on delivery
- the recorder fails closed: if the row cannot be written, the sync still runs (recording is observability, it must not break the job) — but the failure is logged
- `artifacts`/counts carry numbers and source names only

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement**

`run_recorder.py` is deliberately thin — it is not a second `WorkflowRunManager`. `WorkflowRunManager` owns pause/resume, clarification, conversation state and budget; none of that applies to a sync. Do not generalize the two into one class in this package; note the overlap in your report and leave it.

Wire it into `auto_ingest_check` around each per-source call, and into `briefing_tick` around delivery. Keep the existing audit events — the run row is additional, not a replacement.

Counts belong in `artifacts` (already JSON). Numbers and source names only.

- [ ] **Step 4: Run tests to verify they pass**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_routine_runs.py tests/test_run_failure_recording.py -v`
Then the ingest suites: `tests/test_connector_ingest.py` and whatever `ls tests/ | grep -E "ingest|briefing"` finds.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/scheduler/run_recorder.py src/mycelos/scheduler/jobs.py tests/test_routine_runs.py
git commit -m "feat(scheduler): record briefing and source-sync runs"
```

---

### Task 4: `failed_run` in the inbox

**Files:**
- Modify: `src/mycelos/knowledge/inbox_model.py`
- Test: `tests/test_failed_run_inbox.py` (create)

**Interfaces:**
- Consumes: run rows from Tasks 2-3; Package 2's `needs_human`, `collapse_key`, entry shape.
- Produces: `failed_run` entries in `list_entries()` and the count.

**Read `inbox_model.py:449-461` first** — it documents why this was omitted in Package 2. Replace that comment block with the implementation; do not leave a stale explanation of an absence that no longer exists.

- [ ] **Step 1: Write the failing tests**

Cover:
- a failed source-sync run produces a Class 2 entry with a `why` naming the source and the cause
- a successful run produces nothing
- a run still `running` produces nothing
- **consequence entries never collapse** — three failed runs of three different routines are three entries
- a routine that failed on every tick for a day is ONE entry carrying a failure count and the latest cause (the spec's provisional answer to its open question — an hourly sync must not become its own landfill)
- the entry appears in `count()`
- once the routine succeeds again, the entry disappears without an explicit resolve
- the entry's actions map to endpoints that exist (Package 2's final review found `unclassifiable` advertising actions nothing implemented — do not repeat it)
- no note content, rule text or traceback in the entry

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement**

Decide and state in your report: is a `failed_run` entry **synthesized from run rows** (like the other non-suggestion kinds Package 2 synthesizes) or **stored** in `organizer_suggestions`? Synthesizing is consistent with `unclassifiable` and needs no `InboxService._KINDS` change; storing needs one and creates a second source of truth. Prefer synthesizing unless you find a reason not to — and if you store, add `failed_run` to `_KINDS` and say why.

If synthesized, "resolve" is implicit: the entry is derived, so it disappears when the routine next succeeds. Say so in the entry's actions rather than advertising a dismiss that cannot persist.

- [ ] **Step 4: Run tests to verify they pass**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_failed_run_inbox.py tests/test_inbox_model.py tests/test_inbox_policy.py tests/test_inbox_api.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/knowledge/inbox_model.py tests/test_failed_run_inbox.py
git commit -m "feat(inbox): failed source runs become inbox entries"
```

---

### Task 5: Retry action, CHANGELOG, verification

**Files:**
- Modify: `src/mycelos/gateway/routers/inbox.py`
- Modify: `CHANGELOG.md`
- Test: extend `tests/test_failed_run_inbox.py` and `tests/test_inbox_api.py`

**Interfaces:**
- Produces: `POST /api/inbox/runs/{routine_key}/retry` — re-runs the failed routine.

- [ ] **Step 1: Write the failing tests**

Cover:
- retrying a failed source sync triggers the sync and, on success, the entry disappears
- **fail closed**: a retry that fails again does NOT report resolved, and the entry stays with an incremented failure count
- an unknown `routine_key` is 404
- a `routine_key` that is not a retryable kind (e.g. a workflow run, which has its own path) is rejected clearly rather than silently doing nothing
- **injection**: `routine_key` comes from a URL path — assert a key containing traversal, encoded traversal, or SQL metacharacters cannot reach the ingest dispatch or the query. `INGEST_SOURCES` is a dict; the key must be validated against it, never used to build a query or a path.
- retry is audited with the routine key and counts only

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement**

Follow `routers/inbox.py`'s existing conventions: `resolve_user_id`, `JSONResponse` for errors, sync `def`. Package 2's `_is_safe_note_path` is the precedent for guarding a path parameter — `routine_key` needs the equivalent, and the strongest form is an allowlist check against `INGEST_SOURCES` before anything else happens.

Reuse the existing `POST /api/knowledge/ingest/{source}` path rather than duplicating dispatch.

- [ ] **Step 4: Add the CHANGELOG entry** under `## Week 33 (2026)`:

```markdown
### Every routine run leaves a record

A sync that stops running used to be invisible: source syncs wrote nothing
durable, so a dead connector looked exactly like a quiet one. Runs of all four
kinds — workflows, scheduled tasks, the briefing and source syncs — now write
to one table, and a run that ends for any reason says so.

- **Failed runs reach the inbox.** A failed source sync becomes a consequence
  entry naming the source and the cause, retryable from there. A routine that
  keeps failing stays one entry with a failure count rather than one per tick.
- **Failures are recorded honestly.** A crash used to leave a run marked
  "running" forever, relabelled after a restart as though the gateway had
  been restarted under it. Real causes are now recorded when they happen, and
  a run whose fate is genuinely unknown says that instead of guessing.
- **Causes carry no content.** The stored cause names what failed and why —
  never a traceback, a file path, or the data that failed to parse.
- `workflow_events`, a table with no writer and no reader, is removed.

Not in this release: the Routines interface, run-now and pause over HTTP,
per-source schedules, and cost accounting. Source syncs still share one global
switch and a fixed hourly cadence.
```

- [ ] **Step 5: Run the full verification**

Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/security/ -q` — hold the baseline (296 passed, 4 skipped at the time of writing; confirm the current baseline first).
Run: `export PYTHONPATH=$PWD/src; python -m pytest tests/test_routine_runs.py tests/test_run_failure_recording.py tests/test_failed_run_inbox.py tests/test_inbox_model.py tests/test_inbox_api.py tests/test_inbox_policy.py tests/test_workflow_schema.py tests/test_knowledge_base.py -q`
Run the full suite: `python -m pytest tests/ -q`. Note: the sandbox denies port binding, so `tests/e2e/` errors — that is pre-existing. Rerun e2e unsandboxed and compare against the parent commit before calling any failure new.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/gateway/routers/inbox.py CHANGELOG.md tests/
git commit -m "feat(api): retry a failed routine run from the inbox"
```
