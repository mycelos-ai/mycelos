"""Tests for connector setup CLI (recipe-driven)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from mycelos.cli.connector_cmd import _setup_mcp, connector_cmd
from mycelos.connectors.mcp_recipes import get_recipe


# ── Recipe schema ───────────────────────────────────────────────

def test_recipe_driven_github_recipe_exists() -> None:
    r = get_recipe("github")
    assert r is not None
    assert r.kind == "mcp"
    assert r.credentials and r.credentials[0]["env_var"] == "GITHUB_PERSONAL_ACCESS_TOKEN"


def test_recipe_driven_brave_recipe_exists() -> None:
    r = get_recipe("brave-search")
    assert r is not None
    assert r.kind == "mcp"


def test_recipe_driven_telegram_recipe_is_channel() -> None:
    r = get_recipe("telegram")
    assert r is not None
    assert r.kind == "channel"


# ── _setup_mcp ──────────────────────────────────────────────────

def test_setup_mcp_no_key_grants_policy(tmp_data_dir: Path) -> None:
    """Setting up an MCP recipe without credentials grants its capabilities."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-setup-mcp-nokey"
    try:
        app = App(tmp_data_dir)
        app.initialize()
        recipe = get_recipe("fetch")
        assert recipe is not None
        _setup_mcp(app, recipe)

        for cap in recipe.capabilities_preview:
            decision = app.policy_engine.evaluate("default", "any-agent", cap)
            assert decision == "always", f"cap {cap} not granted"
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_setup_mcp_with_key_stores_credential(tmp_data_dir: Path) -> None:
    """Setting up a keyed MCP recipe stores the credential under recipe.id."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-setup-mcp-keyed"
    try:
        app = App(tmp_data_dir)
        app.initialize()
        recipe = get_recipe("brave-search")
        assert recipe is not None

        with patch("click.prompt", return_value="BSA-test-token"), \
             patch("click.confirm", return_value=False):
            _setup_mcp(app, recipe)

        stored = app.credentials.get_credential("brave-search")
        assert stored is not None
        assert stored["api_key"] == "BSA-test-token"
        assert stored["env_var"] == "BRAVE_API_KEY"
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_setup_mcp_registers_in_connector_registry(tmp_data_dir: Path) -> None:
    """After setup, connector_registry.get returns the connector."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-setup-mcp-registry"
    try:
        app = App(tmp_data_dir)
        app.initialize()
        recipe = get_recipe("fetch")
        assert recipe is not None
        _setup_mcp(app, recipe)

        row = app.connector_registry.get("fetch")
        assert row is not None
        assert row["name"] == recipe.name
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


# ── setup_cmd routing ───────────────────────────────────────────

def test_setup_cmd_routes_mcp_recipe(tmp_data_dir: Path) -> None:
    """`connector setup fetch` routes to _setup_mcp (registers recipe in the registry)."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-route-mcp"
    try:
        App(tmp_data_dir).initialize()
        runner = CliRunner()
        result = runner.invoke(
            connector_cmd,
            ["setup", "fetch", "--data-dir", str(tmp_data_dir)],
        )
        assert result.exit_code == 0, result.output

        # Re-open the app and verify the MCP path wrote a registry row
        # (_setup_channel would not have, since fetch is kind="mcp").
        app = App(tmp_data_dir)
        row = app.connector_registry.get("fetch")
        assert row is not None, "fetch was not registered — MCP path did not run"
        assert row["connector_type"] == "mcp"
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


def test_setup_cmd_rejects_unknown_id(tmp_data_dir: Path) -> None:
    """Unknown id exits non-zero with a pointer to `connector list`."""
    from mycelos.app import App

    os.environ["MYCELOS_MASTER_KEY"] = "test-key-route-unknown"
    try:
        App(tmp_data_dir).initialize()
        runner = CliRunner()
        result = runner.invoke(
            connector_cmd,
            ["setup", "does-not-exist", "--data-dir", str(tmp_data_dir)],
        )
        assert result.exit_code == 1
        assert "does-not-exist" in result.output
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)


# ── list / setup error paths (still recipe-based) ───────────────

def test_connector_list_not_initialized(tmp_data_dir: Path) -> None:
    """mycelos connector list fails when not initialized."""
    runner = CliRunner()
    result = runner.invoke(
        connector_cmd, ["list", "--data-dir", str(tmp_data_dir)]
    )
    assert result.exit_code == 1


def test_setup_not_initialized(tmp_data_dir: Path) -> None:
    """mycelos connector setup fails when not initialized."""
    runner = CliRunner()
    result = runner.invoke(
        connector_cmd, ["setup", "--data-dir", str(tmp_data_dir)]
    )
    assert result.exit_code == 1
