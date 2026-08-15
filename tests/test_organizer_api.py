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

        fastapi_app = create_app(data_dir, no_scheduler=True, host="0.0.0.0", allow_insecure_bind=True)
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


def test_accept_move_with_missing_target_is_422_and_stays_pending(api_client) -> None:
    client, app_obj = api_client
    path = _seed_note(app_obj)
    sid = InboxService(app_obj.storage).add(path, "move", {}, 0.7)  # no target
    resp = client.post(f"/api/organizer/suggestions/{sid}/accept")
    assert resp.status_code == 422
    row = app_obj.storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "pending"


def test_accept_merge_failure_is_500_and_stays_pending(api_client) -> None:
    client, app_obj = api_client
    path = _seed_note(app_obj, "Primary")
    # duplicate_path points at a note that does not exist on disk ->
    # _execute_merge returns False
    sid = InboxService(app_obj.storage).add(
        path, "merge", {"duplicate_path": "notes/does-not-exist", "similarity": 0.95}, 0.95)
    resp = client.post(f"/api/organizer/suggestions/{sid}/accept")
    assert resp.status_code == 500
    row = app_obj.storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "pending"


def test_accept_link_with_missing_source_note_is_500_and_stays_pending(api_client) -> None:
    client, app_obj = api_client
    # "from" points at a note path that was never written to disk ->
    # kb.append_related_link returns False
    sid = InboxService(app_obj.storage).add(
        "notes/does-not-exist", "link",
        {"from": "notes/does-not-exist", "to": "notes/some-target"}, 0.85)
    resp = client.post(f"/api/organizer/suggestions/{sid}/accept")
    assert resp.status_code == 500
    row = app_obj.storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "pending"


def test_accept_generic_exception_returns_sanitized_body(api_client, monkeypatch) -> None:
    client, app_obj = api_client
    path = _seed_note(app_obj)
    sid = InboxService(app_obj.storage).add(path, "move", {"target": "notes"}, 0.9)

    def _raise(*args, **kwargs):
        raise RuntimeError("secret-internal-detail")

    monkeypatch.setattr(app_obj.knowledge_base, "move_to_topic", _raise)

    resp = client.post(f"/api/organizer/suggestions/{sid}/accept")
    assert resp.status_code == 500
    assert resp.json() == {"error": "apply failed"}
    assert "secret-internal-detail" not in resp.text
    row = app_obj.storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "pending"


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


def test_accept_all_leaves_merges_pending(api_client) -> None:
    client, app_obj = api_client
    path = _seed_note(app_obj, "Primary")
    inbox = InboxService(app_obj.storage)
    inbox.add(path, "merge", {"duplicate_path": "notes/x", "similarity": 0.95}, 0.95)

    resp = client.post("/api/organizer/accept-all")
    assert resp.status_code == 200
    assert resp.json()["skipped_merges"] == 1
    row = app_obj.storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE kind='merge'")
    assert row["status"] == "pending"  # never blanket-accepted


def test_accept_all_leaves_scope_violations_pending(api_client) -> None:
    """A rejected out-of-scope answer is never resolved in bulk.

    Accepting it as a no-op would clear the only signal that a rule or an
    attachment is wrong, without filing the note anywhere.
    """
    client, app_obj = api_client
    path = _seed_note(app_obj, "Mail")
    InboxService(app_obj.storage).add(
        path, "scope_violation", {"target": "topics/work/vorfina"}, 0.0)

    resp = client.post("/api/organizer/accept-all")
    assert resp.status_code == 200
    assert resp.json()["skipped_scope_violations"] == 1
    row = app_obj.storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE kind='scope_violation'")
    assert row["status"] == "pending"


def test_accept_scope_violation_files_the_note_in_the_fallback(api_client) -> None:
    """Accepting one means 'file it in the in-scope fallback folder' —
    the same apply as a move, one note at a time."""
    client, app_obj = api_client
    path = _seed_note(app_obj, "Mail")
    app_obj.knowledge_base.create_topic("Vorfina")
    sid = InboxService(app_obj.storage).add(
        path, "scope_violation", {"target": "topics/vorfina"}, 0.0)

    resp = client.post(f"/api/organizer/suggestions/{sid}/accept")
    assert resp.status_code == 200
    row = app_obj.storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "accepted"


def test_accept_scope_violation_without_a_target_stays_pending(api_client) -> None:
    """Fail closed: a payload with nowhere to file is not a resolution."""
    client, app_obj = api_client
    path = _seed_note(app_obj, "Mail")
    sid = InboxService(app_obj.storage).add(path, "scope_violation", {}, 0.0)

    resp = client.post(f"/api/organizer/suggestions/{sid}/accept")
    assert resp.status_code == 422
    row = app_obj.storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "pending"


def test_accept_all_counts_failures_and_leaves_them_pending(api_client) -> None:
    client, app_obj = api_client
    path = _seed_note(app_obj)
    inbox = InboxService(app_obj.storage)
    inbox.add("notes/ghost-note", "move", {"target": "topics/x"}, 0.7)

    resp = client.post("/api/organizer/accept-all")
    assert resp.status_code == 200
    assert resp.json()["failed"] == 1
    row = app_obj.storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE note_path='notes/ghost-note'")
    assert row["status"] == "pending"


def test_accept_all_counts_link_with_missing_source_as_failed(api_client) -> None:
    client, app_obj = api_client
    inbox = InboxService(app_obj.storage)
    # "from" points at a note path that was never written to disk ->
    # kb.append_related_link returns False (not an exception)
    sid = inbox.add(
        "notes/does-not-exist", "link",
        {"from": "notes/does-not-exist", "to": "notes/some-target"}, 0.85)

    resp = client.post("/api/organizer/accept-all")
    assert resp.status_code == 200
    assert resp.json()["failed"] == 1
    row = app_obj.storage.fetchone(
        "SELECT status FROM organizer_suggestions WHERE id=?", (sid,))
    assert row["status"] == "pending"
