"""Tests for day-one knowledge — connector ingest into the knowledge base.

Pulls content from connected services (Gmail first) into knowledge notes
with full provenance and external_id idempotency. The hardened organizer
classifies them afterwards.
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
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-ingest"
        a = App(Path(tmp))
        a.initialize()
        yield a


def _gmail_threads_payload(threads):
    """Shape returned by the Gmail MCP search_threads tool (simplified)."""
    return {"threads": threads}


def _thread(tid, subject, snippet, sender="alice@example.com",
            date="2026-06-01T10:00:00Z"):
    return {
        "id": tid,
        "subject": subject,
        "snippet": snippet,
        "from": sender,
        "date": date,
    }


class _FakeMcp:
    """Stands in for app.mcp_manager — records calls, returns payloads."""

    def __init__(self, payload):
        self.calls: list = []
        self._payload = payload

    def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return self._payload


class TestGmailIngest:
    def test_creates_notes_with_provenance(self, app):
        from mycelos.knowledge.connector_ingest import ingest_gmail
        mcp = _FakeMcp(_gmail_threads_payload([
            _thread("t-1", "Invoice June", "Your invoice is attached"),
            _thread("t-2", "Meeting notes", "Summary of the call"),
        ]))
        result = ingest_gmail(app, mcp=mcp)

        assert result["created"] == 2
        rows = app.storage.fetchall(
            "SELECT path, title, created_by, source FROM knowledge_notes "
            "WHERE created_by='import'"
        )
        assert len(rows) == 2
        sources = [json.loads(r["source"]) for r in rows]
        assert all(s["kind"] == "connector" for s in sources)
        assert all(s["connector"] == "gmail" for s in sources)
        assert {s["external_id"] for s in sources} == {"t-1", "t-2"}

    def test_idempotent_on_external_id(self, app):
        """Re-running the ingest must not duplicate notes — the external_id
        is the dedup key, regardless of title or content changes."""
        from mycelos.knowledge.connector_ingest import ingest_gmail
        payload = _gmail_threads_payload([_thread("t-1", "Invoice June", "v1")])
        first = ingest_gmail(app, mcp=_FakeMcp(payload))
        assert first["created"] == 1

        again = _gmail_threads_payload([_thread("t-1", "Invoice June (edited)", "v2")])
        second = ingest_gmail(app, mcp=_FakeMcp(again))
        assert second["created"] == 0
        assert second["skipped_existing"] == 1
        rows = app.storage.fetchall(
            "SELECT path FROM knowledge_notes WHERE created_by='import'"
        )
        assert len(rows) == 1

    def test_notes_enter_organizer_queue(self, app):
        """Ingested notes must be organizer_state='pending' so the (hardened)
        organizer classifies them into topics."""
        from mycelos.knowledge.connector_ingest import ingest_gmail
        ingest_gmail(app, mcp=_FakeMcp(_gmail_threads_payload(
            [_thread("t-9", "Idea", "Try the new approach")]
        )))
        row = app.storage.fetchone(
            "SELECT organizer_state FROM knowledge_notes WHERE created_by='import'"
        )
        assert row["organizer_state"] == "pending"

    def test_error_result_creates_nothing(self, app):
        """A connector error must fail closed — no notes, error reported."""
        from mycelos.knowledge.connector_ingest import ingest_gmail
        result = ingest_gmail(app, mcp=_FakeMcp({"error": "gmail not connected"}))
        assert result.get("error")
        assert result.get("created", 0) == 0
        rows = app.storage.fetchall(
            "SELECT path FROM knowledge_notes WHERE created_by='import'"
        )
        assert rows == []

    def test_handles_nested_content_shape(self, app):
        """MCP servers often wrap results as content blocks with JSON text —
        the parser must unwrap that shape too."""
        from mycelos.knowledge.connector_ingest import ingest_gmail
        wrapped = {
            "content": [
                {"type": "text",
                 "text": json.dumps(_gmail_threads_payload(
                     [_thread("t-7", "Wrapped", "in content blocks")]
                 ))}
            ]
        }
        result = ingest_gmail(app, mcp=_FakeMcp(wrapped))
        assert result["created"] == 1

    def test_audit_event_emitted(self, app):
        from mycelos.knowledge.connector_ingest import ingest_gmail
        ingest_gmail(app, mcp=_FakeMcp(_gmail_threads_payload(
            [_thread("t-3", "Audit me", "body")]
        )))
        row = app.storage.fetchone(
            "SELECT details FROM audit_events WHERE event_type='knowledge.ingest.completed'"
        )
        assert row is not None
        details = json.loads(row["details"])
        assert details["connector"] == "gmail"
        assert details["created"] == 1

    def test_max_threads_cap(self, app):
        from mycelos.knowledge.connector_ingest import ingest_gmail
        threads = [_thread(f"t-{i}", f"Mail {i}", "x") for i in range(10)]
        result = ingest_gmail(app, mcp=_FakeMcp(_gmail_threads_payload(threads)),
                              max_items=3)
        assert result["created"] == 3


class TestIngestEndpoint:
    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient
        from mycelos.gateway.server import create_app
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-ingest-api"
            from mycelos.app import App
            from mycelos.setup import web_init
            a = App(data_dir)
            a.initialize()
            web_init(a, api_key="sk-ant-api03-FAKETESTKEYINGESTAPI")
            fastapi_app = create_app(data_dir, no_scheduler=True,
                                     host="0.0.0.0", allow_insecure_bind=True)
            yield TestClient(fastapi_app)

    def test_post_ingest_gmail(self, client):
        # Wire a fake MCP manager into the running app.
        mycelos = client.app.state.mycelos
        mycelos._mcp_manager = _FakeMcp(_gmail_threads_payload(
            [_thread("t-api-1", "Hello", "world")]
        ))
        resp = client.post("/api/knowledge/ingest/gmail", json={"max_items": 5})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["created"] == 1

    def test_post_ingest_unknown_source_404(self, client):
        resp = client.post("/api/knowledge/ingest/doesnotexist", json={})
        assert resp.status_code == 404
