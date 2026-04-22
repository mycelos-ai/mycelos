"""POST /api/credentials/oauth-keys/validate runs a cheap shape check on
the uploaded OAuth keys JSON so we can tell the user 'this doesn't look
like a gcp-oauth.keys.json' at upload time rather than at auth time."""
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
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-oauth-keys"
        from mycelos.app import App
        from mycelos.gateway.server import create_app
        app = App(data_dir)
        app.initialize()
        fastapi_app = create_app(data_dir, no_scheduler=True, host="0.0.0.0")
        yield TestClient(fastapi_app)


def test_validates_installed_desktop_app_shape(client: TestClient) -> None:
    keys = {
        "installed": {
            "client_id": "123.apps.googleusercontent.com",
            "client_secret": "GOCSPX-xxxx",
            "redirect_uris": ["http://localhost"],
        }
    }
    resp = client.post("/api/credentials/oauth-keys/validate", json={
        "content": json.dumps(keys),
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["kind"] == "desktop"


def test_rejects_web_app_shape(client: TestClient) -> None:
    """Web-app OAuth credentials have a 'web' key not 'installed' —
    our MCP servers don't support web-flow, only desktop. Flag early."""
    keys = {"web": {"client_id": "x", "client_secret": "y"}}
    resp = client.post("/api/credentials/oauth-keys/validate", json={
        "content": json.dumps(keys),
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "desktop" in body["error"].lower()


def test_rejects_malformed_json(client: TestClient) -> None:
    resp = client.post("/api/credentials/oauth-keys/validate", json={
        "content": "this is not json",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "json" in body["error"].lower()


def test_rejects_empty_content(client: TestClient) -> None:
    resp = client.post("/api/credentials/oauth-keys/validate", json={
        "content": "",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False


def test_rejects_installed_missing_secret(client: TestClient) -> None:
    """Desktop shape but incomplete — client_id present, client_secret missing."""
    keys = {"installed": {"client_id": "123.apps.googleusercontent.com"}}
    resp = client.post("/api/credentials/oauth-keys/validate", json={
        "content": json.dumps(keys),
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "client_secret" in body["error"].lower() or "client_id" in body["error"].lower()


def test_rejects_service_account_shape(client: TestClient) -> None:
    """Service-account JSON has 'type': 'service_account'; neither
    'installed' nor 'web'. Should be rejected with a helpful error."""
    sa = {
        "type": "service_account",
        "project_id": "x",
        "private_key": "-----BEGIN PRIVATE KEY-----",
    }
    resp = client.post("/api/credentials/oauth-keys/validate", json={
        "content": json.dumps(sa),
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
