"""CSRF middleware — blocks cross-origin browser POSTs to /api/*.

The threat model: user has Mycelos running on localhost, opens a
malicious site in the same browser, that site's JS fires a
fetch('http://localhost:9100/api/connectors/gmail/tools/.../call', ...)
to exfiltrate data through the user's own session. Our middleware
inspects Origin / Referer and rejects anything not from an allowed
origin — while letting CLI / curl (no Origin) pass through.
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
        os.environ["MYCELOS_MASTER_KEY"] = "csrf-test-key"
        from mycelos.app import App
        from mycelos.gateway.server import create_app
        app = App(data_dir)
        app.initialize()
        fastapi_app = create_app(data_dir, no_scheduler=True, host="0.0.0.0")
        yield TestClient(fastapi_app)


# A POST that would succeed on shape — /api/credentials accepts
# {service, secret} and returns 200. We use it as a target because
# an attacker POSTing here could plant a credential.
VALID_POST_BODY = {"service": "test-x", "secret": "dummy"}


def test_get_without_origin_passes(client: TestClient):
    """GETs are always safe — no CSRF check on read paths."""
    resp = client.get("/api/connectors")
    assert resp.status_code == 200


def test_post_without_origin_passes(client: TestClient):
    """curl / CLI / server-to-server scripts send no Origin — must
    pass through (otherwise the CLI would break)."""
    resp = client.post("/api/credentials", json=VALID_POST_BODY)
    assert resp.status_code == 200, resp.text


def test_post_same_origin_localhost_passes(client: TestClient):
    """Browser same-origin POST — Origin matches our bind host.
    Localhost:* always passes regardless of port (dev setup)."""
    resp = client.post(
        "/api/credentials",
        json=VALID_POST_BODY,
        headers={"Origin": "http://localhost:9100"},
    )
    assert resp.status_code == 200, resp.text


def test_post_same_origin_127_passes(client: TestClient):
    """Same as above but 127.0.0.1 — some browsers/proxies rewrite
    localhost → 127.0.0.1. Must also be allowed."""
    resp = client.post(
        "/api/credentials",
        json=VALID_POST_BODY,
        headers={"Origin": "http://127.0.0.1:9100"},
    )
    assert resp.status_code == 200, resp.text


def test_post_cross_origin_blocked(client: TestClient):
    """A malicious site's cross-origin POST is rejected with 403."""
    resp = client.post(
        "/api/credentials",
        json=VALID_POST_BODY,
        headers={"Origin": "https://evil.example.com"},
    )
    assert resp.status_code == 403
    assert "CSRF" in resp.json().get("error", "")


def test_post_cross_origin_blocked_via_referer(client: TestClient):
    """If Origin is absent but Referer points somewhere else, the
    middleware still blocks. Some browsers strip Origin on certain
    navigation flows but Referer survives."""
    resp = client.post(
        "/api/credentials",
        json=VALID_POST_BODY,
        headers={"Referer": "https://evil.example.com/page"},
    )
    assert resp.status_code == 403


def test_get_cross_origin_still_passes(client: TestClient):
    """GET is safe per HTTP spec — we don't block reads even from
    foreign origins. (The attacker can't do much with just reads;
    the dangerous vector is state mutation.)"""
    resp = client.get(
        "/api/connectors",
        headers={"Origin": "https://evil.example.com"},
    )
    assert resp.status_code == 200


def test_options_preflight_passes(client: TestClient):
    """CORS preflight (OPTIONS) must pass — blocking it would break
    every cross-origin browser request including intentionally
    whitelisted ones."""
    resp = client.options(
        "/api/credentials",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    # Middleware must not 403 this. Handler may return 200 or 405
    # depending on CORS middleware; either is fine for this test's
    # purpose — we're only asserting CSRF didn't block.
    assert resp.status_code != 403


def test_allowed_origins_env_opens_specific_origin(
    monkeypatch, tmp_path
):
    """MYCELOS_ALLOWED_ORIGINS lets the user whitelist a specific
    external origin (e.g. a Grafana dashboard) without opening
    everything."""
    monkeypatch.setenv("MYCELOS_ALLOWED_ORIGINS", "https://dashboard.example.com")
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "csrf-test-key-allowed")

    from mycelos.app import App
    from mycelos.gateway.server import create_app
    app = App(tmp_path)
    app.initialize()
    fastapi_app = create_app(tmp_path, no_scheduler=True, host="0.0.0.0")
    c = TestClient(fastapi_app)

    # Whitelisted origin — passes.
    resp = c.post(
        "/api/credentials",
        json=VALID_POST_BODY,
        headers={"Origin": "https://dashboard.example.com"},
    )
    assert resp.status_code == 200, resp.text

    # Different origin — still blocked.
    resp = c.post(
        "/api/credentials",
        json=VALID_POST_BODY,
        headers={"Origin": "https://evil.example.com"},
    )
    assert resp.status_code == 403


def test_non_api_path_not_gated(client: TestClient):
    """CSRF only gates /api/*. Static assets / frontend routes
    aren't POST endpoints that mutate state, but also shouldn't be
    blocked if they ever get cross-origin posts (e.g. future
    webhooks). Today /static/ is GET-only so this just documents
    intent."""
    # No realistic POST target outside /api/ today, but test the
    # guard clause explicitly by posting to a known non-api path.
    resp = client.post(
        "/some-non-api-path",
        json={},
        headers={"Origin": "https://evil.example.com"},
    )
    # Will 404 from the app (no such route), not 403 from CSRF.
    assert resp.status_code != 403
