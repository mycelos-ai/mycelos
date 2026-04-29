"""Tests for the ChatService Multi-Part attachment build.

We exercise the attachment-stitching logic in isolation by calling a
helper method on the service, so we don't have to spin up a real LLM.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _service(tmp_data_dir: Path):
    """Real ChatService against a real App with a tmp data dir."""
    from mycelos.app import App
    from mycelos.chat.service import ChatService
    os.environ["MYCELOS_MASTER_KEY"] = "multipart-test-key"
    app = App(tmp_data_dir)
    app.initialize()
    service = ChatService(app)
    return service, app


def test_no_attachments_returns_text_user_message(tmp_data_dir: Path) -> None:
    service, app = _service(tmp_data_dir)
    blocks, evicted = service._build_attachment_blocks(
        session_id="s1", budget_tokens=200_000,
    )
    assert blocks == []
    assert evicted == []


def test_pdf_attachment_becomes_document_block(tmp_data_dir: Path) -> None:
    from mycelos.files.session_attachments import SessionAttachmentStore

    service, app = _service(tmp_data_dir)
    store = SessionAttachmentStore(app.data_dir / "sessions")
    store.save("s1", b"%PDF-fake-bytes", "report.pdf")

    blocks, evicted = service._build_attachment_blocks(
        session_id="s1", budget_tokens=200_000,
    )
    assert len(blocks) == 1
    assert blocks[0]["type"] == "document"
    assert blocks[0]["source"]["media_type"] == "application/pdf"
    assert blocks[0]["source"]["type"] == "base64"
    assert evicted == []


def test_image_attachment_becomes_image_block(tmp_data_dir: Path) -> None:
    from mycelos.files.session_attachments import SessionAttachmentStore

    service, app = _service(tmp_data_dir)
    store = SessionAttachmentStore(app.data_dir / "sessions")
    store.save("s1", b"\x89PNG\r\n\x1a\n", "photo.png")

    blocks, evicted = service._build_attachment_blocks(
        session_id="s1", budget_tokens=200_000,
    )
    assert len(blocks) == 1
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["media_type"] == "image/png"


def test_text_attachment_becomes_text_block(tmp_data_dir: Path) -> None:
    from mycelos.files.session_attachments import SessionAttachmentStore

    service, app = _service(tmp_data_dir)
    store = SessionAttachmentStore(app.data_dir / "sessions")
    store.save("s1", b"hello world\nthis is text", "notes.txt")

    blocks, evicted = service._build_attachment_blocks(
        session_id="s1", budget_tokens=200_000,
    )
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"
    assert "hello world" in blocks[0]["text"]
    assert "[Attachment: notes.txt]" in blocks[0]["text"]


def test_eviction_kicks_oldest_first(tmp_data_dir: Path) -> None:
    import time
    from mycelos.files.session_attachments import SessionAttachmentStore

    service, app = _service(tmp_data_dir)
    store = SessionAttachmentStore(app.data_dir / "sessions")
    # Three PDFs of 100k bytes each — at bytes/50, each ≈ 2_000 tokens
    store.save("s1", b"x" * 100_000, "old.pdf")
    time.sleep(0.01)
    store.save("s1", b"x" * 100_000, "mid.pdf")
    time.sleep(0.01)
    store.save("s1", b"x" * 100_000, "new.pdf")

    # Budget: 5_000 tokens — fits 2 of 3 files, oldest evicted
    blocks, evicted = service._build_attachment_blocks(
        session_id="s1", budget_tokens=5_000,
    )
    assert evicted == ["old.pdf"]
    assert len(blocks) == 2


def test_force_include_skips_eviction(tmp_data_dir: Path) -> None:
    import time
    from mycelos.files.session_attachments import SessionAttachmentStore

    service, app = _service(tmp_data_dir)
    store = SessionAttachmentStore(app.data_dir / "sessions")
    store.save("s1", b"x" * 100_000, "important.pdf")
    time.sleep(0.01)
    store.save("s1", b"x" * 100_000, "later.pdf")

    # Mark the OLDER one as force-included → eviction must skip it,
    # kick the newer one instead even though it's not oldest.
    service.mark_force_include("s1", "important.pdf")

    # Budget: 2_500 tokens — fits 1 of 2 files, force-include keeps the older
    blocks, evicted = service._build_attachment_blocks(
        session_id="s1", budget_tokens=2_500,
    )
    assert "important.pdf" not in evicted
    assert "later.pdf" in evicted
    assert len(blocks) == 1
    # Force-include flag is NOT consumed by the build itself — it should
    # still be set, only handle_message's success path clears it.
    assert "important.pdf" in service._session_force_include.get("s1", set())


def test_unsupported_files_are_skipped(tmp_data_dir: Path) -> None:
    from mycelos.files.session_attachments import SessionAttachmentStore

    service, app = _service(tmp_data_dir)
    store = SessionAttachmentStore(app.data_dir / "sessions")
    store.save("s1", b"binary garbage", "weird.bin")

    blocks, evicted = service._build_attachment_blocks(
        session_id="s1", budget_tokens=200_000,
    )
    assert blocks == []
