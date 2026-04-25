"""GET /api/connectors/lookup-env-vars — registry-driven env-var prefill."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "lookup-env-test-key"
        from mycelos.app import App
        from mycelos.gateway.server import create_app
        App(Path(tmp)).initialize()
        fastapi_app = create_app(Path(tmp), no_scheduler=True, host="0.0.0.0")
        yield TestClient(fastapi_app)


def test_lookup_returns_envelope_on_hit(client: TestClient) -> None:
    fake_hit = [{"name": "API_KEY", "secret": True}, {"name": "WORKSPACE", "secret": False}]
    with patch("mycelos.connectors.mcp_search.lookup_env_vars", return_value=fake_hit):
        resp = client.get("/api/connectors/lookup-env-vars?package=@upstash/context7-mcp")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"env_vars": fake_hit}


def test_lookup_returns_empty_on_miss(client: TestClient) -> None:
    with patch("mycelos.connectors.mcp_search.lookup_env_vars", return_value=[]):
        resp = client.get("/api/connectors/lookup-env-vars?package=nonexistent-pkg")
    assert resp.status_code == 200
    assert resp.json() == {"env_vars": []}


def test_lookup_swallows_registry_error(client: TestClient) -> None:
    with patch("mycelos.connectors.mcp_search.lookup_env_vars",
               side_effect=Exception("network down")):
        resp = client.get("/api/connectors/lookup-env-vars?package=anything")
    assert resp.status_code == 200
    assert resp.json() == {"env_vars": []}


def test_lookup_requires_package_query_param(client: TestClient) -> None:
    resp = client.get("/api/connectors/lookup-env-vars")
    assert resp.status_code == 422
