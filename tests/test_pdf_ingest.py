"""Tests for PDF ingest pipeline — extract, summarize, store."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-ingest"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def fake_pdf(tmp_path) -> Path:
    """Create a minimal dummy PDF file (not real PDF content — only for path tests)."""
    pdf = tmp_path / "report_q1-2025.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake content")
    return pdf


# ---------------------------------------------------------------------------
# Helper: LLM mock response
# ---------------------------------------------------------------------------

def _llm_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    return resp


# ---------------------------------------------------------------------------
# ingest_pdf — text extraction path
# ---------------------------------------------------------------------------

class TestIngestTextPdf:
    @patch("mycelos.knowledge.ingest.pdf_page_count", return_value=3)
    @patch(
        "mycelos.knowledge.ingest.extract_text",
        return_value=("This is document text about revenue growth.", "pdf"),
    )
    def test_status_summarized(self, mock_extract, mock_count, app, fake_pdf):
        app.llm.complete = MagicMock(side_effect=[
            _llm_response("- Revenue grew 20%\n- Key action: expand team"),
            _llm_response('["finance", "revenue", "q1"]'),
        ])

        from mycelos.knowledge.ingest import ingest_pdf
        result = ingest_pdf(app, fake_pdf)

        assert result["status"] == "summarized"
        assert result["text_extracted"] is True
        assert result["vision_needed"] is False
        assert result["page_count"] == 3

    @patch("mycelos.knowledge.ingest.pdf_page_count", return_value=3)
    @patch(
        "mycelos.knowledge.ingest.extract_text",
        return_value=("This is document text about revenue growth.", "pdf"),
    )
    def test_note_path_returned(self, mock_extract, mock_count, app, fake_pdf):
        app.llm.complete = MagicMock(side_effect=[
            _llm_response("Summary text"),
            _llm_response('["finance"]'),
        ])

        from mycelos.knowledge.ingest import ingest_pdf
        result = ingest_pdf(app, fake_pdf)

        assert result["note_path"] is not None
        assert isinstance(result["note_path"], str)
        assert len(result["note_path"]) > 0

    @patch("mycelos.knowledge.ingest.pdf_page_count", return_value=2)
    @patch(
        "mycelos.knowledge.ingest.extract_text",
        return_value=("Some document text.", "pdf"),
    )
    def test_audit_event_logged(self, mock_extract, mock_count, app, fake_pdf):
        app.llm.complete = MagicMock(side_effect=[
            _llm_response("Summary"),
            _llm_response('["tag1"]'),
        ])

        from mycelos.knowledge.ingest import ingest_pdf
        ingest_pdf(app, fake_pdf)

        row = app.storage.fetchone(
            "SELECT details FROM audit_events WHERE event_type='knowledge.document.ingested' ORDER BY id DESC LIMIT 1"
        )
        assert row is not None
        details = json.loads(row["details"])
        assert details["method"] == "text"
        assert details["pages"] == 2

    @patch("mycelos.knowledge.ingest.pdf_page_count", return_value=1)
    @patch(
        "mycelos.knowledge.ingest.extract_text",
        return_value=("Short text.", "pdf"),
    )
    def test_tags_extracted_from_llm(self, mock_extract, mock_count, app, fake_pdf):
        app.llm.complete = MagicMock(side_effect=[
            _llm_response("Summary text"),
            _llm_response('["finance", "invoice"]'),
        ])

        from mycelos.knowledge.ingest import ingest_pdf
        result = ingest_pdf(app, fake_pdf)

        # Note should exist in DB; check tags stored
        note_row = app.storage.fetchone(
            "SELECT tags FROM knowledge_notes WHERE path=?", (result["note_path"],)
        )
        assert note_row is not None
        stored_tags = json.loads(note_row["tags"] or "[]")
        assert "finance" in stored_tags or "invoice" in stored_tags


# ---------------------------------------------------------------------------
# ingest_pdf — scanned (no text) path
# ---------------------------------------------------------------------------

class TestIngestScannedPdf:
    @patch("mycelos.knowledge.ingest.pdf_page_count", return_value=5)
    @patch(
        "mycelos.knowledge.ingest.extract_text",
        return_value=("", "vision_needed"),
    )
    def test_status_stored(self, mock_extract, mock_count, app, fake_pdf):
        from mycelos.knowledge.ingest import ingest_pdf
        result = ingest_pdf(app, fake_pdf)

        assert result["status"] == "stored"
        assert result["text_extracted"] is False
        assert result["vision_needed"] is True
        assert result["page_count"] == 5

    @patch("mycelos.knowledge.ingest.pdf_page_count", return_value=5)
    @patch(
        "mycelos.knowledge.ingest.extract_text",
        return_value=("", "vision_needed"),
    )
    def test_note_path_returned(self, mock_extract, mock_count, app, fake_pdf):
        from mycelos.knowledge.ingest import ingest_pdf
        result = ingest_pdf(app, fake_pdf)

        assert result["note_path"] is not None
        assert isinstance(result["note_path"], str)

    @patch("mycelos.knowledge.ingest.pdf_page_count", return_value=5)
    @patch(
        "mycelos.knowledge.ingest.extract_text",
        return_value=("", "vision_needed"),
    )
    def test_scanned_tag_present(self, mock_extract, mock_count, app, fake_pdf):
        from mycelos.knowledge.ingest import ingest_pdf
        result = ingest_pdf(app, fake_pdf)

        note_row = app.storage.fetchone(
            "SELECT tags FROM knowledge_notes WHERE path=?", (result["note_path"],)
        )
        assert note_row is not None
        stored_tags = json.loads(note_row["tags"] or "[]")
        assert "scanned" in stored_tags

    @patch("mycelos.knowledge.ingest.pdf_page_count", return_value=4)
    @patch(
        "mycelos.knowledge.ingest.extract_text",
        return_value=("", "vision_needed"),
    )
    def test_audit_event_logged(self, mock_extract, mock_count, app, fake_pdf):
        from mycelos.knowledge.ingest import ingest_pdf
        ingest_pdf(app, fake_pdf)

        row = app.storage.fetchone(
            "SELECT details FROM audit_events WHERE event_type='knowledge.document.ingested' ORDER BY id DESC LIMIT 1"
        )
        assert row is not None
        details = json.loads(row["details"])
        assert details["method"] == "placeholder"
        assert details["pages"] == 4

    @patch("mycelos.knowledge.ingest.pdf_page_count", return_value=5)
    @patch(
        "mycelos.knowledge.ingest.extract_text",
        return_value=("", "vision_needed"),
    )
    def test_no_llm_calls_for_scanned(self, mock_extract, mock_count, app, fake_pdf):
        """Scanned PDFs must not trigger LLM summarization."""
        app.llm.complete = MagicMock()

        from mycelos.knowledge.ingest import ingest_pdf
        ingest_pdf(app, fake_pdf)

        app.llm.complete.assert_not_called()


# ---------------------------------------------------------------------------
# _summarize helper
# ---------------------------------------------------------------------------

class TestSummarize:
    def test_returns_llm_content(self, app):
        app.llm.complete = MagicMock(return_value=_llm_response("LLM summary result"))

        from mycelos.knowledge.ingest import _summarize
        result = _summarize(app, "Some text content", "doc.pdf")

        assert result == "LLM summary result"

    def test_fallback_on_llm_error(self, app):
        app.llm.complete = MagicMock(side_effect=RuntimeError("LLM unavailable"))

        from mycelos.knowledge.ingest import _summarize
        text = "Fallback text content"
        result = _summarize(app, text, "doc.pdf")

        assert result == text[:2000]


# ---------------------------------------------------------------------------
# _extract_tags helper
# ---------------------------------------------------------------------------

class TestExtractTags:
    def test_returns_tag_list(self, app):
        app.llm.complete = MagicMock(return_value=_llm_response('["finance", "q1", "report"]'))

        from mycelos.knowledge.ingest import _extract_tags
        tags = _extract_tags(app, "Revenue report for Q1")

        assert tags == ["finance", "q1", "report"]

    def test_handles_code_fence(self, app):
        app.llm.complete = MagicMock(return_value=_llm_response('```json\n["finance"]\n```'))

        from mycelos.knowledge.ingest import _extract_tags
        tags = _extract_tags(app, "text")

        assert "finance" in tags

    def test_returns_empty_on_error(self, app):
        app.llm.complete = MagicMock(side_effect=RuntimeError("fail"))

        from mycelos.knowledge.ingest import _extract_tags
        tags = _extract_tags(app, "text")

        assert tags == []

    def test_tags_lowercased(self, app):
        app.llm.complete = MagicMock(return_value=_llm_response('["Finance", "Q1-REPORT"]'))

        from mycelos.knowledge.ingest import _extract_tags
        tags = _extract_tags(app, "text")

        assert all(t == t.lower() for t in tags)

    def test_max_five_tags(self, app):
        app.llm.complete = MagicMock(
            return_value=_llm_response('["a","b","c","d","e","f","g"]')
        )

        from mycelos.knowledge.ingest import _extract_tags
        tags = _extract_tags(app, "text")

        assert len(tags) <= 5


# ---------------------------------------------------------------------------
# vision_analyze — error cases
# ---------------------------------------------------------------------------

class TestVisionAnalyzeErrors:
    def test_missing_note_returns_error(self, app):
        from mycelos.knowledge.ingest import vision_analyze
        result = vision_analyze(app, "nonexistent/note")

        assert result["status"] == "error"
        assert "source file" in result["message"].lower()

    def test_missing_source_file_returns_error(self, app):
        # Insert a note with no source_file
        app.storage.execute(
            "INSERT INTO knowledge_notes (path, title) VALUES (?, ?)",
            ("test/note-no-src", "Note Without Source"),
        )

        from mycelos.knowledge.ingest import vision_analyze
        result = vision_analyze(app, "test/note-no-src")

        assert result["status"] == "error"

    @patch("mycelos.knowledge.ingest.render_pdf_pages", return_value=[])
    def test_empty_render_returns_error(self, mock_render, app, tmp_path):
        # Create a real file so get_document_path resolves it
        from mycelos.knowledge.service import KnowledgeBase
        kb = KnowledgeBase(app)
        doc_dir = kb._knowledge_dir / "documents"
        doc_dir.mkdir(parents=True, exist_ok=True)
        fake_doc = doc_dir / "test.pdf"
        fake_doc.write_bytes(b"%PDF fake")

        relative = str(fake_doc.relative_to(kb._knowledge_dir))
        app.storage.execute(
            "INSERT INTO knowledge_notes (path, title, source_file) VALUES (?, ?, ?)",
            ("test/note-empty-render", "Note", relative),
        )

        from mycelos.knowledge.ingest import vision_analyze
        result = vision_analyze(app, "test/note-empty-render")

        assert result["status"] == "error"
        assert "render" in result["message"].lower()
