"""API tests for POST /api/knowledge/notes (Quick Capture backend)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mycelos.gateway.server import create_app


@pytest.fixture
def knowledge_api_client() -> TestClient:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-knowledge-api"

        from mycelos.app import App
        from mycelos.setup import web_init
        app = App(data_dir)
        app.initialize()
        web_init(app, api_key="sk-ant-api03-FAKETESTKEYFORKNOWAPI")

        fastapi_app = create_app(data_dir, no_scheduler=True, host="0.0.0.0")
        yield TestClient(fastapi_app)


def test_post_note_plain_goes_to_notes(knowledge_api_client: TestClient) -> None:
    resp = knowledge_api_client.post(
        "/api/knowledge/notes",
        json={"title": "Kaffee-Rezept", "content": "3 Loeffel, kochendes Wasser"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["path"].startswith("notes/")
    assert data["parent_path"] == "notes"


def test_post_note_with_parsed_due_goes_to_tasks(knowledge_api_client: TestClient) -> None:
    resp = knowledge_api_client.post(
        "/api/knowledge/notes",
        json={"title": "Dehnen", "content": "erinnere mich in 5 minuten zu dehnen"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["path"].startswith("tasks/")
    assert data["reminder"] is True
    assert data["due"] is not None


def test_post_note_explicit_due_wins(knowledge_api_client: TestClient) -> None:
    resp = knowledge_api_client.post(
        "/api/knowledge/notes",
        json={"title": "Call Lisa", "content": "body", "due": "2026-04-09T14:00:00Z"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["due"] == "2026-04-09T14:00:00Z"
    assert data["path"].startswith("tasks/")


def test_post_note_requires_title(knowledge_api_client: TestClient) -> None:
    resp = knowledge_api_client.post(
        "/api/knowledge/notes",
        json={"content": "no title"},
    )
    assert resp.status_code == 422


def test_post_note_returns_organizer_state_pending(knowledge_api_client: TestClient) -> None:
    resp = knowledge_api_client.post(
        "/api/knowledge/notes",
        json={"title": "Random idea", "content": "Try this."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["organizer_state"] == "pending"


# --- GET /api/knowledge/notes/{path} — detail view metadata ---


def test_get_note_detail_includes_reminder_metadata(knowledge_api_client: TestClient) -> None:
    """Detail endpoint must surface reminder/remind_at/remind_via so the UI
    can show users what they've scheduled."""
    mycelos = knowledge_api_client.app.state.mycelos
    kb = mycelos.knowledge_base
    kb.write(
        title="Clean the grill",
        content="Ask Isabella.",
        type="task",
        status="open",
        due="2026-04-12",
        reminder=True,
    )
    # remind_at has no public setter yet — write it directly to the index
    mycelos.storage.execute(
        "UPDATE knowledge_notes SET remind_at = ? WHERE title = ?",
        ("2026-04-12T09:00:00Z", "Clean the grill"),
    )

    notes = mycelos.storage.fetchall(
        "SELECT path FROM knowledge_notes WHERE title = ?", ("Clean the grill",)
    )
    assert notes, "note must have been indexed"
    path = notes[0]["path"]

    resp = knowledge_api_client.get(f"/api/knowledge/notes/{path}")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["title"] == "Clean the grill"
    assert data["type"] == "task"
    assert data["reminder"] is True
    assert data["remind_at"] == "2026-04-12T09:00:00Z"
    assert data.get("remind_via") is not None


def test_get_note_detail_plain_note_no_reminder(knowledge_api_client: TestClient) -> None:
    """A plain note with no reminder gets reminder=False and remind_at=None."""
    mycelos = knowledge_api_client.app.state.mycelos
    kb = mycelos.knowledge_base
    kb.write(title="Some thought", content="just words", type="note")
    notes = mycelos.storage.fetchall(
        "SELECT path FROM knowledge_notes WHERE title = ?", ("Some thought",)
    )
    path = notes[0]["path"]

    resp = knowledge_api_client.get(f"/api/knowledge/notes/{path}")
    data = resp.json()
    assert data.get("reminder") in (False, 0, None)
    assert data.get("remind_at") is None
