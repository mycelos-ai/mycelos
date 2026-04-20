"""Proxy /credential/{store,delete,list,rotate} endpoints.

Phase 1b: the proxy is the only process that writes credentials. Tests
boot an isolated proxy FastAPI app in-process with a temp DB and master
key, then exercise the four new endpoints through a TestClient.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def proxy_app(tmp_path: Path, monkeypatch):
    """Boot a proxy FastAPI app with a fresh storage + master key."""
    from mycelos.app import App
    from mycelos.security.proxy_server import create_proxy_app

    monkeypatch.setenv("MYCELOS_MASTER_KEY", "phase-1b-test-key-" + "x" * 16)
    monkeypatch.setenv("MYCELOS_PROXY_TOKEN", "test-token")
    monkeypatch.setenv("MYCELOS_DB_PATH", str(tmp_path / "mycelos.db"))

    # App.initialize creates the DB schema the proxy will write into.
    app = App(tmp_path)
    app.initialize()

    proxy = create_proxy_app()
    client = TestClient(proxy)
    client.headers.update({"Authorization": "Bearer test-token"})
    return client


def test_credential_store_persists(proxy_app):
    resp = proxy_app.post(
        "/credential/store",
        json={
            "service": "anthropic",
            "label": "default",
            "payload": {"api_key": "sk-ant-test", "provider": "anthropic"},
            "description": "unit test",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "stored"
    assert data["service"] == "anthropic"

    # Subsequent list returns metadata only (no plaintext key)
    lst = proxy_app.get("/credential/list")
    assert lst.status_code == 200
    items = lst.json()["credentials"]
    match = [i for i in items if i.get("service") == "anthropic"]
    assert len(match) >= 1
    for entry in items:
        # Metadata only — no plaintext key, no encrypted blob
        for forbidden in ("api_key", "encrypted", "nonce", "payload"):
            assert forbidden not in entry, f"{forbidden!r} leaked in list: {entry}"


def test_credential_delete_removes_row(proxy_app):
    proxy_app.post(
        "/credential/store",
        json={"service": "foo", "label": "default", "payload": {"api_key": "x"}},
    )
    resp = proxy_app.delete("/credential/foo/default")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    lst = proxy_app.get("/credential/list").json()["credentials"]
    assert not any(i.get("service") == "foo" for i in lst)


def test_credential_store_requires_auth(proxy_app):
    proxy_app.headers.pop("Authorization", None)
    resp = proxy_app.post(
        "/credential/store",
        json={"service": "x", "label": "default", "payload": {"api_key": "y"}},
    )
    assert resp.status_code == 401


def test_credential_rotate_marks_row(proxy_app):
    proxy_app.headers.update({"Authorization": "Bearer test-token"})
    proxy_app.post(
        "/credential/store",
        json={"service": "bar", "label": "default", "payload": {"api_key": "z"}},
    )
    resp = proxy_app.post("/credential/rotate", json={"service": "bar", "label": "default"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "rotated"
