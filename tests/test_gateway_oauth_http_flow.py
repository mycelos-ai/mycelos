"""Gateway OAuth-HTTP flow endpoints:
- POST /api/connectors/oauth/start  — returns auth_url, stores state
- GET  /api/connectors/oauth/callback — validates state, forwards
  code to proxy, redirects browser to the connectors page.

Uses a MagicMock proxy_client so we don't need a live proxy container.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_mock_proxy():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-gw-oauth-http"
        from mycelos.app import App
        from mycelos.gateway.server import create_app
        app = App(data_dir)
        app.initialize()
        mock = MagicMock()
        mock.oauth_callback.return_value = {
            "status": "connected",
            "expires_at": "2026-04-23T10:30:00+00:00",
        }
        # Seed the client credential via the real credential store
        # so /oauth/start can look up client_id.
        app.credentials.store_credential(
            "gmail-oauth-client",
            {"api_key": json.dumps({
                "installed": {
                    "client_id": "cid.apps.googleusercontent.com",
                    "client_secret": "csec",
                }
            })},
            user_id="default",
        )
        app._proxy_client = mock
        fastapi_app = create_app(data_dir, no_scheduler=True, host="0.0.0.0")
        fastapi_app.state.mycelos._proxy_client = mock
        yield TestClient(fastapi_app), mock, fastapi_app


def test_oauth_start_builds_auth_url_and_stores_state(client_with_mock_proxy):
    client, _mock, fapp = client_with_mock_proxy
    resp = client.post("/api/connectors/oauth/start", json={
        "recipe_id": "gmail",
        "origin": "http://localhost:9100",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    auth_url = body["auth_url"]
    assert auth_url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=cid.apps.googleusercontent.com" in auth_url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A9100%2Fapi%2Fconnectors%2Foauth%2Fcallback" in auth_url
    assert "response_type=code" in auth_url
    assert "code_challenge_method=S256" in auth_url
    assert "access_type=offline" in auth_url
    # state got stored
    state_dict = fapp.state.oauth_pending_states
    assert len(state_dict) == 1
    stored_state = list(state_dict.keys())[0]
    assert f"state={stored_state}" in auth_url
    entry = state_dict[stored_state]
    assert entry["recipe_id"] == "gmail"
    assert entry["origin"] == "http://localhost:9100"
    assert "code_verifier" in entry
    assert "expires_at" in entry


def test_oauth_start_unknown_recipe_404(client_with_mock_proxy):
    client, _mock, _fapp = client_with_mock_proxy
    resp = client.post("/api/connectors/oauth/start", json={
        "recipe_id": "not-a-recipe",
        "origin": "http://localhost:9100",
    })
    assert resp.status_code == 404


def test_oauth_start_rejects_non_oauth_http_recipe(client_with_mock_proxy):
    """A secret-flow recipe like brave-search should not use /oauth/start."""
    client, _mock, _fapp = client_with_mock_proxy
    resp = client.post("/api/connectors/oauth/start", json={
        "recipe_id": "brave-search",
        "origin": "http://localhost:9100",
    })
    assert resp.status_code == 400


def test_oauth_start_requires_client_credential(client_with_mock_proxy):
    """If the oauth_client_credential_service row is missing, 400 with
    'upload client secret first'."""
    client, _mock, fapp = client_with_mock_proxy
    fapp.state.mycelos.credentials.delete_credential(
        "gmail-oauth-client", user_id="default",
    )
    resp = client.post("/api/connectors/oauth/start", json={
        "recipe_id": "gmail",
        "origin": "http://localhost:9100",
    })
    assert resp.status_code == 400
    assert "client" in resp.json().get("detail", "").lower()


def test_oauth_callback_success_redirects(client_with_mock_proxy):
    """Browser arrives at /api/connectors/oauth/callback with a valid
    code+state → gateway calls proxy, redirects to /connectors.html
    with ?connected=gmail."""
    client, mock, fapp = client_with_mock_proxy
    client.post("/api/connectors/oauth/start", json={
        "recipe_id": "gmail",
        "origin": "http://localhost:9100",
    })
    state = list(fapp.state.oauth_pending_states.keys())[0]

    resp = client.get(
        f"/api/connectors/oauth/callback?code=auth-code&state={state}",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    loc = resp.headers["location"]
    assert loc == "/connectors.html?connected=gmail"
    assert mock.oauth_callback.called
    call = mock.oauth_callback.call_args
    assert call.kwargs["recipe_id"] == "gmail"
    assert call.kwargs["code"] == "auth-code"
    assert call.kwargs["redirect_uri"] == "http://localhost:9100/api/connectors/oauth/callback"
    # state was popped (single-use)
    assert state not in fapp.state.oauth_pending_states


def test_oauth_callback_invalid_state_redirects_with_error(client_with_mock_proxy):
    client, _mock, _fapp = client_with_mock_proxy
    resp = client.get(
        "/api/connectors/oauth/callback?code=c&state=totally-fake",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "oauth_error=invalid_state" in resp.headers["location"]


def test_oauth_callback_google_error_redirects_with_error(client_with_mock_proxy):
    client, _mock, _fapp = client_with_mock_proxy
    resp = client.get(
        "/api/connectors/oauth/callback?error=access_denied&state=whatever",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "oauth_error=access_denied" in resp.headers["location"]


def test_oauth_callback_surfaces_proxy_error(client_with_mock_proxy):
    """If the proxy returns a non-connected status we propagate."""
    client, mock, fapp = client_with_mock_proxy
    mock.oauth_callback.return_value = {"error": "invalid_grant"}
    client.post("/api/connectors/oauth/start", json={
        "recipe_id": "gmail",
        "origin": "http://localhost:9100",
    })
    state = list(fapp.state.oauth_pending_states.keys())[0]
    resp = client.get(
        f"/api/connectors/oauth/callback?code=c&state={state}",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "oauth_error=" in resp.headers["location"]
