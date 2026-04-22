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


# ── Setup-guide registry ──


from mycelos.connectors.oauth_setup_guides import (  # noqa: E402
    SETUP_GUIDES,
    get_setup_guide,
)


def test_google_cloud_guide_exists() -> None:
    """The 'google_cloud' guide must exist with a non-empty step list and
    each step must carry a title, body (markdown), and optional cta_url."""
    guide = get_setup_guide("google_cloud")
    assert guide is not None
    assert guide["id"] == "google_cloud"
    assert guide["title"]  # non-empty label
    steps = guide["steps"]
    assert len(steps) >= 5, "Google Cloud setup needs at least 5 concrete steps"
    for i, step in enumerate(steps):
        assert step["title"], f"step {i} missing title"
        assert step["body"], f"step {i} missing body"
        # cta_url is optional — a step that just asks the user to copy
        # a value out of the UI doesn't need a link.


def test_google_cloud_guide_mentions_oauth_desktop_app() -> None:
    """The step that creates the credential must specify Desktop app —
    that's the only OAuth client type the three MCP servers support."""
    guide = get_setup_guide("google_cloud")
    body_text = " ".join(step["body"].lower() for step in guide["steps"])
    assert "desktop app" in body_text or "desktop application" in body_text


def test_unknown_guide_returns_none() -> None:
    assert get_setup_guide("nonexistent-guide") is None


def test_all_guides_in_registry_self_reference() -> None:
    """Every guide's 'id' field must equal its key in SETUP_GUIDES."""
    for key, guide in SETUP_GUIDES.items():
        assert guide["id"] == key


# ── File-based credential fields ──


def test_mcp_recipe_defaults_for_file_credentials() -> None:
    """New fields default to empty strings for recipes that don't need
    file-materialization (the vast majority — env-var-based tools)."""
    r = MCPRecipe(id="x", name="X", description="", command="npx -y x")
    assert r.oauth_keys_credential_service == ""
    assert r.oauth_keys_home_dir == ""
    assert r.oauth_keys_filename == ""
    assert r.oauth_token_filename == ""
    assert r.oauth_token_credential_service == ""


def test_gmail_recipe_uses_file_materialization() -> None:
    r = RECIPES["gmail"]
    # The Gmail MCP package hardcodes ~/.gmail-mcp/gcp-oauth.keys.json —
    # no env var to override. We materialize the file into a tmp HOME.
    assert r.oauth_keys_credential_service == "gmail-oauth-keys"
    assert r.oauth_keys_home_dir == ".gmail-mcp"
    assert r.oauth_keys_filename == "gcp-oauth.keys.json"
    assert r.oauth_token_filename == "credentials.json"
    assert r.oauth_token_credential_service == "gmail-oauth-token"


def test_google_calendar_recipe_uses_file_materialization() -> None:
    r = RECIPES["google-calendar"]
    assert r.oauth_keys_credential_service == "google-calendar-oauth-keys"
    assert r.oauth_keys_home_dir == ".google-calendar-mcp"
    assert r.oauth_keys_filename == "gcp-oauth.keys.json"
    assert r.oauth_token_filename == "token.json"
    assert r.oauth_token_credential_service == "google-calendar-oauth-token"


def test_google_drive_recipe_uses_file_materialization() -> None:
    r = RECIPES["google-drive"]
    assert r.oauth_keys_credential_service == "google-drive-oauth-keys"
    assert r.oauth_keys_home_dir == ".google-drive-mcp"
    assert r.oauth_keys_filename == "gcp-oauth.keys.json"
    assert r.oauth_token_filename == "token.json"
    assert r.oauth_token_credential_service == "google-drive-oauth-token"


def test_non_file_recipes_keep_empty_materialization_fields() -> None:
    """Email, GitHub, Brave etc. use env-var injection and must not
    accidentally inherit file-materialization config."""
    for rid in ("email", "brave-search", "github", "notion", "slack"):
        r = RECIPES.get(rid)
        if r is None:
            continue
        assert r.oauth_keys_credential_service == "", f"{rid} should not materialize"
        assert r.oauth_keys_filename == "", f"{rid} should not materialize"
