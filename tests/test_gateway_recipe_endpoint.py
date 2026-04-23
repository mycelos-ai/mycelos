"""GET /api/connectors/recipes/{recipe_id} — smoke test.

Keeps a minimal test so the endpoint can't silently regress after
recipe-shape changes (e.g. the removal of oauth_cmd in the OAuth-HTTP
refactor where the endpoint forgot to drop it from its response dict).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-recipe-endpoint"
        from mycelos.app import App
        from mycelos.gateway.server import create_app
        app = App(data_dir)
        app.initialize()
        fastapi_app = create_app(data_dir, no_scheduler=True, host="0.0.0.0")
        yield TestClient(fastapi_app)


def test_recipe_endpoint_returns_gmail_with_guide(client):
    resp = client.get("/api/connectors/recipes/gmail")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "gmail"
    assert body["setup_flow"] == "oauth_http"
    assert body["http_endpoint"] == "https://gmailmcp.googleapis.com/mcp/v1"
    assert body["oauth_client_credential_service"] == "gmail-oauth-client"
    assert body["oauth_token_credential_service"] == "gmail-oauth-token"
    # Guide inlined.
    assert body["setup_guide"] is not None
    assert body["setup_guide"]["id"] == "google_cloud"


def test_recipe_endpoint_returns_secret_recipe_without_guide(client):
    resp = client.get("/api/connectors/recipes/brave-search")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["setup_flow"] == "secret"
    assert body["setup_guide"] is None
    # oauth_* fields are empty for secret recipes.
    assert body["oauth_client_credential_service"] == ""
    assert body["oauth_token_credential_service"] == ""
    assert body["http_endpoint"] == ""


def test_recipe_endpoint_unknown_id_returns_404(client):
    resp = client.get("/api/connectors/recipes/does-not-exist")
    assert resp.status_code == 404


def test_recipe_endpoint_does_not_return_removed_oauth_cmd(client):
    """Regression guard: the oauth_cmd field was removed from MCPRecipe
    when we migrated Gmail to OAuth-HTTP. The endpoint must not try to
    read it — this test catches the AttributeError that would otherwise
    surface as a 500 for every recipe request."""
    resp = client.get("/api/connectors/recipes/gmail")
    assert resp.status_code == 200
    body = resp.json()
    assert "oauth_cmd" not in body
