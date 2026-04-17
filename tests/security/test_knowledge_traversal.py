"""SEC: Path traversal protection for KnowledgeBase.

Ensures user-supplied paths cannot escape the knowledge directory via ../
sequences or absolute paths.
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from mycelos.knowledge.service import KnowledgeBase, PathTraversalError


@pytest.fixture
def kb(tmp_path, monkeypatch):
    """Create a KnowledgeBase with a temp knowledge dir."""
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "test-key-for-kb-traversal")
    app = MagicMock()
    app.data_dir = tmp_path
    app.storage.fetchone.return_value = None
    app.storage.fetchall.return_value = []
    app.memory.get.return_value = None
    app.audit = MagicMock()

    kb_dir = tmp_path / "knowledge"
    kb_dir.mkdir()

    # Write a legitimate note
    (kb_dir / "legit-note.md").write_text(
        "---\ntitle: Legit\ntype: note\n---\nContent here", encoding="utf-8"
    )

    obj = object.__new__(KnowledgeBase)
    obj._app = app
    obj._knowledge_dir = kb_dir
    obj._indexer = MagicMock()
    obj._indexer.get_backlinks.return_value = []
    obj._indexer.get_note_meta.return_value = {"path": "legit-note"}
    obj._embedding_provider = MagicMock(dimension=0)
    return obj


class TestPathTraversal:
    """Path traversal attacks must be blocked."""

    def test_read_traversal_blocked(self, kb):
        with pytest.raises(PathTraversalError):
            kb.read("../../etc/passwd")

    def test_read_dot_dot_blocked(self, kb):
        with pytest.raises(PathTraversalError):
            kb.read("../secret")

    def test_update_traversal_blocked(self, kb):
        with pytest.raises(PathTraversalError):
            kb.update("../../etc/shadow", content="pwned")

    def test_move_to_topic_traversal_in_path_blocked(self, kb):
        """Traversal in the note path must be blocked."""
        with pytest.raises(PathTraversalError):
            kb.move_to_topic("../../../etc/passwd", "some-topic")

    def test_set_reminder_traversal_blocked(self, kb):
        with pytest.raises(PathTraversalError):
            kb.set_reminder("../../etc/crontab", "2026-12-31")

    def test_legitimate_path_works(self, kb):
        result = kb.read("legit-note")
        assert result is not None
        assert result["title"] == "Legit"

    def test_nested_path_works(self, kb):
        """Paths within subdirs are fine."""
        nested = kb._knowledge_dir / "topics"
        nested.mkdir()
        (nested / "my-topic.md").write_text(
            "---\ntitle: Topic\ntype: topic\n---\nTopic content", encoding="utf-8"
        )
        result = kb.read("topics/my-topic")
        assert result is not None
        assert result["title"] == "Topic"

    def test_traversal_audit_logged(self, kb):
        """Traversal attempts must be logged (Constitution Rule 1)."""
        with pytest.raises(PathTraversalError):
            kb.read("../../etc/passwd")
        kb._app.audit.log.assert_called_with(
            "knowledge.traversal.blocked",
            details={"path": "../../etc/passwd"},
        )

    def test_absolute_path_traversal(self, kb):
        """Absolute paths that escape the dir should be blocked."""
        # This depends on Path resolution — /etc/passwd + .md won't be relative
        with pytest.raises(PathTraversalError):
            kb.read("/etc/passwd")

    def test_write_traversal_blocked_via_title(self, kb, monkeypatch):
        """A malicious title that generates a traversal path must be blocked.

        This is the Jules/Sentinel CRITICAL finding. Note.generate_path() can
        be steered by the title — a prompt-injected / malicious title can
        produce "../../etc/passwd", so the write path must validate.
        """
        from mycelos.knowledge.note import Note

        # Force Note.generate_path to return a traversal path
        monkeypatch.setattr(
            Note,
            "generate_path",
            lambda self: "../../etc/passwd",
        )
        # Stub bucket_note so it doesn't override our traversal path
        monkeypatch.setattr(
            "mycelos.knowledge.service.bucket_note",
            lambda *a, **kw: None,
        )
        kb._indexer.index_note = MagicMock()

        with pytest.raises(PathTraversalError):
            kb.write(title="ignored", content="pwned", type="note")

    def test_append_related_link_traversal_blocked(self, kb, tmp_path):
        """append_related_link must reject a traversal note_path."""
        with pytest.raises(PathTraversalError):
            kb.append_related_link("../../etc/passwd", "legit-note")

    def test_append_related_link_target_traversal_blocked(self, kb):
        """append_related_link must reject a traversal target_path too.

        A traversal target would leak into the note body as an active wikilink.
        """
        with pytest.raises(PathTraversalError):
            kb.append_related_link("legit-note", "../../etc/passwd")
