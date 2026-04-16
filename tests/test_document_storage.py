"""Tests for KnowledgeBase.store_document and get_document_path."""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def app():
    from mycelos.app import App

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-docs"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def kb(app):
    from mycelos.knowledge.service import KnowledgeBase

    return KnowledgeBase(app)


class TestStoreDocument:
    def test_store_document_returns_path(self, kb):
        path = kb.store_document(
            b"fake pdf content",
            "report.pdf",
            title="Quarterly Report",
            summary="Key findings.",
            tags=["finance"],
        )
        assert path is not None
        assert isinstance(path, str)
        assert len(path) > 0

    def test_pdf_file_exists_in_documents_dir(self, kb):
        kb.store_document(
            b"fake pdf content",
            "report.pdf",
            title="Quarterly Report",
            summary="Key findings.",
            tags=["finance"],
        )
        doc_dir = kb._knowledge_dir / "documents"
        pdf_files = list(doc_dir.glob("*report.pdf"))
        assert len(pdf_files) == 1
        assert pdf_files[0].read_bytes() == b"fake pdf content"

    def test_knowledge_note_has_correct_type_and_source_file(self, app, kb):
        path = kb.store_document(
            b"fake pdf content",
            "report.pdf",
            title="Quarterly Report",
            summary="Key findings.",
            tags=["finance"],
        )
        row = app.storage.fetchone(
            "SELECT type, source_file FROM knowledge_notes WHERE path=?",
            (path,),
        )
        assert row is not None
        assert row["type"] == "document"
        assert row["source_file"] is not None
        assert "report.pdf" in row["source_file"]

    def test_get_document_path_returns_file(self, app, kb):
        path = kb.store_document(
            b"fake pdf content",
            "report.pdf",
            title="Quarterly Report",
            summary="Key findings.",
            tags=["finance"],
        )
        row = app.storage.fetchone(
            "SELECT source_file FROM knowledge_notes WHERE path=?",
            (path,),
        )
        source_file = row["source_file"]
        doc_path = kb.get_document_path(source_file)
        assert doc_path is not None
        assert doc_path.exists()
        assert doc_path.is_file()
        assert doc_path.read_bytes() == b"fake pdf content"

    def test_get_document_path_returns_none_for_missing(self, kb):
        result = kb.get_document_path("documents/nonexistent.pdf")
        assert result is None

    def test_get_document_path_returns_none_for_empty_string(self, kb):
        result = kb.get_document_path("")
        assert result is None

    def test_title_auto_generated_from_filename(self, app, kb):
        path = kb.store_document(b"data", "my_report_2026.pdf")
        row = app.storage.fetchone(
            "SELECT title FROM knowledge_notes WHERE path=?",
            (path,),
        )
        assert row["title"] == "My Report 2026"

    def test_duplicate_filename_gets_counter_suffix(self, kb):
        kb.store_document(b"first", "report.pdf")
        kb.store_document(b"second", "report.pdf")
        doc_dir = kb._knowledge_dir / "documents"
        pdf_files = list(doc_dir.glob("*report*.pdf"))
        assert len(pdf_files) == 2
