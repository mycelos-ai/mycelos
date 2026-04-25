"""MCP Connector Recipes — predefined, one-click connector setup.

Each recipe defines: what MCP server to use, what credentials are needed,
and what capabilities it provides. The user just says "/connector add github"
and Mycelos handles the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class MCPRecipe:
    """A predefined connector recipe (MCP server or chat channel, see `kind`)."""

    id: str                          # "github", "brave-search", "filesystem"
    name: str                        # "GitHub"
    description: str                 # "Access repositories, issues, and PRs"
    command: str                     # "npx -y @modelcontextprotocol/server-github"
    transport: str = "stdio"         # "stdio", "http", "sse"
    kind: Literal["channel", "mcp"] = "mcp"
    credentials: list[dict] = field(default_factory=list)
        # [{"env_var": "GITHUB_TOKEN", "name": "GitHub Personal Access Token",
        #   "help": "Create at https://github.com/settings/tokens"}]
    capabilities_preview: list[str] = field(default_factory=list)
        # ["github.list_repos", "github.create_issue"] — approximate, real ones from discovery
    category: str = "tools"          # "tools", "search", "storage", "code"
    requires_node: bool = True       # Most MCP servers need npx
    static_env: dict[str, str] = field(default_factory=dict)
        # Non-secret env vars that always need to be set for the server
        # to run (e.g. TRANSPORT_MODE=stdio for MCP servers that default
        # to HTTP-relay when no env is set). Merged into the subprocess
        # env alongside credential injection.
    setup_flow: str = "secret"
        # "secret" (default): single password-style input for API key.
        # "oauth_http": render the OAuth authorize-URL redirect wizard for
        # hosted HTTP MCP endpoints. Frontend switches dialog based on this
        # value.
    oauth_setup_guide_id: str = ""
        # Key into the oauth_setup_guides registry (e.g. "google_cloud").
        # Non-empty only when setup_flow == "oauth_http". The guide
        # describes prerequisites like "create a Google Cloud project,
        # enable the Gmail API, download the client_secret JSON" as a
        # step-by-step wizard.
    http_endpoint: str = ""
        # Remote MCP endpoint for HTTP-transport recipes. Empty for
        # stdio-transport recipes.
    oauth_authorize_url: str = ""
        # OAuth 2.0 authorize endpoint (Google, Microsoft, etc.).
    oauth_token_url: str = ""
        # OAuth 2.0 token endpoint (for code exchange AND refresh).
    oauth_scopes: list[str] = field(default_factory=list)
        # Space-joined into the `scope` query param on the auth URL.
    oauth_client_credential_service: str = ""
        # DB row holding {"api_key": json.dumps(client_secret_json)}.
        # The blob is the raw client_secret_*.json the user downloads
        # from Cloud Console.
    oauth_token_credential_service: str = ""
        # Where to store the token blob the upstream package produced.
        # Future MCP-server runs materialize both keys AND token before spawn.


# All available recipes
RECIPES: dict[str, MCPRecipe] = {
    "github": MCPRecipe(
        id="github",
        name="GitHub",
        description="Repositories, Issues, Pull Requests, Code Search, Actions, Discussions",
        command="",  # No local process — uses hosted HTTP endpoint
        transport="http",
        credentials=[{
            "env_var": "GITHUB_PERSONAL_ACCESS_TOKEN",
            "name": "GitHub Personal Access Token",
            "help": "Create at https://github.com/settings/tokens (needs repo + issues scope)",
        }],
        capabilities_preview=["github.list_repos", "github.create_issue", "github.search_code",
                               "github.get_pr", "github.create_pr"],
        category="code",
        requires_node=False,
    ),
    "brave-search": MCPRecipe(
        id="brave-search",
        name="Brave Search",
        description="Web, images, videos, news + AI summaries via Brave Search API",
        # Brave took the MCP server in-house in 2025; the old
        # @modelcontextprotocol/server-brave-search package is deprecated
        # and will refuse to start. The new package lives under @brave/.
        command="npx -y @brave/brave-search-mcp-server",
        credentials=[{
            "env_var": "BRAVE_API_KEY",
            "name": "Brave Search API Key",
            "help": "Get a free key at https://brave.com/search/api/ (2000 queries/month free)",
        }],
        capabilities_preview=["brave_search"],
        category="search",
    ),
    "filesystem": MCPRecipe(
        id="filesystem",
        name="Filesystem",
        description="Read and write files in allowed directories",
        # Arguments (allowed_dirs) must be provided at connection time via connector config.
        command="npx -y @modelcontextprotocol/server-filesystem",
        credentials=[],  # No key needed — allowed_dirs supplied at connection time
        capabilities_preview=["filesystem.read", "filesystem.write", "filesystem.list"],
        category="storage",
    ),
    "fetch": MCPRecipe(
        id="fetch",
        name="HTTP Fetch",
        description="Make HTTP requests to any URL",
        command="npx -y @modelcontextprotocol/server-fetch",
        credentials=[],
        capabilities_preview=["fetch"],
        category="tools",
    ),
    "git": MCPRecipe(
        id="git",
        name="Git",
        description="Git operations — branches, commits, diffs",
        command="npx -y @modelcontextprotocol/server-git",
        credentials=[],
        capabilities_preview=["git.log", "git.diff", "git.branch"],
        category="code",
    ),
    "sqlite": MCPRecipe(
        id="sqlite",
        name="SQLite",
        description="Query SQLite databases",
        # Arguments (db_path) must be provided at connection time via connector config.
        command="npx -y @modelcontextprotocol/server-sqlite",
        credentials=[],  # No key needed — db_path supplied at connection time
        capabilities_preview=["sqlite.query", "sqlite.schema"],
        category="storage",
    ),
    "slack": MCPRecipe(
        id="slack",
        name="Slack",
        description="Send and read Slack messages",
        command="npx -y @modelcontextprotocol/server-slack",
        credentials=[{
            "env_var": "SLACK_BOT_TOKEN",
            "name": "Slack Bot Token (xoxb-...)",
            "help": "Create a Slack App at https://api.slack.com/apps",
        }],
        capabilities_preview=["slack.post_message", "slack.list_channels"],
        category="tools",
    ),
    "gmail": MCPRecipe(
        id="gmail",
        name="Gmail",
        description="Read, search, send, and manage Gmail via Google's official MCP server",
        command="",
        transport="http",
        setup_flow="oauth_http",
        oauth_setup_guide_id="google_cloud",
        http_endpoint="https://gmailmcp.googleapis.com/mcp/v1",
        oauth_authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        oauth_token_url="https://oauth2.googleapis.com/token",
        oauth_scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
        ],
        oauth_client_credential_service="gmail-oauth-client",
        oauth_token_credential_service="gmail-oauth-token",
        category="google",
        capabilities_preview=[
            "search_threads", "get_thread", "list_labels",
            "create_draft", "list_drafts",
        ],
    ),
    "email": MCPRecipe(
        id="email",
        name="Email (Gmail / Outlook / iCloud / IMAP)",
        description=(
            "Read, search, send, reply, forward, and organise email across Gmail, "
            "Yahoo, iCloud, Outlook, Zoho, ProtonMail, and any custom IMAP host. "
            "Runs as an MCP server inside the SecurityProxy container — credentials "
            "never leave the proxy."
        ),
        command="npx -y @n24q02m/better-email-mcp",
        transport="stdio",
        credentials=[{
            "env_var": "EMAIL_CREDENTIALS",
            "name": "Email credentials (user@provider.com:app-password, comma-separated for multiple accounts)",
            "help": (
                "Format: user@gmail.com:app-password  "
                "(comma-separated for multi-account, e.g. you@gmail.com:aaa,you@icloud.com:bbb). "
                "Gmail/Yahoo/iCloud need an app password, not the login password. "
                "Custom IMAP: user@example.com:password:imap.example.com"
            ),
        }],
        capabilities_preview=["messages", "folders", "attachments", "send", "setup", "help"],
        category="communication",
        requires_node=True,
        # The server defaults to a browser-based relay setup page when
        # TRANSPORT_MODE is unset — that's the wrong shape for us.
        # Pin it to stdio so it behaves as a normal MCP subprocess and
        # reads EMAIL_CREDENTIALS from its env.
        static_env={"TRANSPORT_MODE": "stdio"},
    ),
    "telegram": MCPRecipe(
        id="telegram",
        name="Telegram Bot",
        description="Chat with Mycelos via Telegram",
        command="",
        transport="channel",
        kind="channel",
        credentials=[{
            "env_var": "TELEGRAM_BOT_TOKEN",
            "name": "Telegram Bot Token",
            "help": "Create a bot via @BotFather in Telegram. Send /newbot and follow instructions. After setup, restart the gateway (mycelos serve) and send a message to your bot in Telegram.",
        }],
        capabilities_preview=[],
        category="channel",
        requires_node=False,
    ),

    # --- Official Top-Tier MCP Servers (curated, verified) ---

    "playwright": MCPRecipe(
        id="playwright",
        name="Playwright (Browser)",
        description="Browser automation — navigate, click, fill forms, take screenshots, scrape JS-rendered pages",
        command="npx -y @playwright/mcp",
        transport="stdio",
        credentials=[],
        capabilities_preview=["playwright.navigate", "playwright.click", "playwright.fill", "playwright.screenshot"],
        category="tools",
        requires_node=True,
    ),
    "postgres": MCPRecipe(
        id="postgres",
        name="PostgreSQL",
        description="Query PostgreSQL databases — read-only by default, schema inspection",
        command="npx -y @modelcontextprotocol/server-postgres",
        transport="stdio",
        credentials=[{
            "env_var": "POSTGRES_CONNECTION_STRING",
            "name": "PostgreSQL connection string",
            "help": "Format: postgresql://user:password@host:5432/dbname",
        }],
        capabilities_preview=["postgres.query", "postgres.schema"],
        category="storage",
        requires_node=True,
    ),
    "puppeteer": MCPRecipe(
        id="puppeteer",
        name="Puppeteer (Browser)",
        description="Headless Chrome browser control — screenshots, PDF generation, web scraping",
        command="npx -y @modelcontextprotocol/server-puppeteer",
        transport="stdio",
        credentials=[],
        capabilities_preview=["puppeteer.navigate", "puppeteer.screenshot", "puppeteer.pdf"],
        category="tools",
        requires_node=True,
    ),
    "notion": MCPRecipe(
        id="notion",
        name="Notion",
        description="Read and write Notion pages, databases, and blocks",
        command="npx -y @notionhq/notion-mcp-server",
        transport="stdio",
        credentials=[{
            "env_var": "NOTION_API_KEY",
            "name": "Notion Integration Token",
            "help": "Create at notion.so/my-integrations",
        }],
        capabilities_preview=["notion.read", "notion.write", "notion.search"],
        category="tools",
        requires_node=True,
    ),
    "sentry": MCPRecipe(
        id="sentry",
        name="Sentry",
        description="Access Sentry issues, events, and project data for error tracking",
        command="npx -y @sentry/mcp-server-sentry",
        transport="stdio",
        credentials=[{
            "env_var": "SENTRY_AUTH_TOKEN",
            "name": "Sentry Auth Token",
            "help": "Create at sentry.io/settings/account/api/auth-tokens/",
        }],
        capabilities_preview=["sentry.issues", "sentry.events"],
        category="tools",
        requires_node=True,
    ),
    "docker": MCPRecipe(
        id="docker",
        name="Docker",
        description="Manage Docker containers, images, volumes, and networks",
        command="npx -y @modelcontextprotocol/server-docker",
        transport="stdio",
        credentials=[],
        capabilities_preview=["docker.containers", "docker.images", "docker.volumes"],
        category="tools",
        requires_node=True,
    ),
    "chrome-devtools": MCPRecipe(
        id="chrome-devtools",
        name="Chrome DevTools",
        description="Control Chrome browser via DevTools Protocol — debugging, DOM inspection, network, performance",
        command="npx -y @anthropic-ai/mcp-chrome-devtools",
        transport="stdio",
        credentials=[],
        capabilities_preview=["chrome.navigate", "chrome.dom", "chrome.network", "chrome.console"],
        category="tools",
        requires_node=True,
    ),
    "linear": MCPRecipe(
        id="linear",
        name="Linear",
        description="Manage Linear issues, projects, and teams",
        command="npx -y @anthropic-ai/mcp-linear",
        transport="stdio",
        credentials=[{
            "env_var": "LINEAR_API_KEY",
            "name": "Linear API Key",
            "help": "Create at linear.app/settings/api",
        }],
        capabilities_preview=["linear.issues", "linear.projects"],
        category="tools",
        requires_node=True,
    ),
}


def get_recipe(recipe_id: str) -> MCPRecipe | None:
    """Get a recipe by ID."""
    return RECIPES.get(recipe_id)


def list_recipes(category: str | None = None) -> list[MCPRecipe]:
    """List available recipes, optionally filtered by category."""
    recipes = list(RECIPES.values())
    if category:
        recipes = [r for r in recipes if r.category == category]
    return recipes


def is_node_available() -> bool:
    """Check if Node.js (npx) is installed."""
    import shutil
    return shutil.which("npx") is not None
