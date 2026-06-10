"""Tests for knowledge provenance — who created a note, from what source.

The positioning claim "knowledge with provenance" requires every note to
answer: who created me (created_by) and from what (source JSON with kind,
conversation/connector references). Provenance must survive edits.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-prov"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def kb(app):
    from mycelos.knowledge.service import KnowledgeBase
    return KnowledgeBase(app)


class TestWriteProvenance:
    def test_write_records_created_by_and_source(self, kb, app):
        path = kb.write(
            "Meeting notes", "Discussed the launch", type="note",
            created_by="mycelos",
            source={"kind": "chat", "conversation_id": "sess-123"},
        )
        row = app.storage.fetchone(
            "SELECT created_by, source FROM knowledge_notes WHERE path=?", (path,)
        )
        assert row["created_by"] == "mycelos"
        assert json.loads(row["source"]) == {"kind": "chat", "conversation_id": "sess-123"}

    def test_write_without_origin_defaults_to_user(self, kb, app):
        path = kb.write("Plain note", "no origin", type="note")
        row = app.storage.fetchone(
            "SELECT created_by, source FROM knowledge_notes WHERE path=?", (path,)
        )
        assert row["created_by"] == "user"
        assert row["source"] is None

    def test_read_surfaces_provenance(self, kb):
        path = kb.write(
            "Sourced note", "body", type="note",
            created_by="organizer",
            source={"kind": "merge", "merged_from": "notes/dup"},
        )
        note = kb.read(path)
        assert note["created_by"] == "organizer"
        assert note["source"]["kind"] == "merge"

    def test_update_preserves_provenance(self, kb, app):
        """Provenance that does not survive an edit is worse than none."""
        path = kb.write(
            "Editable", "v1", type="note",
            created_by="mycelos", source={"kind": "chat"},
        )
        kb.update(path, content="v2")
        row = app.storage.fetchone(
            "SELECT created_by, source FROM knowledge_notes WHERE path=?", (path,)
        )
        assert row["created_by"] == "mycelos"
        assert json.loads(row["source"]) == {"kind": "chat"}

    def test_audit_event_carries_creator(self, kb, app):
        kb.write(
            "Audited", "body", type="note",
            created_by="mycelos", source={"kind": "chat"},
        )
        row = app.storage.fetchone(
            "SELECT agent_id FROM audit_events WHERE event_type='knowledge.note.created' "
            "ORDER BY id DESC LIMIT 1"
        )
        assert row["agent_id"] == "mycelos"

    def test_frontmatter_contains_provenance(self, kb, app):
        """Files stay self-describing: created_by/source land in frontmatter."""
        path = kb.write(
            "Self describing", "body", type="note",
            created_by="mycelos", source={"kind": "chat"},
        )
        md = (app.data_dir / "knowledge" / (path + ".md")).read_text(encoding="utf-8")
        assert "created_by: mycelos" in md
        assert "kind: chat" in md

    def test_store_document_records_source(self, kb, app):
        path = kb.store_document(
            b"%PDF-1.4 fake", "invoice.pdf", title="Invoice",
            created_by="user", source={"kind": "upload", "filename": "invoice.pdf"},
        )
        row = app.storage.fetchone(
            "SELECT created_by, source FROM knowledge_notes WHERE path=?", (path,)
        )
        assert row["created_by"] == "user"
        assert json.loads(row["source"])["kind"] == "upload"
