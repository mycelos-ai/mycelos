"""API tests for /api/organizer/* endpoints."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mycelos.knowledge.inbox import InboxService


@pytest.fixture
def api_client():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-organizer-api"

        from mycelos.app import App
        from mycelos.setup import web_init
        from mycelos.gateway.server import create_app

        app_obj = App(data_dir)
        app_obj.initialize()
        web_init(app_obj, api_key="sk-ant-api03-FAKETESTKEYFORORG")

        fastapi_app = create_app(data_dir, no_scheduler=True, host="0.0.0.0")
        client = TestClient(fastapi_app)
        app_obj_from_state = fastapi_app.state.mycelos
        yield client, app_obj_from_state


def _seed_note(app_obj, title: str = "Seeded") -> str:
    return app_obj.knowledge_base.write(title=title, content="body", topic="notes")


def test_list_suggestions_empty(api_client) -> None:
    client, _ = api_client
    resp = client.get("/api/organizer/suggestions")
    assert resp.status_code == 200
    # list_pending_by_topic returns a list of topic groups (empty when no suggestions)
    assert resp.json() == []


def test_list_suggestions_after_seed(api_client) -> None:
    client, app_obj = api_client
    path = _seed_note(app_obj)
    InboxService(app_obj.storage).add(path, "move", {"target": "projects/mycelos"}, 0.7)

    resp = client.get("/api/organizer/suggestions")
    assert resp.status_code == 200
    data = resp.json()
    # list_pending_by_topic returns groups; find the one with our note
    assert isinstance(data, list)
    assert len(data) >= 1
    all_notes = [n for g in data for n in g.get("notes", [])]
    assert any(n["note_path"] == path for n in all_notes)


def test_accept_suggestion_flips_status(api_client) -> None:
    client, app_obj = api_client
    path = _seed_note(app_obj)
    sid = InboxService(app_obj.storage).add(path, "move", {"target": "notes"}, 0.9)

    resp = client.post(f"/api/organizer/suggestions/{sid}/accept")
    assert resp.status_code == 200

    row = app_obj.storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,)
    )
    assert row["status"] == "accepted"


def test_dismiss_suggestion_flips_status(api_client) -> None:
    client, app_obj = api_client
    path = _seed_note(app_obj)
    sid = InboxService(app_obj.storage).add(
        path, "link", {"from": path, "to": "notes/y"}, 0.85
    )

    resp = client.post(f"/api/organizer/suggestions/{sid}/dismiss")
    assert resp.status_code == 200

    row = app_obj.storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,)
    )
    assert row["status"] == "dismissed"


def test_accept_unknown_suggestion_returns_404(api_client) -> None:
    client, _ = api_client
    resp = client.post("/api/organizer/suggestions/99999/accept")
    assert resp.status_code == 404


def test_force_run_returns_counts(api_client, monkeypatch) -> None:
    client, app_obj = api_client
    monkeypatch.setattr(
        app_obj.knowledge_organizer,
        "run",
        lambda user_id="default": {
            "processed": 0, "archived": 0, "moved": 0, "suggested": 0, "linked": 0
        },
    )
    resp = client.post("/api/organizer/run")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) >= {"processed", "archived", "moved", "suggested", "linked"}
