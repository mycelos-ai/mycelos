"""The MCPRecipe dataclass gained a setup_flow field so the frontend
can render different setup dialogs for different credential shapes.
Recipes default to 'secret' (single password-style input); OAuth-based
recipes declare 'oauth_http' which triggers the hosted-OAuth wizard."""
from __future__ import annotations

from mycelos.connectors.mcp_recipes import MCPRecipe, RECIPES


def test_default_setup_flow_is_secret() -> None:
    """Every existing recipe uses the plain-secret flow unless it opts out."""
    r = MCPRecipe(id="x", name="X", description="", command="npx -y x")
    assert r.setup_flow == "secret"


def test_non_oauth_recipes_keep_secret_flow() -> None:
    """Make sure we didn't accidentally flip other recipes."""
    for rid in ("brave-search", "github", "notion", "slack", "telegram", "email"):
        r = RECIPES.get(rid)
        if r is None:
            continue  # recipe may be renamed or removed in future
        assert r.setup_flow == "secret", f"recipe {rid} unexpectedly switched flows"


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


# ── oauth_http fields ──


def test_mcp_recipe_defaults_for_oauth_http_fields() -> None:
    """All new oauth_http fields default to empty (list for scopes).
    Only recipes that explicitly opt in get the HTTP flow."""
    r = MCPRecipe(id="x", name="X", description="", command="npx -y x")
    assert r.http_endpoint == ""
    assert r.oauth_authorize_url == ""
    assert r.oauth_token_url == ""
    assert r.oauth_scopes == []
    assert r.oauth_client_credential_service == ""
    assert r.oauth_token_credential_service == ""


def test_gmail_recipe_declares_oauth_http() -> None:
    r = RECIPES["gmail"]
    assert r.setup_flow == "oauth_http"
    assert r.http_endpoint == "https://gmailmcp.googleapis.com/mcp/v1"
    assert r.oauth_authorize_url == "https://accounts.google.com/o/oauth2/v2/auth"
    assert r.oauth_token_url == "https://oauth2.googleapis.com/token"
    assert "https://www.googleapis.com/auth/gmail.readonly" in r.oauth_scopes
    assert "https://www.googleapis.com/auth/gmail.compose" in r.oauth_scopes
    assert r.oauth_client_credential_service == "gmail-oauth-client"
    assert r.oauth_token_credential_service == "gmail-oauth-token"


def test_oauth_browser_is_gone() -> None:
    """The old file-materialization setup_flow value is removed; no
    recipe still declares it."""
    for r in RECIPES.values():
        assert r.setup_flow != "oauth_browser", f"{r.id} still uses oauth_browser"


def test_old_file_mat_fields_removed_from_dataclass() -> None:
    """The file-materialization fields are gone from MCPRecipe."""
    r = MCPRecipe(id="x", name="X", description="", command="npx -y x")
    assert not hasattr(r, "oauth_cmd")
    assert not hasattr(r, "oauth_keys_credential_service")
    assert not hasattr(r, "oauth_keys_home_dir")
    assert not hasattr(r, "oauth_keys_filename")
    assert not hasattr(r, "oauth_token_filename")


def test_calendar_and_drive_recipes_are_removed() -> None:
    """Calendar and Drive come back in a follow-up plan. For now only
    Gmail is wired up so the review surface stays small."""
    assert "google-calendar" not in RECIPES
    assert "google-drive" not in RECIPES


def test_google_cloud_guide_covers_mcp_api_activation() -> None:
    """The guide must tell the user to enable the *MCP* API variant
    (e.g. gmailmcp.googleapis.com), not just the plain Gmail API."""
    guide = get_setup_guide("google_cloud")
    body_text = " ".join(step["body"].lower() for step in guide["steps"])
    assert "gmailmcp" in body_text or "mcp api" in body_text


def test_google_cloud_guide_covers_redirect_uri_registration() -> None:
    guide = get_setup_guide("google_cloud")
    body_text = " ".join(step["body"].lower() for step in guide["steps"])
    assert "redirect" in body_text and ("uri" in body_text or "url" in body_text)
