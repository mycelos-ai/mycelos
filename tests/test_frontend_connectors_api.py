"""/api/connectors/recipes returns {channels, mcp} grouped by recipe.kind."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-recipes-api-key"
        from mycelos.app import App
        from mycelos.gateway.server import create_app
        App(Path(tmp)).initialize()
        fastapi_app = create_app(Path(tmp), no_scheduler=True, host="0.0.0.0")
        yield TestClient(fastapi_app)


def test_recipes_endpoint_returns_channels_and_mcp(client: TestClient) -> None:
    resp = client.get("/api/connectors/recipes")
    assert resp.status_code == 200
    data = resp.json()
    assert "channels" in data
    assert "mcp" in data
    assert isinstance(data["channels"], list)
    assert isinstance(data["mcp"], list)


def test_telegram_appears_under_channels(client: TestClient) -> None:
    resp = client.get("/api/connectors/recipes")
    assert resp.status_code == 200
    channel_ids = {r["id"] for r in resp.json()["channels"]}
    mcp_ids = {r["id"] for r in resp.json()["mcp"]}
    assert "telegram" in channel_ids
    assert "telegram" not in mcp_ids


def test_github_appears_under_mcp(client: TestClient) -> None:
    resp = client.get("/api/connectors/recipes")
    assert resp.status_code == 200
    mcp_ids = {r["id"] for r in resp.json()["mcp"]}
    channel_ids = {r["id"] for r in resp.json()["channels"]}
    assert "github" in mcp_ids
    assert "github" not in channel_ids


def test_each_recipe_has_kind_field(client: TestClient) -> None:
    resp = client.get("/api/connectors/recipes")
    for r in resp.json()["channels"]:
        assert r.get("kind") == "channel"
    for r in resp.json()["mcp"]:
        assert r.get("kind") == "mcp"


def test_single_recipe_endpoint_includes_kind(client: TestClient) -> None:
    """/api/connectors/recipes/{id} still returns a single recipe dict,
    now including the kind field."""
    resp = client.get("/api/connectors/recipes/telegram")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("id") == "telegram"
    assert data.get("kind") == "channel"

    resp2 = client.get("/api/connectors/recipes/github")
    assert resp2.status_code == 200
    assert resp2.json().get("kind") == "mcp"
