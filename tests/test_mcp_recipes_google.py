"""The three Google MCP recipes (gmail, google-calendar, google-drive) must
be present in RECIPES with the right npm packages, env vars, and static_env.
If upstream renames a package or changes an env var name, this test fails
before a user hits a 'server not found' runtime error."""
from __future__ import annotations

from mycelos.connectors.mcp_recipes import RECIPES


def test_gmail_recipe_points_at_gongrzhe_autoauth() -> None:
    r = RECIPES.get("gmail")
    assert r is not None, "gmail recipe must exist"
    assert "@gongrzhe/server-gmail-autoauth-mcp" in r.command
    assert r.transport == "stdio"
    envs = {c["env_var"] for c in r.credentials}
    # The server reads the oauth-keys JSON path from GMAIL_OAUTH_PATH
    # (see upstream README). We store the JSON blob itself via that
    # credential so Mycelos materializes it to disk in the proxy.
    assert "GMAIL_OAUTH_PATH" in envs
    assert r.category == "communication"


def test_google_calendar_recipe_points_at_cocal() -> None:
    r = RECIPES.get("google-calendar")
    assert r is not None, "google-calendar recipe must exist"
    assert "@cocal/google-calendar-mcp" in r.command
    assert r.transport == "stdio"
    envs = {c["env_var"] for c in r.credentials}
    assert "GOOGLE_OAUTH_CREDENTIALS" in envs
    assert r.category == "communication"


def test_google_drive_recipe_points_at_piotr_agier() -> None:
    r = RECIPES.get("google-drive")
    assert r is not None, "google-drive recipe must exist"
    assert "@piotr-agier/google-drive-mcp" in r.command
    assert r.transport == "stdio"
    envs = {c["env_var"] for c in r.credentials}
    assert "GDRIVE_OAUTH_PATH" in envs
    assert r.category == "storage"


def test_stale_gog_gmail_recipe_is_gone() -> None:
    """The pre-MCP `gmail` recipe pointed at the gog CLI via
    transport='builtin' — that code path is being deleted. Make sure
    nothing still claims the gog shape for gmail."""
    r = RECIPES["gmail"]
    assert r.transport != "builtin"
    assert "gog" not in (r.command or "").lower()


def test_stale_google_drive_npm_package_is_gone() -> None:
    """The pre-MCP `google-drive` recipe pointed at
    @modelcontextprotocol/server-google-drive, which was never
    published. Make sure the replacement is in place."""
    r = RECIPES["google-drive"]
    assert "@modelcontextprotocol/server-google-drive" not in r.command
