# Knowledge Milestone B — Lazy Organizer + Inbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the background `knowledge-organizer` system handler that lifecycles, classifies, and lazy-links pending notes; the Organizer Inbox UI; and the `/api/organizer/*` endpoints.

**Architecture:** New system handler `agents/handlers/knowledge_organizer_handler.py` registered under agent id `knowledge-organizer` (hidden from sidebar). Two triggers: periodic (every 60 minutes) and pressure (≥10 pending). Each run processes at most 30 notes. Per note: lifecycle sweep first (pure SQL), then LLM classification (Haiku via broker), then confidence-branching into silent move vs. inbox suggestion. Lazy Linker piggy-backs on classification results. Inbox UI is a collapsible card at the top of the Knowledge page, grouped by suggestion kind. Six new API endpoints.

**Tech Stack:** Python 3.12, SQLite, LLMBroker (LiteLLM), cassette recorder for integration tests, Alpine.js, existing `BackgroundTaskScheduler`.

**Spec reference:** `docs/superpowers/specs/2026-04-08-knowledge-organize-design.md` — Sections 4, 5, 7 (Inbox card), 8 (all `/api/organizer/*` endpoints), 9 (organizer unit + integration + API tests), 11.

**Prerequisite:** Milestone A is merged. v3 schema and `POST /api/knowledge/notes` already exist.

**Out of scope:** Smart Import, Topic Map auto-Mermaid (Milestone C).

---

## File Structure

### Files created
- `src/mycelos/knowledge/organizer.py` — pure classification logic (`classify_one`, lifecycle predicates, `SILENT_CONFIDENCE` constant). No scheduler, no LLM wiring — just functions.
- `src/mycelos/agents/handlers/knowledge_organizer_handler.py` — the system handler, wires organizer to LLMBroker and scheduler.
- `src/mycelos/knowledge/inbox.py` — CRUD layer for `organizer_suggestions` (list grouped, accept, dismiss, apply).
- `tests/test_organizer_lifecycle.py` — lifecycle predicate unit tests under `freezegun`.
- `tests/test_organizer_classify.py` — classification branching unit tests with a fake broker.
- `tests/test_organizer_inbox.py` — inbox CRUD unit tests.
- `tests/integration/test_organizer_live.py` — live organizer run with cassette-recorded Haiku.
- `tests/test_organizer_api.py` — API tests for `/api/organizer/*`.

### Files modified
- `src/mycelos/app.py` — register `knowledge-organizer` handler.
- `src/mycelos/gateway/routes.py` — add six `/api/organizer/*` endpoints.
- `src/mycelos/frontend/pages/knowledge.html` — add the Organizer Inbox card at the top.
- `src/mycelos/frontend/shared/api.js` — add `loadOrganizerSuggestions`, `acceptSuggestion`, `dismissSuggestion` helpers.
- `src/mycelos/i18n/locales/en.yaml` — organizer/inbox strings.
- `src/mycelos/i18n/locales/de.yaml` — same keys, German.
- `src/mycelos/cli/doctor.py` — add `--check organizer` subcheck.
- `CHANGELOG.md`

---

## Task 1: Lifecycle predicates (pure SQL, freezegun-testable)

**Files:**
- Create: `src/mycelos/knowledge/organizer.py`
- Test: `tests/test_organizer_lifecycle.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_organizer_lifecycle.py
from __future__ import annotations

from datetime import datetime, timezone

import freezegun
import pytest

from mycelos.knowledge.organizer import (
    is_done_task_older_than,
    is_fired_reminder_past,
)


@freezegun.freeze_time("2026-04-15T12:00:00Z")
def test_done_task_older_than_7_days() -> None:
    note = {"status": "done", "updated_at": "2026-04-07T11:00:00Z", "type": "task"}
    assert is_done_task_older_than(note, days=7) is True


@freezegun.freeze_time("2026-04-15T12:00:00Z")
def test_done_task_exactly_7_days_boundary_off() -> None:
    note = {"status": "done", "updated_at": "2026-04-08T13:00:00Z", "type": "task"}
    assert is_done_task_older_than(note, days=7) is False


@freezegun.freeze_time("2026-04-15T12:00:00Z")
def test_active_task_not_archived() -> None:
    note = {"status": "active", "updated_at": "2026-01-01T00:00:00Z", "type": "task"}
    assert is_done_task_older_than(note, days=7) is False


@freezegun.freeze_time("2026-04-15T12:00:00Z")
def test_fired_reminder_past_one_day() -> None:
    note = {"reminder": True, "due": "2026-04-13T12:00:00Z"}
    assert is_fired_reminder_past(note, days=1) is True


@freezegun.freeze_time("2026-04-15T12:00:00Z")
def test_reminder_not_yet_due() -> None:
    note = {"reminder": True, "due": "2026-04-20T12:00:00Z"}
    assert is_fired_reminder_past(note, days=1) is False


@freezegun.freeze_time("2026-04-15T12:00:00Z")
def test_non_reminder_never_matches() -> None:
    note = {"reminder": False, "due": "2020-01-01T00:00:00Z"}
    assert is_fired_reminder_past(note, days=1) is False
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_organizer_lifecycle.py -v`
Expected: `ImportError: cannot import name 'is_done_task_older_than'`.

- [ ] **Step 3: Implement**

Create `src/mycelos/knowledge/organizer.py`:

```python
"""Pure organizer logic — classification branching + lifecycle predicates.

No scheduler, no LLM wiring, no storage writes. This module is the
testable core; see ``agents/handlers/knowledge_organizer_handler.py``
for the runtime glue.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol


SILENT_CONFIDENCE = 0.8


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_done_task_older_than(note: dict, *, days: int) -> bool:
    if note.get("type") != "task":
        return False
    if note.get("status") != "done":
        return False
    updated = _parse_iso(note.get("updated_at"))
    if not updated:
        return False
    now = datetime.now(tz=timezone.utc)
    return (now - updated) >= timedelta(days=days)


def is_fired_reminder_past(note: dict, *, days: int) -> bool:
    if not note.get("reminder"):
        return False
    due = _parse_iso(note.get("due"))
    if not due:
        return False
    now = datetime.now(tz=timezone.utc)
    return (now - due) >= timedelta(days=days)


@dataclass
class Classification:
    topic_path: str | None
    confidence: float
    related_note_paths: list[str]
    new_topic_name: str | None


class ClassifierCallable(Protocol):
    def __call__(self, note: dict, topics: list[str], neighbors: list[dict]) -> Classification: ...


def decide_action(
    result: Classification,
    *,
    topic_exists: bool,
) -> str:
    """Return one of: 'silent_move' | 'suggest_move' | 'suggest_new_topic'."""
    if result.confidence >= SILENT_CONFIDENCE and topic_exists and result.topic_path:
        return "silent_move"
    if result.confidence >= SILENT_CONFIDENCE and result.new_topic_name:
        return "suggest_new_topic"
    return "suggest_move"
```

- [ ] **Step 4: Run — expect pass**

Run: `pytest tests/test_organizer_lifecycle.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/knowledge/organizer.py tests/test_organizer_lifecycle.py
git commit -m "feat(knowledge): organizer lifecycle predicates + decide_action"
```

---

## Task 2: Classification branching unit test

**Files:**
- Test: `tests/test_organizer_classify.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_organizer_classify.py
from __future__ import annotations

from mycelos.knowledge.organizer import Classification, decide_action, SILENT_CONFIDENCE


def test_high_confidence_existing_topic_is_silent_move() -> None:
    r = Classification(topic_path="projects/mycelos", confidence=0.9,
                       related_note_paths=[], new_topic_name=None)
    assert decide_action(r, topic_exists=True) == "silent_move"


def test_high_confidence_but_unknown_topic_falls_back_to_suggest() -> None:
    r = Classification(topic_path="projects/mycelos", confidence=0.95,
                       related_note_paths=[], new_topic_name=None)
    assert decide_action(r, topic_exists=False) == "suggest_move"


def test_high_confidence_with_new_topic_name_is_suggest_new_topic() -> None:
    r = Classification(topic_path=None, confidence=0.92,
                       related_note_paths=[], new_topic_name="Coffee Stuff")
    assert decide_action(r, topic_exists=False) == "suggest_new_topic"


def test_low_confidence_is_suggest_move() -> None:
    r = Classification(topic_path="x", confidence=0.5,
                       related_note_paths=[], new_topic_name=None)
    assert decide_action(r, topic_exists=True) == "suggest_move"


def test_threshold_boundary() -> None:
    r = Classification(topic_path="x", confidence=SILENT_CONFIDENCE,
                       related_note_paths=[], new_topic_name=None)
    assert decide_action(r, topic_exists=True) == "silent_move"
```

- [ ] **Step 2: Run — expect pass (decide_action already exists from Task 1)**

Run: `pytest tests/test_organizer_classify.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_organizer_classify.py
git commit -m "test(knowledge): decide_action branching"
```

---

## Task 3: Inbox CRUD layer

**Files:**
- Create: `src/mycelos/knowledge/inbox.py`
- Test: `tests/test_organizer_inbox.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_organizer_inbox.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mycelos.knowledge.inbox import InboxService
from mycelos.storage.database import SQLiteStorage


@pytest.fixture
def storage(tmp_path: Path) -> SQLiteStorage:
    s = SQLiteStorage(tmp_path / "inbox.db")
    s.initialize()
    # Seed a note so the FK-ish relationship holds
    s.execute(
        "INSERT INTO knowledge_notes (path, title, content, parent_path, organizer_state) "
        "VALUES (?, ?, ?, ?, ?)",
        ("notes/x", "X", "", "notes", "pending"),
    )
    return s


def test_add_move_suggestion(storage: SQLiteStorage) -> None:
    inbox = InboxService(storage)
    sid = inbox.add(
        note_path="notes/x",
        kind="move",
        payload={"target": "projects/mycelos", "alternatives": []},
        confidence=0.65,
    )
    assert sid > 0

    pending = inbox.list_pending()
    assert len(pending["move"]) == 1
    assert pending["move"][0]["note_path"] == "notes/x"
    assert pending["move"][0]["payload"]["target"] == "projects/mycelos"


def test_accept_suggestion_marks_accepted(storage: SQLiteStorage) -> None:
    inbox = InboxService(storage)
    sid = inbox.add("notes/x", "move", {"target": "notes"}, 0.7)
    inbox.accept(sid)

    row = storage.fetchone("SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "accepted"
    assert inbox.list_pending() == {"move": [], "new_topic": [], "link": [], "refine_type": []}


def test_dismiss_suggestion_marks_dismissed(storage: SQLiteStorage) -> None:
    inbox = InboxService(storage)
    sid = inbox.add("notes/x", "link", {"from": "notes/x", "to": "notes/y"}, 0.9)
    inbox.dismiss(sid)

    row = storage.fetchone("SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "dismissed"


def test_list_pending_groups_by_kind(storage: SQLiteStorage) -> None:
    inbox = InboxService(storage)
    inbox.add("notes/x", "move", {"target": "notes"}, 0.7)
    inbox.add("notes/x", "new_topic", {"name": "Coffee"}, 0.9)
    inbox.add("notes/x", "link", {"from": "notes/x", "to": "notes/y"}, 0.8)

    grouped = inbox.list_pending()
    assert len(grouped["move"]) == 1
    assert len(grouped["new_topic"]) == 1
    assert len(grouped["link"]) == 1
    assert len(grouped["refine_type"]) == 0
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_organizer_inbox.py -v`
Expected: `ImportError: cannot import name 'InboxService'`.

- [ ] **Step 3: Implement**

Create `src/mycelos/knowledge/inbox.py`:

```python
"""CRUD layer for ``organizer_suggestions``.

Returns pending suggestions grouped by kind. Does not apply actions on
the note itself — the organizer handler is responsible for moving the
note, creating topics, or appending wikilinks after calling ``accept``.
"""
from __future__ import annotations

import json
from typing import Any

from mycelos.storage.database import SQLiteStorage


_KINDS = ("move", "new_topic", "link", "refine_type")


class InboxService:
    def __init__(self, storage: SQLiteStorage) -> None:
        self._storage = storage

    def add(
        self,
        note_path: str,
        kind: str,
        payload: dict[str, Any],
        confidence: float,
    ) -> int:
        if kind not in _KINDS:
            raise ValueError(f"unknown suggestion kind: {kind}")
        cursor = self._storage.execute(
            "INSERT INTO organizer_suggestions (note_path, kind, payload, confidence) "
            "VALUES (?, ?, ?, ?)",
            (note_path, kind, json.dumps(payload), float(confidence)),
        )
        return int(cursor.lastrowid)

    def get(self, suggestion_id: int) -> dict | None:
        row = self._storage.fetchone(
            "SELECT * FROM organizer_suggestions WHERE id=?", (suggestion_id,)
        )
        if not row:
            return None
        row["payload"] = json.loads(row["payload"])
        return row

    def list_pending(self) -> dict[str, list[dict]]:
        rows = self._storage.fetchall(
            "SELECT * FROM organizer_suggestions WHERE status='pending' "
            "ORDER BY confidence DESC, created_at DESC"
        )
        grouped: dict[str, list[dict]] = {k: [] for k in _KINDS}
        for row in rows:
            row["payload"] = json.loads(row["payload"])
            kind = row["kind"]
            if kind in grouped:
                grouped[kind].append(row)
        return grouped

    def accept(self, suggestion_id: int) -> None:
        self._storage.execute(
            "UPDATE organizer_suggestions SET status='accepted' WHERE id=?",
            (suggestion_id,),
        )

    def dismiss(self, suggestion_id: int) -> None:
        self._storage.execute(
            "UPDATE organizer_suggestions SET status='dismissed' WHERE id=?",
            (suggestion_id,),
        )
```

- [ ] **Step 4: Run — expect pass**

Run: `pytest tests/test_organizer_inbox.py -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/knowledge/inbox.py tests/test_organizer_inbox.py
git commit -m "feat(knowledge): organizer inbox CRUD"
```

---

## Task 4: `knowledge-organizer` system handler

**Files:**
- Create: `src/mycelos/agents/handlers/knowledge_organizer_handler.py`
- Modify: `src/mycelos/app.py`

- [ ] **Step 1: Read the existing handler pattern**

Run: `grep -rn "class.*Handler" src/mycelos/agents/handlers/ --include='*.py' | head -20`
Expected: a handler base class or protocol. Also inspect a simple existing handler such as `auditor_handler.py` or `evaluator_handler.py` to see the exact shape (constructor args, `run` / `handle` signature, how they call `LLMBroker`).

- [ ] **Step 2: Implement the handler**

Create `src/mycelos/agents/handlers/knowledge_organizer_handler.py`. Mirror the constructor shape of the reference handler from Step 1 — the following is the logic, wrapped in the codebase's actual handler protocol:

```python
"""Knowledge Organizer — background system handler.

Runs every 60 minutes (periodic trigger) or whenever ≥10 notes are
``organizer_state='pending'`` (pressure trigger). Per run processes at
most 30 notes through lifecycle → classification → action.

Matches the handler pattern of ``auditor_handler.py`` in the same
directory; adapt the base class import and ``handle``/``run`` signature
to whatever that file uses.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from mycelos.knowledge.inbox import InboxService
from mycelos.knowledge.organizer import (
    Classification,
    SILENT_CONFIDENCE,
    decide_action,
    is_done_task_older_than,
    is_fired_reminder_past,
)

BATCH_LIMIT = 30
PRESSURE_THRESHOLD = 10
PERIODIC_INTERVAL_MINUTES = 60


class KnowledgeOrganizerHandler:
    """System handler. Not user-facing, not registered in the sidebar."""

    agent_id = "knowledge-organizer"
    model = "haiku"

    def __init__(self, app: Any) -> None:
        self._app = app

    # ---- trigger entry ------------------------------------------------

    def run(self, user_id: str = "default") -> dict:
        storage = self._app.storage
        inbox = InboxService(storage)
        knowledge = self._app.knowledge_for(user_id)

        pending = storage.fetchall(
            "SELECT * FROM knowledge_notes WHERE organizer_state='pending' "
            "LIMIT ?",
            (BATCH_LIMIT,),
        )

        archived = 0
        moved = 0
        suggested = 0
        linked = 0

        topics = [t["path"] for t in knowledge.list_topics()]

        for note in pending:
            # Lifecycle first — pure SQL, no LLM
            if is_done_task_older_than(note, days=7):
                self._archive_note(storage, note)
                self._app.audit.log(
                    "organizer.archive",
                    user_id=user_id,
                    details={"path": note["path"], "reason": "done>7d"},
                )
                archived += 1
                continue
            if is_fired_reminder_past(note, days=1):
                self._archive_note(storage, note)
                self._app.audit.log(
                    "organizer.archive",
                    user_id=user_id,
                    details={"path": note["path"], "reason": "reminder_past"},
                )
                archived += 1
                continue

            # Classification
            result = self._classify(note, topics)
            topic_exists = result.topic_path in topics if result.topic_path else False
            action = decide_action(result, topic_exists=topic_exists)

            if action == "silent_move":
                knowledge.move(note["path"], result.topic_path)
                self._mark_state(storage, note["path"], "ok")
                self._app.audit.log(
                    "organizer.move",
                    user_id=user_id,
                    details={"from": note["path"], "to": result.topic_path,
                             "confidence": result.confidence},
                )
                moved += 1
            elif action == "suggest_new_topic":
                inbox.add(
                    note_path=note["path"],
                    kind="new_topic",
                    payload={"name": result.new_topic_name, "members": [note["path"]]},
                    confidence=result.confidence,
                )
                self._mark_seen(storage, note["path"])
                suggested += 1
            else:  # suggest_move
                inbox.add(
                    note_path=note["path"],
                    kind="move",
                    payload={
                        "target": result.topic_path,
                        "alternatives": [],
                        "reason": "low_confidence",
                    },
                    confidence=result.confidence,
                )
                self._mark_seen(storage, note["path"])
                suggested += 1

            # Lazy Linker
            for related in result.related_note_paths or []:
                inbox.add(
                    note_path=note["path"],
                    kind="link",
                    payload={"from": note["path"], "to": related},
                    confidence=result.confidence,
                )
                linked += 1

        return {
            "processed": len(pending),
            "archived": archived,
            "moved": moved,
            "suggested": suggested,
            "linked": linked,
        }

    # ---- classification ----------------------------------------------

    def _classify(self, note: dict, topics: list[str]) -> Classification:
        prompt = self._build_prompt(note, topics)
        broker = self._app.llm_broker
        raw = broker.complete(
            model=self.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,
            temperature=0.0,
        )
        return self._parse_classification(raw)

    def _build_prompt(self, note: dict, topics: list[str]) -> str:
        topic_list = "\n".join(f"- {t}" for t in topics) or "(none yet)"
        body = (note.get("content") or "")[:400]
        return (
            f"Title: {note.get('title')}\n"
            f"Body: {body}\n\n"
            f"Existing topics:\n{topic_list}\n\n"
            f"Respond as JSON with keys: topic_path, confidence (0..1), "
            f"related_note_paths (list), new_topic_name (optional)."
        )

    def _parse_classification(self, raw: str) -> Classification:
        try:
            data = json.loads(raw.strip())
        except Exception:
            return Classification(
                topic_path=None, confidence=0.0, related_note_paths=[], new_topic_name=None
            )
        return Classification(
            topic_path=data.get("topic_path"),
            confidence=float(data.get("confidence", 0.0)),
            related_note_paths=list(data.get("related_note_paths") or []),
            new_topic_name=data.get("new_topic_name"),
        )

    # ---- state helpers ------------------------------------------------

    def _archive_note(self, storage, note: dict) -> None:
        storage.execute(
            "UPDATE knowledge_notes SET status='archived', organizer_state='archived', "
            "organizer_seen_at=? WHERE path=?",
            (datetime.now(tz=timezone.utc).isoformat(), note["path"]),
        )

    def _mark_state(self, storage, path: str, state: str) -> None:
        storage.execute(
            "UPDATE knowledge_notes SET organizer_state=?, organizer_seen_at=? WHERE path=?",
            (state, datetime.now(tz=timezone.utc).isoformat(), path),
        )

    def _mark_seen(self, storage, path: str) -> None:
        storage.execute(
            "UPDATE knowledge_notes SET organizer_seen_at=? WHERE path=?",
            (datetime.now(tz=timezone.utc).isoformat(), path),
        )


_SYSTEM_PROMPT = (
    "You classify short personal notes into existing topics. "
    "Return strict JSON. If no topic fits and the note is novel, "
    "propose a new_topic_name. Keep confidence honest — <0.8 means uncertain."
)
```

- [ ] **Step 3: Register the handler in `app.py`**

Open `src/mycelos/app.py`. Find the handler registration block (search for `evaluator-agent` or `auditor-agent`). Add the organizer next to them:

```python
from mycelos.agents.handlers.knowledge_organizer_handler import KnowledgeOrganizerHandler
# ...
handlers["knowledge-organizer"] = KnowledgeOrganizerHandler(self)
```

Also register the scheduled trigger in the same place the existing background tasks are registered. If the codebase uses `BackgroundTaskScheduler.add_periodic(name, minutes, fn)`:

```python
self.background_scheduler.add_periodic(
    name="knowledge_organizer_periodic",
    minutes=60,
    fn=lambda: handlers["knowledge-organizer"].run("default"),
)
```

If the pattern is different, match it — do not invent a new scheduler API.

- [ ] **Step 4: Commit (no unit test yet — the handler is exercised by the integration test in Task 6)**

```bash
git add src/mycelos/agents/handlers/knowledge_organizer_handler.py src/mycelos/app.py
git commit -m "feat(knowledge): knowledge-organizer system handler + periodic trigger"
```

---

## Task 5: `/api/organizer/*` endpoints

**Files:**
- Modify: `src/mycelos/gateway/routes.py`
- Test: `tests/test_organizer_api.py`

- [ ] **Step 1: Write the failing API test**

```python
# tests/test_organizer_api.py
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from mycelos.knowledge.inbox import InboxService
from tests.integration.conftest import integration_app  # type: ignore


def _seed_note(app) -> str:
    knowledge = app.knowledge_for("default")
    return knowledge.write(title="Seeded", content="body", topic="notes")


def test_list_suggestions_empty(integration_app) -> None:
    client = TestClient(integration_app)
    resp = client.get("/api/organizer/suggestions", headers={"X-User-Id": "default"})
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"move": [], "new_topic": [], "link": [], "refine_type": []}


def test_list_suggestions_after_seed(integration_app) -> None:
    app_obj = integration_app.state.mycelos_app
    path = _seed_note(app_obj)
    inbox = InboxService(app_obj.storage)
    inbox.add(path, "move", {"target": "projects/mycelos"}, 0.7)

    client = TestClient(integration_app)
    resp = client.get("/api/organizer/suggestions", headers={"X-User-Id": "default"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["move"]) == 1
    assert data["move"][0]["note_path"] == path


def test_accept_suggestion_flips_status(integration_app) -> None:
    app_obj = integration_app.state.mycelos_app
    path = _seed_note(app_obj)
    inbox = InboxService(app_obj.storage)
    sid = inbox.add(path, "move", {"target": "projects/mycelos"}, 0.9)

    client = TestClient(integration_app)
    resp = client.post(f"/api/organizer/suggestions/{sid}/accept",
                       headers={"X-User-Id": "default"})
    assert resp.status_code == 200

    row = app_obj.storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,)
    )
    assert row["status"] == "accepted"


def test_dismiss_suggestion_flips_status(integration_app) -> None:
    app_obj = integration_app.state.mycelos_app
    path = _seed_note(app_obj)
    inbox = InboxService(app_obj.storage)
    sid = inbox.add(path, "link", {"from": path, "to": "notes/y"}, 0.85)

    client = TestClient(integration_app)
    resp = client.post(f"/api/organizer/suggestions/{sid}/dismiss",
                       headers={"X-User-Id": "default"})
    assert resp.status_code == 200

    row = app_obj.storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,)
    )
    assert row["status"] == "dismissed"


def test_force_run_endpoint_returns_counts(integration_app) -> None:
    client = TestClient(integration_app)
    resp = client.post("/api/organizer/run", headers={"X-User-Id": "default"})
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) >= {"processed", "archived", "moved", "suggested", "linked"}
```

- [ ] **Step 2: Run — expect 404s on every test**

Run: `pytest tests/test_organizer_api.py -v`
Expected: All 5 tests FAIL with 404.

- [ ] **Step 3: Add the endpoints**

In `src/mycelos/gateway/routes.py`:

```python
from mycelos.knowledge.inbox import InboxService


@router.get("/api/organizer/suggestions")
async def list_organizer_suggestions(request: Request) -> JSONResponse:
    user_id = request.headers.get("X-User-Id", "default")
    app = request.app.state.mycelos_app
    inbox = InboxService(app.storage)
    return JSONResponse(inbox.list_pending())


@router.post("/api/organizer/suggestions/{sid:int}/accept")
async def accept_organizer_suggestion(request: Request, sid: int) -> JSONResponse:
    user_id = request.headers.get("X-User-Id", "default")
    app = request.app.state.mycelos_app
    inbox = InboxService(app.storage)
    sug = inbox.get(sid)
    if not sug:
        return JSONResponse({"error": "not found"}, status_code=404)

    knowledge = app.knowledge_for(user_id)
    kind = sug["kind"]
    payload = sug["payload"]

    if kind == "move":
        knowledge.move(sug["note_path"], payload["target"])
    elif kind == "new_topic":
        new_topic = knowledge.create_topic(payload["name"])
        for member in payload.get("members", []):
            knowledge.move(member, new_topic)
    elif kind == "link":
        knowledge.append_related_link(payload["from"], payload["to"])
    elif kind == "refine_type":
        knowledge.update_type(sug["note_path"], payload["target"])

    inbox.accept(sid)
    app.audit.log(
        "organizer.suggestion.accepted",
        user_id=user_id,
        details={"id": sid, "kind": kind},
    )
    return JSONResponse({"ok": True})


@router.post("/api/organizer/suggestions/{sid:int}/dismiss")
async def dismiss_organizer_suggestion(request: Request, sid: int) -> JSONResponse:
    user_id = request.headers.get("X-User-Id", "default")
    app = request.app.state.mycelos_app
    inbox = InboxService(app.storage)
    if not inbox.get(sid):
        return JSONResponse({"error": "not found"}, status_code=404)
    inbox.dismiss(sid)
    app.audit.log(
        "organizer.suggestion.dismissed",
        user_id=user_id,
        details={"id": sid},
    )
    return JSONResponse({"ok": True})


@router.post("/api/organizer/run")
async def force_organizer_run(request: Request) -> JSONResponse:
    user_id = request.headers.get("X-User-Id", "default")
    app = request.app.state.mycelos_app
    handler = app.agent_handlers["knowledge-organizer"]
    return JSONResponse(handler.run(user_id))
```

- [ ] **Step 4: Add `KnowledgeService.move`, `create_topic`, `append_related_link`, `update_type` if they don't yet exist**

Run: `grep -n "def move\|def create_topic\|append_related_link\|def update_type" src/mycelos/knowledge/service.py`

For each method that is missing, add it to `KnowledgeService`:

```python
def move(self, note_path: str, target_topic: str) -> None:
    self._storage.execute(
        "UPDATE knowledge_notes SET parent_path=?, organizer_state='ok' WHERE path=?",
        (target_topic, note_path),
    )

def append_related_link(self, note_path: str, target_path: str) -> None:
    note = self._storage.fetchone(
        "SELECT content FROM knowledge_notes WHERE path=?", (note_path,)
    )
    if not note:
        return
    body = note["content"] or ""
    heading = "## Verwandt"
    link = f"- [[{target_path}]]"
    if heading not in body:
        body = body.rstrip() + f"\n\n{heading}\n{link}\n"
    elif link not in body:
        body = body.rstrip() + f"\n{link}\n"
    self._storage.execute(
        "UPDATE knowledge_notes SET content=? WHERE path=?",
        (body, note_path),
    )
    # Re-write the file on disk using the existing path resolver
    file_path = self._knowledge_dir / (note_path + ".md")
    if file_path.exists():
        existing = file_path.read_text()
        if heading not in existing:
            file_path.write_text(existing.rstrip() + f"\n\n{heading}\n{link}\n")
        elif link not in existing:
            file_path.write_text(existing.rstrip() + f"\n{link}\n")

def update_type(self, note_path: str, new_type: str) -> None:
    self._storage.execute(
        "UPDATE knowledge_notes SET type=? WHERE path=?",
        (new_type, note_path),
    )
```

`create_topic` already exists in the codebase (used by `auto_classify`); re-use it.

- [ ] **Step 5: Run the API tests**

Run: `pytest tests/test_organizer_api.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/gateway/routes.py src/mycelos/knowledge/service.py tests/test_organizer_api.py
git commit -m "feat(knowledge): /api/organizer/* endpoints + service helpers"
```

---

## Task 6: Organizer integration test (cassette-recorded Haiku)

**Files:**
- Create: `tests/integration/test_organizer_live.py`

- [ ] **Step 1: Write the integration test**

```python
# tests/integration/test_organizer_live.py
from __future__ import annotations

import pytest

from mycelos.knowledge.inbox import InboxService


@pytest.mark.integration
def test_organizer_end_to_end(integration_app) -> None:
    """Seed 5 notes, run the organizer, assert every note changed state."""
    app_obj = integration_app.state.mycelos_app
    knowledge = app_obj.knowledge_for("default")

    # Seed a topic so at least one silent_move is possible
    knowledge.create_topic("Coffee Stuff", tags=["coffee"])

    seeds = [
        ("Coffee with Lisa", "Meet Lisa for coffee next week"),
        ("Espresso grinder tuning", "Notes on grinder adjustment"),
        ("Random idea", "Mycelos should support vision models"),
        ("Shopping list", "Milk, bread, eggs"),
        ("TODO: refactor parser", "Deterministic parser cleanup"),
    ]
    for title, body in seeds:
        knowledge.write(title=title, content=body)  # bucketing kicks in

    handler = app_obj.agent_handlers["knowledge-organizer"]
    result = handler.run("default")

    assert result["processed"] == 5
    assert result["archived"] == 0
    # At least one note should be handled (silent move OR suggestion)
    assert result["moved"] + result["suggested"] >= 5

    inbox = InboxService(app_obj.storage)
    pending = inbox.list_pending()
    total = sum(len(v) for v in pending.values())
    assert total >= 1  # some classifications should land in the inbox
```

- [ ] **Step 2: Run the test — first run records the cassette**

Run: `MYCELOS_LLM_CASSETTE=auto ANTHROPIC_API_KEY=... pytest tests/integration/test_organizer_live.py -v`
Expected: PASS, and a new cassette file appears under `tests/cassettes/`.

Then run it again in replay mode:

Run: `pytest tests/integration/test_organizer_live.py -v`
Expected: PASS without an API key.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_organizer_live.py tests/cassettes/
git commit -m "test(knowledge): integration test for organizer run with cassette"
```

---

## Task 7: Organizer Inbox card UI

**Files:**
- Modify: `src/mycelos/frontend/pages/knowledge.html`
- Modify: `src/mycelos/frontend/shared/api.js`
- Modify: `src/mycelos/i18n/locales/en.yaml`
- Modify: `src/mycelos/i18n/locales/de.yaml`

- [ ] **Step 1: Add i18n keys**

Edit `en.yaml`:

```yaml
organizer:
  inbox_heading: "Organizer Inbox"
  move_group: "{count} notes want a different topic"
  new_topic_group: "{count} new topics proposed"
  link_group: "{count} link suggestions"
  accept: "Accept"
  dismiss: "Dismiss"
  walk_through: "Go through one by one"
```

Edit `de.yaml`:

```yaml
organizer:
  inbox_heading: "Organizer Inbox"
  move_group: "{count} Notizen wollen in ein anderes Topic"
  new_topic_group: "{count} neue Topics vorgeschlagen"
  link_group: "{count} Link-Vorschläge"
  accept: "Akzeptieren"
  dismiss: "Verwerfen"
  walk_through: "Einzeln durchgehen"
```

- [ ] **Step 2: Add API helpers**

Append to `src/mycelos/frontend/shared/api.js`:

```javascript
export async function loadOrganizerSuggestions() {
  const resp = await fetch('/api/organizer/suggestions', {
    headers: { 'X-User-Id': 'default' },
  });
  return resp.json();
}

export async function acceptSuggestion(id) {
  await fetch(`/api/organizer/suggestions/${id}/accept`, {
    method: 'POST', headers: { 'X-User-Id': 'default' },
  });
}

export async function dismissSuggestion(id) {
  await fetch(`/api/organizer/suggestions/${id}/dismiss`, {
    method: 'POST', headers: { 'X-User-Id': 'default' },
  });
}
```

- [ ] **Step 3: Add the inbox card to `knowledge.html`**

At the very top of the main content column of `knowledge.html` (above the topic tree), insert this Alpine component:

```html
<div x-data="organizerInbox()" x-init="load()"
     x-show="hasPending()"
     class="organizer-inbox-card"
     style="background:#1a1d24;border:1px solid rgba(106,214,255,0.3);
            border-radius:12px;padding:16px;margin-bottom:16px">
  <div style="font-size:14px;font-weight:600;color:#fff;margin-bottom:12px"
       x-text="$t('organizer.inbox_heading')"></div>

  <template x-if="groups.move.length">
    <div class="inbox-group" style="margin-bottom:12px">
      <div style="color:#cdd5e0;font-size:13px;margin-bottom:6px"
           x-text="$t('organizer.move_group', { count: groups.move.length })"></div>
      <template x-for="s in groups.move.slice(0, 5)" :key="s.id">
        <div class="inbox-item" style="display:flex;justify-content:space-between;
             align-items:center;padding:6px 0;font-size:12px;color:#fff">
          <span x-text="`${s.note_path} → ${s.payload.target}`"></span>
          <span>
            <button @click="accept(s.id)" class="btn-small" x-text="$t('organizer.accept')"></button>
            <button @click="dismiss(s.id)" class="btn-small" x-text="$t('organizer.dismiss')"></button>
          </span>
        </div>
      </template>
    </div>
  </template>

  <template x-if="groups.new_topic.length">
    <div class="inbox-group" style="margin-bottom:12px">
      <div style="color:#cdd5e0;font-size:13px;margin-bottom:6px"
           x-text="$t('organizer.new_topic_group', { count: groups.new_topic.length })"></div>
      <template x-for="s in groups.new_topic" :key="s.id">
        <div class="inbox-item" style="display:flex;justify-content:space-between;
             align-items:center;padding:6px 0;font-size:12px;color:#fff">
          <span x-text="s.payload.name"></span>
          <span>
            <button @click="accept(s.id)" class="btn-small" x-text="$t('organizer.accept')"></button>
            <button @click="dismiss(s.id)" class="btn-small" x-text="$t('organizer.dismiss')"></button>
          </span>
        </div>
      </template>
    </div>
  </template>

  <template x-if="groups.link.length">
    <div class="inbox-group">
      <div style="color:#cdd5e0;font-size:13px;margin-bottom:6px"
           x-text="$t('organizer.link_group', { count: groups.link.length })"></div>
      <template x-for="s in groups.link" :key="s.id">
        <div class="inbox-item" style="display:flex;justify-content:space-between;
             align-items:center;padding:6px 0;font-size:12px;color:#fff">
          <span x-text="`${s.payload.from} ↔ ${s.payload.to}`"></span>
          <span>
            <button @click="accept(s.id)" class="btn-small" x-text="$t('organizer.accept')"></button>
            <button @click="dismiss(s.id)" class="btn-small" x-text="$t('organizer.dismiss')"></button>
          </span>
        </div>
      </template>
    </div>
  </template>
</div>

<script type="module">
  import { loadOrganizerSuggestions, acceptSuggestion, dismissSuggestion }
    from '/static/shared/api.js';

  window.organizerInbox = function () {
    return {
      groups: { move: [], new_topic: [], link: [], refine_type: [] },
      async load() {
        this.groups = await loadOrganizerSuggestions();
      },
      hasPending() {
        return (this.groups.move.length + this.groups.new_topic.length +
                this.groups.link.length + this.groups.refine_type.length) > 0;
      },
      async accept(id) { await acceptSuggestion(id); await this.load(); },
      async dismiss(id) { await dismissSuggestion(id); await this.load(); },
    };
  };
</script>
```

- [ ] **Step 4: Manually verify**

Load `/knowledge`. If suggestions exist (or after running `POST /api/organizer/run`), the inbox card is visible and Accept/Dismiss buttons flip state and hide the item.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/frontend/pages/knowledge.html src/mycelos/frontend/shared/api.js src/mycelos/i18n/locales/en.yaml src/mycelos/i18n/locales/de.yaml
git commit -m "feat(frontend): organizer inbox card"
```

---

## Task 8: Doctor `--check organizer` subcheck

**Files:**
- Modify: `src/mycelos/cli/doctor.py`

- [ ] **Step 1: Read the existing check pattern**

Run: `grep -n "def _check_\|--check" src/mycelos/cli/doctor.py | head -20`
Note how existing subchecks like `_check_reminders` are structured.

- [ ] **Step 2: Add the check**

Add to `src/mycelos/cli/doctor.py`, following the existing `_check_*` pattern:

```python
def _check_organizer(app) -> dict:
    pending = app.storage.fetchone(
        "SELECT COUNT(*) AS c FROM knowledge_notes WHERE organizer_state='pending'"
    )["c"]
    last_run = app.storage.fetchone(
        "SELECT MAX(created_at) AS ts FROM audit_events WHERE event_type LIKE 'organizer.%'"
    )
    return {
        "pending": pending,
        "last_run": last_run["ts"] if last_run else None,
        "status": "ok" if pending < 100 else "backlog",
    }
```

Then register it in the subcheck dispatch (the dict/branch that handles the `--check` CLI flag) next to the existing entries.

- [ ] **Step 3: Commit**

```bash
git add src/mycelos/cli/doctor.py
git commit -m "feat(doctor): add organizer subcheck"
```

---

## Task 9: CHANGELOG + final verification

- [ ] **Step 1: Add changelog entries**

Under the current week in `CHANGELOG.md`:

```markdown
### Knowledge — Lazy Organizer + Inbox (Milestone B)
- New `knowledge-organizer` system handler (Haiku): periodic (60m) + pressure (≥10 pending) triggers, batch limit 30
- Lifecycle: done tasks >7d → archived; fired reminders >1d past → archived
- Classification: silent move at confidence ≥0.8 on existing topic; inbox suggestion otherwise
- Lazy Linker: append-only `## Verwandt` wikilinks via accepted suggestions
- Six new `/api/organizer/*` endpoints
- Organizer Inbox card at the top of the Knowledge page
- `mycelos doctor --check organizer` subcheck
```

- [ ] **Step 2: Run the fast test suite**

Run: `pytest tests/test_organizer_lifecycle.py tests/test_organizer_classify.py tests/test_organizer_inbox.py tests/test_organizer_api.py -v`
Expected: All PASS.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): Knowledge Milestone B — Lazy Organizer + Inbox"
```
