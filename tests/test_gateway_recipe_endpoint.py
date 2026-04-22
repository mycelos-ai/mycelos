"""GET /api/connectors/recipes/{id} returns the recipe metadata plus
the resolved setup guide (if any). Frontend uses this to render the
right connector-setup dialog."""
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
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-recipes"
        from mycelos.app import App
        from mycelos.gateway.server import create_app
        app = App(data_dir)
        app.initialize()
        fastapi_app = create_app(data_dir, no_scheduler=True, host="0.0.0.0")
        yield TestClient(fastapi_app)


def test_recipe_endpoint_returns_gmail_with_guide(client: TestClient) -> None:
    resp = client.get("/api/connectors/recipes/gmail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "gmail"
    assert body["setup_flow"] == "oauth_browser"
    assert body["oauth_cmd"].startswith("npx -y @gongrzhe/")
    # Guide is inlined so the frontend needs only one roundtrip.
    assert body["setup_guide"] is not None
    assert body["setup_guide"]["id"] == "google_cloud"
    assert len(body["setup_guide"]["steps"]) >= 5


def test_recipe_endpoint_returns_secret_recipe_without_guide(client: TestClient) -> None:
    resp = client.get("/api/connectors/recipes/brave-search")
    assert resp.status_code == 200
    body = resp.json()
    assert body["setup_flow"] == "secret"
    assert body["setup_guide"] is None


def test_recipe_endpoint_unknown_id_returns_404(client: TestClient) -> None:
    resp = client.get("/api/connectors/recipes/this-does-not-exist")
    assert resp.status_code == 404
