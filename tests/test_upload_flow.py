"""Tests for the new /api/upload + /api/sessions/.../attachments flow."""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "upload-flow-test-key"
        from mycelos.app import App
        from mycelos.gateway.server import create_app
        App(Path(tmp)).initialize()
        fastapi_app = create_app(Path(tmp), no_scheduler=True, host="0.0.0.0")
        with TestClient(fastapi_app) as c:
            yield c, Path(tmp)


def test_upload_saves_to_session_folder(client) -> None:
    c, tmp = client
    files = {"file": ("hello.txt", io.BytesIO(b"hi"), "text/plain")}
    resp = c.post("/api/upload", files=files, data={"session_id": "s-test"})
    assert resp.status_code == 200, resp.text

    saved = tmp / "sessions" / "s-test" / "attachments" / "hello.txt"
    assert saved.exists()
    assert saved.read_bytes() == b"hi"


def test_upload_does_not_write_marker_into_session_history(client) -> None:
    c, tmp = client
    files = {"file": ("doc.pdf", io.BytesIO(b"%PDF-fake"), "application/pdf")}
    resp = c.post("/api/upload", files=files, data={"session_id": "s-mk"})
    assert resp.status_code == 200, resp.text

    from mycelos.app import App
    app = App(tmp)
    msgs = app.session_store.load_messages("s-mk")
    assert all(
        not m.get("content", "").lstrip().startswith("[System:")
        for m in msgs
    ), msgs


def test_upload_does_not_auto_ingest_into_kb(client) -> None:
    c, tmp = client
    files = {"file": ("auto.pdf", io.BytesIO(b"%PDF-fake"), "application/pdf")}
    resp = c.post("/api/upload", files=files, data={"session_id": "s-kb"})
    assert resp.status_code == 200

    from mycelos.app import App
    app = App(tmp)
    notes = app.storage.fetchall(
        "SELECT path FROM knowledge_notes WHERE path LIKE '%auto%'"
    )
    assert notes == [], notes


def test_upload_oversized_pdf_rejected(client) -> None:
    c, _ = client
    big = b"x" * (33 * 1024 * 1024)
    files = {"file": ("big.pdf", io.BytesIO(big), "application/pdf")}
    resp = c.post("/api/upload", files=files, data={"session_id": "s-big"})
    assert resp.status_code == 200  # SSE returns 200 with error event in stream
    assert "too large" in resp.text.lower()


def test_upload_unsupported_type_rejected(client) -> None:
    c, _ = client
    files = {"file": ("evil.exe", io.BytesIO(b"\x00\x01"), "application/octet-stream")}
    resp = c.post("/api/upload", files=files, data={"session_id": "s-evil"})
    assert "unsupported" in resp.text.lower()


def test_serve_attachment_endpoint(client) -> None:
    c, _ = client
    files = {"file": ("readme.txt", io.BytesIO(b"content"), "text/plain")}
    c.post("/api/upload", files=files, data={"session_id": "s-srv"})

    resp = c.get("/api/sessions/s-srv/attachments/readme.txt")
    assert resp.status_code == 200
    assert resp.content == b"content"


def test_serve_attachment_path_traversal(client) -> None:
    c, _ = client
    resp = c.get("/api/sessions/s/attachments/../../etc/passwd")
    assert resp.status_code in (400, 404)


def test_serve_attachment_missing(client) -> None:
    c, _ = client
    resp = c.get("/api/sessions/no-such/attachments/no.txt")
    assert resp.status_code == 404


def test_upload_empty_file_rejected(client) -> None:
    c, _ = client
    files = {"file": ("empty.txt", io.BytesIO(b""), "text/plain")}
    resp = c.post("/api/upload", files=files, data={"session_id": "s-empty"})
    assert resp.status_code == 200
    assert "empty" in resp.text.lower()


def test_serve_attachment_session_id_traversal(client) -> None:
    """Session id like '..' must not escape the sessions tree."""
    c, _ = client
    # First upload one file so there's something on disk
    files = {"file": ("h.txt", io.BytesIO(b"x"), "text/plain")}
    c.post("/api/upload", files=files, data={"session_id": "victim"})

    # Try to read it through a traversal session id
    resp = c.get("/api/sessions/..%2Fvictim%2Fattachments/h.txt")
    # Either FastAPI normalises the URL and routes elsewhere, or our guard kicks in.
    assert resp.status_code in (400, 404), resp.text
