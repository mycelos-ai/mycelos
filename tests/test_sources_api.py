"""API tests for /api/sources/* endpoints."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_client():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-sources-api"

        from mycelos.app import App
        from mycelos.setup import web_init
        from mycelos.gateway.server import create_app

        app_obj = App(data_dir)
        app_obj.initialize()
        web_init(app_obj, api_key="sk-ant-api03-FAKETESTKEYFORSRC")

        fastapi_app = create_app(data_dir, no_scheduler=True, host="0.0.0.0", allow_insecure_bind=True)
        client = TestClient(fastapi_app)
        app_obj_from_state = fastapi_app.state.mycelos
        yield client, app_obj_from_state


def test_get_source_returns_attachments_and_rule(api_client) -> None:
    client, app_obj = api_client
    topic = app_obj.knowledge_base.create_topic("Vorfina")
    client.post("/api/sources/gmail/attachments", json={"topic_path": topic})
    client.put("/api/sources/gmail/rule", json={"rule_text": "Invoices to Vorfina."})
    resp = client.get("/api/sources/gmail")
    assert resp.status_code == 200
    data = resp.json()
    assert data["attachments"][0]["topic_path"] == topic
    assert data["rule_text"] == "Invoices to Vorfina."


def test_attach_rejects_unknown_topic(api_client) -> None:
    """Fail closed: a typo must not become a silent attachment."""
    client, _ = api_client
    resp = client.post("/api/sources/gmail/attachments",
                       json={"topic_path": "topics/does-not-exist"})
    assert resp.status_code == 422


def test_attach_accepts_root(api_client) -> None:
    client, _ = api_client
    resp = client.post("/api/sources/gmail/attachments", json={"topic_path": ""})
    assert resp.status_code == 200


def test_get_source_reports_subtree_size(api_client) -> None:
    """The UI shows 'covers N folders beneath' — the API supplies N."""
    client, app_obj = api_client
    kb = app_obj.knowledge_base
    parent = kb.create_topic("Vorfina")
    kb.create_topic("Mandanten", parent=parent)
    client.post("/api/sources/gmail/attachments", json={"topic_path": parent})
    data = client.get("/api/sources/gmail").json()
    assert data["attachments"][0]["covers"] >= 1


def test_detach(api_client) -> None:
    client, app_obj = api_client
    topic = app_obj.knowledge_base.create_topic("Vorfina")
    client.post("/api/sources/gmail/attachments", json={"topic_path": topic})
    resp = client.request("DELETE", "/api/sources/gmail/attachments",
                          json={"topic_path": topic})
    assert resp.status_code == 200
    assert client.get("/api/sources/gmail").json()["attachments"] == []
