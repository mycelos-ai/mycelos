"""Tests for gateway authentication UX — session login + Basic Auth fallback.

When a password is configured, browsers get a proper HTML login page and a
session cookie instead of the raw Basic-Auth popup (which demands a
meaningless username). Basic Auth keeps working for scripts/curl.
"""
from __future__ import annotations

import os
import tempfile
from base64 import b64encode
from pathlib import Path

import pytest
from starlette.testclient import TestClient

PASSWORD = "s3cret-pass"


def _make_client(password=None):
    from mycelos.gateway.server import create_app
    tmp = tempfile.mkdtemp()
    data_dir = Path(tmp)
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-auth"
    from mycelos.app import App
    from mycelos.setup import web_init
    a = App(data_dir)
    a.initialize()
    web_init(a, api_key="sk-ant-api03-FAKETESTKEYAUTH")
    fastapi_app = create_app(data_dir, no_scheduler=True, host="0.0.0.0",
                             password=password, allow_insecure_bind=not password)
    return TestClient(fastapi_app, follow_redirects=False)


@pytest.fixture
def client():
    return _make_client(password=PASSWORD)


@pytest.fixture
def open_client():
    return _make_client(password=None)


def _basic(pw):
    return {"Authorization": "Basic " + b64encode(f"mycelos:{pw}".encode()).decode()}


class TestNoPasswordConfigured:
    def test_api_open_without_auth(self, open_client):
        resp = open_client.get("/api/health")
        assert resp.status_code == 200
        resp = open_client.get("/api/ui/theme")
        assert resp.status_code == 200


class TestLoginFlow:
    def test_api_requires_auth(self, client):
        resp = client.get("/api/ui/theme")
        assert resp.status_code == 401

    def test_health_always_public(self, client):
        assert client.get("/api/health").status_code == 200

    def test_browser_redirected_to_login(self, client):
        """A browser navigation (Accept: text/html) gets the login page,
        not a Basic-Auth popup."""
        resp = client.get("/pages/chat.html",
                          headers={"Accept": "text/html,application/xhtml+xml"})
        assert resp.status_code in (302, 303, 307)
        assert "/login" in resp.headers["location"]

    def test_login_page_is_public(self, client):
        resp = client.get("/login", headers={"Accept": "text/html"})
        assert resp.status_code == 200
        assert "password" in resp.text.lower()

    def test_login_wrong_password(self, client):
        resp = client.post("/api/auth/login", json={"password": "wrong"})
        assert resp.status_code == 401
        assert "mycelos_session" not in resp.cookies

    def test_login_sets_session_cookie(self, client):
        resp = client.post("/api/auth/login", json={"password": PASSWORD})
        assert resp.status_code == 200
        assert "mycelos_session" in resp.cookies

    def test_session_cookie_grants_access(self, client):
        login = client.post("/api/auth/login", json={"password": PASSWORD})
        token = login.cookies["mycelos_session"]
        resp = client.get("/api/ui/theme", cookies={"mycelos_session": token})
        assert resp.status_code == 200

    def test_garbage_cookie_denied(self, client):
        resp = client.get("/api/ui/theme",
                          cookies={"mycelos_session": "forged-token"})
        assert resp.status_code == 401

    def test_basic_auth_still_works(self, client):
        """Scripts and curl keep using Basic Auth — no breaking change."""
        resp = client.get("/api/ui/theme", headers=_basic(PASSWORD))
        assert resp.status_code == 200
        resp = client.get("/api/ui/theme", headers=_basic("wrong"))
        assert resp.status_code == 401

    def test_logout_invalidates_session(self, client):
        login = client.post("/api/auth/login", json={"password": PASSWORD})
        token = login.cookies["mycelos_session"]
        client.post("/api/auth/logout", cookies={"mycelos_session": token})
        resp = client.get("/api/ui/theme", cookies={"mycelos_session": token})
        assert resp.status_code == 401

    def test_login_assets_reachable_without_auth(self, client):
        """The login page needs its CSS/JS — shared assets are public."""
        resp = client.get("/shared/base.css")
        assert resp.status_code == 200

    def test_next_param_rejects_absolute_urls(self, client):
        """Open-redirect guard: ?next= must only accept same-origin paths."""
        resp = client.post(
            "/api/auth/login",
            json={"password": PASSWORD, "next": "https://evil.example/phish"},
        )
        assert resp.status_code == 200
        assert resp.json().get("next", "/") in ("/", "/pages/chat.html")
