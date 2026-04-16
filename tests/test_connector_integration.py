"""Tests for connector setup integration with ConnectorRegistry + generations."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from mycelos.app import App


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-connector-integration"
        a = App(Path(tmp))
        a.initialize()
        yield a


def test_app_has_state_manager(app: App) -> None:
    """App should expose a state_manager property."""
    assert app.state_manager is not None


def test_app_has_connector_registry(app: App) -> None:
    """App should expose a connector_registry property."""
    assert app.connector_registry is not None


def test_connector_register_creates_generation(app: App) -> None:
    """Registering a connector and creating a generation should include it in snapshot."""
    app.connector_registry.register(
        connector_id="ddg",
        name="DuckDuckGo",
        connector_type="search",
        capabilities=["search.web", "search.news"],
    )
    gen_id = app.config.apply_from_state(
        state_manager=app.state_manager,
        description="Added DuckDuckGo",
        trigger="connector_setup",
    )
    assert gen_id is not None

    row = app.storage.fetchone(
        "SELECT config_snapshot FROM config_generations WHERE id = ?", (gen_id,)
    )
    snapshot = json.loads(row["config_snapshot"])
    assert "ddg" in snapshot["connectors"]
    assert "search.web" in snapshot["connectors"]["ddg"]["capabilities"]


def test_connector_rollback_removes_connector(app: App) -> None:
    """Rollback should remove a connector that was added after the target generation."""
    # Gen 1: empty
    gen1 = app.config.apply_from_state(app.state_manager, "empty", "test")

    # Add connector
    app.connector_registry.register("ddg", "DDG", "search", ["search.web"])

    # Gen 2: with connector
    gen2 = app.config.apply_from_state(app.state_manager, "with ddg", "test")

    # Verify it exists
    assert app.connector_registry.get("ddg") is not None

    # Rollback to Gen 1
    app.config.rollback(to_generation=gen1, state_manager=app.state_manager)

    # Should be gone
    assert app.connector_registry.get("ddg") is None


def test_multiple_connectors_in_snapshot(app: App) -> None:
    """Multiple connectors should all appear in the generation snapshot."""
    app.connector_registry.register("ddg", "DDG", "search", ["search.web"])
    app.connector_registry.register("brave", "Brave", "search", ["search.web", "search.news"])

    gen_id = app.config.apply_from_state(app.state_manager, "two connectors", "test")

    row = app.storage.fetchone(
        "SELECT config_snapshot FROM config_generations WHERE id = ?", (gen_id,)
    )
    snapshot = json.loads(row["config_snapshot"])
    assert "ddg" in snapshot["connectors"]
    assert "brave" in snapshot["connectors"]
    assert "search.news" in snapshot["connectors"]["brave"]["capabilities"]


def test_state_manager_caches(app: App) -> None:
    """Accessing state_manager twice should return the same instance."""
    sm1 = app.state_manager
    sm2 = app.state_manager
    assert sm1 is sm2
