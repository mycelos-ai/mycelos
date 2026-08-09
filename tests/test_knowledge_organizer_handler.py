from __future__ import annotations

import json
from pathlib import Path

import pytest

from mycelos.agents.handlers.knowledge_organizer_handler import KnowledgeOrganizerHandler
from mycelos.knowledge.inbox import InboxService
from mycelos.storage.database import SQLiteStorage


class _FakeLLMResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeBroker:
    def __init__(self, response_payload: dict) -> None:
        self.calls: list = []
        self._payload = response_payload

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return _FakeLLMResponse(json.dumps(self._payload))


class _FakeAudit:
    def __init__(self) -> None:
        self.events: list = []

    def log(self, event_type, user_id=None, details=None) -> None:
        self.events.append((event_type, user_id, details))


class _FakeKB:
    def __init__(self, topics: list[str], duplicates: dict[str, list[dict]] | None = None) -> None:
        self._topics = topics
        self._duplicates = duplicates or {}
        self.moved: list = []
        self._knowledge_dir = Path("/fake")

    def list_topics(self, limit: int = 100) -> list[dict]:
        return [{"path": t} for t in self._topics]

    def move_to_topic(self, path: str, target: str) -> bool:
        self.moved.append((path, target))
        return True

    def find_duplicates(self, path: str, threshold: float = 0.92, top_k: int = 3) -> list[dict]:
        return self._duplicates.get(path, [])


class _FakeKBWithFiles(_FakeKB):
    """Fake KB that supports file reads and merge operations."""

    def __init__(self, topics, duplicates=None, files=None):
        super().__init__(topics, duplicates)
        self._files = files or {}
        self.archived: list[str] = []
        self.updated: list[tuple] = []
        self._knowledge_dir = Path("/fake")

    def update(self, path: str, content: str | None = None, tags: list[str] | None = None,
               append: bool = False, **kwargs) -> bool:
        self.updated.append((path, content, tags, append))
        return True

    def archive_note(self, path: str) -> bool:
        self.archived.append(path)
        return True


class _FakeApp:
    def __init__(self, storage: SQLiteStorage, broker: _FakeBroker, kb: _FakeKB) -> None:
        self.storage = storage
        self.llm = broker
        self.audit = _FakeAudit()
        self.knowledge_base = kb

    def resolve_cheapest_model(self) -> str | None:
        return "test-cheapest"

    def resolve_strongest_model(self) -> str | None:
        return "test-strongest"


@pytest.fixture
def storage(tmp_path: Path) -> SQLiteStorage:
    s = SQLiteStorage(tmp_path / "org.db")
    s.initialize()
    return s


@pytest.fixture
def handler_env(storage: SQLiteStorage):
    """(handler, storage, kb, audit) with a seeded 'notes/a' note."""
    kb = _FakeKB(topics=["topics/x"])
    broker = _FakeBroker({"topic_path": None, "confidence": 0.0,
                          "related_note_paths": [], "new_topic_name": None})
    app = _FakeApp(storage, broker, kb)
    handler = KnowledgeOrganizerHandler(app)
    _insert_note(
        storage,
        path="notes/a", title="Note A",
        type="note", status="active", organizer_state="suggested",
        created_at="2026-04-10T10:00:00Z",
    )
    return handler, storage, kb, app.audit


def _insert_note(storage: SQLiteStorage, **fields) -> None:
    cols = ",".join(fields.keys())
    placeholders = ",".join("?" * len(fields))
    storage.execute(
        f"INSERT INTO knowledge_notes ({cols}) VALUES ({placeholders})",
        tuple(fields.values()),
    )


def test_silent_move_on_high_confidence_existing_topic(storage: SQLiteStorage) -> None:
    _insert_note(
        storage,
        path="notes/idea-1", title="Espresso tuning",
        type="note", status="active", organizer_state="pending",
    )
    broker = _FakeBroker({"topic_path": "topics/coffee", "confidence": 0.95,
                          "related_note_paths": [], "new_topic_name": None})
    kb = _FakeKB(topics=["topics/coffee"])
    app = _FakeApp(storage, broker, kb)

    handler = KnowledgeOrganizerHandler(app)
    result = handler.run("default")

    assert result["processed"] == 1
    assert result["moved"] == 1
    assert result["suggested"] == 0
    assert kb.moved == [("notes/idea-1", "topics/coffee")]
    row = storage.fetchone("SELECT organizer_state FROM knowledge_notes WHERE path=?",
                           ("notes/idea-1",))
    assert row["organizer_state"] == "ok"


def test_suggest_on_low_confidence(storage: SQLiteStorage) -> None:
    _insert_note(
        storage,
        path="notes/idea-2", title="Stray thought",
        type="note", status="active", organizer_state="pending",
    )
    broker = _FakeBroker({"topic_path": "topics/coffee", "confidence": 0.4,
                          "related_note_paths": [], "new_topic_name": None})
    kb = _FakeKB(topics=["topics/coffee"])
    app = _FakeApp(storage, broker, kb)

    handler = KnowledgeOrganizerHandler(app)
    result = handler.run("default")

    assert result["processed"] == 1
    assert result["suggested"] == 1
    assert result["moved"] == 0
    inbox = InboxService(storage)
    pending = inbox.list_pending()
    assert len(pending["move"]) == 1


def test_lifecycle_archives_done_task_older_than_7d(storage: SQLiteStorage) -> None:
    import freezegun
    with freezegun.freeze_time("2026-04-20T12:00:00Z"):
        _insert_note(
            storage,
            path="tasks/old", title="Old task", type="task",
            status="done", updated_at="2026-04-05T10:00:00Z",
            organizer_state="pending",
        )
        broker = _FakeBroker({"topic_path": None, "confidence": 0.0,
                              "related_note_paths": [], "new_topic_name": None})
        kb = _FakeKB(topics=[])
        app = _FakeApp(storage, broker, kb)

        handler = KnowledgeOrganizerHandler(app)
        result = handler.run("default")

        assert result["archived"] == 1
        row = storage.fetchone("SELECT status, organizer_state FROM knowledge_notes WHERE path=?",
                               ("tasks/old",))
        assert row["status"] == "archived"
        assert row["organizer_state"] == "archived"
        # Broker should not be called — lifecycle short-circuits
        assert broker.calls == []


def test_duplicate_detected_creates_merge_suggestion(storage: SQLiteStorage) -> None:
    _insert_note(
        storage,
        path="notes/shopping-1", title="Shopping list",
        type="note", status="active", organizer_state="pending",
        created_at="2026-04-10T10:00:00Z",
    )
    _insert_note(
        storage,
        path="notes/shopping-2", title="Einkaufsliste",
        type="note", status="active", organizer_state="ok",
        created_at="2026-04-12T10:00:00Z",
    )

    duplicates = {
        "notes/shopping-1": [
            {"path": "notes/shopping-2", "title": "Einkaufsliste", "score": 0.95,
             "status": "active", "created_at": "2026-04-12T10:00:00Z"}
        ]
    }
    broker = _FakeBroker({"topic_path": "topics/misc", "confidence": 0.9,
                          "related_note_paths": [], "new_topic_name": None})
    kb = _FakeKB(topics=["topics/misc"], duplicates=duplicates)
    app = _FakeApp(storage, broker, kb)

    handler = KnowledgeOrganizerHandler(app)
    result = handler.run("default")

    inbox = InboxService(storage)
    pending = inbox.list_pending()
    assert len(pending["merge"]) == 1
    suggestion = pending["merge"][0]
    assert suggestion["note_path"] == "notes/shopping-1"
    assert suggestion["payload"]["duplicate_path"] == "notes/shopping-2"
    assert suggestion["payload"]["similarity"] == 0.95


def test_lazy_linker_adds_link_suggestions(storage: SQLiteStorage) -> None:
    _insert_note(
        storage,
        path="notes/idea-3", title="Pour over",
        type="note", status="active", organizer_state="pending",
    )
    broker = _FakeBroker({
        "topic_path": "topics/coffee", "confidence": 0.9,
        "related_note_paths": ["topics/coffee/espresso"],
        "new_topic_name": None,
    })
    kb = _FakeKB(topics=["topics/coffee"])
    app = _FakeApp(storage, broker, kb)

    handler = KnowledgeOrganizerHandler(app)
    result = handler.run("default")

    assert result["linked"] == 1
    inbox = InboxService(storage)
    assert len(inbox.list_pending()["link"]) == 1


def test_merge_is_never_auto_accepted(storage: SQLiteStorage, tmp_path: Path) -> None:
    """Regression for P0-2: destructive merges must NOT be auto-accepted on
    staleness. A false-positive duplicate plus a day of inattention must not
    silently archive (and eventually hard-delete) real content. Merge requires
    explicit user confirmation, matching the project's mandatory-dry-run rule.
    """
    import freezegun

    _insert_note(storage, path="notes/old", title="Original",
                 type="note", status="active", organizer_state="ok",
                 created_at="2026-04-10T10:00:00Z")
    _insert_note(storage, path="notes/new", title="Duplicate",
                 type="note", status="active", organizer_state="ok",
                 created_at="2026-04-12T10:00:00Z")

    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "new.md").write_text(
        "---\ntitle: Duplicate\ntags: []\n---\nDuplicate content here",
        encoding="utf-8",
    )

    # Stale merge suggestion (>24h old).
    storage.execute(
        "INSERT INTO organizer_suggestions (note_path, kind, payload, confidence, created_at, status) "
        "VALUES (?, 'merge', ?, 0.95, '2026-04-13 06:00:00', 'pending')",
        ("notes/old", json.dumps({"duplicate_path": "notes/new", "similarity": 0.95})),
    )

    kb = _FakeKBWithFiles(topics=[], files={})
    kb._knowledge_dir = tmp_path

    broker = _FakeBroker({"topic_path": None, "confidence": 0.0,
                          "related_note_paths": [], "new_topic_name": None})
    app = _FakeApp(storage, broker, kb)

    with freezegun.freeze_time("2026-04-14T12:00:00Z"):
        handler = KnowledgeOrganizerHandler(app)
        handler.run("default")

    # The merge suggestion must remain pending (awaiting a human).
    row = storage.fetchone("SELECT status FROM organizer_suggestions WHERE kind='merge'")
    assert row["status"] == "pending"
    # Nothing was archived, nothing was mutated.
    assert kb.archived == []
    assert kb.updated == []


def test_non_destructive_suggestions_still_auto_accept(storage: SQLiteStorage) -> None:
    """The auto-accept of non-destructive 'move' suggestions must keep working
    so the P0-2 fix narrows behavior to merge only, not all kinds."""
    import freezegun

    _insert_note(storage, path="notes/stray", title="Stray",
                 type="note", status="active", organizer_state="suggested",
                 created_at="2026-04-10T10:00:00Z")
    storage.execute(
        "INSERT INTO organizer_suggestions (note_path, kind, payload, confidence, created_at, status) "
        "VALUES (?, 'move', ?, 0.97, '2026-04-13 06:00:00', 'pending')",
        ("notes/stray", json.dumps({"target": "topics/misc"})),
    )

    kb = _FakeKBWithFiles(topics=["topics/misc"])
    broker = _FakeBroker({"topic_path": None, "confidence": 0.0,
                          "related_note_paths": [], "new_topic_name": None})
    app = _FakeApp(storage, broker, kb)

    with freezegun.freeze_time("2026-04-14T12:00:00Z"):
        handler = KnowledgeOrganizerHandler(app)
        handler.run("default")

    row = storage.fetchone("SELECT status FROM organizer_suggestions WHERE kind='move'")
    assert row["status"] == "accepted"
    assert ("notes/stray", "topics/misc") in kb.moved


def test_sweep_duplicates_finds_pairs(storage: SQLiteStorage) -> None:
    _insert_note(storage, path="notes/a", title="Note A",
                 type="note", status="active", organizer_state="ok",
                 created_at="2026-04-10T10:00:00Z")
    _insert_note(storage, path="notes/b", title="Note B",
                 type="note", status="active", organizer_state="ok",
                 created_at="2026-04-12T10:00:00Z")

    duplicates = {
        "notes/a": [{"path": "notes/b", "title": "Note B", "score": 0.95,
                      "status": "active", "created_at": "2026-04-12T10:00:00Z"}],
        "notes/b": [{"path": "notes/a", "title": "Note A", "score": 0.95,
                      "status": "active", "created_at": "2026-04-10T10:00:00Z"}],
    }
    kb = _FakeKB(topics=[], duplicates=duplicates)
    broker = _FakeBroker({"topic_path": None, "confidence": 0.0,
                          "related_note_paths": [], "new_topic_name": None})
    app = _FakeApp(storage, broker, kb)

    handler = KnowledgeOrganizerHandler(app)
    count = handler.sweep_duplicates("default")

    assert count == 1  # Only one pair, not two (A,B == B,A)
    inbox = InboxService(storage)
    pending = inbox.list_pending()
    assert len(pending["merge"]) == 1
    # Primary should be the older note
    assert pending["merge"][0]["note_path"] == "notes/a"
    assert pending["merge"][0]["payload"]["duplicate_path"] == "notes/b"


def _stale_suggestion(storage, note_path: str, kind: str, payload: dict,
                      confidence: float) -> int:
    """Insert a pending suggestion backdated past the 24h staleness window."""
    cursor = storage.execute(
        "INSERT INTO organizer_suggestions "
        "(note_path, kind, payload, confidence, created_at) "
        "VALUES (?, ?, ?, ?, datetime('now', '-25 hours'))",
        (note_path, kind, json.dumps(payload), confidence),
    )
    return cursor.lastrowid


def test_auto_accept_skips_low_confidence(handler_env) -> None:
    handler, storage, kb, audit = handler_env
    sid = _stale_suggestion(storage, "notes/a", "move",
                            {"target": "topics/x"}, confidence=0.5)
    accepted = handler._auto_accept_stale(storage, kb, "u1")
    assert accepted == 0
    assert kb.moved == []
    row = storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "pending"  # left for the user, not applied


def test_auto_accept_applies_high_confidence_move(handler_env) -> None:
    handler, storage, kb, audit = handler_env
    sid = _stale_suggestion(storage, "notes/a", "move",
                            {"target": "topics/x"}, confidence=0.97)
    accepted = handler._auto_accept_stale(storage, kb, "u1")
    assert accepted == 1
    assert ("notes/a", "topics/x") in kb.moved
    row = storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "accepted"


def test_auto_accept_failure_is_not_marked_accepted(handler_env) -> None:
    handler, storage, kb, audit = handler_env
    kb.move_to_topic = lambda path, target: False  # simulate missing note
    sid = _stale_suggestion(storage, "notes/a", "move",
                            {"target": "topics/x"}, confidence=0.97)
    accepted = handler._auto_accept_stale(storage, kb, "u1")
    assert accepted == 0
    row = storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "failed"
    note = storage.fetchone(
        "SELECT organizer_state FROM knowledge_notes WHERE path=?", ("notes/a",))
    assert note["organizer_state"] == "pending"  # re-enters classification
    assert any(e[0] == "organizer.auto_accept_failed" for e in audit.events)


def test_auto_accept_merge_stays_pending(handler_env) -> None:
    handler, storage, kb, audit = handler_env
    sid = _stale_suggestion(storage, "notes/a", "merge",
                            {"duplicate_path": "notes/b"}, confidence=1.0)
    accepted = handler._auto_accept_stale(storage, kb, "u1")
    assert accepted == 0
    row = storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "pending"
