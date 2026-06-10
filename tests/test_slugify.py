"""Tests for the single slugify() used by every topic/path computation.

Two different slug algorithms existed (note.generate_path vs the
auto-accept/inbox `name.lower().replace(' ', '-')`), so umlaut topic names
produced parent paths pointing at topics that don't exist.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from mycelos.knowledge.note import Note, slugify


class TestSlugify:
    def test_basic(self):
        assert slugify("Security Fail Closed") == "security-fail-closed"

    def test_german_umlauts_transliterated(self):
        assert slugify("Ernährung") == "ernaehrung"
        assert slugify("Über Größe") == "ueber-groesse"
        assert slugify("Spaß") == "spass"

    def test_punctuation_collapses(self):
        assert slugify("Hello, World!") == "hello-world"

    def test_generate_path_uses_slugify(self):
        note = Note(title="Ernährung Tipps", type="note")
        assert note.generate_path() == "notes/ernaehrung-tipps"


@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-slug"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def kb(app):
    from mycelos.knowledge.service import KnowledgeBase
    return KnowledgeBase(app)


class TestTopicSlugConsistency:
    def test_auto_accept_new_topic_uses_created_path(self, app, kb):
        """Auto-accepting a new_topic suggestion with an umlaut name must move
        members to the topic that was actually created — not to a recomputed
        slug that doesn't exist."""
        import json
        from mycelos.agents.handlers.knowledge_organizer_handler import (
            KnowledgeOrganizerHandler,
        )
        note_path = kb.write("Mein Salat", "gesund", type="note")
        app.storage.execute(
            "INSERT INTO organizer_suggestions (note_path, kind, payload, confidence, created_at, status) "
            "VALUES (?, 'new_topic', ?, 0.9, datetime('now', '-25 hours'), 'pending')",
            (note_path, json.dumps({"name": "Ernährung", "members": [note_path]})),
        )

        handler = KnowledgeOrganizerHandler(app)
        handler._auto_accept_stale(app.storage, kb, "default")

        row = app.storage.fetchone(
            "SELECT parent_path FROM knowledge_notes WHERE path=?", (note_path,)
        )
        parent = row["parent_path"]
        assert parent, "note must have been moved to the new topic"
        # The parent must be a topic that actually exists.
        topic = app.storage.fetchone(
            "SELECT path FROM knowledge_notes WHERE path=? AND type='topic'", (parent,)
        )
        assert topic is not None, f"parent {parent!r} does not exist as a topic"

    def test_inbox_synthetic_member_target_matches_real_slug(self, app, kb):
        """The inbox's synthetic member rows must compute the same topic path
        the accept endpoint creates."""
        import json
        from mycelos.knowledge.inbox import InboxService
        from mycelos.knowledge.note import slugify as _slug

        inbox = InboxService(app.storage)
        inbox.add(
            note_path="notes/a", kind="new_topic",
            payload={"name": "Ernährung", "members": ["notes/a", "notes/b"]},
            confidence=0.9,
        )
        groups = inbox.list_pending_by_topic()
        group = next(g for g in groups if g["is_new"])
        assert group["topic"] == f"topics/{_slug('Ernährung')}"
        synthetic = [n for n in group["notes"] if n.get("_synthetic")]
        assert synthetic
        assert synthetic[0]["payload"]["target"] == f"topics/{_slug('Ernährung')}"
