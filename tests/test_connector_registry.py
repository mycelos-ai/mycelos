"""Tests for ConnectorRegistry — CRUD for connectors + capabilities."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mycelos.connectors.connector_registry import ConnectorRegistry
from mycelos.storage.database import SQLiteStorage


@pytest.fixture
def storage() -> SQLiteStorage:
    with tempfile.TemporaryDirectory() as tmp:
        s = SQLiteStorage(Path(tmp) / "test.db")
        s.initialize()
        yield s


@pytest.fixture
def registry(storage: SQLiteStorage) -> ConnectorRegistry:
    return ConnectorRegistry(storage)


def test_register_connector(registry: ConnectorRegistry):
    registry.register(
        connector_id="ddg",
        name="DuckDuckGo",
        connector_type="search",
        capabilities=["search.web", "search.news"],
    )
    c = registry.get("ddg")
    assert c is not None
    assert c["name"] == "DuckDuckGo"
    assert c["connector_type"] == "search"
    assert c["status"] == "active"
    assert set(c["capabilities"]) == {"search.web", "search.news"}


def test_register_with_optional_fields(registry: ConnectorRegistry):
    registry.register(
        connector_id="brave",
        name="Brave Search",
        connector_type="search",
        capabilities=["search.web.brave"],
        description="Premium search API",
        setup_type="key",
    )
    c = registry.get("brave")
    assert c["description"] == "Premium search API"
    assert c["setup_type"] == "key"


def test_get_nonexistent(registry: ConnectorRegistry):
    assert registry.get("nope") is None


def test_list_connectors_all(registry: ConnectorRegistry):
    registry.register("ddg", "DDG", "search", ["search.web"])
    registry.register("http", "HTTP", "http", ["http.get", "http.post"])
    result = registry.list_connectors()
    assert len(result) == 2
    names = {c["name"] for c in result}
    assert names == {"DDG", "HTTP"}


def test_list_connectors_by_status(registry: ConnectorRegistry):
    registry.register("ddg", "DDG", "search", ["search.web"])
    registry.register("old", "Old", "search", ["search.old"])
    registry.set_status("old", "inactive")
    active = registry.list_connectors(status="active")
    assert len(active) == 1
    assert active[0]["name"] == "DDG"


def test_list_connectors_includes_capabilities(registry: ConnectorRegistry):
    registry.register("ddg", "DDG", "search", ["search.web", "search.news"])
    result = registry.list_connectors()
    assert set(result[0]["capabilities"]) == {"search.web", "search.news"}


def test_set_status(registry: ConnectorRegistry):
    registry.register("ddg", "DDG", "search", ["search.web"])
    registry.set_status("ddg", "inactive")
    c = registry.get("ddg")
    assert c["status"] == "inactive"


def test_remove_cascades(registry: ConnectorRegistry, storage: SQLiteStorage):
    registry.register("ddg", "DDG", "search", ["search.web"])
    registry.remove("ddg")
    assert registry.get("ddg") is None
    rows = storage.fetchall(
        "SELECT * FROM connector_capabilities WHERE connector_id = ?", ("ddg",)
    )
    assert rows == []


def test_register_no_capabilities(registry: ConnectorRegistry):
    registry.register("empty", "Empty", "test", [])
    c = registry.get("empty")
    assert c is not None
    assert c["capabilities"] == []
