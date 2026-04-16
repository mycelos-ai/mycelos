"""Tests for Knowledge System v2 — topics, auto-classify, new tools.

Covers:
- Schema: parent_path, reminder, sort_order columns
- Topic notes: creation, linking children, index generation
- Auto-classify: LLM topic matching on insert
- New tools: note_done, note_remind, note_move
- Extended note_write: topic + reminder params
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mycelos.knowledge.note import Note, parse_frontmatter, render_note


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-kv2"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def kb(app):
    from mycelos.knowledge.service import KnowledgeBase
    return KnowledgeBase(app)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class TestSchemaV2:
    """knowledge_notes has parent_path, reminder, sort_order columns."""

    def test_parent_path_column_exists(self, tmp_path):
        from mycelos.storage.database import SQLiteStorage
        db = SQLiteStorage(tmp_path / "test.db")
        db.execute(
            """INSERT INTO knowledge_notes (path, title, parent_path)
               VALUES (?, ?, ?)""",
            ("tasks/milch", "Milch kaufen", "topics/einkauf"),
        )
        row = db.fetchone("SELECT parent_path FROM knowledge_notes WHERE path = ?", ("tasks/milch",))
        assert row["parent_path"] == "topics/einkauf"

    def test_reminder_column_defaults_false(self, tmp_path):
        from mycelos.storage.database import SQLiteStorage
        db = SQLiteStorage(tmp_path / "test.db")
        db.execute(
            "INSERT INTO knowledge_notes (path, title) VALUES (?, ?)",
            ("notes/test", "Test"),
        )
        row = db.fetchone("SELECT reminder FROM knowledge_notes WHERE path = ?", ("notes/test",))
        assert row["reminder"] == 0 or row["reminder"] is False

    def test_sort_order_column_defaults_zero(self, tmp_path):
        from mycelos.storage.database import SQLiteStorage
        db = SQLiteStorage(tmp_path / "test.db")
        db.execute(
            "INSERT INTO knowledge_notes (path, title) VALUES (?, ?)",
            ("notes/test", "Test"),
        )
        row = db.fetchone("SELECT sort_order FROM knowledge_notes WHERE path = ?", ("notes/test",))
        assert row["sort_order"] == 0


# ---------------------------------------------------------------------------
# Topic notes
# ---------------------------------------------------------------------------

class TestTopicNotes:
    """Topics are notes with type='topic', children link via parent_path."""

    def test_create_topic(self, kb):
        path = kb.create_topic("Einkäufe", tags=["einkauf"])
        assert path.startswith("topics/")
        note = kb.read(path)
        assert note["type"] == "topic"
        assert "einkauf" in note["tags"]

    def test_write_note_with_topic(self, kb):
        topic_path = kb.create_topic("Einkäufe")
        note_path = kb.write(
            title="Milch kaufen",
            content="2 Liter",
            type="task",
            topic=topic_path,
        )
        note = kb.read(note_path)
        # Note should be linked to the topic
        meta = kb._indexer.get_note_meta(note_path)
        assert meta["parent_path"] == topic_path

    def test_list_children(self, kb):
        topic_path = kb.create_topic("Reisen")
        kb.write(title="Elias Reise", content="Wien", topic=topic_path)
        kb.write(title="Japan Trip", content="Tokyo", topic=topic_path)
        kb.write(title="Unrelated", content="Something else")

        children = kb.list_children(topic_path)
        assert len(children) == 2
        titles = {c["title"] for c in children}
        assert "Elias Reise" in titles
        assert "Japan Trip" in titles

    def test_list_topics(self, kb):
        kb.create_topic("Einkäufe")
        kb.create_topic("Projekte")
        kb.write(title="Random note", content="Not a topic")

        topics = kb.list_topics()
        assert len(topics) >= 2
        names = {t["title"] for t in topics}
        assert "Einkäufe" in names
        assert "Projekte" in names


# ---------------------------------------------------------------------------
# Auto-classify on insert
# ---------------------------------------------------------------------------

class TestAutoClassify:
    """LLM classifies notes on insert: topic, type, tags, due."""

    def test_classify_assigns_topic(self, app, kb):
        # Create an existing topic
        kb.create_topic("Einkäufe", tags=["einkauf"])

        # Mock LLM to return classification
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "topic": "einkauf",
            "suggested_tags": ["einkauf", "lebensmittel"],
            "suggested_type": "task",
        })
        mock_response.total_tokens = 50
        mock_llm.complete.return_value = mock_response
        app._llm = mock_llm

        path = kb.write(
            title="Butter kaufen",
            content="Irische Butter",
            auto_classify=True,
        )
        meta = kb._indexer.get_note_meta(path)
        # Should have been classified into Einkäufe topic
        assert meta["parent_path"] is not None or meta["tags"] != "[]"

    def test_classify_creates_new_topic(self, app, kb):
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "topic": "__new__",
            "new_topic_name": "Fitness",
            "suggested_tags": ["fitness", "sport"],
            "suggested_type": "note",
        })
        mock_response.total_tokens = 50
        mock_llm.complete.return_value = mock_response
        app._llm = mock_llm

        path = kb.write(
            title="Gym membership",
            content="Check prices",
            auto_classify=True,
        )
        # A new topic "Fitness" should exist
        topics = kb.list_topics()
        topic_names = {t["title"] for t in topics}
        assert "Fitness" in topic_names

    def test_classify_skipped_when_flag_false(self, app, kb):
        """No LLM call when auto_classify=False."""
        mock_llm = MagicMock()
        app._llm = mock_llm

        kb.write(title="Quick note", content="No classify", auto_classify=False)
        mock_llm.complete.assert_not_called()


# ---------------------------------------------------------------------------
# note_done tool
# ---------------------------------------------------------------------------

class TestNoteDone:
    """note_done marks a task as done."""

    def test_mark_task_done(self, kb):
        path = kb.write(title="Buy milk", content="2L", type="task", status="open")
        result = kb.mark_done(path)
        assert result is True
        note = kb.read(path)
        assert note["status"] == "done"

    def test_mark_done_nonexistent(self, kb):
        result = kb.mark_done("nonexistent/path")
        assert result is False


# ---------------------------------------------------------------------------
# note_remind tool
# ---------------------------------------------------------------------------

class TestNoteRemind:
    """note_remind sets due date + reminder flag."""

    def test_set_reminder(self, kb):
        path = kb.write(title="Send invoice", content="...", type="task")
        result = kb.set_reminder(path, "2026-04-01")
        assert result is True

        meta = kb._indexer.get_note_meta(path)
        assert meta["due"] == "2026-04-01"
        assert meta["reminder"] == 1 or meta["reminder"] is True

    def test_reminder_nonexistent(self, kb):
        result = kb.set_reminder("nonexistent", "2026-04-01")
        assert result is False


# ---------------------------------------------------------------------------
# note_move tool
# ---------------------------------------------------------------------------

class TestNoteMove:
    """note_move changes a note's parent topic."""

    def test_move_to_topic(self, kb):
        topic_a = kb.create_topic("Arbeit")
        topic_b = kb.create_topic("Privat")
        path = kb.write(title="Meeting notes", content="...", topic=topic_a)

        result = kb.move_to_topic(path, topic_b)
        assert result is True

        meta = kb._indexer.get_note_meta(path)
        assert meta["parent_path"] == topic_b

        # Should be in topic_b's children now
        children_b = kb.list_children(topic_b)
        assert any(c["path"] == path for c in children_b)

        # Should NOT be in topic_a's children
        children_a = kb.list_children(topic_a)
        assert not any(c["path"] == path for c in children_a)

    def test_move_nonexistent(self, kb):
        topic = kb.create_topic("Test")
        result = kb.move_to_topic("nonexistent", topic)
        assert result is False


# ---------------------------------------------------------------------------
# Note model: reminder + parent_path in frontmatter
# ---------------------------------------------------------------------------

class TestNoteModelV2:
    """Note model supports reminder and parent_path fields."""

    def test_note_with_reminder(self):
        note = Note(title="Task", content="Do it", type="task",
                    due="2026-04-01", reminder=True)
        assert note.reminder is True
        md = render_note(note)
        assert "reminder: true" in md

    def test_note_with_parent_path(self):
        note = Note(title="Child", content="...", parent_path="topics/einkauf")
        assert note.parent_path == "topics/einkauf"
        md = render_note(note)
        assert "parent_path: topics/einkauf" in md

    def test_parse_frontmatter_with_reminder(self):
        md = "---\ntitle: Task\ntype: task\nreminder: true\nparent_path: topics/test\nstatus: open\npriority: 0\ndue: null\n---\n\nContent"
        note = parse_frontmatter(md)
        assert note.reminder is True
        assert note.parent_path == "topics/test"


# ---------------------------------------------------------------------------
# Topic index generation
# ---------------------------------------------------------------------------

class TestTopicIndex:
    """Topic notes get auto-generated content listing children."""

    def test_topic_index_includes_children(self, kb):
        topic_path = kb.create_topic("Steuerberatung")
        kb.write(title="Idee: KI-Chatbot", content="...", topic=topic_path)
        kb.write(title="Meeting Müller", content="...", topic=topic_path)

        # Re-generate topic indexes
        kb.regenerate_topic_indexes()

        topic = kb.read(topic_path)
        assert "Idee: KI-Chatbot" in topic["content"]
        assert "Meeting Müller" in topic["content"]

    def test_topic_index_shows_tasks(self, kb):
        topic_path = kb.create_topic("Projekt X")
        kb.write(title="Angebot erstellen", content="...",
                 type="task", status="open", due="2026-04-01", topic=topic_path)

        kb.regenerate_topic_indexes()

        topic = kb.read(topic_path)
        assert "Angebot erstellen" in topic["content"]
        assert "2026-04-01" in topic["content"]


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

class TestToolRegistration:
    """New tools are registered in ToolRegistry."""

    def test_note_done_tool_registered(self):
        from mycelos.tools.registry import ToolRegistry
        ToolRegistry.reset()
        ToolRegistry._ensure_initialized()
        assert ToolRegistry.get_schema("note_done") is not None

    def test_note_remind_tool_registered(self):
        from mycelos.tools.registry import ToolRegistry
        ToolRegistry.reset()
        ToolRegistry._ensure_initialized()
        assert ToolRegistry.get_schema("note_remind") is not None

    def test_note_move_tool_registered(self):
        from mycelos.tools.registry import ToolRegistry
        ToolRegistry.reset()
        ToolRegistry._ensure_initialized()
        assert ToolRegistry.get_schema("note_move") is not None
