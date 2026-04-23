"""POST /oauth/callback — proxy-internal endpoint the gateway calls
after receiving the browser's OAuth callback. Exchanges the code for
a token and stores it. No subprocess spawning."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def proxy_app(tmp_path: Path, monkeypatch):
    from mycelos.app import App
    from mycelos.security.proxy_server import create_proxy_app
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "phase-1b-test-key-" + "x" * 16)
    monkeypatch.setenv("MYCELOS_PROXY_TOKEN", "test-token")
    monkeypatch.setenv("MYCELOS_DB_PATH", str(tmp_path / "mycelos.db"))
    app = App(tmp_path)
    app.initialize()
    proxy = create_proxy_app()
    client = TestClient(proxy)
    client.headers.update({"Authorization": "Bearer test-token"})
    return client


def _seed_client_cred(proxy_app):
    proxy_app.post("/credential/store", json={
        "service": "gmail-oauth-client",
        "label": "default",
        "payload": {"api_key": json.dumps({
            "installed": {
                "client_id": "cid.apps.googleusercontent.com",
                "client_secret": "csec",
            }
        })},
        "description": "test",
    })


def test_oauth_callback_requires_auth(proxy_app):
    proxy_app.headers.pop("Authorization", None)
    resp = proxy_app.post("/oauth/callback", json={
        "recipe_id": "gmail",
        "code": "c",
        "code_verifier": "v",
        "redirect_uri": "http://localhost:9100/api/connectors/oauth/callback",
    })
    assert resp.status_code == 401


def test_oauth_callback_exchanges_and_persists(proxy_app):
    _seed_client_cred(proxy_app)

    fake_resp = type("R", (), {
        "status_code": 200,
        "json": lambda self: {
            "access_token": "ya29.ok",
            "refresh_token": "1//rtok",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
            "token_type": "Bearer",
        },
        "text": "",
    })()
    with patch("mycelos.security.oauth_token_manager.httpx.post", return_value=fake_resp):
        resp = proxy_app.post("/oauth/callback", json={
            "recipe_id": "gmail",
            "code": "auth-code",
            "code_verifier": "verifier",
            "redirect_uri": "http://localhost:9100/api/connectors/oauth/callback",
        })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "connected"
    assert "expires_at" in body

    lst = proxy_app.get("/credential/list").json()
    services = [c["service"] for c in lst.get("credentials", [])]
    assert "gmail-oauth-token" in services


def test_oauth_callback_unknown_recipe_404(proxy_app):
    resp = proxy_app.post("/oauth/callback", json={
        "recipe_id": "not-a-recipe",
        "code": "c",
        "code_verifier": "v",
        "redirect_uri": "http://x",
    })
    assert resp.status_code == 404


def test_oauth_callback_rejects_non_oauth_http_recipe(proxy_app):
    """Running /oauth/callback for brave-search (secret flow) makes
    no sense — should be 400."""
    resp = proxy_app.post("/oauth/callback", json={
        "recipe_id": "brave-search",
        "code": "c",
        "code_verifier": "v",
        "redirect_uri": "http://x",
    })
    assert resp.status_code == 400


def test_oauth_callback_surfaces_google_error(proxy_app):
    _seed_client_cred(proxy_app)
    fake_resp = type("R", (), {
        "status_code": 400,
        "text": '{"error": "invalid_grant"}',
    })()
    with patch("mycelos.security.oauth_token_manager.httpx.post", return_value=fake_resp):
        resp = proxy_app.post("/oauth/callback", json={
            "recipe_id": "gmail",
            "code": "bad",
            "code_verifier": "v",
            "redirect_uri": "http://x",
        })
    assert resp.status_code == 502
    assert "invalid_grant" in resp.json().get("error", "").lower()
