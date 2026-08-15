"""placement_confidence: the marker that replaces the move suggestion."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from mycelos.agents.handlers.knowledge_organizer_handler import (
    KnowledgeOrganizerHandler,
)
from mycelos.knowledge.source_attachment import SourceAttachmentService
from mycelos.storage.database import SQLiteStorage
from tests.test_knowledge_organizer_handler import (
    _FakeApp,
    _FakeBroker,
    _FakeKB,
    _insert_note,
)


@pytest.fixture
def storage(tmp_path: Path) -> SQLiteStorage:
    s = SQLiteStorage(tmp_path / "uncertain_placement.db")
    s.initialize()
    return s


@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-uncertain-placement"
        a = App(Path(tmp))
        a.initialize()
        yield a


def test_column_exists_on_fresh_database(storage) -> None:
    cols = [r["name"] for r in storage.fetchall("PRAGMA table_info(knowledge_notes)")]
    assert "placement_confidence" in cols


def _declared_type(storage: SQLiteStorage) -> str:
    """The declared type of knowledge_notes.placement_confidence."""
    row = next(
        r
        for r in storage.fetchall("PRAGMA table_info(knowledge_notes)")
        if r["name"] == "placement_confidence"
    )
    return row["type"]


def test_column_type_is_real_on_fresh_database(storage) -> None:
    """The column must hold numbers, not strings.

    Under TEXT affinity SQLite stores a bound float as '0.6', so
    row["placement_confidence"] == 0.6 is False. Pin the declared type so
    schema.sql and the migration cannot drift apart.
    """
    assert _declared_type(storage) == "REAL"


def test_column_defaults_to_null(app) -> None:
    """A note nobody classified carries no confidence, not a fake zero."""
    path = app.knowledge_base.write(title="Hand written", content="x", topic="notes")
    row = app.storage.fetchone(
        "SELECT placement_confidence FROM knowledge_notes WHERE path=?", (path,))
    assert row["placement_confidence"] is None


def test_migration_adds_column_to_an_existing_database(tmp_path: Path) -> None:
    """A pre-existing DB gets the column without losing data — the
    failure mode that shipped in the source-attachment work.

    Simulate a database written before the column existed by dropping it
    from an initialized file, then opening a *fresh* SQLiteStorage on the
    same file (mirrors a new process opening ~/.mycelos/mycelos.db).
    """
    db_path = tmp_path / "existing.db"
    old = SQLiteStorage(db_path)
    old.initialize()
    old.execute(
        "INSERT INTO knowledge_notes (path, title) VALUES (?, ?)",
        ("notes/pre-existing", "Pre existing"),
    )
    # SQLite 3.35+ supports DROP COLUMN; older builds need a table rebuild.
    old.execute("ALTER TABLE knowledge_notes DROP COLUMN placement_confidence")
    cols = [r["name"] for r in old.fetchall("PRAGMA table_info(knowledge_notes)")]
    assert "placement_confidence" not in cols
    old.close()

    # Fresh instance, as a new process opening the pre-existing file would.
    reopened = SQLiteStorage(db_path)
    cols = [r["name"] for r in reopened.fetchall("PRAGMA table_info(knowledge_notes)")]
    assert "placement_confidence" in cols

    row = reopened.fetchone(
        "SELECT title, placement_confidence FROM knowledge_notes WHERE path=?",
        ("notes/pre-existing",),
    )
    assert row is not None, "the migration must not lose existing rows"
    assert row["title"] == "Pre existing"
    assert row["placement_confidence"] is None

    # A migrated database must declare the same type as a fresh one, or a
    # float written here would read back as a string on one of them.
    assert _declared_type(reopened) == "REAL"

    # Idempotent: opening the same file again must not error.
    again = SQLiteStorage(db_path)
    assert again.fetchone(
        "SELECT COUNT(*) AS c FROM knowledge_notes")["c"] == 1


# ---- filing instead of queuing -----------------------------------------
#
# The fakes come from tests/test_knowledge_organizer_handler.py and the
# scoped setup mirrors tests/test_organizer_source_scoping.py — reused, not
# duplicated.


def _seed_note(storage: SQLiteStorage, path: str, connector: str | None = None) -> None:
    source = (
        json.dumps({"kind": "connector", "connector": connector})
        if connector else None
    )
    _insert_note(
        storage,
        path=path, title=path,
        type="note", status="active", organizer_state="pending",
        created_at="2026-08-10T10:00:00Z",
        source=source,
    )


@pytest.fixture
def handler_env(storage: SQLiteStorage):
    """(handler, storage, kb, broker) — unscoped organizer run."""
    kb = _FakeKB(topics=["topics/work"])
    broker = _FakeBroker({"topic_path": None, "confidence": 0.0,
                          "related_note_paths": [], "new_topic_name": None})
    app = _FakeApp(storage, broker, kb)
    return KnowledgeOrganizerHandler(app), storage, kb, broker


@pytest.fixture
def scoped_handler_env(storage: SQLiteStorage):
    """(handler, storage, kb, broker, svc) — organizer with source scoping."""
    kb = _FakeKB(topics=["topics/work/vorfina", "topics/work/vorfina/mandanten",
                         "topics/private"])
    broker = _FakeBroker({"topic_path": None, "confidence": 0.0,
                          "related_note_paths": [], "new_topic_name": None})
    app = _FakeApp(storage, broker, kb)
    handler = KnowledgeOrganizerHandler(app)
    return handler, storage, kb, broker, SourceAttachmentService(storage)


def test_low_confidence_files_the_note_and_records_confidence(handler_env) -> None:
    handler, storage, kb, broker = handler_env
    _seed_note(storage, "notes/a")
    broker.answer = [{"note_path": "notes/a", "topic_path": "topics/work",
                      "confidence": 0.6, "related_note_paths": [],
                      "new_topic_name": None}]
    handler.run(user_id="default")
    assert ("notes/a", "topics/work") in kb.moved       # filed, not queued
    row = storage.fetchone(
        "SELECT placement_confidence, organizer_state FROM knowledge_notes "
        "WHERE path=?", ("notes/a",))
    assert row["placement_confidence"] == 0.6
    assert row["organizer_state"] == "ok"


def test_low_confidence_counts_as_a_move_and_is_audited(handler_env) -> None:
    """A filed placement is a move, not a suggestion — and every state
    change carries an audit event with paths only."""
    handler, storage, kb, broker = handler_env
    _seed_note(storage, "notes/a")
    broker.answer = [{"note_path": "notes/a", "topic_path": "topics/work",
                      "confidence": 0.6, "related_note_paths": [],
                      "new_topic_name": None}]
    result = handler.run(user_id="default")
    assert result["moved"] == 1
    assert result["suggested"] == 0
    events = handler._app.audit.events
    entry = next(e for e in events if e[0] == "organizer.uncertain_placement")
    assert entry[2] == {"path": "notes/a", "target": "topics/work",
                        "confidence": 0.6}


def test_low_confidence_creates_no_move_suggestion(handler_env) -> None:
    """The inbox must not grow by one entry per uncertain placement."""
    handler, storage, kb, broker = handler_env
    _seed_note(storage, "notes/a")
    broker.answer = [{"note_path": "notes/a", "topic_path": "topics/work",
                      "confidence": 0.6, "related_note_paths": [],
                      "new_topic_name": None}]
    handler.run(user_id="default")
    row = storage.fetchone(
        "SELECT COUNT(*) AS c FROM organizer_suggestions "
        "WHERE note_path=? AND kind='move'", ("notes/a",))
    assert row["c"] == 0


def test_high_confidence_files_without_a_confidence_marker(handler_env) -> None:
    """Certain placements are not 'uncertain' — the review view must not
    fill up with notes that were never in doubt."""
    handler, storage, kb, broker = handler_env
    _seed_note(storage, "notes/a")
    broker.answer = [{"note_path": "notes/a", "topic_path": "topics/work",
                      "confidence": 0.97, "related_note_paths": [],
                      "new_topic_name": None}]
    handler.run(user_id="default")
    assert ("notes/a", "topics/work") in kb.moved
    row = storage.fetchone(
        "SELECT placement_confidence FROM knowledge_notes WHERE path=?",
        ("notes/a",))
    assert row["placement_confidence"] is None


def test_no_target_still_routes_to_the_failure_path(handler_env) -> None:
    """An answer with neither a topic nor a name has nowhere to file —
    it must not vanish."""
    handler, storage, kb, broker = handler_env
    _seed_note(storage, "notes/a")
    broker.answer = [{"note_path": "notes/a", "topic_path": None,
                      "confidence": 0.4, "related_note_paths": [],
                      "new_topic_name": None}]
    handler.run(user_id="default")
    assert kb.moved == []
    row = storage.fetchone(
        "SELECT organizer_attempts, placement_confidence FROM knowledge_notes "
        "WHERE path=?", ("notes/a",))
    assert row["organizer_attempts"] >= 1
    assert row["placement_confidence"] is None


def test_unknown_topic_at_low_confidence_is_not_filed(handler_env) -> None:
    """A proposed topic that does not exist is no usable target: filing
    there would invent a folder the user never approved."""
    handler, storage, kb, broker = handler_env
    _seed_note(storage, "notes/a")
    broker.answer = [{"note_path": "notes/a", "topic_path": "topics/ghost",
                      "confidence": 0.6, "related_note_paths": [],
                      "new_topic_name": None}]
    handler.run(user_id="default")
    assert kb.moved == []
    row = storage.fetchone(
        "SELECT organizer_attempts, placement_confidence FROM knowledge_notes "
        "WHERE path=?", ("notes/a",))
    assert row["organizer_attempts"] >= 1
    assert row["placement_confidence"] is None


def test_failed_move_is_not_marked_ok(handler_env) -> None:
    """Fail closed: when the move raises, the note is neither marked ok nor
    given a confidence — it stays in the queue and is retried. The
    neighbouring silent_move branch swallows this; this branch must not."""
    handler, storage, kb, broker = handler_env
    _seed_note(storage, "notes/a")

    def _boom(path: str, target: str) -> bool:
        raise RuntimeError("filesystem unavailable")
    kb.move_to_topic = _boom

    broker.answer = [{"note_path": "notes/a", "topic_path": "topics/work",
                      "confidence": 0.6, "related_note_paths": [],
                      "new_topic_name": None}]
    result = handler.run(user_id="default")

    row = storage.fetchone(
        "SELECT placement_confidence, organizer_state, organizer_attempts "
        "FROM knowledge_notes WHERE path=?", ("notes/a",))
    assert row["placement_confidence"] is None
    assert row["organizer_state"] != "ok"
    assert row["organizer_attempts"] >= 1      # classification failure recorded
    assert result["moved"] == 0


def test_out_of_scope_answer_is_still_rejected(scoped_handler_env) -> None:
    """The scope boundary is untouched by this change: an out-of-scope
    answer is rejected and still produces an inbox entry, uncertain or
    not."""
    handler, storage, kb, broker, svc = scoped_handler_env
    svc.attach("gmail", "topics/work/vorfina")
    _seed_note(storage, "notes/mail-1", connector="gmail")
    broker.answer = [{"note_path": "notes/mail-1", "topic_path": "topics/private",
                      "confidence": 0.6, "related_note_paths": [],
                      "new_topic_name": None}]
    handler.run(user_id="default")
    assert ("notes/mail-1", "topics/private") not in kb.moved
    row = storage.fetchone(
        "SELECT COUNT(*) AS c FROM organizer_suggestions WHERE note_path=?",
        ("notes/mail-1",))
    assert row["c"] >= 1
    note = storage.fetchone(
        "SELECT placement_confidence FROM knowledge_notes WHERE path=?",
        ("notes/mail-1",))
    assert note["placement_confidence"] is None
