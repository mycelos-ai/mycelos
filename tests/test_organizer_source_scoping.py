"""The organizer may only file a source's notes inside its attachments."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mycelos.knowledge.source_attachment import SourceAttachmentService
from tests.test_knowledge_organizer_handler import (
    _FakeApp,
    _FakeBroker,
    _FakeKB,
    _insert_note,
)
from mycelos.agents.handlers.knowledge_organizer_handler import KnowledgeOrganizerHandler
from mycelos.storage.database import SQLiteStorage


@pytest.fixture
def storage(tmp_path: Path) -> SQLiteStorage:
    s = SQLiteStorage(tmp_path / "org.db")
    s.initialize()
    return s


def _seed_note(storage: SQLiteStorage, path: str, connector: str | None) -> None:
    source = json.dumps({"kind": "connector", "connector": connector}) if connector else None
    _insert_note(
        storage,
        path=path, title=path,
        type="note", status="active", organizer_state="pending",
        created_at="2026-08-10T10:00:00Z",
        source=source,
    )


@pytest.fixture
def handler_env(storage: SQLiteStorage):
    """(handler, storage, kb, broker, svc) — a scoped org fixture.

    Mirrors tests/test_knowledge_organizer_handler.py's handler_env but
    also exposes the broker and a SourceAttachmentService on the same
    storage, and gives the fake KB a created_topics list.
    """
    kb = _FakeKB(topics=["topics/work/vorfina", "topics/work/vorfina/mandanten",
                         "topics/private"])
    broker = _FakeBroker({"topic_path": None, "confidence": 0.0,
                          "related_note_paths": [], "new_topic_name": None})
    app = _FakeApp(storage, broker, kb)
    handler = KnowledgeOrganizerHandler(app)
    svc = SourceAttachmentService(storage)
    return handler, storage, kb, broker, svc


def test_prompt_lists_only_permitted_topics(handler_env) -> None:
    """A note from a scoped source must not even see other topics."""
    handler, storage, kb, broker, svc = handler_env
    svc.attach("gmail", "topics/work/vorfina")
    _seed_note(storage, "notes/mail-1", connector="gmail")
    handler.run(user_id="default")
    prompt = broker.calls[0][0][-1]["content"]
    assert "topics/work/vorfina" in prompt
    assert "topics/private" not in prompt


def test_answer_outside_permitted_set_is_rejected(handler_env) -> None:
    """Deterministic validation, not trust: the LLM may lie."""
    handler, storage, kb, broker, svc = handler_env
    svc.attach("gmail", "topics/work/vorfina")
    _seed_note(storage, "notes/mail-1", connector="gmail")
    broker.answer = [{"note_path": "notes/mail-1",
                      "topic_path": "topics/private",   # outside!
                      "confidence": 0.99,
                      "related_note_paths": [],
                      "new_topic_name": None}]
    handler.run(user_id="default")
    assert ("notes/mail-1", "topics/private") not in kb.moved
    note = storage.fetchone(
        "SELECT parent_path FROM knowledge_notes WHERE path=?", ("notes/mail-1",))
    assert note["parent_path"] in (None, "topics/work/vorfina")
    row = storage.fetchone(
        "SELECT COUNT(*) AS c FROM organizer_suggestions WHERE note_path=?",
        ("notes/mail-1",))
    assert row["c"] >= 1          # never silently misfiled


def test_answer_inside_permitted_set_is_applied(handler_env) -> None:
    handler, storage, kb, broker, svc = handler_env
    svc.attach("gmail", "topics/work/vorfina")
    _seed_note(storage, "notes/mail-1", connector="gmail")
    broker.answer = [{"note_path": "notes/mail-1",
                      "topic_path": "topics/work/vorfina/mandanten",
                      "confidence": 0.99,
                      "related_note_paths": [],
                      "new_topic_name": None}]
    handler.run(user_id="default")
    assert ("notes/mail-1", "topics/work/vorfina/mandanten") in kb.moved


def test_new_folder_directly_under_attachment_always_asks(handler_env) -> None:
    """Even at confidence 1.0 — opening a new main category is the user's call."""
    handler, storage, kb, broker, svc = handler_env
    svc.attach("gmail", "topics/work/vorfina")
    _seed_note(storage, "notes/mail-1", connector="gmail")
    broker.answer = [{"note_path": "notes/mail-1",
                      "topic_path": None,
                      "confidence": 1.0,
                      "related_note_paths": [],
                      "new_topic_name": "Schmidt"}]
    handler.run(user_id="default")
    row = storage.fetchone(
        "SELECT kind FROM organizer_suggestions WHERE note_path=?",
        ("notes/mail-1",))
    # 'new_topic_confirm', NOT 'new_topic' — must never be eligible for
    # the 24h auto-accept sweep (should_auto_accept only checks kind +
    # confidence, it has no notion of "always ask").
    assert row is not None and row["kind"] == "new_topic_confirm"
    assert kb.created_topics == []      # nothing created without confirmation


def test_scoped_new_topic_confirm_survives_stale_auto_accept(handler_env) -> None:
    """Regression: a scoped new-folder suggestion must NOT be auto-accepted
    after 24h, however high its confidence — and if it ever were applied,
    it must land under the attachment, never at root. Reproduces the
    Critical bypass: 'new_topic' was in _AUTO_ACCEPTABLE_KINDS, so the
    stale sweep created the topic at root via _apply_suggestion's
    f"topics/{slugify(name)}", outside the source's permitted subtree and
    without ever asking the user."""
    handler, storage, kb, broker, svc = handler_env
    svc.attach("gmail", "topics/work/vorfina")
    _seed_note(storage, "notes/mail-1", connector="gmail")
    broker.answer = [{"note_path": "notes/mail-1",
                      "topic_path": None,
                      "confidence": 1.0,
                      "related_note_paths": [],
                      "new_topic_name": "Schmidt"}]
    handler.run(user_id="default")

    row = storage.fetchone(
        "SELECT id, kind, status FROM organizer_suggestions WHERE note_path=?",
        ("notes/mail-1",))
    assert row["kind"] == "new_topic_confirm"
    assert row["status"] == "pending"

    # Backdate it past the 24h staleness window, then run the sweep.
    storage.execute(
        "UPDATE organizer_suggestions SET created_at=datetime('now', '-25 hours') "
        "WHERE id=?", (row["id"],),
    )
    accepted = handler._auto_accept_stale(storage, kb, "default")

    assert accepted == 0
    assert kb.created_topics == []      # topic was NOT created at all
    assert kb.moved == []               # note was NOT moved
    still_pending = storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (row["id"],))
    assert still_pending["status"] == "pending"   # still awaiting the user


def test_note_without_source_keeps_full_tree(handler_env) -> None:
    """Hand-written notes are unscoped — attachments only bind source content."""
    handler, storage, kb, broker, svc = handler_env
    svc.attach("gmail", "topics/work/vorfina")
    _seed_note(storage, "notes/own-1", connector=None)
    handler.run(user_id="default")
    prompt = broker.calls[0][0][-1]["content"]
    assert "topics/private" in prompt


def test_source_without_attachments_is_unscoped(handler_env) -> None:
    """No attachment configured yet → behave as today, not as 'nothing allowed'."""
    handler, storage, kb, broker, svc = handler_env
    _seed_note(storage, "notes/mail-1", connector="gmail")
    handler.run(user_id="default")
    prompt = broker.calls[0][0][-1]["content"]
    assert "topics/private" in prompt
