"""POST /api/connectors with env_vars stores a multi-var credential blob."""

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
        os.environ["MYCELOS_MASTER_KEY"] = "custom-mcp-test-key"
        from mycelos.app import App
        from mycelos.gateway.server import create_app
        App(Path(tmp)).initialize()
        fastapi_app = create_app(Path(tmp), no_scheduler=True, host="0.0.0.0")
        yield TestClient(fastapi_app)


def test_post_with_env_vars_stores_multi_blob(client, tmp_path) -> None:
    resp = client.post("/api/connectors", json={
        "name": "context7",
        "command": "npx -y @upstash/context7-mcp",
        "env_vars": {"API_KEY": "ctx_abc", "WORKSPACE": "ws_42"},
    })
    assert resp.status_code == 200, resp.text
    listed = client.get("/api/connectors").json()
    assert any(c.get("id") == "context7" for c in listed), listed


def test_post_with_env_vars_writes_multi_sentinel(tmp_data_dir: Path) -> None:
    """Direct App-level test — verify on-disk credential shape."""
    from mycelos.app import App
    from mycelos.gateway.server import create_app

    os.environ["MYCELOS_MASTER_KEY"] = "custom-mcp-direct-test"
    App(tmp_data_dir).initialize()
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0")
    c = TestClient(fastapi_app)

    resp = c.post("/api/connectors", json={
        "name": "context7",
        "command": "npx -y @upstash/context7-mcp",
        "env_vars": {"API_KEY": "ctx_abc", "WORKSPACE": "ws_42"},
    })
    assert resp.status_code == 200, resp.text

    app = App(tmp_data_dir)
    cred = app.credentials.get_credential("context7")
    assert cred is not None
    assert cred["env_var"] == "__multi__"
    blob = json.loads(cred["api_key"])
    assert blob == {"API_KEY": "ctx_abc", "WORKSPACE": "ws_42"}


def test_post_with_legacy_secret_still_works(tmp_data_dir: Path) -> None:
    """Existing recipe-style POST {secret: '...'} path is preserved."""
    from mycelos.app import App
    from mycelos.gateway.server import create_app

    os.environ["MYCELOS_MASTER_KEY"] = "custom-mcp-legacy-test"
    App(tmp_data_dir).initialize()
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0")
    c = TestClient(fastapi_app)

    resp = c.post("/api/connectors", json={
        "name": "myconn",
        "command": "npx -y some-pkg",
        "secret": "abc123",
    })
    assert resp.status_code == 200, resp.text

    app = App(tmp_data_dir)
    cred = app.credentials.get_credential("myconn")
    assert cred is not None
    assert cred["env_var"] != "__multi__"
    assert cred["env_var"] == "MYCONN_API_KEY"
    assert cred["api_key"] == "abc123"


def test_post_env_vars_wins_over_secret(tmp_data_dir: Path) -> None:
    """When both env_vars and secret are sent, env_vars wins (the explicit, multi-var path)."""
    from mycelos.app import App
    from mycelos.gateway.server import create_app

    os.environ["MYCELOS_MASTER_KEY"] = "custom-mcp-precedence-test"
    App(tmp_data_dir).initialize()
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0")
    c = TestClient(fastapi_app)

    resp = c.post("/api/connectors", json={
        "name": "both",
        "command": "npx -y some-pkg",
        "secret": "ignored",
        "env_vars": {"REAL_KEY": "kept"},
    })
    assert resp.status_code == 200, resp.text

    app = App(tmp_data_dir)
    cred = app.credentials.get_credential("both")
    assert cred is not None
    assert cred["env_var"] == "__multi__"
    assert json.loads(cred["api_key"]) == {"REAL_KEY": "kept"}


def test_post_env_vars_filters_empty_keys(tmp_data_dir: Path) -> None:
    """Rows with empty key are dropped; values may be empty (intentional feature flag pattern)."""
    from mycelos.app import App
    from mycelos.gateway.server import create_app

    os.environ["MYCELOS_MASTER_KEY"] = "custom-mcp-filter-test"
    App(tmp_data_dir).initialize()
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0")
    c = TestClient(fastapi_app)

    resp = c.post("/api/connectors", json={
        "name": "filt",
        "command": "npx -y some-pkg",
        "env_vars": {"": "dropped", "  ": "also dropped", "REAL": "kept", "FLAG": ""},
    })
    assert resp.status_code == 200, resp.text

    app = App(tmp_data_dir)
    cred = app.credentials.get_credential("filt")
    assert cred is not None
    blob = json.loads(cred["api_key"])
    assert blob == {"REAL": "kept", "FLAG": ""}


def test_post_no_creds_at_all_still_registers(tmp_data_dir: Path) -> None:
    """Some MCPs need no credentials — connector should register, no credential row."""
    from mycelos.app import App
    from mycelos.gateway.server import create_app

    os.environ["MYCELOS_MASTER_KEY"] = "custom-mcp-nocred-test"
    App(tmp_data_dir).initialize()
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0")
    c = TestClient(fastapi_app)

    resp = c.post("/api/connectors", json={
        "name": "envless",
        "command": "npx -y some-envless-pkg",
    })
    assert resp.status_code == 200, resp.text

    app = App(tmp_data_dir)
    assert app.connector_registry.get("envless") is not None
    assert app.credentials.get_credential("envless") is None
