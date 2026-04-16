"""Tests for MCP Recipes + Client."""

from __future__ import annotations

import pytest

from mycelos.connectors.mcp_recipes import (
    MCPRecipe,
    RECIPES,
    get_recipe,
    is_node_available,
    list_recipes,
)


# --- Recipes ---

def test_recipes_exist():
    assert len(RECIPES) >= 5
    assert "github" in RECIPES
    assert "brave-search" in RECIPES
    assert "filesystem" in RECIPES
    assert "fetch" in RECIPES


def test_get_recipe():
    r = get_recipe("github")
    assert r is not None
    assert r.name == "GitHub"
    assert r.transport == "http"  # official hosted endpoint
    assert len(r.credentials) == 1
    assert r.credentials[0]["env_var"] == "GITHUB_PERSONAL_ACCESS_TOKEN"


def test_get_recipe_none():
    assert get_recipe("nonexistent") is None


def test_list_recipes():
    all_recipes = list_recipes()
    assert len(all_recipes) >= 5


def test_list_recipes_by_category():
    search = list_recipes(category="search")
    assert all(r.category == "search" for r in search)
    assert any(r.id == "brave-search" for r in search)


def test_recipe_has_required_fields():
    for recipe_id, recipe in RECIPES.items():
        assert recipe.id == recipe_id
        assert recipe.name
        assert recipe.description
        # Channels, HTTP-hosted servers, and builtins don't need a command
        if recipe.transport not in ("channel", "http", "builtin"):
            assert recipe.command
        assert recipe.transport in ("stdio", "http", "sse", "channel", "builtin")


def test_brave_search_recipe():
    r = get_recipe("brave-search")
    assert r is not None
    assert "brave" in r.command.lower()
    assert r.credentials[0]["env_var"] == "BRAVE_API_KEY"


def test_filesystem_recipe_no_credentials():
    r = get_recipe("filesystem")
    assert r is not None
    assert r.credentials == []


def test_fetch_recipe_no_credentials():
    r = get_recipe("fetch")
    assert r is not None
    assert r.credentials == []


def test_node_available_returns_bool():
    result = is_node_available()
    assert isinstance(result, bool)


# --- MCPRecipe dataclass ---

def test_recipe_frozen():
    r = get_recipe("github")
    with pytest.raises(AttributeError):
        r.name = "changed"


def test_recipe_categories():
    categories = {r.category for r in RECIPES.values()}
    assert "tools" in categories
    assert "search" in categories
    assert "code" in categories


# --- MCP Client ---

def test_mcp_client_import():
    from mycelos.connectors.mcp_client import MycelosMCPClient
    client = MycelosMCPClient("test", "echo hello")
    assert client.connector_id == "test"
    assert not client.is_connected
    assert client.tools == []


def test_mcp_client_build_env():
    from mycelos.connectors.mcp_client import MycelosMCPClient
    from unittest.mock import MagicMock

    mock_proxy = MagicMock()
    mock_proxy.get_credential.return_value = {"api_key": "secret123"}

    client = MycelosMCPClient(
        "github", "npx server",
        env_vars={"GITHUB_PERSONAL_ACCESS_TOKEN": "credential:github"},
        credential_proxy=mock_proxy,
    )
    env = client._build_env()
    assert env.get("GITHUB_PERSONAL_ACCESS_TOKEN") == "secret123"
    # PATH should be inherited
    assert "PATH" in env


def test_mcp_client_build_env_no_proxy():
    from mycelos.connectors.mcp_client import MycelosMCPClient
    client = MycelosMCPClient("test", "echo", env_vars={"KEY": "direct-value"})
    env = client._build_env()
    assert env.get("KEY") == "direct-value"


def test_mcp_client_build_env_missing_credential():
    from mycelos.connectors.mcp_client import MycelosMCPClient
    from unittest.mock import MagicMock

    mock_proxy = MagicMock()
    mock_proxy.get_credential.return_value = None

    client = MycelosMCPClient(
        "test", "echo",
        env_vars={"TOKEN": "credential:missing"},
        credential_proxy=mock_proxy,
    )
    env = client._build_env()
    assert "TOKEN" not in env  # Should not be set if credential missing


def test_mcp_client_scoped_env():
    """MCP server should only see its own credentials, not others."""
    from mycelos.connectors.mcp_client import MycelosMCPClient
    import os

    # Set a "dangerous" env var
    os.environ["ANTHROPIC_API_KEY"] = "should-not-leak"

    client = MycelosMCPClient("github", "echo", env_vars={})
    env = client._build_env()

    # The MCP server should NOT see other API keys
    assert "ANTHROPIC_API_KEY" not in env

    # Clean up
    del os.environ["ANTHROPIC_API_KEY"]
