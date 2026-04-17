"""Tests for the Mycelos Gateway — FastAPI server with SSE streaming."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from mycelos.gateway.server import create_app


@pytest.fixture
def client() -> TestClient:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-gateway"

        from mycelos.app import App
        from mycelos.setup import web_init
        app = App(data_dir)
        app.initialize()
        # Run full web-init so /api/chat's onboarding gate (which checks for
        # credentials + models) is satisfied in tests.
        web_init(app, api_key="sk-ant-api03-FAKETESTKEYFORGATEWAYTESTS")

        fastapi_app = create_app(data_dir, no_scheduler=True, host="0.0.0.0")
        yield TestClient(fastapi_app)


# --- Health endpoint ---


def test_health_returns_ok(client: TestClient):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert "generation_id" in data


# --- Config endpoint ---


def test_config_returns_snapshot(client: TestClient):
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["schema_version"] == 2


# --- Sessions endpoint ---


def test_sessions_returns_list(client: TestClient):
    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_knowledge_endpoints(client: TestClient):
    kb = client.app.state.mycelos.knowledge_base
    first = kb.write("Gateway Note 1", "hello world", type="note")
    second = kb.write("Gateway Note 2", f"Links [[{first}]]", type="note")
    kb.sync_relations()

    list_resp = client.get("/api/knowledge/notes")
    assert list_resp.status_code == 200
    assert any(n["path"] == first for n in list_resp.json())

    read_resp = client.get(f"/api/knowledge/notes/{first}")
    assert read_resp.status_code == 200
    assert read_resp.json()["title"] == "Gateway Note 1"

    graph_resp = client.get("/api/knowledge/graph")
    assert graph_resp.status_code == 200
    graph = graph_resp.json()
    assert graph["stats"]["notes"] >= 2
    assert any(e["source"] == second and e["target"] == first for e in graph["edges"])

    sync_resp = client.post("/api/knowledge/sync-relations")
    assert sync_resp.status_code == 200
    assert sync_resp.json()["notes"] >= 2


def test_knowledge_notes_supports_query_search(client: TestClient):
    kb = client.app.state.mycelos.knowledge_base
    kb.write("Python Playbook", "advanced python notes", type="reference")
    kb.write("Cooking Notes", "pasta tips", type="note")
    resp = client.get("/api/knowledge/notes", params={"query": "python"})
    assert resp.status_code == 200
    titles = [n["title"] for n in resp.json()]
    assert "Python Playbook" in titles
    assert "Cooking Notes" not in titles


def test_knowledge_notes_supports_type_and_status_filters(client: TestClient):
    kb = client.app.state.mycelos.knowledge_base
    kb.write("Task Open", "x", type="task", status="open")
    kb.write("Task Done", "x", type="task", status="done")
    kb.write("Regular Note", "x", type="note", status="active")

    filtered = client.get("/api/knowledge/notes", params={"type": "task", "status": "open"})
    assert filtered.status_code == 200
    rows = filtered.json()
    assert len(rows) == 1
    assert rows[0]["title"] == "Task Open"


def test_knowledge_note_missing_path_returns_not_found_payload(client: TestClient):
    resp = client.get("/api/knowledge/notes/does/not/exist")
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "not_found"


def test_knowledge_sync_relations_updates_graph_edges(client: TestClient):
    kb = client.app.state.mycelos.knowledge_base
    first = kb.write("First", "f", type="note")
    second = kb.write("Second", "No links yet", type="note")
    kb.update(second, content=f"Now links [[{first}]]")

    sync_resp = client.post("/api/knowledge/sync-relations")
    assert sync_resp.status_code == 200
    assert sync_resp.json()["links"] >= 1

    graph = client.get("/api/knowledge/graph").json()
    assert any(edge["source"] == second and edge["target"] == first for edge in graph["edges"])


# --- i18n endpoint ---


def test_i18n_returns_translations(client: TestClient):
    """GET /api/i18n returns language and web translations."""
    resp = client.get("/api/i18n")
    assert resp.status_code == 200
    data = resp.json()
    assert "lang" in data
    assert "translations" in data
    assert isinstance(data["translations"], dict)
    assert "sidebar" in data["translations"]
    assert "dashboard" in data["translations"]["sidebar"]


# --- Transcribe endpoint ---


def test_transcribe_returns_text(client: TestClient):
    """POST /api/transcribe returns transcribed text when proxy is available."""
    mycelos = client.app.state.mycelos
    mock_proxy = MagicMock()
    mock_proxy.stt_transcribe.return_value = {"text": "Hello world"}
    mycelos.set_proxy_client(mock_proxy)

    resp = client.post(
        "/api/transcribe",
        files={"audio": ("test.webm", b"fake-audio-data", "audio/webm")},
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "Hello world"

    mycelos.set_proxy_client(None)


def test_transcribe_no_proxy_returns_503(client: TestClient):
    """POST /api/transcribe returns 503 when proxy is not available."""
    mycelos = client.app.state.mycelos
    mycelos.set_proxy_client(None)

    resp = client.post(
        "/api/transcribe",
        files={"audio": ("test.webm", b"fake-audio-data", "audio/webm")},
    )
    assert resp.status_code == 503
    assert "not available" in resp.json()["error"]


def test_transcribe_empty_result(client: TestClient):
    """POST /api/transcribe returns empty text when audio is not understood."""
    mycelos = client.app.state.mycelos
    mock_proxy = MagicMock()
    mock_proxy.stt_transcribe.return_value = {"text": ""}
    mycelos.set_proxy_client(mock_proxy)

    resp = client.post(
        "/api/transcribe",
        files={"audio": ("test.webm", b"fake-audio-data", "audio/webm")},
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == ""

    mycelos.set_proxy_client(None)


# --- Chat endpoint ---


def test_chat_returns_sse_stream(client: TestClient):
    """POST /api/chat should return SSE stream with events."""
    # Mock the LLM so we don't need a real API key
    mycelos = client.app.state.mycelos
    mock_response = MagicMock()
    mock_response.content = "Hello from gateway!"
    mock_response.total_tokens = 25
    mock_response.model = "test-model"
    mock_response.tool_calls = None

    with patch.object(mycelos.llm, "complete", return_value=mock_response):
        resp = client.post("/api/chat", json={
            "message": "Hello",
            "user_id": "test-user",
        })

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    # Parse SSE events
    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]

    assert "session" in types  # Session created
    assert "text" in types or "agent" in types  # Response


def test_chat_with_session_id(client: TestClient):
    """Providing session_id should reuse the session."""
    mycelos = client.app.state.mycelos
    mock_response = MagicMock()
    mock_response.content = "Response"
    mock_response.total_tokens = 10
    mock_response.model = "test"
    mock_response.tool_calls = None

    with patch.object(mycelos.llm, "complete", return_value=mock_response):
        # First message — creates session
        resp1 = client.post("/api/chat", json={"message": "Hi"})
        events1 = _parse_sse(resp1.text)
        session_id = next(e["data"]["session_id"] for e in events1 if e["type"] == "session")

        # Second message — same session
        resp2 = client.post("/api/chat", json={
            "message": "Follow up",
            "session_id": session_id,
        })
        events2 = _parse_sse(resp2.text)
        session_id2 = next(e["data"]["session_id"] for e in events2 if e["type"] == "session")

    assert session_id == session_id2


def test_chat_error_returns_error_event(client: TestClient):
    """LLM error should produce an error event, not HTTP 500."""
    mycelos = client.app.state.mycelos

    with patch.object(mycelos.llm, "complete", side_effect=Exception("API down")):
        resp = client.post("/api/chat", json={"message": "Hello"})

    assert resp.status_code == 200  # SSE stream, not HTTP error
    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert "error" in types


def test_chat_done_event_has_metadata(client: TestClient):
    """Done event should include tokens and model."""
    mycelos = client.app.state.mycelos
    mock_response = MagicMock()
    mock_response.content = "Test"
    mock_response.total_tokens = 42
    mock_response.model = "claude-test"
    mock_response.tool_calls = None

    with patch.object(mycelos.llm, "complete", return_value=mock_response):
        resp = client.post("/api/chat", json={"message": "Hi"})

    events = _parse_sse(resp.text)
    done = next((e for e in events if e["type"] == "done"), None)
    assert done is not None
    assert done["data"]["tokens"] >= 42  # May include tool overhead
    assert done["data"]["model"] == "claude-test"


# --- User ID resolution ---


def test_health_includes_user_info(client: TestClient):
    """GET /api/health should include user object."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "user" in data
    assert data["user"]["id"] == "default"
    assert "name" in data["user"]


def test_connector_add_uses_resolved_user(client: TestClient):
    """POST /api/connectors should use resolved user_id in audit log."""
    resp = client.post("/api/connectors", json={
        "name": "test-user-connector",
        "command": "npx -y @test/mcp",
    })
    assert resp.status_code == 200

    # Verify audit event was logged
    mycelos = client.app.state.mycelos
    events = mycelos.storage.fetchall(
        "SELECT * FROM audit_events WHERE event_type = 'connector.added' ORDER BY created_at DESC LIMIT 1"
    )
    assert len(events) >= 1
    assert events[0]["user_id"] == "default"  # Default user from DB


# --- Connector/Credential 422 fix ---


def test_connector_add_returns_200(client: TestClient):
    """POST /api/connectors should accept valid connector (not 422)."""
    resp = client.post("/api/connectors", json={
        "name": "test-fix-422",
        "command": "npx -y @test/mcp-server",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "registered"
    assert data["connector"] == "test-fix-422"


def test_connector_add_duplicate_returns_409(client: TestClient):
    """POST /api/connectors with duplicate name returns 409."""
    client.post("/api/connectors", json={"name": "dup-test", "command": "npx -y @test/mcp"})
    resp = client.post("/api/connectors", json={"name": "dup-test", "command": "npx -y @test/mcp"})
    assert resp.status_code == 409


def test_credential_add_returns_200(client: TestClient):
    """POST /api/credentials should accept valid credential (not 422)."""
    resp = client.post("/api/credentials", json={
        "service": "test-service",
        "secret": "test-secret-value",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "stored"


# --- Audit endpoint ---


def test_audit_endpoint_returns_events(client: TestClient):
    """GET /api/audit returns recent audit events."""
    # gateway.started is logged automatically during create_app
    resp = client.get("/api/audit")
    assert resp.status_code == 200
    events = resp.json()
    assert isinstance(events, list)
    assert len(events) >= 1
    event = events[0]
    assert "event_type" in event
    assert "created_at" in event
    assert "details" in event


def test_audit_endpoint_respects_limit(client: TestClient):
    """GET /api/audit?limit=1 returns at most 1 event."""
    resp = client.get("/api/audit", params={"limit": 1})
    assert resp.status_code == 200
    assert len(resp.json()) <= 1


def test_audit_endpoint_filters_by_event_type(client: TestClient):
    """GET /api/audit?event_type=gateway filters by prefix."""
    resp = client.get("/api/audit", params={"event_type": "gateway"})
    assert resp.status_code == 200
    events = resp.json()
    for event in events:
        assert event["event_type"].startswith("gateway")


def test_audit_details_parsed_as_dict(client: TestClient):
    """The details field should be a dict, not a JSON string."""
    resp = client.get("/api/audit")
    events = resp.json()
    if events:
        assert isinstance(events[0]["details"], (dict, type(None)))


# --- Root / index redirect ---


class TestRootRedirect:
    def test_root_serves_chat_redirect(self, client: TestClient):
        """GET / must lead the user to chat — either via HTTP redirect or
        via an index.html that contains a meta-refresh pointing at chat."""
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (200, 302, 307)
        if resp.status_code in (302, 307):
            assert "/pages/chat.html" in resp.headers.get("location", "")
        else:
            # Meta-refresh index.html
            assert "/pages/chat.html" in resp.text


# --- Workflow run sidebar endpoints ---


class TestWorkflowRunsActiveEndpoint:
    def test_active_returns_empty_list_initially(self, client: TestClient):
        resp = client.get("/api/workflow-runs", params={"status": "active"})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_active_filters_to_running_paused_waiting(self, client: TestClient):
        mycelos = client.app.state.mycelos
        mycelos.workflow_registry.register(
            workflow_id="wf-active-test",
            name="Active Test",
            steps=[],
            plan="x",
            model="anthropic/claude-haiku-4-5",
            allowed_tools=[],
        )
        for run_id, status in [
            ("run-running", "running"),
            ("run-waiting", "waiting_input"),
            ("run-paused", "paused"),
            ("run-done", "completed"),
            ("run-failed", "failed"),
        ]:
            mycelos.storage.execute(
                "INSERT INTO workflow_runs (id, workflow_id, user_id, status, conversation) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, "wf-active-test", "default", status, "[]"),
            )
        resp = client.get("/api/workflow-runs", params={"status": "active"})
        assert resp.status_code == 200
        ids = {r["id"] for r in resp.json()}
        assert ids == {"run-running", "run-waiting", "run-paused"}

    def test_active_includes_workflow_name(self, client: TestClient):
        mycelos = client.app.state.mycelos
        mycelos.workflow_registry.register(
            workflow_id="wf-name-test",
            name="Name Test",
            steps=[],
            plan="x",
            model="anthropic/claude-haiku-4-5",
            allowed_tools=[],
        )
        mycelos.storage.execute(
            "INSERT INTO workflow_runs (id, workflow_id, user_id, status, conversation) "
            "VALUES (?, ?, ?, ?, ?)",
            ("run-named", "wf-name-test", "default", "waiting_input", "[]"),
        )
        resp = client.get("/api/workflow-runs", params={"status": "active"})
        assert resp.status_code == 200
        rows = resp.json()
        row = next(r for r in rows if r["id"] == "run-named")
        assert row["workflow_name"] == "Name Test"


class TestWorkflowRunsScheduledEndpoint:
    def test_scheduled_empty_by_default(self, client: TestClient):
        resp = client.get("/api/workflow-runs/scheduled")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_scheduled_returns_active_cron_rows(self, client: TestClient):
        mycelos = client.app.state.mycelos
        mycelos.workflow_registry.register(
            workflow_id="wf-cron-1",
            name="Morning Briefing",
            steps=[],
            plan="x",
            model="anthropic/claude-haiku-4-5",
            allowed_tools=[],
        )
        mycelos.storage.execute(
            "INSERT INTO scheduled_tasks (id, workflow_id, schedule, next_run, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("cron-1", "wf-cron-1", "0 8 * * *", "2026-04-09T08:00:00Z", "active"),
        )
        resp = client.get("/api/workflow-runs/scheduled")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["workflow_name"] == "Morning Briefing"
        assert rows[0]["schedule"] == "0 8 * * *"
        assert rows[0]["next_run"] == "2026-04-09T08:00:00Z"


class TestChatExplicitWorkflowResume:
    def test_chat_with_workflow_run_id_resumes_that_run(self, client: TestClient):
        """POSTing /api/chat with workflow_run_id must resume that specific run,
        not whichever is first in get_pending_runs()."""
        from unittest.mock import patch
        mycelos = client.app.state.mycelos
        mycelos.workflow_registry.register(
            workflow_id="wf-explicit",
            name="Explicit",
            steps=[],
            plan="Say hello.",
            model="anthropic/claude-haiku-4-5",
            allowed_tools=[],
        )
        mycelos.storage.execute(
            "INSERT INTO workflow_runs (id, workflow_id, user_id, status, conversation) "
            "VALUES (?, ?, ?, ?, ?)",
            ("run-target", "wf-explicit", "default", "waiting_input",
             '[{"role":"assistant","content":"What topic?"}]'),
        )
        mycelos.storage.execute(
            "INSERT INTO workflow_runs (id, workflow_id, user_id, status, conversation) "
            "VALUES (?, ?, ?, ?, ?)",
            ("run-decoy", "wf-explicit", "default", "waiting_input", "[]"),
        )

        from mycelos.chat.service import ChatService
        called_with: dict = {}

        def spy(self, run, user_answer, session_id):
            called_with["run_id"] = run["id"]
            from mycelos.chat.events import agent_event, done_event
            return [agent_event("Mycelos"), done_event()]

        with patch.object(ChatService, "_resume_workflow", spy):
            resp = client.post("/api/chat", json={
                "message": "My answer",
                "workflow_run_id": "run-target",
            })

        assert resp.status_code == 200
        assert called_with.get("run_id") == "run-target", \
            f"Expected explicit run to be resumed, got {called_with}"


# --- Models endpoint ---


def test_models_includes_agent_name_and_agents_list(client: TestClient):
    """/api/models must include an `agents` list and agent_name on assignments.

    The UI uses the agents list to render explicit rows for registered agents
    even when they have no custom model assignment yet (inherit system default).
    """
    resp = client.get("/api/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "assignments" in data
    assert "agents" in data
    agent_ids = {a["id"] for a in data["agents"]}
    # After web_init the Mycelos agent must be registered and named.
    assert "mycelos" in agent_ids
    mycelos = next(a for a in data["agents"] if a["id"] == "mycelos")
    assert mycelos["name"] == "Mycelos"
    # Agent-scoped assignment rows carry agent_name; system-default rows
    # (agent_id=NULL) may have agent_name=None.
    for a in data["assignments"]:
        if a["agent_id"] is not None:
            assert a["agent_name"], f"agent_name must be set for {a['agent_id']}"


def test_web_init_assigns_mycelos_agent(client: TestClient):
    """After web_init the mycelos agent must have an execution assignment.

    Previously the mycelos chat agent inherited system defaults silently,
    which made it invisible in the Settings UI. It now has its own row.
    """
    data = client.get("/api/models").json()
    mycelos_assigns = [
        a for a in data["assignments"]
        if a["agent_id"] == "mycelos" and a["purpose"] == "execution"
    ]
    assert mycelos_assigns, "mycelos agent should have its own execution assignment after web_init"


def test_update_agent_assignments_replaces_priority_order(client: TestClient):
    """PUT /api/models/assignments/:agent_id replaces the per-agent model chain."""
    initial = client.get("/api/models").json()
    # Pick any two real registered models to reorder
    all_model_ids = [m["id"] for m in initial["models"]]
    assert len(all_model_ids) >= 1, "test DB has no models after web_init"
    # Use the mycelos agent (always present after web_init)
    new_chain = all_model_ids[:2] if len(all_model_ids) >= 2 else all_model_ids
    resp = client.put(
        "/api/models/assignments/mycelos",
        json={"purpose": "execution", "model_ids": new_chain},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["model_ids"] == new_chain
    # Verify the server state reflects the new order
    after = client.get("/api/models").json()
    mycelos_assignments = [a for a in after["assignments"] if a["agent_id"] == "mycelos" and a["purpose"] == "execution"]
    mycelos_assignments.sort(key=lambda a: a["priority"])
    assert [a["model_id"] for a in mycelos_assignments] == new_chain


def test_update_agent_assignments_rejects_unknown_model(client: TestClient):
    """Validation: unknown model IDs must be rejected (fail-closed)."""
    resp = client.put(
        "/api/models/assignments/mycelos",
        json={"purpose": "execution", "model_ids": ["provider/does-not-exist"]},
    )
    assert resp.status_code == 400
    assert "not registered" in resp.json()["error"]


def test_update_agent_assignments_rejects_unknown_agent(client: TestClient):
    resp = client.put(
        "/api/models/assignments/nosuch-agent",
        json={"purpose": "execution", "model_ids": []},
    )
    assert resp.status_code == 404


def test_update_system_defaults_replaces_chain(client: TestClient):
    """PUT /api/models/system-defaults replaces a purpose's default chain
    without touching the other purpose's chain."""
    initial = client.get("/api/models").json()
    model_ids = [m["id"] for m in initial["models"]][:2]
    # Capture classification chain before we change execution.
    initial_class = [
        a for a in initial["assignments"]
        if a["agent_id"] is None and a["purpose"] == "classification"
    ]
    initial_class.sort(key=lambda a: a["priority"])
    class_before = [a["model_id"] for a in initial_class]

    resp = client.put(
        "/api/models/system-defaults",
        json={"purpose": "execution", "model_ids": model_ids},
    )
    assert resp.status_code == 200, resp.text

    after = client.get("/api/models").json()
    exec_after = sorted(
        [a for a in after["assignments"] if a["agent_id"] is None and a["purpose"] == "execution"],
        key=lambda a: a["priority"],
    )
    assert [a["model_id"] for a in exec_after] == model_ids
    # Classification chain must be preserved.
    class_after = sorted(
        [a for a in after["assignments"] if a["agent_id"] is None and a["purpose"] == "classification"],
        key=lambda a: a["priority"],
    )
    assert [a["model_id"] for a in class_after] == class_before


def test_update_system_defaults_rejects_invalid_purpose(client: TestClient):
    resp = client.put(
        "/api/models/system-defaults",
        json={"purpose": "nonsense", "model_ids": []},
    )
    assert resp.status_code == 400


# --- Helper ---


def _parse_sse(text: str) -> list[dict]:
    """Parse SSE text into list of {type, data} dicts."""
    events = []
    current_type = None
    for line in text.split("\n"):
        if line.startswith("event: "):
            current_type = line[7:].strip()
        elif line.startswith("data: ") and current_type:
            try:
                data = json.loads(line[6:])
            except json.JSONDecodeError:
                data = {"raw": line[6:]}
            events.append({"type": current_type, "data": data})
            current_type = None
    return events
