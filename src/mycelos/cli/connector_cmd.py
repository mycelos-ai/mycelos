"""mycelos connector setup -- interactive connector configuration.

Usage:
    mycelos connector setup              # Shows list of available connectors
    mycelos connector setup brave-search # Jumps directly to Brave Search setup
    mycelos connector list               # Shows configured connectors
"""

from __future__ import annotations

import os
from pathlib import Path
from mycelos.cli import default_data_dir
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from mycelos.app import App
from mycelos.i18n import t

console = Console()

# Available connectors with their configuration requirements
CONNECTORS: dict[str, dict[str, Any]] = {
    "web-search-duckduckgo": {
        "name": "Web Search (DuckDuckGo)",
        "description": "Search the web -- no API key needed",
        "requires_key": False,
        "capabilities": ["search.web", "search.news"],
        "category": "search",
    },
    "web-search-brave": {
        "name": "Web Search (Brave)",
        "description": "Search the web via Brave Search API",
        "requires_key": True,
        "env_var": "BRAVE_API_KEY",
        "key_help": (
            "Get a free API key at https://brave.com/search/api/ "
            "(2000 queries/month free)"
        ),
        "capabilities": ["search.web", "search.news"],
        "category": "search",
    },
    "http": {
        "name": "HTTP / Web Access",
        "description": "Fetch web pages and call APIs",
        "requires_key": False,
        "capabilities": ["http.get", "http.post"],
        "category": "web",
    },
    "telegram": {
        "name": "Telegram Bot",
        "description": "Chat with Mycelos via Telegram",
        "requires_key": True,
        "env_var": "TELEGRAM_BOT_TOKEN",
        "key_help": (
            "Create a bot at @BotFather in Telegram:\n"
            "  1. Open Telegram and message @BotFather\n"
            "  2. Send /newbot and follow the instructions\n"
            "  3. Copy the bot token (looks like: 123456:ABC-DEF...)"
        ),
        "capabilities": [],
        "category": "channel",
        "setup_type": "telegram",
    },
    "github": {
        "name": "GitHub",
        "description": "Access repositories, issues, and pull requests via MCP",
        "requires_key": True,
        "env_var": "GITHUB_PERSONAL_ACCESS_TOKEN",
        "key_help": (
            "Create a Personal Access Token at https://github.com/settings/tokens\n"
            "  Recommended scopes: repo, issues, pull_requests"
        ),
        "capabilities": ["github.read", "github.write"],
        "category": "code",
    },
}


@click.group()
def connector_cmd() -> None:
    """Manage external service connectors."""
    pass


@connector_cmd.command("setup")
@click.argument("connector_name", required=False)
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=default_data_dir,
)
def setup_cmd(connector_name: str | None, data_dir: Path) -> None:
    """Set up a connector.

    Without argument, shows available connectors to choose from.
    """
    db_path = data_dir / "mycelos.db"
    if not db_path.exists():
        console.print(
            f"[red]{t('common.error')}:[/red] {t('connector.not_initialized')}"
        )
        raise SystemExit(1)

    # Load master key from file if not already set
    if not os.environ.get("MYCELOS_MASTER_KEY"):
        key_file = data_dir / ".master_key"
        if key_file.exists():
            os.environ["MYCELOS_MASTER_KEY"] = key_file.read_text().strip()

    app = App(data_dir)

    if connector_name:
        # Direct setup for named connector
        if connector_name not in CONNECTORS:
            console.print(f"[red]{t('connector.unknown', name=connector_name)}[/red]")
            console.print(t("connector.available", names=', '.join(CONNECTORS.keys())))
            raise SystemExit(1)
        _setup_connector(app, connector_name, CONNECTORS[connector_name])
    else:
        # Interactive selection
        _show_connector_menu(app)


@connector_cmd.command("list")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=default_data_dir,
)
def list_cmd(data_dir: Path) -> None:
    """List configured connectors and their status.

    The source of truth is the connector_registry in the DB (what
    the web UI reads). Recipes that aren't registered yet are shown
    in a second table so the user knows what else is available to
    install.
    """
    db_path = data_dir / "mycelos.db"
    if not db_path.exists():
        console.print(f"[red]{t('common.error')}:[/red] {t('connector.not_initialized_short')}")
        raise SystemExit(1)

    if not os.environ.get("MYCELOS_MASTER_KEY"):
        key_file = data_dir / ".master_key"
        if key_file.exists():
            os.environ["MYCELOS_MASTER_KEY"] = key_file.read_text().strip()

    app = App(data_dir)
    from mycelos.connectors.mcp_recipes import RECIPES
    registered = app.connector_registry.list_connectors()
    registered_ids = {c["id"] for c in registered}

    state_styles = {
        "healthy": "[green]● healthy[/green]",
        "ready": "[cyan]● ready[/cyan]",
        "failing": "[red]● failing[/red]",
        "setup_incomplete": "[yellow]● setup incomplete[/yellow]",
    }

    # ── Configured connectors (from the registry) ────────────────
    configured_table = Table(title="Configured connectors")
    configured_table.add_column("Connector", style="bold")
    configured_table.add_column("Type", style="dim")
    configured_table.add_column("State")
    configured_table.add_column("Capabilities", overflow="fold")

    if registered:
        for c in registered:
            state = state_styles.get(c.get("operational_state"), c.get("operational_state") or "?")
            caps = ", ".join(c.get("capabilities") or []) or "[dim]—[/dim]"
            configured_table.add_row(
                c.get("name") or c["id"],
                c.get("connector_type") or "?",
                state,
                caps,
            )
        console.print(configured_table)
    else:
        console.print("[dim]No connectors configured yet.[/dim]")

    # ── Available recipes (what else can be installed) ───────────
    # Recipes are not all MCP connectors: channels (Telegram, Slack)
    # surface incoming messages and don't expose tools; builtin
    # services (email-via-IMAP) are in-process helpers. Show the
    # `Kind` column so users understand which tier each entry is
    # before they try `connector setup`.
    def _kind_of(recipe) -> str:
        t = (recipe.transport or "").lower()
        if t == "channel":
            return "channel"
        if t == "builtin":
            return "service"
        if t == "http":
            return "mcp (http)"
        return "mcp"

    available = [r for rid, r in RECIPES.items() if rid not in registered_ids]
    if available:
        available_table = Table(title="Available recipes (not yet configured)")
        available_table.add_column("Recipe", style="bold")
        available_table.add_column("Kind", style="dim")
        available_table.add_column("Category", style="dim")
        available_table.add_column("Setup", style="dim")
        available_table.add_column("Description", overflow="fold")
        for r in sorted(available, key=lambda x: (_kind_of(x), x.category, x.id)):
            setup_hint = r.setup_flow or "secret"
            desc = (r.description or "").splitlines()[0] if r.description else ""
            available_table.add_row(r.id, _kind_of(r), r.category, setup_hint, desc)
        console.print()
        console.print(available_table)
        console.print(
            "\n[dim]Use [/dim][bold]mycelos connector setup <id>[/bold]"
            "[dim] to configure one, or open the Connectors page in the web UI.[/dim]"
        )


def _show_connector_menu(app: App) -> None:
    """Show interactive connector selection menu."""
    console.print(f"\n[bold]{t('connector.available_title')}[/bold]\n")

    available: list[tuple[str, dict[str, Any]]] = []
    configured = app.credentials.list_services()

    for i, (key, info) in enumerate(CONNECTORS.items(), 1):
        if info.get("coming_soon"):
            status = "[dim](coming soon)[/dim]"
        elif not info["requires_key"]:
            status = "[green](ready, no key needed)[/green]"
        elif f"connector:{key}" in configured:
            status = "[green](configured)[/green]"
        else:
            status = "[yellow](not configured)[/yellow]"

        console.print(f"  ({i}) {info['name']}  {status}")
        console.print(f"      [dim]{info['description']}[/dim]")
        available.append((key, info))

    console.print()
    choice = click.prompt(
        "Which connector to set up? (number or 'q' to quit)",
        default="q",
    )

    if choice.lower() == "q":
        return

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(available):
            key, info = available[idx]
            if info.get("coming_soon"):
                console.print(f"\n[yellow]{t('connector.coming_soon', name=info['name'])}[/yellow]")
                return
            _setup_connector(app, key, info)
        else:
            console.print(f"[red]{t('connector.invalid_selection')}[/red]")
    except ValueError:
        console.print(f"[red]{t('connector.invalid_input')}[/red]")


def _setup_connector(app: App, key: str, info: dict[str, Any]) -> None:
    """Set up a specific connector."""
    if info.get("setup_type") == "telegram":
        _setup_telegram_connector(app, key, info)
        return

    console.print(f"\n[bold]{t('connector.setup_title', name=info['name'])}[/bold]")
    console.print(f"[dim]{info['description']}[/dim]\n")

    # Register connector in DB
    app.connector_registry.register(
        connector_id=key,
        name=info["name"],
        connector_type=info["category"],
        capabilities=info["capabilities"],
        description=info.get("description"),
        setup_type="key" if info["requires_key"] else info.get("setup_type", "none"),
    )

    if not info["requires_key"]:
        console.print(
            f"[green]{t('connector.no_key_needed')}[/green]"
        )
        # Set policy for capabilities
        for cap in info["capabilities"]:
            app.policy_engine.set_policy("default", None, cap, "always")
        app.audit.log(
            "connector.setup",
            details={"connector": key, "capabilities": info["capabilities"]},
        )
        console.print(t("connector.capabilities_enabled", caps=', '.join(info['capabilities'])))

        # Create new config generation
        app.config.apply_from_state(
            state_manager=app.state_manager,
            description=f"Connector '{info['name']}' eingerichtet",
            trigger="connector_setup",
        )

        console.print(f"\n[green]{t('connector.ready', name=info['name'])}[/green]")
        return

    # Connector needs an API key
    console.print(
        f"[yellow]{info.get('key_help', t('connector.key_help'))}[/yellow]\n"
    )

    # Check if already configured — MCP connector credentials live under
    # the bare connector id now; we still check the legacy prefixed key
    # so configs stored before the migration are detected.
    service_name = key
    existing = (
        app.credentials.get_credential(service_name)
        or app.credentials.get_credential(f"connector:{key}")
    )
    if existing:
        console.print(f"[green]{t('connector.already_configured')}[/green]")
        if not click.confirm(t("connector.reconfigure"), default=False):
            return

    # Ask for API key
    api_key = click.prompt(
        f"Enter your {info['name']} API key", hide_input=True
    )

    # Store encrypted
    app.credentials.store_credential(
        service_name,
        {
            "api_key": api_key,
            "env_var": info.get("env_var", ""),
            "connector": key,
        },
    )

    # Set policy for capabilities
    for cap in info["capabilities"]:
        app.policy_engine.set_policy("default", None, cap, "always")

    app.audit.log(
        "connector.setup",
        details={"connector": key, "capabilities": info["capabilities"]},
    )

    console.print(f"\n[green]{t('connector.configured', name=info['name'])}[/green]")
    console.print(f"[dim]{t('connector.key_encrypted')}[/dim]")
    console.print(t("connector.capabilities_enabled", caps=', '.join(info['capabilities'])))

    # Create new config generation
    app.config.apply_from_state(
        state_manager=app.state_manager,
        description=f"Connector '{info['name']}' eingerichtet",
        trigger="connector_setup",
    )

    # Test the connector
    if click.confirm(f"\n{t('connector.test_prompt')}", default=True):
        _test_connector(app, key, info, api_key)


def _test_connector(
    app: App, key: str, info: dict[str, Any], api_key: str
) -> None:
    """Run a quick test of the connector."""
    console.print(f"\n[dim]{t('connector.testing')}[/dim]")

    if key == "web-search-brave":
        try:
            import httpx

            resp = httpx.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": "hello world", "count": 1},
                headers={
                    "X-Subscription-Token": api_key,
                    "Accept": "application/json",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("web", {}).get("results", [])
                if results:
                    console.print(
                        f"[green]{t('connector.success')}[/green] {t('connector.found', result=results[0].get('title', 'result'))}"
                    )
                else:
                    console.print(f"[green]{t('connector.success')}[/green] {t('connector.api_responded')}")
            elif resp.status_code == 401:
                console.print(f"[red]{t('connector.api_key_invalid')}[/red] {t('connector.brave_key_check')}")
            else:
                console.print(f"[yellow]{t('connector.api_status', status=resp.status_code)}[/yellow]")
        except Exception as e:
            console.print(f"[red]{t('connector.test_failed', error=e)}[/red]")

    elif key == "web-search-duckduckgo":
        try:
            from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                results = list(ddgs.text("hello world", max_results=1))
            if results:
                console.print(
                    f"[green]{t('connector.success')}[/green] {t('connector.found', result=results[0].get('title', 'result'))}"
                )
            else:
                console.print(f"[green]{t('connector.success')}[/green] {t('connector.search_works')}")
        except Exception as e:
            console.print(f"[red]{t('connector.test_failed', error=e)}[/red]")

    elif key == "http":
        try:
            import httpx

            resp = httpx.get("https://example.com", timeout=10)
            console.print(f"[green]{t('connector.success')}[/green] {t('connector.http_status', status=resp.status_code)}")
        except Exception as e:
            console.print(f"[red]{t('connector.test_failed', error=e)}[/red]")

    elif key == "github":
        try:
            import httpx

            resp = httpx.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                console.print(
                    f"[green]{t('connector.success')}[/green] "
                    f"Authenticated as [bold]{data.get('login', '?')}[/bold]"
                )
            elif resp.status_code == 401:
                console.print(f"[red]Token invalid or expired. Check your token at github.com/settings/tokens[/red]")
            else:
                console.print(f"[yellow]GitHub API returned status {resp.status_code}[/yellow]")
        except Exception as e:
            console.print(f"[red]{t('connector.test_failed', error=e)}[/red]")

    else:
        console.print(f"[yellow]No test available for '{key}'. Connector registered.[/yellow]")


def _collect_telegram_allowlist(token: str) -> list[int]:
    """Collect Telegram user IDs for the allowlist.

    Primary: auto-detect via getUpdates (user sends /start to bot).
    Fallback: manual ID entry.
    At least one user ID is required (fail-closed).
    """
    import httpx
    import time

    allowed_users: list[int] = []

    console.print(
        "\n[bold]Access control[/bold]\n"
        "Your bot needs to know who may use it.\n"
    )

    # Verify token first and get bot username
    bot_username = None
    try:
        resp = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        data = resp.json()
        if data.get("ok"):
            bot_username = data["result"].get("username", "your bot")
            console.print(f"[green]Bot verified:[/green] @{bot_username}\n")
    except Exception:
        pass

    # Clear pending updates so we only see fresh ones
    try:
        httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", params={"offset": -1, "limit": 1}, timeout=10)
    except Exception:
        pass

    # Ask user to send /start
    bot_ref = f"@{bot_username}" if bot_username else "your bot"
    console.print(f"  Send [bold]/start[/bold] to {bot_ref} in Telegram, then press [bold]Enter[/bold].")
    console.print(f"  [dim](This auto-detects your user ID — no manual lookup needed)[/dim]\n")
    click.pause("  Waiting...")

    # Poll getUpdates for new messages
    console.print("  [dim]Checking for messages...[/dim]")
    try:
        resp = httpx.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"timeout": 5, "limit": 100},
            timeout=15,
        )
        data = resp.json()
        if data.get("ok"):
            seen: set[int] = set()
            for update in data.get("result", []):
                msg = update.get("message") or update.get("my_chat_member", {}).get("from")
                if msg:
                    user = msg.get("from", msg) if isinstance(msg, dict) and "from" in msg else msg
                    uid = user.get("id")
                    name = user.get("first_name", str(uid))
                    if uid and uid not in seen:
                        seen.add(uid)
                        allowed_users.append(uid)
                        console.print(f"  [green]Found:[/green] {name} (ID: {uid})")
    except Exception as e:
        console.print(f"  [yellow]Could not fetch updates: {e}[/yellow]")

    if allowed_users:
        console.print(f"\n  [green]Added {len(allowed_users)} user(s) to allowlist.[/green]")
        # Offer to add more
        if click.confirm("\n  Add more users?", default=False):
            _add_manual_ids(allowed_users)
    else:
        # Fallback: manual entry
        console.print(
            "\n  [yellow]No messages found.[/yellow]\n"
            "  Enter your Telegram user ID manually.\n"
            "  [dim](Find it by messaging @userinfobot in Telegram)[/dim]"
        )
        _add_manual_ids(allowed_users, required=True)

    return allowed_users


def _add_manual_ids(allowed_users: list[int], required: bool = False) -> None:
    """Prompt for manual Telegram user ID entry."""
    while True:
        ids_str = click.prompt("  Telegram user ID(s) (comma-separated)", default="")
        for part in ids_str.split(","):
            part = part.strip()
            if part:
                try:
                    uid = int(part)
                    if uid not in allowed_users:
                        allowed_users.append(uid)
                        console.print(f"  [green]Added:[/green] {uid}")
                except ValueError:
                    console.print(f"  [yellow]Skipping invalid ID: {part}[/yellow]")
        if allowed_users:
            break
        if required:
            console.print("  [red]At least one user ID is required.[/red]")
        else:
            break


def _setup_telegram_connector(app: App, key: str, info: dict[str, Any]) -> None:
    """Set up Telegram bot — token, allowlist, mode. Writes to channels table (NixOS State)."""
    import json as _json

    console.print(f"\n[bold]{t('connector.setup_title', name=info['name'])}[/bold]")
    console.print(f"[dim]{info['description']}[/dim]\n")
    console.print(f"[yellow]{info['key_help']}[/yellow]\n")

    # Check if already configured
    existing = app.credentials.get_credential("telegram")
    if existing and existing.get("api_key"):
        console.print("[green]Telegram Bot is already configured.[/green]")
        if not click.confirm("Reconfigure?", default=False):
            return

    # Bot token
    token = click.prompt("Bot token", hide_input=True)
    if not token or ":" not in token:
        console.print("[red]Invalid token format. Expected format: 123456:ABC-DEF...[/red]")
        return

    # Allowlist — auto-detect via getUpdates or manual fallback
    allowed_users: list[int] = _collect_telegram_allowlist(token)

    # Mode selection
    console.print(
        "\n[bold]Connection mode[/bold]\n"
        "  (1) [bold]Polling[/bold] (default) — works everywhere, no setup needed\n"
        "  (2) [bold]Webhook[/bold] — needs a public URL (for servers with a domain)"
    )
    mode_choice = click.prompt("Mode", default="1")
    mode = "webhook" if mode_choice == "2" else "polling"

    config: dict[str, Any] = {}
    if mode == "webhook":
        webhook_url = click.prompt("Public webhook URL (e.g. https://mycelos.example.com)")
        config["webhook_url"] = webhook_url
        # Generate a webhook secret
        import secrets
        config["webhook_secret"] = secrets.token_urlsafe(32)

    # Store token encrypted (credentials table)
    app.credentials.store_credential("telegram", {
        "api_key": token,
        "env_var": "TELEGRAM_BOT_TOKEN",
        "provider": "telegram",
    })

    # Write channel config to channels table (NixOS State)
    app.storage.execute("DELETE FROM channels WHERE id = 'telegram'")
    app.storage.execute(
        """INSERT INTO channels (id, channel_type, mode, status, config, allowed_users)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("telegram", "telegram", mode, "active",
         _json.dumps(config), _json.dumps(allowed_users)),
    )

    # Register connector
    app.connector_registry.register(
        connector_id="telegram",
        name="Telegram Bot",
        connector_type="channel",
        capabilities=[],
        description="Chat with Mycelos via Telegram",
        setup_type="channel",
    )

    app.audit.log("connector.setup", details={
        "connector": "telegram",
        "mode": mode,
        "allowed_users": allowed_users,
    })

    # Create new config generation (captures channels table in snapshot)
    app.config.apply_from_state(
        state_manager=app.state_manager,
        description=f"Telegram Bot configured (mode={mode})",
        trigger="connector_setup",
    )

    console.print(f"\n[green]Telegram Bot configured![/green]")
    console.print(f"  Mode: [bold]{mode}[/bold]")
    console.print(f"  Allowed users: {', '.join(str(u) for u in allowed_users)}")
    if mode == "webhook":
        console.print(f"  Webhook: {config['webhook_url']}/telegram/webhook")

    console.print(f"\n[dim]Start the gateway:[/dim]  [bold]mycelos serve[/bold]")
    if mode == "polling":
        console.print(f"[dim]Telegram will start automatically — no webhook setup needed.[/dim]")


def _test_telegram_bot(token: str) -> None:
    """Test Telegram bot token by calling getMe API."""
    import httpx

    console.print("[dim]Checking bot token...[/dim]")
    try:
        resp = httpx.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            bot_info = data["result"]
            name = bot_info.get("first_name", "?")
            username = bot_info.get("username", "?")
            console.print(f"[green]Token valid![/green] Bot: [bold]{name}[/bold] (@{username})")
            console.print(f"[dim]Open Telegram and message @{username} to test.[/dim]")
        else:
            console.print(f"[red]Token invalid: {data.get('description', 'unknown error')}[/red]")
    except Exception as e:
        console.print(f"[red]Connection failed: {e}[/red]")



# ---------------------------------------------------------------------------
# `mycelos connector tools <id>` and `mycelos connector call <id> [tool]`
# ---------------------------------------------------------------------------

def _gateway_url() -> str:
    from mycelos.cli.serve_cmd import DEFAULT_PORT
    return f"http://localhost:{DEFAULT_PORT}"


def _fetch_connector_tools(connector_id: str) -> list[dict]:
    """GET /api/connectors/<id>/tools or raise with a friendly message.

    Talks to the running gateway — needs `mycelos serve` to be up.
    Centralized so both `tools` and `call` share error handling.
    """
    import httpx
    try:
        resp = httpx.get(f"{_gateway_url()}/api/connectors/{connector_id}/tools", timeout=10)
    except httpx.ConnectError as e:
        raise click.ClickException(
            f"Cannot reach the gateway at {_gateway_url()}. "
            "Start it with `mycelos serve` first."
        ) from e
    if resp.status_code == 404:
        raise click.ClickException(f"Unknown connector '{connector_id}'.")
    if resp.status_code >= 400:
        raise click.ClickException(f"Gateway returned {resp.status_code}: {resp.text}")
    body = resp.json()
    return body.get("tools") or []


@connector_cmd.command("tools")
@click.argument("connector_id")
def tools_cmd(connector_id: str) -> None:
    """List the MCP tools exposed by one connector.

    Example:
        mycelos connector tools gmail
    """
    tools = _fetch_connector_tools(connector_id)
    if not tools:
        console.print(
            f"[yellow]Connector '{connector_id}' has no tools registered. "
            "Either the MCP session isn't running, or this connector type "
            "doesn't expose MCP tools (e.g. http, search built-ins).[/yellow]"
        )
        return

    table = Table(title=f"Tools for '{connector_id}'")
    table.add_column("Tool", style="bold")
    table.add_column("Required args")
    table.add_column("Description", overflow="fold")
    table.add_column("Policy", style="dim")

    for tool in tools:
        schema = tool.get("input_schema") or {}
        required = schema.get("required") or []
        req_str = ", ".join(required) if required else "[dim]—[/dim]"
        policy = tool.get("policy") or "default"
        if tool.get("blocked"):
            policy = f"[red]{policy} (blocked)[/red]"
        table.add_row(
            tool["name"],
            req_str,
            (tool.get("description") or "").strip().splitlines()[0] if tool.get("description") else "",
            policy,
        )

    console.print(table)
    console.print(
        f"\n[dim]Use [/dim][bold]mycelos connector call {connector_id} <tool>[/bold]"
        f"[dim] to invoke one interactively.[/dim]"
    )


def _prompt_for_arguments(input_schema: dict) -> dict:
    """Walk the JSON schema's `properties` and ask the user for each.

    Required fields are mandatory; optional ones can be skipped with
    Enter. Numeric / boolean / array types get parsed best-effort.
    """
    properties = input_schema.get("properties") or {}
    required = set(input_schema.get("required") or [])
    if not properties:
        return {}

    args: dict = {}
    console.print("[dim]Press Enter to skip optional arguments.[/dim]")
    for prop_name, prop_def in properties.items():
        is_required = prop_name in required
        prop_type = prop_def.get("type", "string")
        desc = (prop_def.get("description") or "").strip().splitlines()[0]
        default = prop_def.get("default")
        label_bits = [prop_name]
        if is_required:
            label_bits.append("[bold red]*[/bold red]")
        label_bits.append(f"[dim]({prop_type})[/dim]")
        if desc:
            label_bits.append(f"[dim]— {desc[:80]}[/dim]")
        prompt_label = " ".join(label_bits)
        # click.prompt doesn't render Rich markup; use console first.
        console.print(prompt_label)
        raw = click.prompt(
            "  >",
            default="" if not is_required else (str(default) if default is not None else None),
            show_default=False,
        )
        raw = (raw or "").strip()
        if not raw and not is_required:
            continue
        # Type coercion (best-effort). json.JSONDecodeError is a
        # subclass of ValueError, so a single ValueError catch covers
        # both bad ints/floats and malformed JSON for array/object.
        try:
            if prop_type == "integer":
                args[prop_name] = int(raw)
            elif prop_type == "number":
                args[prop_name] = float(raw)
            elif prop_type == "boolean":
                args[prop_name] = raw.lower() in ("true", "1", "yes", "y", "ja", "j")
            elif prop_type in ("array", "object"):
                import json as _json
                args[prop_name] = _json.loads(raw)
            else:
                args[prop_name] = raw
        except ValueError:
            console.print(
                f"[yellow]Could not parse {prop_name} as {prop_type}; "
                "sending as string.[/yellow]"
            )
            args[prop_name] = raw
    return args


@connector_cmd.command("call")
@click.argument("connector_id")
@click.argument("tool_name", required=False)
@click.option(
    "--json",
    "json_args",
    default=None,
    help="JSON-encoded arguments object. Skips interactive prompts.",
)
def call_cmd(connector_id: str, tool_name: str | None, json_args: str | None) -> None:
    """Invoke one MCP tool on a connector.

    \b
    Examples:
        mycelos connector call gmail                                    # pick tool interactively
        mycelos connector call gmail list_labels                        # prompts for args
        mycelos connector call gmail search_threads --json '{"query":"is:unread","pageSize":3}'

    Needs `mycelos serve` to be running.
    """
    import httpx
    import json as _json

    tools = _fetch_connector_tools(connector_id)
    if not tools:
        raise click.ClickException(
            f"Connector '{connector_id}' has no tools available — "
            "is the MCP session running? Try `mycelos connector tools {cid}`."
        )

    # Resolve which tool.
    by_name = {t["name"]: t for t in tools}
    if tool_name is None:
        # Interactive picker.
        console.print(f"\n[bold]Select a tool from '{connector_id}':[/bold]\n")
        for idx, t in enumerate(tools, start=1):
            desc = (t.get("description") or "").strip().splitlines()[0]
            console.print(f"  [bold cyan]{idx:>2}[/bold cyan]  {t['name']}  [dim]— {desc[:80]}[/dim]")
        choice = click.prompt(
            "\nPick a number",
            type=click.IntRange(1, len(tools)),
        )
        tool = tools[choice - 1]
        tool_name = tool["name"]
    else:
        if tool_name not in by_name:
            raise click.ClickException(
                f"Tool '{tool_name}' not found on '{connector_id}'. "
                f"Available: {', '.join(by_name) or '(none)'}"
            )
        tool = by_name[tool_name]

    # Resolve arguments.
    if json_args is not None:
        try:
            arguments = _json.loads(json_args)
        except _json.JSONDecodeError as e:
            raise click.ClickException(f"--json is not valid JSON: {e}") from e
        if not isinstance(arguments, dict):
            raise click.ClickException("--json must be an object")
    else:
        console.print(f"\n[bold]Arguments for {tool_name}:[/bold]")
        arguments = _prompt_for_arguments(tool.get("input_schema") or {})

    # Call.
    console.print(
        f"\n[dim]→ Calling[/dim] [bold]{connector_id}.{tool_name}[/bold]"
        f"[dim]({_json.dumps(arguments)})[/dim]"
    )
    try:
        resp = httpx.post(
            f"{_gateway_url()}/api/connectors/{connector_id}/tools/{tool_name}/call",
            json={"arguments": arguments},
            timeout=120,
        )
    except httpx.ConnectError as e:
        raise click.ClickException(
            f"Cannot reach the gateway at {_gateway_url()}. "
            "Start it with `mycelos serve` first."
        ) from e

    if resp.status_code >= 400:
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        msg = body.get("error") or body.get("detail") or resp.text
        console.print(f"[red]✗ Call failed ({resp.status_code}):[/red] {msg}")
        raise SystemExit(1)

    body = resp.json()
    result = body.get("result")
    console.print("[green]✓ OK[/green]\n")
    # Pretty-print result. MCP tools usually return {content: [{type, text}, ...]}
    if isinstance(result, dict) and "content" in result and isinstance(result["content"], list):
        for block in result["content"]:
            if isinstance(block, dict) and block.get("type") == "text":
                console.print(block.get("text", ""))
            else:
                console.print_json(_json.dumps(block, default=str))
    else:
        console.print_json(_json.dumps(result, default=str))
