"""Tests for note_save_attachment + attachment_load tools."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_note_save_attachment_persists_to_kb(tmp_data_dir: Path) -> None:
    from mycelos.app import App
    from mycelos.files.session_attachments import SessionAttachmentStore
    from mycelos.tools.attachments import execute_note_save_attachment

    os.environ["MYCELOS_MASTER_KEY"] = "attach-tools-test-1"
    app = App(tmp_data_dir)
    app.initialize()

    store = SessionAttachmentStore(app.data_dir / "sessions")
    saved = store.save("test-session", b"hello pdf", "report.pdf")

    result = execute_note_save_attachment(
        {"filename": "report.pdf", "summary": "A test report.", "tags": ["x"]},
        context={"app": app, "session_id": "test-session"},
    )
    assert result["status"] == "saved"
    assert result["path"].startswith("notes/")

    # Note exists in KB — knowledge_base method is `read`, not `get`
    note = app.knowledge_base.read(result["path"])
    assert note is not None


def test_note_save_attachment_missing_file(tmp_data_dir: Path) -> None:
    from mycelos.app import App
    from mycelos.tools.attachments import execute_note_save_attachment

    os.environ["MYCELOS_MASTER_KEY"] = "attach-tools-test-2"
    app = App(tmp_data_dir)
    app.initialize()

    result = execute_note_save_attachment(
        {"filename": "nope.pdf", "summary": "x", "tags": []},
        context={"app": app, "session_id": "no-such-session"},
    )
    assert result["status"] == "error"
    assert "not found" in result["message"].lower()


def test_attachment_load_sets_force_include_flag() -> None:
    from mycelos.tools.attachments import execute_attachment_load

    class FakeService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def mark_force_include(self, session_id: str, filename: str) -> None:
            self.calls.append((session_id, filename))

    svc = FakeService()
    result = execute_attachment_load(
        {"filename": "foo.pdf"},
        context={"chat_service": svc, "session_id": "s1"},
    )
    assert result["status"] == "loaded"
    assert result["filename"] == "foo.pdf"
    assert svc.calls == [("s1", "foo.pdf")]


def test_attachment_load_missing_context() -> None:
    from mycelos.tools.attachments import execute_attachment_load

    result = execute_attachment_load(
        {"filename": "x"},
        context={},  # no chat_service / session_id
    )
    assert result["status"] == "error"
