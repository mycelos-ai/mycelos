"""MCPRecipe has an explicit `kind` field."""
from __future__ import annotations

from mycelos.connectors.mcp_recipes import RECIPES, MCPRecipe, get_recipe


def test_recipe_kind_default_is_mcp() -> None:
    r = MCPRecipe(id="x", name="X", description="Y", command="")
    assert r.kind == "mcp"


def test_telegram_is_channel_kind() -> None:
    r = get_recipe("telegram")
    assert r is not None
    assert r.kind == "channel"


def test_all_non_telegram_recipes_are_mcp_kind() -> None:
    for rid, recipe in RECIPES.items():
        if rid == "telegram":
            continue
        assert recipe.kind == "mcp", f"{rid} kind is {recipe.kind!r}, expected 'mcp'"


def test_kind_only_accepts_channel_or_mcp() -> None:
    # Exhaustive check: every recipe's kind is one of the two allowed values.
    for recipe in RECIPES.values():
        assert recipe.kind in ("channel", "mcp")


def test_mcp_memory_recipe_is_gone() -> None:
    """mcp-memory was removed — Mycelos's own Knowledge Base owns this concept."""
    from mycelos.connectors.mcp_recipes import RECIPES
    assert "mcp-memory" not in RECIPES
