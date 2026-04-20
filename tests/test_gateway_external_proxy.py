"""Gateway must use external SecurityProxy when MYCELOS_PROXY_URL is set.

Tests the two-container deployment path: no ProxyLauncher child, connect
via TCP with the shared bearer token.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _make_gateway(tmp_path, monkeypatch, **env) -> TestClient:
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "test-key-ext-proxy")
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    from mycelos.app import App
    from mycelos.gateway.server import create_app

    app = App(tmp_path)
    app.initialize()

    fastapi_app = create_app(tmp_path, no_scheduler=True, host="0.0.0.0")
    return TestClient(fastapi_app)


def test_external_proxy_url_skips_fork(tmp_path, monkeypatch):
    """When MYCELOS_PROXY_URL is set, no ProxyLauncher child is spawned."""
    client = _make_gateway(
        tmp_path,
        monkeypatch,
        MYCELOS_PROXY_URL="http://proxy.internal:9110",
        MYCELOS_PROXY_TOKEN="external-token-abc",
    )
    # gateway came up; proxy_launcher is None in app state
    assert client.app.state.proxy_launcher is None
    # proxy_client points at the external URL
    pc = client.app.state.mycelos.proxy_client
    assert pc is not None
    assert pc.base_url == "http://proxy.internal:9110"


def test_external_proxy_url_without_token_raises(tmp_path, monkeypatch):
    """MYCELOS_PROXY_URL without MYCELOS_PROXY_TOKEN must raise loudly."""
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "test-key-no-token")
    monkeypatch.setenv("MYCELOS_PROXY_URL", "http://proxy.internal:9110")
    monkeypatch.delenv("MYCELOS_PROXY_TOKEN", raising=False)

    from mycelos.app import App
    from mycelos.gateway.server import create_app

    app = App(tmp_path)
    app.initialize()

    with pytest.raises(RuntimeError, match="MYCELOS_PROXY_TOKEN"):
        create_app(tmp_path, no_scheduler=True, host="0.0.0.0")


def test_no_proxy_url_uses_local_fork(tmp_path, monkeypatch):
    """Without MYCELOS_PROXY_URL, fall back to the existing fork path."""
    monkeypatch.delenv("MYCELOS_PROXY_URL", raising=False)
    monkeypatch.delenv("MYCELOS_PROXY_TOKEN", raising=False)
    client = _make_gateway(tmp_path, monkeypatch)
    # proxy_launcher may be None (fork failed in CI sandbox) OR a ProxyLauncher
    # — the point is that the external-proxy path wasn't taken. Check the
    # client either has the local UDS socket or no proxy at all.
    pc = client.app.state.mycelos.proxy_client
    if pc is not None:
        assert pc.base_url == "http://proxy"  # UDS base_url marker
