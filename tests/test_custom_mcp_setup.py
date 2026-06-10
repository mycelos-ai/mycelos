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
        fastapi_app = create_app(Path(tmp), no_scheduler=True, host="0.0.0.0", allow_insecure_bind=True)
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
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0", allow_insecure_bind=True)
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
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0", allow_insecure_bind=True)
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
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0", allow_insecure_bind=True)
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
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0", allow_insecure_bind=True)
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
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0", allow_insecure_bind=True)
    c = TestClient(fastapi_app)

    resp = c.post("/api/connectors", json={
        "name": "envless",
        "command": "npx -y some-envless-pkg",
    })
    assert resp.status_code == 200, resp.text

    app = App(tmp_data_dir)
    assert app.connector_registry.get("envless") is not None
    assert app.credentials.get_credential("envless") is None


def test_reconnect_custom_mcp_uses_registry_not_recipe(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: reconnect() of a custom (recipe-less) MCP must
    reconstruct command + env_vars from the registry row instead of
    blowing up with 'Unknown recipe'.

    Before the fix, MCPConnectorManager.reconnect() hardcoded
    connect_recipe(), so any /connectors/<id>/test on a custom MCP
    (e.g. one wrapping a remote SSE server via `npx mcp-remote`)
    surfaced 'reconnect failed: Unknown recipe: <id>'.
    """
    from mycelos.app import App
    from mycelos.gateway.server import create_app

    os.environ["MYCELOS_MASTER_KEY"] = "custom-mcp-reconnect-test"
    App(tmp_data_dir).initialize()
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0", allow_insecure_bind=True)
    c = TestClient(fastapi_app)

    resp = c.post("/api/connectors", json={
        "name": "yt-summary",
        "command": 'npx -y mcp-remote https://example.test/mcp/sse --header "Authorization:Bearer tok_x"',
    })
    assert resp.status_code == 200, resp.text

    app = App(tmp_data_dir)
    mgr = app.mcp_manager

    captured: dict[str, object] = {}

    def fake_connect(connector_id, command, env_vars=None, transport="stdio"):
        captured["connector_id"] = connector_id
        captured["command"] = command
        captured["env_vars"] = env_vars
        captured["transport"] = transport
        return []

    monkeypatch.setattr(mgr, "connect", fake_connect)

    mgr.reconnect("yt-summary")

    assert captured["connector_id"] == "yt-summary"
    assert isinstance(captured["command"], str)
    assert captured["command"].startswith("npx -y mcp-remote")
    assert captured["transport"] == "stdio"


def test_reconnect_unknown_connector_raises_clean_error(
    tmp_data_dir: Path,
) -> None:
    """If neither a recipe nor a registry row exists, reconnect should
    raise a descriptive error rather than the misleading 'Unknown recipe'."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "custom-mcp-unknown-test"
    App(tmp_data_dir).initialize()
    app = App(tmp_data_dir)

    with pytest.raises(ValueError, match="Unknown connector"):
        app.mcp_manager.reconnect("not-a-real-thing")


# ---------------------------------------------------------------------------
# PATCH /api/connectors/{id} — edit in place
# ---------------------------------------------------------------------------


def _make_patch_client(tmp_data_dir: Path, master_key: str) -> TestClient:
    from mycelos.app import App
    from mycelos.gateway.server import create_app

    os.environ["MYCELOS_MASTER_KEY"] = master_key
    App(tmp_data_dir).initialize()
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0", allow_insecure_bind=True)
    return TestClient(fastapi_app)


def test_patch_updates_command(tmp_data_dir: Path) -> None:
    """Editing the command rewrites the registry description and
    triggers an MCP restart attempt."""
    c = _make_patch_client(tmp_data_dir, "patch-cmd-test")

    create = c.post("/api/connectors", json={
        "name": "yt-summary",
        "command": "npx -y mcp-remote http://yt-summary:8000/mcp/sse",
    })
    assert create.status_code == 200, create.text

    edit = c.patch("/api/connectors/yt-summary", json={
        "command": (
            'npx -y mcp-remote http://yt-summary:8000/mcp/sse --allow-http '
            '--header "Authorization:Bearer yts_new"'
        ),
    })
    assert edit.status_code == 200, edit.text
    body = edit.json()
    assert body["status"] == "updated"
    assert "command" in body["changed"]
    assert body["restart_attempted"] is True

    listed = c.get("/api/connectors").json()
    row = next(r for r in listed if r["id"] == "yt-summary")
    assert row["description"].startswith("MCP: npx -y mcp-remote")
    assert "--allow-http" in row["description"]


def test_patch_updates_display_name_only(tmp_data_dir: Path) -> None:
    """Display-name edit is metadata-only — no MCP restart."""
    c = _make_patch_client(tmp_data_dir, "patch-name-test")

    c.post("/api/connectors", json={
        "name": "myconn",
        "command": "npx -y some-pkg",
    })

    edit = c.patch("/api/connectors/myconn", json={
        "name": "My Pretty Connector",
    })
    assert edit.status_code == 200, edit.text
    body = edit.json()
    assert "name" in body["changed"]
    assert body["restart_attempted"] is False

    row = next(r for r in c.get("/api/connectors").json() if r["id"] == "myconn")
    assert row["name"] == "My Pretty Connector"


def test_patch_updates_env_vars_triggers_restart(tmp_data_dir: Path) -> None:
    """Credential edit rewrites the stored blob and triggers a restart."""
    c = _make_patch_client(tmp_data_dir, "patch-env-test")

    c.post("/api/connectors", json={
        "name": "ctx7",
        "command": "npx -y @upstash/context7-mcp",
        "env_vars": {"API_KEY": "old"},
    })

    edit = c.patch("/api/connectors/ctx7", json={
        "env_vars": {"API_KEY": "rotated", "WORKSPACE": "ws-1"},
    })
    assert edit.status_code == 200, edit.text
    body = edit.json()
    assert "env_vars" in body["changed"]
    assert body["restart_attempted"] is True

    from mycelos.app import App
    app = App(tmp_data_dir)
    cred = app.credentials.get_credential("ctx7")
    assert cred is not None
    assert cred["env_var"] == "__multi__"
    blob = json.loads(cred["api_key"])
    assert blob == {"API_KEY": "rotated", "WORKSPACE": "ws-1"}


def test_patch_rejects_command_edit_on_recipe_backed(tmp_data_dir: Path) -> None:
    """Recipe-backed connectors get their command from the recipe;
    editing it via PATCH would silently diverge from the recipe."""
    c = _make_patch_client(tmp_data_dir, "patch-recipe-test")

    # `brave-search` is a real recipe — see mcp_recipes.py.
    c.post("/api/connectors", json={
        "name": "brave-search",
        "secret": "BSAabc123",
    })

    edit = c.patch("/api/connectors/brave-search", json={
        "command": "npx -y something-else",
    })
    assert edit.status_code == 400, edit.text
    assert "recipe-backed" in edit.json()["error"].lower()


def test_patch_unknown_connector_returns_404(tmp_data_dir: Path) -> None:
    c = _make_patch_client(tmp_data_dir, "patch-404-test")
    resp = c.patch("/api/connectors/nope", json={"name": "x"})
    assert resp.status_code == 404


def test_patch_empty_body_is_a_noop(tmp_data_dir: Path) -> None:
    """Nothing in the body → no changes, no restart, still 200."""
    c = _make_patch_client(tmp_data_dir, "patch-noop-test")
    c.post("/api/connectors", json={
        "name": "noop", "command": "npx -y some-pkg",
    })

    resp = c.patch("/api/connectors/noop", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["changed"] == []
    assert body["restart_attempted"] is False
