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
    "google": {
        "name": "Google (Gmail, Calendar, Drive)",
        "description": "Access Gmail, Calendar, and Drive via gog CLI",
        "requires_key": False,
        "capabilities": [
            "google.gmail.read",
            "google.gmail.send",
            "google.calendar.read",
            "google.drive.read",
        ],
        "category": "google",
        "setup_type": "gog",
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
    """List configured connectors and their status."""
    db_path = data_dir / "mycelos.db"
    if not db_path.exists():
        console.print(f"[red]{t('common.error')}:[/red] {t('connector.not_initialized_short')}")
        raise SystemExit(1)

    if not os.environ.get("MYCELOS_MASTER_KEY"):
        key_file = data_dir / ".master_key"
        if key_file.exists():
            os.environ["MYCELOS_MASTER_KEY"] = key_file.read_text().strip()

    app = App(data_dir)
    configured = app.credentials.list_services()
    registered = {c["id"]: c for c in app.connector_registry.list_connectors()}

    table = Table(title="Connectors")
    table.add_column("Connector", style="bold")
    table.add_column("Status")
    table.add_column("Capabilities")

    for key, info in CONNECTORS.items():
        if info.get("coming_soon"):
            status = "[dim]Coming soon[/dim]"
        elif key in registered:
            status = "[green]Registered[/green]"
        elif not info["requires_key"]:
            status = "[green]Ready[/green] (no key needed)"
        elif f"connector:{key}" in configured:
            status = "[green]Configured[/green]"
        else:
            status = "[yellow]Not configured[/yellow]"
        caps = ", ".join(
            registered[key]["capabilities"] if key in registered else info["capabilities"]
        )
        table.add_row(info["name"], status, caps)

    console.print(table)


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
    if info.get("setup_type") == "gog":
        _setup_gog_connector(app, key, info)
        return
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

    # Check if already configured
    service_name = f"connector:{key}"
    existing = app.credentials.get_credential(service_name)
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


def _setup_gog_connector(app: App, key: str, info: dict[str, Any]) -> None:
    """Set up Google services via gog CLI.

    gog handles OAuth and token management in its own keyring.
    Mycelos agents never see Google credentials.
    """
    from mycelos.connectors.google_tools import (
        get_gog_accounts,
        gmail_search,
        is_gog_installed,
    )

    console.print(f"\n[bold]{t('connector.setup_title', name=info['name'])}[/bold]")
    console.print(
        f"[dim]{t('connector.gog_oauth')}[/dim]\n"
    )

    # Step 1: Check if gog is installed
    if not is_gog_installed():
        console.print(f"[yellow]{t('connector.gog_missing')}[/yellow]")
        console.print(t("connector.gog_install"))
        console.print(f"{t('connector.gog_more_info')}\n")
        if click.confirm("Try to install gog now? (requires Homebrew)"):
            import subprocess

            try:
                console.print(f"[dim]{t('connector.running_brew_install')}[/dim]")
                result = subprocess.run(
                    ["brew", "install", "gogcli"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode == 0:
                    console.print(f"[green]{t('connector.gog_installed')}[/green]")
                else:
                    console.print(
                        f"[red]{t('connector.install_failed', error=result.stderr[:200])}[/red]"
                    )
                    console.print(t("connector.install_manually"))
                    return
            except (FileNotFoundError, subprocess.TimeoutExpired):
                console.print(f"[red]{t('connector.homebrew_missing')}[/red]")
                console.print(t("connector.install_gog_manually"))
                return
        else:
            console.print(t("connector.install_and_retry"))
            return

    # Step 2: Check for connected accounts
    accounts = get_gog_accounts()
    if accounts:
        console.print(f"[green]{t('connector.google_accounts_found', count=len(accounts))}[/green]")
        for acc in accounts:
            console.print(f"  - {acc}")
    else:
        console.print(f"[yellow]{t('connector.google_none')}[/yellow]")
        console.print(
            f"{t('connector.google_connect_with')}\n"
        )
        email = click.prompt(
            "Enter your Gmail address (or 'skip' to do it later)",
            default="skip",
        )
        if email != "skip":
            import subprocess

            console.print(f"\n[dim]{t('connector.running_gog_auth', email=email)}[/dim]")
            console.print(
                f"[dim]{t('connector.browser_signin')}[/dim]\n"
            )
            try:
                subprocess.run(["gog", "auth", "add", email], timeout=120)
                accounts = get_gog_accounts()
                if email in str(accounts):
                    console.print(f"\n[green]{t('connector.account_connected', email=email)}[/green]")
                else:
                    console.print(
                        f"\n[yellow]{t('connector.account_maybe_not_connected', email=email)}[/yellow]"
                    )
                    return
            except (FileNotFoundError, subprocess.TimeoutExpired):
                console.print(
                    f"[red]{t('connector.auth_failed')}[/red]"
                )
                return
        else:
            console.print(
                t("connector.auth_later")
            )
            return

    # Step 3: Register connector in DB
    app.connector_registry.register(
        connector_id=key,
        name=info["name"],
        connector_type=info["category"],
        capabilities=info["capabilities"],
        description=info.get("description"),
        setup_type="gog",
    )

    # Step 4: Set policies for all capabilities
    for cap in info["capabilities"]:
        app.policy_engine.set_policy("default", None, cap, "always")
    app.audit.log(
        "connector.setup",
        details={"connector": key, "capabilities": info["capabilities"]},
    )
    console.print(
        f"\n{t('connector.capabilities_enabled', caps=', '.join(info['capabilities']))}"
    )

    # Step 4: Test Gmail access
    if click.confirm("\nTest Gmail access now?", default=True):
        console.print(f"[dim]{t('connector.searching_emails')}[/dim]")
        result = gmail_search("newer_than:1d", max_results=3)
        if "error" in result:
            console.print(f"[red]{t('connector.test_failed', error=result['error'])}[/red]")
        else:
            threads = result.get("threads", result.get("messages", []))
            if isinstance(threads, list):
                console.print(
                    f"[green]{t('connector.gmail_success_count', count=len(threads))}[/green]"
                )
            else:
                console.print(
                    f"[green]{t('connector.gmail_success')}[/green]"
                )

    # Create new config generation
    app.config.apply_from_state(
        state_manager=app.state_manager,
        description=f"Connector '{info['name']}' eingerichtet",
        trigger="connector_setup",
    )

    console.print(f"\n[green]{t('connector.ready', name=info['name'])}[/green]")
    console.print(
        f"[dim]{t('connector.gog_credentials_safe')}[/dim]"
    )
