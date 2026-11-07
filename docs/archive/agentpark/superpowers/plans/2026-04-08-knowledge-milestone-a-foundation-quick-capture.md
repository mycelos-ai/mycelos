# Knowledge Milestone A — Foundation + Quick Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship deterministic bucketing, v3 migration, bilingual note parser, `POST /api/knowledge/notes`, Cmd+K Quick Capture modal, and the "Pending" Knowledge-page rename as one cohesive, user-visible milestone.

**Architecture:** Add a v3 SQLite migration (`organizer_state` column + `organizer_suggestions` table). Introduce a pure `bucket_note()` helper in the knowledge service and wire it into every note-creation path. Ship a DE+EN deterministic note parser in Python *and* JavaScript, both driven by a shared JSON fixture. Add a `POST /api/knowledge/notes` endpoint and a `quick-capture.html` partial included by every sidebar-layout page, bound via global `Cmd/Ctrl+K`. Rename "Uncategorized" → "Pending" in the knowledge page and add a sidebar footer hint.

**Tech Stack:** Python 3.12, SQLite (WAL), FastAPI/Starlette routes in `gateway/routes.py`, Alpine.js + vanilla JS for frontend, Playwright for E2E, pytest + Hypothesis for unit tests.

**Spec reference:** `docs/superpowers/specs/2026-04-08-knowledge-organize-design.md` — Sections 1, 2, 7 (partial: Pending rename, Cmd+K hint), 8 (`POST /api/knowledge/notes` only), 9 (unit + migration + Playwright), 10, 11.

**Out of scope for this milestone (covered by Milestones B and C):** Lazy Organizer agent/handler, Inbox UI, Smart Import, Topic Map auto-Mermaid, lifecycle archiving, `POST /api/knowledge/import`, `/api/organizer/*` endpoints. The v3 schema is fully introduced here so later milestones can assume it exists.

---

## File Structure

### Files created
- `src/mycelos/knowledge/parse_note.py` — Python port of the deterministic DE+EN note parser. Pure functions, no I/O.
- `src/mycelos/frontend/shared/parse-note.js` — JS port of the same parser. Exports `parseNoteText(text)`.
- `src/mycelos/frontend/shared/quick-capture.html` — Alpine.js component: modal, inline parse chips, toast, keyboard binding.
- `tests/fixtures/parse-note-vectors.json` — shared DE+EN test vectors loaded by both the Python tests and the Playwright tests.
- `tests/test_parse_note.py` — unit tests for `parse_note.py` driven by the vectors file.
- `tests/test_bucket_note.py` — unit tests for the `bucket_note` helper.
- `tests/test_knowledge_notes_api.py` — API tests for the new `POST /api/knowledge/notes` endpoint.
- `tests/test_knowledge_v3_migration.py` — migration test verifying v3 columns + `organizer_suggestions` table.
- `tests/e2e/test_quick_capture.py` — Playwright E2E test for Cmd+K flow.

### Files modified
- `src/mycelos/storage/database.py` — add three entries to the `_MIGRATIONS` list and the `organizer_suggestions` `CREATE TABLE IF NOT EXISTS` to `_ensure_schema`.
- `src/mycelos/storage/schema.sql` — add the `organizer_suggestions` table and the two new `knowledge_notes` columns so fresh installs get them.
- `src/mycelos/knowledge/service.py` — add `bucket_note()` helper; call it from `write()` when `topic` is empty; set `organizer_state='pending'` on every new note.
- `src/mycelos/gateway/routes.py` — add `POST /api/knowledge/notes` endpoint.
- `src/mycelos/frontend/pages/knowledge.html` — rename the "Uncategorized" bucket to "Pending"; add an empty state; include `quick-capture.html`.
- `src/mycelos/frontend/pages/chat.html` — include `quick-capture.html`.
- `src/mycelos/frontend/pages/workflows.html` — include `quick-capture.html`.
- `src/mycelos/frontend/pages/connectors.html` — include `quick-capture.html`.
- `src/mycelos/frontend/pages/settings.html` — include `quick-capture.html`.
- `src/mycelos/frontend/pages/agents.html` — include `quick-capture.html`.
- `src/mycelos/frontend/pages/dashboard.html` — include `quick-capture.html`.
- `src/mycelos/frontend/shared/sidebar.html` — add `⌘K · Quick Capture` footer hint below Recent Sessions.
- `src/mycelos/i18n/locales/en.yaml` — add `quick_capture.*` and `knowledge.pending` keys.
- `src/mycelos/i18n/locales/de.yaml` — same keys in German.
- `CHANGELOG.md` — add entries under the current week.

---

## Task 1: v3 Migration — schema additions

**Files:**
- Modify: `src/mycelos/storage/schema.sql`
- Modify: `src/mycelos/storage/database.py` (the `_MIGRATIONS` list and `_ensure_schema`)
- Test: `tests/test_knowledge_v3_migration.py`

- [ ] **Step 1: Write the failing migration test**

Create `tests/test_knowledge_v3_migration.py`:

```python
"""v3 migration: organizer_state + organizer_seen_at columns + organizer_suggestions table."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from mycelos.storage.database import SQLiteStorage


def test_v3_migration_adds_columns(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "m.db")
    storage.initialize()

    cols = {row["name"] for row in storage.fetchall("PRAGMA table_info(knowledge_notes)")}
    assert "organizer_state" in cols
    assert "organizer_seen_at" in cols


def test_v3_migration_creates_suggestions_table(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "m.db")
    storage.initialize()

    rows = storage.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='organizer_suggestions'"
    )
    assert len(rows) == 1

    cols = {row["name"] for row in storage.fetchall("PRAGMA table_info(organizer_suggestions)")}
    assert cols >= {"id", "note_path", "kind", "payload", "confidence", "created_at", "status"}


def test_v3_migration_creates_pending_index(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "m.db")
    storage.initialize()

    rows = storage.fetchall(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_organizer_pending'"
    )
    assert len(rows) == 1


def test_v3_migration_defaults_existing_notes_to_pending(tmp_path: Path) -> None:
    """A v2-era DB without organizer_state gets the column with default 'pending'."""
    db = tmp_path / "legacy.db"
    # Hand-craft a legacy v2 knowledge_notes row without the v3 columns.
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE knowledge_notes (path TEXT PRIMARY KEY, title TEXT, content TEXT, "
        "parent_path TEXT, reminder INTEGER, sort_order INTEGER)"
    )
    conn.execute(
        "INSERT INTO knowledge_notes (path, title, content) VALUES ('notes/x', 'X', '')"
    )
    conn.commit()
    conn.close()

    storage = SQLiteStorage(db)
    # Trigger migrations by touching the DB
    storage._get_connection()

    row = storage.fetchone("SELECT organizer_state FROM knowledge_notes WHERE path='notes/x'")
    assert row is not None
    assert row["organizer_state"] == "pending"
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `pytest tests/test_knowledge_v3_migration.py -v`
Expected: All four tests FAIL because the new columns/table don't exist.

- [ ] **Step 3: Add v3 columns to `_MIGRATIONS` list**

Edit `src/mycelos/storage/database.py`. Locate the `_MIGRATIONS` list inside `_ensure_schema` (around line 68) and append these two entries at the end, just before the closing `]`:

```python
            ("knowledge_notes", "organizer_state", "TEXT NOT NULL DEFAULT 'pending'"),
            ("knowledge_notes", "organizer_seen_at", "TEXT"),
```

- [ ] **Step 4: Add `organizer_suggestions` table + index to `_ensure_schema`**

Still in `src/mycelos/storage/database.py`, immediately after the `_MIGRATIONS` for loop (after the `pass  # Column might already exist in some edge case` line), append:

```python
        # v3: organizer_suggestions table — idempotent.
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS organizer_suggestions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                note_path  TEXT NOT NULL,
                kind       TEXT NOT NULL,
                payload    TEXT NOT NULL,
                confidence REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                status     TEXT NOT NULL DEFAULT 'pending'
            );
            CREATE INDEX IF NOT EXISTS idx_organizer_pending
                ON organizer_suggestions(status) WHERE status = 'pending';
            """
        )
        self._conn.commit()
```

Note: `note_path` intentionally has no `REFERENCES knowledge_notes(path)` here because `CREATE TABLE IF NOT EXISTS` cannot be re-applied when the referenced table is empty during migration. The application enforces the relationship; a future migration can add the FK if needed.

- [ ] **Step 5: Add the same schema to `schema.sql` for fresh installs**

Edit `src/mycelos/storage/schema.sql`. Find the `knowledge_notes` `CREATE TABLE` block and add these two columns at the end of the column list (before the closing `)`):

```sql
    organizer_state TEXT NOT NULL DEFAULT 'pending',
    organizer_seen_at TEXT,
```

Then append at the very bottom of the file:

```sql
CREATE TABLE IF NOT EXISTS organizer_suggestions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    note_path  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    status     TEXT NOT NULL DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS idx_organizer_pending
    ON organizer_suggestions(status) WHERE status = 'pending';
```

- [ ] **Step 6: Run migration tests to confirm they pass**

Run: `pytest tests/test_knowledge_v3_migration.py -v`
Expected: All four tests PASS.

- [ ] **Step 7: Run the full storage/knowledge test suite to check for regressions**

Run: `pytest tests/test_storage.py tests/test_knowledge.py -v`
Expected: PASS (existing tests untouched, no breakage).

- [ ] **Step 8: Commit**

```bash
git add src/mycelos/storage/database.py src/mycelos/storage/schema.sql tests/test_knowledge_v3_migration.py
git commit -m "feat(knowledge): v3 migration — organizer_state + organizer_suggestions"
```

---

## Task 2: Shared parser test vectors

**Files:**
- Create: `tests/fixtures/parse-note-vectors.json`

- [ ] **Step 1: Create the vectors file**

Create `tests/fixtures/parse-note-vectors.json`:

```json
{
  "description": "Shared DE+EN test vectors for parse_note. Loaded by both Python unit tests and Playwright E2E tests. 'due' values are expressed as offsets from a fixed reference time (see tests/test_parse_note.py REFERENCE_NOW) so results are deterministic.",
  "reference_now": "2026-04-08T10:00:00Z",
  "vectors": [
    {
      "name": "plain_note_en",
      "input": "Buy milk on the way home",
      "expected": {"type": "note", "due": null, "tags": [], "wikilinks": [], "reminder": false}
    },
    {
      "name": "plain_note_de",
      "input": "Milch auf dem Heimweg kaufen",
      "expected": {"type": "note", "due": null, "tags": [], "wikilinks": [], "reminder": false}
    },
    {
      "name": "todo_prefix_en",
      "input": "TODO: refactor the parser",
      "expected": {"type": "task", "due": null, "tags": [], "wikilinks": [], "reminder": false}
    },
    {
      "name": "todo_prefix_de",
      "input": "AUFGABE: Parser umbauen",
      "expected": {"type": "task", "due": null, "tags": [], "wikilinks": [], "reminder": false}
    },
    {
      "name": "remind_me_en",
      "input": "remind me in 5 minutes to stretch",
      "expected": {"type": "task", "due": "2026-04-08T10:05:00Z", "tags": [], "wikilinks": [], "reminder": true}
    },
    {
      "name": "remind_me_de",
      "input": "erinnere mich in 5 minuten zu strecken",
      "expected": {"type": "task", "due": "2026-04-08T10:05:00Z", "tags": [], "wikilinks": [], "reminder": true}
    },
    {
      "name": "tomorrow_en",
      "input": "Call Lisa tomorrow 2pm",
      "expected": {"type": "task", "due": "2026-04-09T14:00:00Z", "tags": [], "wikilinks": [], "reminder": false}
    },
    {
      "name": "tomorrow_de",
      "input": "Lisa morgen 14 Uhr anrufen",
      "expected": {"type": "task", "due": "2026-04-09T14:00:00Z", "tags": [], "wikilinks": [], "reminder": false}
    },
    {
      "name": "tags_en",
      "input": "Review docs #project #urgent",
      "expected": {"type": "note", "due": null, "tags": ["project", "urgent"], "wikilinks": [], "reminder": false}
    },
    {
      "name": "tags_de",
      "input": "Doku prüfen #projekt #dringend",
      "expected": {"type": "note", "due": null, "tags": ["projekt", "dringend"], "wikilinks": [], "reminder": false}
    },
    {
      "name": "wikilink_en",
      "input": "See [[notes/architecture]] for context",
      "expected": {"type": "note", "due": null, "tags": [], "wikilinks": ["notes/architecture"], "reminder": false}
    },
    {
      "name": "wikilink_de",
      "input": "Siehe [[notes/architecture]] für kontext",
      "expected": {"type": "note", "due": null, "tags": [], "wikilinks": ["notes/architecture"], "reminder": false}
    },
    {
      "name": "explicit_date_de",
      "input": "14.04. 15:00 Arzttermin",
      "expected": {"type": "task", "due": "2026-04-14T15:00:00Z", "tags": [], "wikilinks": [], "reminder": false}
    },
    {
      "name": "in_hours_en",
      "input": "remind me in 2 hours to check the oven",
      "expected": {"type": "task", "due": "2026-04-08T12:00:00Z", "tags": [], "wikilinks": [], "reminder": true}
    },
    {
      "name": "in_hours_de",
      "input": "erinnere mich in 2 stunden den ofen zu checken",
      "expected": {"type": "task", "due": "2026-04-08T12:00:00Z", "tags": [], "wikilinks": [], "reminder": true}
    }
  ]
}
```

- [ ] **Step 2: Commit**

```bash
git add tests/fixtures/parse-note-vectors.json
git commit -m "test(knowledge): shared DE+EN parser test vectors"
```

---

## Task 3: Python `parse_note` module

**Files:**
- Create: `src/mycelos/knowledge/parse_note.py`
- Test: `tests/test_parse_note.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_parse_note.py`:

```python
"""Unit tests for mycelos.knowledge.parse_note — DE+EN deterministic note parser."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mycelos.knowledge.parse_note import parse_note_text

FIXTURE = Path(__file__).parent / "fixtures" / "parse-note-vectors.json"
VECTORS = json.loads(FIXTURE.read_text())
REFERENCE_NOW = datetime.fromisoformat(VECTORS["reference_now"].replace("Z", "+00:00"))


@pytest.mark.parametrize("vector", VECTORS["vectors"], ids=lambda v: v["name"])
def test_parse_note_vectors(vector: dict) -> None:
    result = parse_note_text(vector["input"], now=REFERENCE_NOW)
    expected = vector["expected"]
    assert result["type"] == expected["type"], f"type mismatch for {vector['name']}"
    assert result["tags"] == expected["tags"], f"tags mismatch for {vector['name']}"
    assert result["wikilinks"] == expected["wikilinks"], f"wikilinks mismatch for {vector['name']}"
    assert result["reminder"] == expected["reminder"], f"reminder mismatch for {vector['name']}"
    assert result["due"] == expected["due"], f"due mismatch for {vector['name']}"


def test_parse_note_empty_string() -> None:
    result = parse_note_text("", now=REFERENCE_NOW)
    assert result == {"type": "note", "due": None, "tags": [], "wikilinks": [], "reminder": False}


def test_parse_note_preserves_raw_text_in_body_semantics() -> None:
    """Wikilinks are noted but the body-level text is not rewritten by the parser."""
    result = parse_note_text("Call [[contacts/lisa]] tomorrow 2pm", now=REFERENCE_NOW)
    assert result["wikilinks"] == ["contacts/lisa"]
    assert result["due"] == "2026-04-09T14:00:00Z"
    assert result["type"] == "task"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_parse_note.py -v`
Expected: `ImportError: cannot import name 'parse_note_text'`.

- [ ] **Step 3: Implement the parser**

Create `src/mycelos/knowledge/parse_note.py`:

```python
"""Deterministic DE+EN note parser.

Runs both in the `POST /api/knowledge/notes` handler and (as a sibling
JS port) in the browser Quick-Capture modal. No LLM, no I/O. Given the
same input and reference time, it must return the same result in both
languages and in both runtimes. Shared test vectors in
``tests/fixtures/parse-note-vectors.json`` enforce that.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import TypedDict


class ParsedNote(TypedDict):
    type: str          # "note" | "task"
    due: str | None    # ISO8601 UTC
    tags: list[str]
    wikilinks: list[str]
    reminder: bool


_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_TAG_RE = re.compile(r"(?:^|\s)#([A-Za-zÄÖÜäöüß0-9_-]+)")
_TODO_RE = re.compile(r"^\s*(TODO|FIXME|AUFGABE)\s*[:\s]", re.IGNORECASE)

_REMIND_RE = re.compile(
    r"\b(remind\s+me|erinnere\s+mich|erinner\s+mich)\b",
    re.IGNORECASE,
)
_IN_DURATION_RE = re.compile(
    r"\bin\s+(\d+)\s+(minute|minuten|minutes|min|stunde|stunden|hour|hours|std|h)\b",
    re.IGNORECASE,
)

# "tomorrow 2pm" / "morgen 14 uhr" / "morgen 14:00"
_TOMORROW_RE = re.compile(
    r"\b(tomorrow|morgen)\b(?:\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|uhr)?)?",
    re.IGNORECASE,
)

# "14.04. 15:00" / "14.04.2026 15:00"
_GERMAN_DATE_RE = re.compile(
    r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})?\s*(\d{1,2})(?::(\d{2}))?",
)


def _to_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def _duration_seconds(amount: int, unit: str) -> int:
    u = unit.lower()
    if u.startswith("min"):
        return amount * 60
    if u.startswith(("h", "std", "stunde", "hour")):
        return amount * 3600
    return amount * 60


def _parse_12h_to_24h(hour: int, ampm: str | None) -> int:
    if not ampm:
        return hour
    ampm = ampm.lower()
    if ampm == "pm" and hour < 12:
        return hour + 12
    if ampm == "am" and hour == 12:
        return 0
    return hour


def parse_note_text(text: str, *, now: datetime | None = None) -> ParsedNote:
    """Parse free-form note text into structured fields. Pure function."""
    if now is None:
        now = datetime.now(tz=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    wikilinks = _WIKILINK_RE.findall(text)
    tags = _TAG_RE.findall(text)

    reminder = bool(_REMIND_RE.search(text))
    type_: str = "note"
    due: str | None = None

    if _TODO_RE.match(text):
        type_ = "task"

    in_dur = _IN_DURATION_RE.search(text)
    if in_dur:
        amount = int(in_dur.group(1))
        seconds = _duration_seconds(amount, in_dur.group(2))
        due = _to_iso(now + timedelta(seconds=seconds))
        type_ = "task"

    if due is None:
        tomorrow = _TOMORROW_RE.search(text)
        if tomorrow:
            hour = int(tomorrow.group(2)) if tomorrow.group(2) else 9
            minute = int(tomorrow.group(3)) if tomorrow.group(3) else 0
            hour = _parse_12h_to_24h(hour, tomorrow.group(4))
            target = (now + timedelta(days=1)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            due = _to_iso(target)
            type_ = "task"

    if due is None:
        gdate = _GERMAN_DATE_RE.search(text)
        if gdate:
            day = int(gdate.group(1))
            month = int(gdate.group(2))
            year = int(gdate.group(3)) if gdate.group(3) else now.year
            hour = int(gdate.group(4))
            minute = int(gdate.group(5)) if gdate.group(5) else 0
            target = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
            due = _to_iso(target)
            type_ = "task"

    if reminder and due is None:
        # "remind me" without a time: default to +1h so the caller can still
        # persist the reminder intent. The server may overwrite this.
        due = _to_iso(now + timedelta(hours=1))
        type_ = "task"

    return ParsedNote(
        type=type_,
        due=due,
        tags=tags,
        wikilinks=wikilinks,
        reminder=reminder,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_parse_note.py -v`
Expected: All 17 vector cases + the two extra tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/knowledge/parse_note.py tests/test_parse_note.py
git commit -m "feat(knowledge): deterministic DE+EN note parser"
```

---

## Task 4: `bucket_note()` helper + wiring

**Files:**
- Modify: `src/mycelos/knowledge/service.py` (new helper at module level + call site inside `write`)
- Test: `tests/test_bucket_note.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bucket_note.py`:

```python
"""Unit tests for bucket_note() — deterministic parent-path choice."""
from __future__ import annotations

from mycelos.knowledge.service import bucket_note


def test_bucket_note_reminder_goes_to_tasks() -> None:
    assert bucket_note({"reminder": True}) == "tasks"


def test_bucket_note_due_goes_to_tasks() -> None:
    assert bucket_note({"due": "2026-04-10T12:00:00Z"}) == "tasks"


def test_bucket_note_reminder_and_due_go_to_tasks() -> None:
    assert bucket_note({"reminder": True, "due": "2026-04-10T12:00:00Z"}) == "tasks"


def test_bucket_note_plain_goes_to_notes() -> None:
    assert bucket_note({}) == "notes"


def test_bucket_note_explicit_parent_wins() -> None:
    assert (
        bucket_note({"parent_path": "projects/mycelos", "reminder": True})
        == "projects/mycelos"
    )


def test_bucket_note_empty_parent_still_buckets() -> None:
    assert bucket_note({"parent_path": "", "due": "2026-04-10T12:00:00Z"}) == "tasks"


def test_bucket_note_none_values_are_ignored() -> None:
    assert bucket_note({"reminder": None, "due": None}) == "notes"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_bucket_note.py -v`
Expected: `ImportError: cannot import name 'bucket_note'`.

- [ ] **Step 3: Add the helper to `service.py`**

Open `src/mycelos/knowledge/service.py`. At the top of the file, right after the existing imports and before the first class/function definition, add:

```python
def bucket_note(note: dict) -> str:
    """Decide the parent_path for a fresh note. No LLM, no I/O.

    Rules (in order):
    1. If the caller supplied a non-empty ``parent_path`` it wins.
    2. Reminders or notes with a due date → ``tasks``.
    3. Everything else → ``notes``.
    """
    explicit = note.get("parent_path")
    if explicit:
        return explicit
    if note.get("reminder"):
        return "tasks"
    if note.get("due"):
        return "tasks"
    return "notes"
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `pytest tests/test_bucket_note.py -v`
Expected: All 7 tests PASS.

- [ ] **Step 5: Wire `bucket_note` into `KnowledgeService.write`**

In `src/mycelos/knowledge/service.py`, locate the `write(...)` method (around line 157). After the `auto_classify` block ends and just before `note = Note(...)` (around line 201), insert:

```python
        # Deterministic bucketing: if no topic was supplied, place the note
        # under ``tasks/`` or ``notes/`` based on its own fields.
        if not topic:
            topic = bucket_note({
                "reminder": reminder,
                "due": due,
                "parent_path": "",
            })
```

- [ ] **Step 6: Run the knowledge service tests**

Run: `pytest tests/test_knowledge.py -v`
Expected: PASS (bucketing only fires when `topic` is empty, so the `topic=...` call sites already in tests are unaffected).

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/knowledge/service.py tests/test_bucket_note.py
git commit -m "feat(knowledge): bucket_note() helper + wire into write()"
```

---

## Task 5: `POST /api/knowledge/notes` endpoint

**Files:**
- Modify: `src/mycelos/gateway/routes.py`
- Test: `tests/test_knowledge_notes_api.py`

- [ ] **Step 1: Write the failing API test**

Create `tests/test_knowledge_notes_api.py`:

```python
"""API tests for POST /api/knowledge/notes (Quick Capture backend)."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from tests.integration.conftest import integration_app  # type: ignore  # reuse existing fixture


def test_post_note_plain_goes_to_notes(integration_app) -> None:
    client = TestClient(integration_app)
    resp = client.post(
        "/api/knowledge/notes",
        json={"title": "Kaffee-Rezept", "content": "3 Löffel, kochendes Wasser"},
        headers={"X-User-Id": "default"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"].startswith("notes/")
    assert data["parent_path"] == "notes"


def test_post_note_with_due_goes_to_tasks(integration_app) -> None:
    client = TestClient(integration_app)
    resp = client.post(
        "/api/knowledge/notes",
        json={"title": "Call Lisa", "content": "tomorrow 2pm", "due": "2026-04-09T14:00:00Z"},
        headers={"X-User-Id": "default"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"].startswith("tasks/")


def test_post_note_parser_extracts_due_from_content(integration_app) -> None:
    """If the caller does not pass `due`, the server parses the body."""
    client = TestClient(integration_app)
    resp = client.post(
        "/api/knowledge/notes",
        json={"title": "Dehnen", "content": "erinnere mich in 5 minuten zu dehnen"},
        headers={"X-User-Id": "default"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"].startswith("tasks/")
    assert data["reminder"] is True
    assert data["due"] is not None


def test_post_note_requires_title(integration_app) -> None:
    client = TestClient(integration_app)
    resp = client.post(
        "/api/knowledge/notes",
        json={"content": "no title"},
        headers={"X-User-Id": "default"},
    )
    assert resp.status_code == 422


def test_post_note_returns_organizer_state_pending(integration_app) -> None:
    client = TestClient(integration_app)
    resp = client.post(
        "/api/knowledge/notes",
        json={"title": "Random idea", "content": "I should try this."},
        headers={"X-User-Id": "default"},
    )
    assert resp.status_code == 200
    assert resp.json()["organizer_state"] == "pending"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_knowledge_notes_api.py -v`
Expected: All 5 tests FAIL with 404 (endpoint does not exist).

- [ ] **Step 3: Add the endpoint**

Open `src/mycelos/gateway/routes.py`. Add these imports near the top if they aren't already there:

```python
from mycelos.knowledge.parse_note import parse_note_text
from mycelos.knowledge.service import bucket_note
```

Then add this route anywhere in the routes section:

```python
@router.post("/api/knowledge/notes")
async def create_knowledge_note(request: Request) -> JSONResponse:
    """Quick-Capture endpoint: create a note from a minimal JSON payload.

    The server is the source of truth for the parsed fields: if the client
    supplies ``due``/``reminder``, they win; otherwise the server parses
    the content with the deterministic DE+EN parser.
    """
    payload = await request.json()
    title = payload.get("title")
    if not title or not isinstance(title, str):
        return JSONResponse({"error": "title is required"}, status_code=422)

    content = payload.get("content") or ""
    user_id = request.headers.get("X-User-Id", "default")

    # Server-side parse for any field the client did not pass.
    parsed = parse_note_text(f"{title}\n{content}")
    due = payload.get("due") or parsed["due"]
    reminder = bool(payload.get("reminder") if "reminder" in payload else parsed["reminder"])
    tags = payload.get("tags") or parsed["tags"]
    type_ = payload.get("type") or parsed["type"]

    parent = payload.get("parent_path") or bucket_note(
        {"parent_path": "", "reminder": reminder, "due": due}
    )

    app = request.app.state.mycelos_app
    knowledge = app.knowledge_for(user_id)
    path = knowledge.write(
        title=title,
        content=content,
        type=type_,
        tags=tags,
        due=due,
        reminder=reminder,
        topic=parent,
    )

    app.audit.log(
        "knowledge.note.created",
        user_id=user_id,
        details={"path": path, "source": "quick_capture"},
    )

    return JSONResponse(
        {
            "path": path,
            "parent_path": parent,
            "type": type_,
            "due": due,
            "reminder": reminder,
            "tags": tags,
            "organizer_state": "pending",
        }
    )
```

- [ ] **Step 4: Run API tests**

Run: `pytest tests/test_knowledge_notes_api.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/gateway/routes.py tests/test_knowledge_notes_api.py
git commit -m "feat(knowledge): POST /api/knowledge/notes (Quick Capture backend)"
```

---

## Task 6: JS parser port — `parse-note.js`

**Files:**
- Create: `src/mycelos/frontend/shared/parse-note.js`

- [ ] **Step 1: Create the JS module**

Create `src/mycelos/frontend/shared/parse-note.js`:

```javascript
// Deterministic DE+EN note parser — client-side twin of
// mycelos/knowledge/parse_note.py. The Python version is the source of
// truth; this module is kept in lockstep via shared test vectors in
// tests/fixtures/parse-note-vectors.json.

const WIKILINK_RE = /\[\[([^\]|]+)(?:\|[^\]]+)?\]\]/g;
const TAG_RE = /(?:^|\s)#([A-Za-zÄÖÜäöüß0-9_-]+)/g;
const TODO_RE = /^\s*(TODO|FIXME|AUFGABE)\s*[:\s]/i;
const REMIND_RE = /\b(remind\s+me|erinnere\s+mich|erinner\s+mich)\b/i;
const IN_DURATION_RE = /\bin\s+(\d+)\s+(minuten|minutes|minute|min|stunden|stunde|hours|hour|std|h)\b/i;
const TOMORROW_RE = /\b(tomorrow|morgen)\b(?:\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|uhr)?)?/i;
const GERMAN_DATE_RE = /\b(\d{1,2})\.(\d{1,2})\.(\d{4})?\s*(\d{1,2})(?::(\d{2}))?/;

function toIso(date) {
  const pad = (n) => String(n).padStart(2, '0');
  return (
    `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}` +
    `T${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}Z`
  );
}

function durationSeconds(amount, unit) {
  const u = unit.toLowerCase();
  if (u.startsWith('min')) return amount * 60;
  if (u.startsWith('h') || u.startsWith('std') || u.startsWith('stunde') || u.startsWith('hour')) {
    return amount * 3600;
  }
  return amount * 60;
}

function twelveToTwentyFour(hour, ampm) {
  if (!ampm) return hour;
  const a = ampm.toLowerCase();
  if (a === 'pm' && hour < 12) return hour + 12;
  if (a === 'am' && hour === 12) return 0;
  return hour;
}

export function parseNoteText(text, now = new Date()) {
  const wikilinks = [];
  let m;
  const wRe = new RegExp(WIKILINK_RE.source, 'g');
  while ((m = wRe.exec(text)) !== null) wikilinks.push(m[1]);

  const tags = [];
  const tRe = new RegExp(TAG_RE.source, 'g');
  while ((m = tRe.exec(text)) !== null) tags.push(m[1]);

  const reminder = REMIND_RE.test(text);
  let type = 'note';
  let due = null;

  if (TODO_RE.test(text)) type = 'task';

  const dur = text.match(IN_DURATION_RE);
  if (dur) {
    const amount = parseInt(dur[1], 10);
    const seconds = durationSeconds(amount, dur[2]);
    due = toIso(new Date(now.getTime() + seconds * 1000));
    type = 'task';
  }

  if (due === null) {
    const tm = text.match(TOMORROW_RE);
    if (tm) {
      let hour = tm[2] ? parseInt(tm[2], 10) : 9;
      const minute = tm[3] ? parseInt(tm[3], 10) : 0;
      hour = twelveToTwentyFour(hour, tm[4]);
      const target = new Date(now.getTime());
      target.setUTCDate(target.getUTCDate() + 1);
      target.setUTCHours(hour, minute, 0, 0);
      due = toIso(target);
      type = 'task';
    }
  }

  if (due === null) {
    const gd = text.match(GERMAN_DATE_RE);
    if (gd) {
      const day = parseInt(gd[1], 10);
      const month = parseInt(gd[2], 10) - 1;
      const year = gd[3] ? parseInt(gd[3], 10) : now.getUTCFullYear();
      const hour = parseInt(gd[4], 10);
      const minute = gd[5] ? parseInt(gd[5], 10) : 0;
      due = toIso(new Date(Date.UTC(year, month, day, hour, minute, 0)));
      type = 'task';
    }
  }

  if (reminder && due === null) {
    due = toIso(new Date(now.getTime() + 3600 * 1000));
    type = 'task';
  }

  return { type, due, tags, wikilinks, reminder };
}
```

- [ ] **Step 2: Commit**

```bash
git add src/mycelos/frontend/shared/parse-note.js
git commit -m "feat(knowledge): JS parser twin for Quick Capture"
```

---

## Task 7: Quick Capture modal partial

**Files:**
- Create: `src/mycelos/frontend/shared/quick-capture.html`
- Modify: `src/mycelos/i18n/locales/en.yaml`
- Modify: `src/mycelos/i18n/locales/de.yaml`

- [ ] **Step 1: Add i18n strings**

Edit `src/mycelos/i18n/locales/en.yaml`. Add under the top-level mapping:

```yaml
quick_capture:
  placeholder: "Capture a thought, task, or reminder…"
  hint_footer: "⌘K · Quick Capture"
  saved_to: "Saved to {path}"
  error: "Could not save note"
  esc_hint: "Esc to cancel · Enter to save"
knowledge:
  pending: "Pending"
  empty_state: "Press ⌘K to write your first note"
```

Edit `src/mycelos/i18n/locales/de.yaml`. Add the same keys in German:

```yaml
quick_capture:
  placeholder: "Gedanke, Aufgabe oder Erinnerung festhalten…"
  hint_footer: "⌘K · Quick Capture"
  saved_to: "Gespeichert unter {path}"
  error: "Notiz konnte nicht gespeichert werden"
  esc_hint: "Esc zum Abbrechen · Enter zum Speichern"
knowledge:
  pending: "Offen"
  empty_state: "Drücke ⌘K für deine erste Notiz"
```

- [ ] **Step 2: Create the partial**

Create `src/mycelos/frontend/shared/quick-capture.html`:

```html
<!-- Quick Capture modal. Included by every sidebar-layout page.
     Opens via global Cmd+K / Ctrl+K. Alpine.js component is defined
     at the bottom of this file. -->
<div x-data="quickCapture()"
     x-init="init()"
     @keydown.escape.window="close()"
     x-show="open"
     x-cloak
     class="quick-capture-overlay"
     @click.self="close()">
  <div class="quick-capture-modal">
    <div class="quick-capture-header" x-text="$t('quick_capture.placeholder')"></div>
    <input type="text"
           x-model="text"
           x-ref="input"
           @keydown.enter.prevent="save()"
           @input="reparse()"
           class="quick-capture-input"
           :placeholder="$t('quick_capture.placeholder')" />
    <div class="quick-capture-chips" x-show="hasChips()">
      <span class="chip" x-show="parsed.type === 'task'">📋 task</span>
      <span class="chip" x-show="parsed.reminder">⏰ reminder</span>
      <span class="chip" x-show="parsed.due" x-text="formatDue(parsed.due)"></span>
      <template x-for="tag in parsed.tags" :key="tag">
        <span class="chip">#<span x-text="tag"></span></span>
      </template>
    </div>
    <div class="quick-capture-footer" x-text="$t('quick_capture.esc_hint')"></div>
  </div>
</div>

<template x-if="$store.quickCaptureToast && $store.quickCaptureToast.message">
  <div class="quick-capture-toast"
       x-text="$store.quickCaptureToast.message"
       x-transition></div>
</template>

<style>
  [x-cloak] { display: none !important; }
  .quick-capture-overlay {
    position: fixed; inset: 0;
    background: rgba(10, 12, 20, 0.6);
    display: flex; align-items: flex-start; justify-content: center;
    padding-top: 15vh;
    z-index: 9999;
  }
  .quick-capture-modal {
    background: #1a1d24; color: #fff;
    border-radius: 12px; width: min(560px, 90vw);
    padding: 20px; border: 1px solid rgba(255,255,255,0.08);
    box-shadow: 0 24px 64px -16px rgba(0,0,0,0.7);
  }
  .quick-capture-header {
    font-size: 11px; color: #8b96aa; text-transform: uppercase;
    letter-spacing: 1px; margin-bottom: 8px;
  }
  .quick-capture-input {
    width: 100%; background: transparent; border: none;
    font-size: 18px; color: #fff; padding: 0;
  }
  .quick-capture-input:focus { outline: none; }
  .quick-capture-chips {
    margin-top: 12px; display: flex; gap: 6px; flex-wrap: wrap;
  }
  .quick-capture-chips .chip {
    background: rgba(106,214,255,0.15); color: #6ad6ff;
    padding: 4px 8px; border-radius: 6px; font-size: 11px;
  }
  .quick-capture-footer {
    margin-top: 14px; font-size: 10px; color: #8b96aa;
  }
  .quick-capture-toast {
    position: fixed; right: 24px; bottom: 24px;
    background: #2a2e3a; color: #fff;
    padding: 12px 16px; border-radius: 8px;
    border: 1px solid rgba(106,214,255,0.3);
    z-index: 10000;
  }
</style>

<script type="module">
  import { parseNoteText } from '/static/shared/parse-note.js';
  window.__parseNoteText = parseNoteText;
</script>

<script>
  function quickCapture() {
    return {
      open: false,
      text: '',
      parsed: { type: 'note', due: null, tags: [], wikilinks: [], reminder: false },
      init() {
        window.addEventListener('keydown', (e) => {
          if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
            e.preventDefault();
            this.openModal();
          }
        });
        if (!Alpine.store('quickCaptureToast')) {
          Alpine.store('quickCaptureToast', { message: '' });
        }
      },
      openModal() {
        this.open = true;
        this.text = '';
        this.parsed = { type: 'note', due: null, tags: [], wikilinks: [], reminder: false };
        this.$nextTick(() => this.$refs.input && this.$refs.input.focus());
      },
      close() { this.open = false; },
      reparse() {
        if (window.__parseNoteText) {
          this.parsed = window.__parseNoteText(this.text, new Date());
        }
      },
      hasChips() {
        return this.parsed.type === 'task' ||
               this.parsed.reminder ||
               this.parsed.due ||
               (this.parsed.tags && this.parsed.tags.length);
      },
      formatDue(due) {
        if (!due) return '';
        try {
          const d = new Date(due);
          return '📅 ' + d.toLocaleString(undefined, {
            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
          });
        } catch (e) { return '📅 ' + due; }
      },
      async save() {
        const text = this.text.trim();
        if (!text) { this.close(); return; }
        const [title, ...rest] = text.split('\n');
        const content = rest.join('\n');
        try {
          const resp = await fetch('/api/knowledge/notes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-User-Id': 'default' },
            body: JSON.stringify({ title, content }),
          });
          if (!resp.ok) throw new Error('save failed');
          const data = await resp.json();
          Alpine.store('quickCaptureToast', {
            message: (window.t ? window.t('quick_capture.saved_to', { path: data.path }) : `Saved to ${data.path}`),
          });
          setTimeout(() => {
            Alpine.store('quickCaptureToast', { message: '' });
          }, 8000);
        } catch (e) {
          Alpine.store('quickCaptureToast', {
            message: (window.t ? window.t('quick_capture.error') : 'Could not save note'),
          });
          setTimeout(() => {
            Alpine.store('quickCaptureToast', { message: '' });
          }, 5000);
        }
        this.close();
      },
    };
  }
</script>
```

- [ ] **Step 3: Commit**

```bash
git add src/mycelos/frontend/shared/quick-capture.html src/mycelos/i18n/locales/en.yaml src/mycelos/i18n/locales/de.yaml
git commit -m "feat(knowledge): Quick Capture modal partial + i18n strings"
```

---

## Task 8: Include Quick Capture on every sidebar-layout page

**Files:**
- Modify: `src/mycelos/frontend/pages/chat.html`
- Modify: `src/mycelos/frontend/pages/knowledge.html`
- Modify: `src/mycelos/frontend/pages/workflows.html`
- Modify: `src/mycelos/frontend/pages/connectors.html`
- Modify: `src/mycelos/frontend/pages/settings.html`
- Modify: `src/mycelos/frontend/pages/agents.html`
- Modify: `src/mycelos/frontend/pages/dashboard.html`

- [ ] **Step 1: Find the existing sidebar include pattern**

Run: `grep -n 'sidebar.html' src/mycelos/frontend/pages/*.html`
Expected: A list of pages that include `{% include "shared/sidebar.html" %}` (or equivalent). Note the include syntax used by this codebase.

- [ ] **Step 2: Add the Quick Capture include next to the sidebar include on every listed page**

For each file listed in step 1, immediately *after* the sidebar include line, insert:

```jinja
{% include "shared/quick-capture.html" %}
```

Use the exact same include syntax already used by that file. If some files use a different pattern (e.g. server-side template vs. client-side `<script>` include), match the file's existing pattern.

- [ ] **Step 3: Sanity-check the pages load**

Start the dev server and load chat, knowledge, workflows, connectors, settings, agents, dashboard in the browser. Verify no console errors and that `Cmd+K` opens the modal on each page.

- [ ] **Step 4: Commit**

```bash
git add src/mycelos/frontend/pages/chat.html src/mycelos/frontend/pages/knowledge.html src/mycelos/frontend/pages/workflows.html src/mycelos/frontend/pages/connectors.html src/mycelos/frontend/pages/settings.html src/mycelos/frontend/pages/agents.html src/mycelos/frontend/pages/dashboard.html
git commit -m "feat(frontend): include Quick Capture modal on every sidebar page"
```

---

## Task 9: Sidebar Quick Capture footer hint

**Files:**
- Modify: `src/mycelos/frontend/shared/sidebar.html`

- [ ] **Step 1: Add the hint below Recent Sessions**

Open `src/mycelos/frontend/shared/sidebar.html`. Find the block that renders "Recent Sessions" and, immediately after its closing block/container, insert:

```html
<div class="sidebar-footer-hint" x-text="$t('quick_capture.hint_footer')"
     style="padding:8px 12px;font-size:11px;color:#8b96aa;opacity:0.7"></div>
```

- [ ] **Step 2: Load the page and verify the hint renders**

Open the chat page in the browser. Expected: a dimmed `⌘K · Quick Capture` line at the bottom of the sidebar (DE: the same hint via i18n fallthrough — both locales use the same string).

- [ ] **Step 3: Commit**

```bash
git add src/mycelos/frontend/shared/sidebar.html
git commit -m "feat(frontend): sidebar footer hint for Quick Capture"
```

---

## Task 10: Knowledge page — "Pending" rename + empty state

**Files:**
- Modify: `src/mycelos/frontend/pages/knowledge.html`

- [ ] **Step 1: Find the Uncategorized bucket**

Run: `grep -n 'Uncategorized\|uncategorized' src/mycelos/frontend/pages/knowledge.html`
Note the exact labels used — there may be more than one occurrence (label + count + empty state).

- [ ] **Step 2: Replace the Uncategorized label with the Pending i18n key**

For each user-facing "Uncategorized" string found in step 1, replace it with `$t('knowledge.pending')` (Alpine.js) or the server-side template equivalent used by this page. Keep backend topic paths unchanged — only the label changes.

- [ ] **Step 3: Add the empty state**

Find the section that renders the Uncategorized (now Pending) bucket contents. When the count is zero AND the topic tree is otherwise empty, render:

```html
<div class="knowledge-empty-state"
     x-show="pendingCount === 0 && topics.length === 0"
     style="padding:48px 24px;text-align:center;color:#8b96aa">
  <div style="font-size:14px" x-text="$t('knowledge.empty_state')"></div>
</div>
```

If the page already has a different mechanism for empty states, integrate this into that mechanism rather than bolting on a new element.

- [ ] **Step 4: Manually verify in the browser**

Load `/knowledge`. Expected: the "Uncategorized" label is now "Pending" / "Offen" depending on language. If the DB has zero notes, the empty state prompt is visible.

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/frontend/pages/knowledge.html
git commit -m "feat(knowledge): rename Uncategorized → Pending + empty state"
```

---

## Task 11: Playwright E2E for Quick Capture

**Files:**
- Create: `tests/e2e/test_quick_capture.py`

- [ ] **Step 1: Write the Playwright test**

Create `tests/e2e/test_quick_capture.py`:

```python
"""Playwright E2E tests for Cmd+K Quick Capture."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


@pytest.mark.e2e
@pytest.mark.parametrize("page_url", [
    "/chat",
    "/knowledge",
    "/workflows",
    "/connectors",
])
def test_cmd_k_opens_modal(page: Page, live_server: str, page_url: str) -> None:
    page.goto(f"{live_server}{page_url}")
    page.keyboard.press("Meta+K")
    modal = page.locator(".quick-capture-modal")
    expect(modal).to_be_visible()


@pytest.mark.e2e
def test_quick_capture_save_flow(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/chat")
    page.keyboard.press("Meta+K")
    page.locator(".quick-capture-input").fill("Buy milk tomorrow 2pm")
    page.keyboard.press("Enter")

    toast = page.locator(".quick-capture-toast")
    expect(toast).to_be_visible(timeout=3000)
    expect(toast).to_contain_text("tasks/")


@pytest.mark.e2e
def test_esc_closes_without_saving(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/chat")
    page.keyboard.press("Meta+K")
    expect(page.locator(".quick-capture-modal")).to_be_visible()

    page.locator(".quick-capture-input").fill("Nothing to save")
    page.keyboard.press("Escape")
    expect(page.locator(".quick-capture-modal")).not_to_be_visible()
```

- [ ] **Step 2: Run the E2E tests**

Run: `pytest tests/e2e/test_quick_capture.py -v -m e2e`
Expected: All tests PASS. (If the codebase uses `Control+K` as the hotkey on Linux CI, the `Meta+K` press still triggers the handler because the component listens for `metaKey || ctrlKey`.)

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_quick_capture.py
git commit -m "test(knowledge): Playwright E2E for Quick Capture"
```

---

## Task 12: CHANGELOG + final verification

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add changelog entries under the current week**

Open `CHANGELOG.md`. Find (or create) the `## Week NN (YYYY)` header for the current ISO week. Append under it:

```markdown
### Knowledge — Foundation + Quick Capture (Milestone A)
- v3 schema migration: `organizer_state`, `organizer_seen_at` columns on `knowledge_notes` and a new `organizer_suggestions` table with a partial index on pending suggestions
- `bucket_note()` deterministic helper — reminders/due → `tasks/`, everything else → `notes/`
- `parse_note` DE+EN parser in Python and JavaScript, driven by shared test vectors in `tests/fixtures/parse-note-vectors.json`
- `POST /api/knowledge/notes` Quick Capture endpoint
- `Cmd+K` Quick Capture modal on every sidebar-layout page with inline parse chips and a save toast
- Knowledge page: `Uncategorized` renamed to `Pending` + empty-state prompt
- Sidebar footer hint for Quick Capture discoverability
```

- [ ] **Step 2: Run the full fast test suite**

Run: `pytest tests/test_knowledge_v3_migration.py tests/test_parse_note.py tests/test_bucket_note.py tests/test_knowledge_notes_api.py tests/test_knowledge.py tests/test_storage.py -v`
Expected: All PASS.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): Knowledge Milestone A — Foundation + Quick Capture"
```
