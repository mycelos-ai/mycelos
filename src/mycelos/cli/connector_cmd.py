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
from mycelos.connectors.mcp_recipes import MCPRecipe
from mycelos.i18n import t

console = Console()

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

    if not os.environ.get("MYCELOS_MASTER_KEY"):
        key_file = data_dir / ".master_key"
        if key_file.exists():
            os.environ["MYCELOS_MASTER_KEY"] = key_file.read_text().strip()

    app = App(data_dir)

    if connector_name:
        from mycelos.connectors.mcp_recipes import get_recipe
        recipe = get_recipe(connector_name)
        if recipe is None:
            console.print(
                f"[red]{t('connector.unknown_id', name=connector_name)}[/red]\n"
                f"{t('connector.see_list')}"
            )
            raise SystemExit(1)
        if recipe.kind == "channel":
            _setup_channel(app, recipe)
        else:
            _setup_mcp(app, recipe)
    else:
        _show_connector_menu(app)


@connector_cmd.command("list")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=default_data_dir,
)
def list_cmd(data_dir: Path) -> None:
    """List configured connectors plus available recipes, split by kind."""
    db_path = data_dir / "mycelos.db"
    if not db_path.exists():
        console.print(
            f"[red]{t('common.error')}:[/red] {t('connector.not_initialized_short')}"
        )
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

    # ── Installed ─────────────────────────────────────────────────
    if registered:
        installed = Table(title="Installed connectors")
        installed.add_column("Connector", style="bold")
        installed.add_column("Type", style="dim")
        installed.add_column("State")
        installed.add_column("Capabilities", overflow="fold")
        for c in registered:
            state = state_styles.get(
                c.get("operational_state"), c.get("operational_state") or "?"
            )
            caps = ", ".join(c.get("capabilities") or []) or "[dim]—[/dim]"
            installed.add_row(
                c.get("name") or c["id"],
                c.get("connector_type") or "?",
                state,
                caps,
            )
        console.print(installed)
    else:
        console.print("[dim]No connectors configured yet.[/dim]")

    # ── Available channels ────────────────────────────────────────
    available_channels = [
        r for r in RECIPES.values()
        if r.kind == "channel" and r.id not in registered_ids
    ]
    if available_channels:
        tbl = Table(title="Channels (not yet configured)")
        tbl.add_column("Recipe", style="bold")
        tbl.add_column("Setup", style="dim")
        tbl.add_column("Description", overflow="fold")
        for r in sorted(available_channels, key=lambda x: x.id):
            desc = (r.description or "").splitlines()[0] if r.description else ""
            tbl.add_row(r.id, r.setup_flow or "secret", desc)
        console.print()
        console.print(tbl)

    # ── Available MCP connectors ──────────────────────────────────
    available_mcp = [
        r for r in RECIPES.values()
        if r.kind == "mcp" and r.id not in registered_ids
    ]
    if available_mcp:
        tbl = Table(title="MCP Connectors (not yet configured)")
        tbl.add_column("Recipe", style="bold")
        tbl.add_column("Category", style="dim")
        tbl.add_column("Setup", style="dim")
        tbl.add_column("Description", overflow="fold")
        for r in sorted(available_mcp, key=lambda x: (x.category, x.id)):
            desc = (r.description or "").splitlines()[0] if r.description else ""
            tbl.add_row(r.id, r.category, r.setup_flow or "secret", desc)
        console.print()
        console.print(tbl)

    if available_channels or available_mcp:
        console.print(
            "\n[dim]Use [/dim][bold]mycelos connector setup <id>[/bold]"
            "[dim] to configure one, or open the Connectors page in the web UI.[/dim]"
        )


def _show_connector_menu(app: App) -> None:
    """Show interactive connector selection menu grouped by kind."""
    from mycelos.connectors.mcp_recipes import RECIPES

    console.print(f"\n[bold]{t('connector.available_title')}[/bold]\n")

    entries: list[MCPRecipe] = sorted(
        RECIPES.values(),
        key=lambda r: (0 if r.kind == "channel" else 1, r.category, r.id),
    )

    configured_ids = {c["id"] for c in app.connector_registry.list_connectors()}

    last_kind: str | None = None
    numbered: list[MCPRecipe] = []
    for recipe in entries:
        if recipe.kind != last_kind:
            header = "Channels" if recipe.kind == "channel" else "MCP Connectors"
            console.print(f"\n[bold cyan]{header}[/bold cyan]")
            last_kind = recipe.kind
        numbered.append(recipe)
        idx = len(numbered)
        if recipe.id in configured_ids:
            status = "[green](configured)[/green]"
        elif not recipe.credentials:
            status = "[green](ready, no key needed)[/green]"
        else:
            status = "[yellow](not configured)[/yellow]"
        console.print(f"  ({idx}) {recipe.name}  {status}")
        console.print(f"      [dim]{recipe.description}[/dim]")

    console.print()
    choice = click.prompt(
        "Which connector to set up? (number or 'q' to quit)",
        default="q",
    )
    if choice.lower() == "q":
        return
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(numbered):
            recipe = numbered[idx]
            if recipe.kind == "channel":
                _setup_channel(app, recipe)
            else:
                _setup_mcp(app, recipe)
        else:
            console.print(f"[red]{t('connector.invalid_selection')}[/red]")
    except ValueError:
        console.print(f"[red]{t('connector.invalid_input')}[/red]")


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


def _setup_mcp(app: App, recipe: MCPRecipe) -> None:
    """Set up an MCP-kind recipe — credential prompt, policy grant, registry row."""
    console.print(f"\n[bold]{t('connector.setup_title', name=recipe.name)}[/bold]")
    console.print(f"[dim]{recipe.description}[/dim]\n")

    # OAuth recipes require a browser redirect; no state mutation here —
    # the web UI flow does the registry/credential/audit writes.
    if recipe.setup_flow == "oauth_http":
        console.print(
            f"[yellow]{recipe.name} uses OAuth. Open the Connectors page "
            f"in the web UI and click Setup.[/yellow]"
        )
        return

    app.connector_registry.register(
        connector_id=recipe.id,
        name=recipe.name,
        connector_type=recipe.kind,
        capabilities=list(recipe.capabilities_preview),
        description=recipe.description,
        setup_type=recipe.setup_flow or ("key" if recipe.credentials else "none"),
    )

    if not recipe.credentials:
        console.print(f"[green]{t('connector.no_key_needed')}[/green]")
        for cap in recipe.capabilities_preview:
            app.policy_engine.set_policy("default", None, cap, "always")
        app.audit.log(
            "connector.setup",
            details={"connector": recipe.id, "capabilities": list(recipe.capabilities_preview)},
        )
        app.config.apply_from_state(
            state_manager=app.state_manager,
            description=f"Connector '{recipe.name}' eingerichtet",
            trigger="connector_setup",
        )
        console.print(f"\n[green]{t('connector.ready', name=recipe.name)}[/green]")
        return

    cred = recipe.credentials[0]
    help_text = cred.get("help", "")
    if help_text:
        console.print(f"[yellow]{help_text}[/yellow]\n")

    existing = app.credentials.get_credential(recipe.id)
    if existing:
        console.print(f"[green]{t('connector.already_configured')}[/green]")
        if not click.confirm(t("connector.reconfigure"), default=False):
            return

    api_key = click.prompt(f"Enter your {cred['name']}", hide_input=True)

    app.credentials.store_credential(
        recipe.id,
        {
            "api_key": api_key,
            "env_var": cred["env_var"],
            "connector": recipe.id,
        },
    )

    for cap in recipe.capabilities_preview:
        app.policy_engine.set_policy("default", None, cap, "always")

    app.audit.log(
        "connector.setup",
        details={"connector": recipe.id, "capabilities": list(recipe.capabilities_preview)},
    )

    console.print(f"\n[green]{t('connector.configured', name=recipe.name)}[/green]")
    console.print(f"[dim]{t('connector.key_encrypted')}[/dim]")

    app.config.apply_from_state(
        state_manager=app.state_manager,
        description=f"Connector '{recipe.name}' eingerichtet",
        trigger="connector_setup",
    )

    if click.confirm(f"\n{t('connector.test_prompt')}", default=True):
        _test_connector_by_recipe(app, recipe, api_key)


def _test_connector_by_recipe(app: App, recipe: MCPRecipe, api_key: str) -> None:
    """Lightweight connectivity test dispatched by recipe.id."""
    if recipe.id == "brave-search":
        _test_brave(api_key)
    elif recipe.id == "github":
        _test_github(api_key)
    else:
        console.print(
            f"[yellow]No quick test available for '{recipe.id}'. "
            f"Connector registered.[/yellow]"
        )


def _test_brave(api_key: str) -> None:
    import httpx
    try:
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": "hello world", "count": 1},
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200:
            console.print(f"[green]{t('connector.success')}[/green] {t('connector.api_responded')}")
        elif resp.status_code == 401:
            console.print(f"[red]{t('connector.api_key_invalid')}[/red]")
        else:
            console.print(f"[yellow]{t('connector.api_status', status=resp.status_code)}[/yellow]")
    except Exception as e:
        console.print(f"[red]{t('connector.test_failed', error=e)}[/red]")


def _test_github(api_key: str) -> None:
    import httpx
    try:
        resp = httpx.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            console.print(
                f"[green]{t('connector.success')}[/green] "
                f"Authenticated as [bold]{data.get('login', '?')}[/bold]"
            )
        elif resp.status_code == 401:
            console.print(f"[red]{t('connector.api_key_invalid')}[/red]")
        else:
            console.print(f"[yellow]{t('connector.api_status', status=resp.status_code)}[/yellow]")
    except Exception as e:
        console.print(f"[red]{t('connector.test_failed', error=e)}[/red]")


def _setup_channel(app: App, recipe: MCPRecipe) -> None:
    """Set up a channel-kind recipe. Today only Telegram; other channels
    plug in here by branching on recipe.id."""
    if recipe.id == "telegram":
        # Wrap the legacy dict-shape that _setup_telegram_connector expects.
        info = {
            "name": recipe.name,
            "description": recipe.description,
            "key_help": recipe.credentials[0]["help"] if recipe.credentials else "",
        }
        _setup_telegram_connector(app, recipe.id, info)
        return
    console.print(f"[red]No channel setup handler for '{recipe.id}'.[/red]")
    raise SystemExit(1)


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
