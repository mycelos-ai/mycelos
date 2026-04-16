"""Tests for admin and session endpoints."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-admin-routes"

        from mycelos.app import App
        from mycelos.setup import web_init
        app = App(data_dir)
        app.initialize()
        web_init(app, api_key="sk-ant-api03-FAKETESTKEYADMINROUTES")

        from mycelos.gateway.server import create_app
        fastapi_app = create_app(data_dir, no_scheduler=True, host="0.0.0.0")
        yield TestClient(fastapi_app)


# --- Session download (moved from admin to /api/sessions/) ---


def test_session_download_jsonl(client: TestClient):
    mycelos = client.app.state.mycelos
    sid = mycelos.session_store.create_session()
    mycelos.session_store.append_message(sid, role="user", content="test")

    resp = client.get(f"/api/sessions/{sid}/download?format=jsonl")
    assert resp.status_code == 200
    assert "application/x-ndjson" in resp.headers.get("content-type", "")
    lines = resp.text.strip().split("\n")
    assert len(lines) >= 1
    for line in lines:
        json.loads(line)


def test_session_download_json(client: TestClient):
    mycelos = client.app.state.mycelos
    sid = mycelos.session_store.create_session()
    mycelos.session_store.append_message(sid, role="user", content="test")

    resp = client.get(f"/api/sessions/{sid}/download?format=json")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


def test_session_download_markdown(client: TestClient):
    mycelos = client.app.state.mycelos
    sid = mycelos.session_store.create_session()
    mycelos.session_store.append_message(sid, role="user", content="hello world")
    mycelos.session_store.append_tool_call(
        sid, tool_call_id="t1", name="note_write", args={"content": "x"}
    )
    mycelos.session_store.append_tool_result(
        sid, tool_call_id="t1", name="note_write", result={"ok": True}, duration_ms=50
    )

    resp = client.get(f"/api/sessions/{sid}/download?format=markdown")
    assert resp.status_code == 200
    assert "text/markdown" in resp.headers.get("content-type", "")
    body = resp.text
    assert "# Session" in body or "hello world" in body
    assert "note_write" in body


def test_session_download_defaults_to_markdown(client: TestClient):
    mycelos = client.app.state.mycelos
    sid = mycelos.session_store.create_session()
    mycelos.session_store.append_message(sid, role="user", content="test")

    resp = client.get(f"/api/sessions/{sid}/download")
    assert resp.status_code == 200
    assert "text/markdown" in resp.headers.get("content-type", "")


def test_session_download_invalid_format(client: TestClient):
    mycelos = client.app.state.mycelos
    sid = mycelos.session_store.create_session()
    mycelos.session_store.append_message(sid, role="user", content="test")

    resp = client.get(f"/api/sessions/{sid}/download?format=csv")
    assert resp.status_code == 400


# --- Run ↔ Session linking ---


def _register_test_workflow(mycelos, wf_id: str = "linktest-wf") -> None:
    mycelos.workflow_registry.register(
        wf_id, "Link Test",
        steps=[{"id": "s1"}],
        plan="Do it.",
        allowed_tools=[],
    )


def test_workflow_run_detail_includes_session_id(client: TestClient):
    """GET /api/workflow-runs/{id} returns session_id so UI can link back."""
    mycelos = client.app.state.mycelos
    sid = mycelos.session_store.create_session()
    _register_test_workflow(mycelos)
    mycelos.workflow_run_manager.start(
        workflow_id="linktest-wf", run_id="run-link-1", session_id=sid,
    )

    resp = client.get("/api/workflow-runs/run-link-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == sid


def test_workflow_run_detail_session_id_null_for_headless(client: TestClient):
    """Headless runs still work — session_id just comes back as null."""
    mycelos = client.app.state.mycelos
    _register_test_workflow(mycelos, "linktest-wf-2")
    mycelos.workflow_run_manager.start(
        workflow_id="linktest-wf-2", run_id="run-link-headless",
    )

    resp = client.get("/api/workflow-runs/run-link-headless")
    assert resp.status_code == 200
    assert resp.json()["session_id"] is None


# --- Doctor (health-check) endpoint ---


def test_admin_doctor_returns_checks(client: TestClient):
    """GET /api/admin/doctor runs the full health-check suite and returns
    a structured list. Read-only — no mutation, no LLM, no subprocess."""
    resp = client.get("/api/admin/doctor")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    # Every entry has the three fields the UI renders
    for entry in data:
        assert "category" in entry
        assert "status" in entry
        assert "details" in entry
    # Storage is always part of the suite
    assert any(e["category"] == "storage" for e in data)


def test_admin_doctor_skips_server_selfcheck(client: TestClient):
    """The doctor endpoint runs inside the gateway — pinging ourselves
    would be redundant and noisy, so the 'server' category must not
    appear in the response."""
    resp = client.get("/api/admin/doctor")
    assert resp.status_code == 200
    data = resp.json()
    assert all(e["category"] != "server" for e in data)


# --- Inbox endpoint (for the header bell dropdown) ---


def test_admin_inbox_empty_state(client: TestClient):
    """A fresh install has nothing to show in the inbox."""
    resp = client.get("/api/admin/inbox")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {
        "reminders": [],
        "waiting_workflows": [],
        "failed_workflows": [],
        "total": 0,
    }


def test_admin_inbox_lists_due_reminders(client: TestClient):
    """Tasks with reminder=1 and a due date in the past appear in the list."""
    mycelos = client.app.state.mycelos
    kb = mycelos.knowledge_base
    from datetime import date
    kb.write(
        title="Take out trash",
        content="today",
        type="task",
        status="open",
        due=date.today().isoformat(),
        reminder=True,
    )

    resp = client.get("/api/admin/inbox")
    data = resp.json()
    titles = [r["title"] for r in data["reminders"]]
    assert "Take out trash" in titles
    assert data["total"] >= 1


def test_admin_inbox_hides_future_reminders(client: TestClient):
    """Reminders scheduled for the future must not pollute the bell —
    the inbox is for things that need attention *now*, not a calendar."""
    mycelos = client.app.state.mycelos
    kb = mycelos.knowledge_base
    from datetime import date, timedelta
    kb.write(
        title="Way in the future",
        content="x",
        type="task",
        status="open",
        due=(date.today() + timedelta(days=3)).isoformat(),
        reminder=True,
    )

    resp = client.get("/api/admin/inbox")
    titles = [r["title"] for r in resp.json()["reminders"]]
    assert "Way in the future" not in titles


def test_admin_inbox_hides_dismissed_reminders(client: TestClient):
    """Once a reminder is dismissed (reminder_fired_at set), it stays
    out of the bell."""
    mycelos = client.app.state.mycelos
    kb = mycelos.knowledge_base
    from datetime import date
    kb.write(
        title="Been there done that",
        content="x",
        type="task",
        status="open",
        due=date.today().isoformat(),
        reminder=True,
    )
    mycelos.storage.execute(
        "UPDATE knowledge_notes SET reminder_fired_at='2026-04-11T08:00:00Z' WHERE title=?",
        ("Been there done that",),
    )

    resp = client.get("/api/admin/inbox")
    titles = [r["title"] for r in resp.json()["reminders"]]
    assert "Been there done that" not in titles


def test_admin_inbox_honors_remind_at_precision(client: TestClient):
    """remind_at in the future hides the row even if due is today."""
    mycelos = client.app.state.mycelos
    kb = mycelos.knowledge_base
    from datetime import date, datetime, timezone, timedelta
    kb.write(
        title="Reminds tomorrow even though due today",
        content="x",
        type="task",
        status="open",
        due=date.today().isoformat(),
        reminder=True,
    )
    future = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    mycelos.storage.execute(
        "UPDATE knowledge_notes SET remind_at=? WHERE title=?",
        (future, "Reminds tomorrow even though due today"),
    )

    resp = client.get("/api/admin/inbox")
    titles = [r["title"] for r in resp.json()["reminders"]]
    assert "Reminds tomorrow even though due today" not in titles


def test_admin_inbox_lists_waiting_workflows(client: TestClient):
    mycelos = client.app.state.mycelos
    mycelos.workflow_registry.register(
        "inbox-wf-1", "Inbox WF",
        steps=[{"id": "s1"}],
        plan="do",
        allowed_tools=[],
    )
    mycelos.workflow_run_manager.start(workflow_id="inbox-wf-1", run_id="inbox-run-1")
    mycelos.workflow_run_manager.wait_for_input("inbox-run-1", prompt="need answer")

    resp = client.get("/api/admin/inbox")
    data = resp.json()
    waiting_ids = [w["id"] for w in data["waiting_workflows"]]
    assert "inbox-run-1" in waiting_ids
    entry = next(w for w in data["waiting_workflows"] if w["id"] == "inbox-run-1")
    assert entry["status"] == "waiting_input"
    assert entry.get("workflow_name")


def test_admin_inbox_dismiss_sets_fired_at(client: TestClient):
    """POST /api/admin/inbox/dismiss marks a reminder as fired so it
    disappears from the bell and the scheduler won't re-fire it."""
    mycelos = client.app.state.mycelos
    kb = mycelos.knowledge_base
    from datetime import date
    kb.write(
        title="Dismiss me via API",
        content="x",
        type="task",
        status="open",
        due=date.today().isoformat(),
        reminder=True,
    )
    path = mycelos.storage.fetchone(
        "SELECT path FROM knowledge_notes WHERE title=?",
        ("Dismiss me via API",),
    )["path"]

    resp = client.post("/api/admin/inbox/dismiss", json={"path": path})
    assert resp.status_code == 200
    assert resp.json()["status"] == "dismissed"

    # Row now has reminder_fired_at set
    row = mycelos.storage.fetchone(
        "SELECT reminder_fired_at FROM knowledge_notes WHERE path=?", (path,)
    )
    assert row["reminder_fired_at"] is not None

    # And the inbox no longer shows it
    inbox = client.get("/api/admin/inbox").json()
    titles = [r["title"] for r in inbox["reminders"]]
    assert "Dismiss me via API" not in titles


def test_admin_inbox_dismiss_unknown_path_404(client: TestClient):
    resp = client.post(
        "/api/admin/inbox/dismiss",
        json={"path": "tasks/never-existed"},
    )
    assert resp.status_code == 404


def test_admin_inbox_only_recent_failed_workflows(client: TestClient):
    """Failed runs older than 24 hours don't clutter the inbox."""
    mycelos = client.app.state.mycelos
    mycelos.workflow_registry.register(
        "inbox-wf-2", "Inbox WF 2",
        steps=[{"id": "s1"}],
        plan="do",
        allowed_tools=[],
    )
    # Recent failure (within 24h)
    mycelos.workflow_run_manager.start(workflow_id="inbox-wf-2", run_id="recent-fail")
    mycelos.workflow_run_manager.fail("recent-fail", error="boom")
    # Old failure (backdate to 2 days ago)
    mycelos.workflow_run_manager.start(workflow_id="inbox-wf-2", run_id="old-fail")
    mycelos.workflow_run_manager.fail("old-fail", error="old boom")
    mycelos.storage.execute(
        "UPDATE workflow_runs SET updated_at = datetime('now', '-2 days') WHERE id = ?",
        ("old-fail",),
    )

    resp = client.get("/api/admin/inbox")
    data = resp.json()
    failed_ids = {f["id"] for f in data["failed_workflows"]}
    assert "recent-fail" in failed_ids
    assert "old-fail" not in failed_ids
