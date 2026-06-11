"""Tests for the UI theme API — user-customizable appearance.

The Neural Mycelium design system is built on CSS custom properties; a theme
is a named preset plus an optional accent color. The choice is persisted
server-side (system memory scope) so it follows the user across devices,
like EU mode does — not just one browser's localStorage.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client():
    from mycelos.gateway.server import create_app
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-theme"
        from mycelos.app import App
        from mycelos.setup import web_init
        a = App(data_dir)
        a.initialize()
        web_init(a, api_key="sk-ant-api03-FAKETESTKEYTHEME")
        fastapi_app = create_app(data_dir, no_scheduler=True,
                                 host="0.0.0.0", allow_insecure_bind=True)
        yield TestClient(fastapi_app)


def test_get_theme_defaults(client):
    resp = client.get("/api/ui/theme")
    assert resp.status_code == 200
    data = resp.json()
    assert data["preset"] == "mycelium-dark"
    assert data["accent"] is None


def test_post_theme_persists(client):
    resp = client.post("/api/ui/theme",
                       json={"preset": "mycelium-light", "accent": "#ff8800"})
    assert resp.status_code == 200, resp.text
    data = client.get("/api/ui/theme").json()
    assert data["preset"] == "mycelium-light"
    assert data["accent"] == "#ff8800"


def test_post_theme_accent_only(client):
    resp = client.post("/api/ui/theme", json={"accent": "#22cc88"})
    assert resp.status_code == 200
    data = client.get("/api/ui/theme").json()
    assert data["preset"] == "mycelium-dark"  # unchanged default
    assert data["accent"] == "#22cc88"


def test_post_theme_reset_accent(client):
    client.post("/api/ui/theme", json={"accent": "#22cc88"})
    resp = client.post("/api/ui/theme", json={"accent": None})
    assert resp.status_code == 200
    assert client.get("/api/ui/theme").json()["accent"] is None


def test_invalid_preset_rejected(client):
    resp = client.post("/api/ui/theme", json={"preset": "hotdog-stand"})
    assert resp.status_code == 422


def test_invalid_accent_rejected(client):
    for bad in ("red", "#12345", "#gggggg", "javascript:alert(1)"):
        resp = client.post("/api/ui/theme", json={"accent": bad})
        assert resp.status_code == 422, f"accepted invalid accent {bad!r}"


def test_theme_change_is_audited(client):
    client.post("/api/ui/theme", json={"preset": "graphite-dark"})
    mycelos = client.app.state.mycelos
    row = mycelos.storage.fetchone(
        "SELECT details FROM audit_events WHERE event_type='ui.theme.updated' "
        "ORDER BY id DESC LIMIT 1"
    )
    assert row is not None
