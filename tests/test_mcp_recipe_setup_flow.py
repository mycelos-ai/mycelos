"""The MCPRecipe dataclass gained a setup_flow field so the frontend
can render different setup dialogs for different credential shapes.
Recipes default to 'secret' (single password-style input); OAuth-based
recipes declare 'oauth_browser' which triggers the Google-style wizard."""
from __future__ import annotations

from mycelos.connectors.mcp_recipes import MCPRecipe, RECIPES


def test_default_setup_flow_is_secret() -> None:
    """Every existing recipe uses the plain-secret flow unless it opts out."""
    r = MCPRecipe(id="x", name="X", description="", command="npx -y x")
    assert r.setup_flow == "secret"


def test_gmail_recipe_declares_oauth_browser() -> None:
    r = RECIPES["gmail"]
    assert r.setup_flow == "oauth_browser"
    # The oauth_cmd is what the proxy spawns when the user clicks
    # "Start OAuth consent" — upstream's auth subcommand.
    assert r.oauth_cmd == "npx -y @gongrzhe/server-gmail-autoauth-mcp auth"
    # A setup-guide id links the recipe to a step-by-step wizard
    # (Google Cloud project creation, etc.). All three Google recipes
    # share the 'google_cloud' guide.
    assert r.oauth_setup_guide_id == "google_cloud"


def test_google_calendar_recipe_declares_oauth_browser() -> None:
    r = RECIPES["google-calendar"]
    assert r.setup_flow == "oauth_browser"
    assert r.oauth_cmd == "npx -y @cocal/google-calendar-mcp auth"
    assert r.oauth_setup_guide_id == "google_cloud"


def test_google_drive_recipe_declares_oauth_browser() -> None:
    r = RECIPES["google-drive"]
    assert r.setup_flow == "oauth_browser"
    assert r.oauth_cmd == "npx -y @piotr-agier/google-drive-mcp auth"
    assert r.oauth_setup_guide_id == "google_cloud"


def test_non_oauth_recipes_keep_secret_flow() -> None:
    """Make sure we didn't accidentally flip other recipes."""
    for rid in ("brave-search", "github", "notion", "slack", "telegram", "email"):
        r = RECIPES.get(rid)
        if r is None:
            continue  # recipe may be renamed or removed in future
        assert r.setup_flow == "secret", f"recipe {rid} unexpectedly switched flows"
        assert r.oauth_cmd == "", f"recipe {rid} should not declare oauth_cmd"
