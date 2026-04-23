"""The gateway exposes /api/connectors/oauth/start + /stop + a WS
passthrough so browsers don't need to know the proxy exists.
Internally it forwards to the proxy_client's oauth_* helpers."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_mock_proxy():
    """Build a gateway TestClient where proxy_client is a MagicMock —
    so we can assert on the calls the gateway makes without booting a
    real proxy container."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-gw-oauth"
        from mycelos.app import App
        from mycelos.gateway.server import create_app
        app = App(data_dir)
        app.initialize()

        # Attach a mock proxy_client BEFORE building the FastAPI app,
        # so handlers reading app.state.mycelos see it.
        mock = MagicMock()
        mock.oauth_start.return_value = {"session_id": "oauth-testsid"}
        mock.oauth_stop.return_value = {"status": "stopped"}
        app._proxy_client = mock

        fastapi_app = create_app(data_dir, no_scheduler=True, host="0.0.0.0")
        # Override state in case create_app replaced the App instance.
        fastapi_app.state.mycelos._proxy_client = mock
        yield TestClient(fastapi_app), mock


def test_oauth_start_passthrough_sends_recipe_id(client_with_mock_proxy):
    """After materialization refactor the gateway sends just
    {recipe_id}: the proxy does the env/HOME setup itself."""
    client, mock = client_with_mock_proxy
    resp = client.post("/api/connectors/oauth/start", json={
        "recipe_id": "gmail",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"] == "oauth-testsid"
    assert body["ws_url"] == "/api/connectors/oauth/stream/oauth-testsid"

    # Proxy was called with recipe_id=gmail (no env_vars, no oauth_cmd).
    call = mock.oauth_start.call_args
    kwargs = call.kwargs or {}
    assert kwargs.get("recipe_id") == "gmail"


def test_oauth_start_unknown_recipe_404(client_with_mock_proxy):
    client, _ = client_with_mock_proxy
    resp = client.post("/api/connectors/oauth/start", json={
        "recipe_id": "no-such-recipe",
        "env_vars": {},
    })
    assert resp.status_code == 404


def test_oauth_start_rejects_non_oauth_recipe(client_with_mock_proxy):
    """Running oauth/start on a plain-secret recipe like brave-search
    is nonsensical and should be rejected up front."""
    client, _ = client_with_mock_proxy
    resp = client.post("/api/connectors/oauth/start", json={
        "recipe_id": "brave-search",
        "env_vars": {},
    })
    assert resp.status_code == 400
    detail = (resp.json().get("detail") or resp.json().get("error") or "").lower()
    assert "oauth_browser" in detail or "setup_flow" in detail


def test_oauth_stop_passthrough_calls_proxy(client_with_mock_proxy):
    client, mock = client_with_mock_proxy
    resp = client.post("/api/connectors/oauth/stop", json={
        "session_id": "oauth-abc",
    })
    assert resp.status_code == 200
    assert mock.oauth_stop.called
    # Accept kwarg or positional
    call = mock.oauth_stop.call_args
    if call.kwargs:
        assert call.kwargs.get("session_id") == "oauth-abc"
    else:
        assert "oauth-abc" in call.args


def test_oauth_start_503_when_proxy_missing():
    """If no proxy_client is attached, oauth/start must 503 rather
    than NPE — the browser should see a clear 'Proxy not available'."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-gw-oauth-2"
        from mycelos.app import App
        from mycelos.gateway.server import create_app
        app = App(data_dir)
        app.initialize()
        # Do NOT attach a proxy_client — that's the point of the test.
        fastapi_app = create_app(data_dir, no_scheduler=True, host="0.0.0.0")
        fastapi_app.state.mycelos._proxy_client = None
        with TestClient(fastapi_app) as client:
            resp = client.post("/api/connectors/oauth/start", json={
                "recipe_id": "gmail",
                "env_vars": {},
            })
            assert resp.status_code == 503
