"""mycelos credential — manage encrypted credentials.

Usage:
    mycelos credential list                # Show stored services
    mycelos credential store <service>     # Store/update a credential
    mycelos credential delete <service>    # Delete a credential
    mycelos credential test <service>      # Quick test that the key works
"""

from __future__ import annotations

import os
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from mycelos.app import App
from mycelos.i18n import t

console = Console()

# Known services with hints
_SERVICE_HINTS: dict[str, dict[str, str]] = {
    "anthropic": {
        "name": "Anthropic (Claude)",
        "env_var": "ANTHROPIC_API_KEY",
        "help": "Get an API key at https://console.anthropic.com/settings/keys",
    },
    "openai": {
        "name": "OpenAI (GPT, Whisper)",
        "env_var": "OPENAI_API_KEY",
        "help": "Get an API key at https://platform.openai.com/api-keys",
    },
    "openrouter": {
        "name": "OpenRouter",
        "env_var": "OPENROUTER_API_KEY",
        "help": "Get an API key at https://openrouter.ai/keys",
    },
    "gemini": {
        "name": "Google Gemini",
        "env_var": "GEMINI_API_KEY",
        "help": "Get an API key at https://aistudio.google.com/app/apikey",
    },
    "brave": {
        "name": "Brave Search",
        "env_var": "BRAVE_API_KEY",
        "help": "Get a free key at https://brave.com/search/api/ (2000 queries/month free)",
    },
    "telegram": {
        "name": "Telegram Bot",
        "env_var": "TELEGRAM_BOT_TOKEN",
        "help": "Create a bot at @BotFather in Telegram",
    },
    "github": {
        "name": "GitHub",
        "env_var": "GITHUB_PERSONAL_ACCESS_TOKEN",
        "help": "Create a PAT at https://github.com/settings/tokens",
    },
}


def _get_app(data_dir: Path) -> App:
    """Get initialized App instance."""
    if not (data_dir / "mycelos.db").exists():
        console.print(f"[red]{t('credential.not_initialized')}[/red]")
        raise SystemExit(1)
    master_key = os.environ.get("MYCELOS_MASTER_KEY")
    if not master_key:
        key_file = data_dir / ".master_key"
        if key_file.exists():
            os.environ["MYCELOS_MASTER_KEY"] = key_file.read_text().strip()
        else:
            console.print(f"[red]{t('credential.master_key_missing')}[/red]")
            raise SystemExit(1)
    app = App(data_dir)
    return app


@click.group()
def credential_cmd() -> None:
    """Manage encrypted credentials (API keys, tokens)."""
    pass


@credential_cmd.command("list")
@click.option("--data-dir", type=click.Path(path_type=Path),
              default=Path.home() / ".mycelos")
def list_cmd(data_dir: Path) -> None:
    """List all stored credentials."""
    app = _get_app(data_dir)
    services = app.credentials.list_services()

    if not services:
        console.print(f"[dim]{t('credential.no_credentials')}[/dim]")
        return

    table = Table(title="Stored Credentials")
    table.add_column("Service", style="cyan")
    table.add_column("Name", style="white")
    table.add_column("Key Preview", style="dim")

    for service in sorted(services):
        hint = _SERVICE_HINTS.get(service, {})
        name = hint.get("name", service)
        # Show first/last chars of key for identification
        cred = app.credentials.get_credential(service)
        if cred and cred.get("api_key"):
            key = cred["api_key"]
            preview = f"{key[:4]}...{key[-4:]}" if len(key) > 12 else "****"
        else:
            preview = "[dim]empty[/dim]"
        table.add_row(service, name, preview)

    console.print(table)


@credential_cmd.command("store")
@click.argument("service")
@click.option("--data-dir", type=click.Path(path_type=Path),
              default=Path.home() / ".mycelos")
def store_cmd(service: str, data_dir: Path) -> None:
    """Store or update a credential."""
    app = _get_app(data_dir)
    hint = _SERVICE_HINTS.get(service, {})

    name = hint.get("name", service)
    console.print(f"\n[bold]{name}[/bold]")
    if hint.get("help"):
        console.print(f"[dim]{hint['help']}[/dim]")

    # Check if already stored
    existing = app.credentials.get_credential(service)
    if existing and existing.get("api_key"):
        key = existing["api_key"]
        console.print(f"[yellow]Already stored:[/yellow] {key[:4]}...{key[-4:]}")
        if not click.confirm("Overwrite?"):
            return

    # Prompt for key
    api_key = click.prompt("API key", hide_input=True)
    if not api_key.strip():
        console.print(f"[red]{t('credential.empty_key')}[/red]")
        return

    api_key = api_key.strip()
    env_var = hint.get("env_var", f"{service.upper()}_API_KEY")

    # Test the key if it's an LLM provider
    _LLM_PROVIDERS = {"anthropic", "openai", "openrouter", "gemini"}
    if service in _LLM_PROVIDERS:
        console.print(f"  {t('credential.testing_key')}")
        os.environ[env_var] = api_key
        try:
            from mycelos.cli.init_cmd import _check_connectivity
            from mycelos.llm.broker import LiteLLMBroker

            # Pick a cheap model for testing
            test_models = {
                "anthropic": "anthropic/claude-haiku-4-5",
                "openai": "openai/gpt-4o-mini",
                "openrouter": "openrouter/anthropic/claude-haiku-4-5",
                "gemini": "gemini/gemini-2.5-flash",
            }
            broker = LiteLLMBroker(default_model=test_models.get(service, f"{service}/default"))
            success, msg = _check_connectivity(broker)
            if success:
                console.print(f"  [green]✓ {t('credential.key_works')}[/green]")
            else:
                console.print(f"  [red]✗ {t('credential.key_test_failed', error=msg[:100])}[/red]")
                if not click.confirm(f"  {t('credential.store_anyway')}", default=False):
                    del os.environ[env_var]
                    return
        except Exception as e:
            console.print(f"  [yellow]{t('credential.test_error', error=e)}[/yellow]")

    app.credentials.store_credential(service, {
        "api_key": api_key,
        "env_var": env_var,
    })
    app.audit.log("credential.stored", details={"service": service})
    console.print(f"[green]✓ {t('credential.stored', service=service)}[/green]")


@credential_cmd.command("delete")
@click.argument("service")
@click.option("--data-dir", type=click.Path(path_type=Path),
              default=Path.home() / ".mycelos")
def delete_cmd(service: str, data_dir: Path) -> None:
    """Delete a stored credential."""
    app = _get_app(data_dir)

    existing = app.credentials.get_credential(service)
    if not existing:
        console.print(f"[yellow]No credential found for '{service}'.[/yellow]")
        return

    if click.confirm(f"Delete credential for '{service}'?"):
        app.credentials.delete_credential(service)
        app.audit.log("credential.deleted", details={"service": service})
        console.print(f"[green]Credential '{service}' deleted.[/green]")


@credential_cmd.command("export")
@click.option("--data-dir", type=click.Path(path_type=Path),
              default=Path.home() / ".mycelos")
@click.option("--output", "-o", type=click.Path(path_type=Path),
              default=None, help="Output file (default: ./mycelos-credentials.json)")
def export_cmd(data_dir: Path, output: Path | None) -> None:
    """Export all credentials to a JSON file (UNENCRYPTED).

    WARNING: The export file contains plaintext API keys.
    Store it safely and delete after import.
    """
    import json

    app = _get_app(data_dir)
    services = app.credentials.list_services()

    if not services:
        console.print("[dim]No credentials to export.[/dim]")
        return

    console.print(
        "[bold yellow]WARNING:[/bold yellow] This exports credentials as "
        "[bold red]plaintext[/bold red]. The file will contain your API keys unencrypted.\n"
        "Store it safely and delete after import."
    )
    if not click.confirm("Continue with export?"):
        return

    dump: dict[str, dict] = {}
    for service in services:
        cred = app.credentials.get_credential(service)
        if cred:
            dump[service] = cred

    out_path = output or Path("mycelos-credentials.json")
    # Write with restricted permissions atomically (no world-readable window)
    import stat
    fd = os.open(str(out_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as f:
        json.dump(dump, f, indent=2, ensure_ascii=False)

    console.print(f"\n[green]Exported {len(dump)} credential(s) to {out_path}[/green]")
    console.print(f"[dim]File permissions set to 600 (owner only).[/dim]")
    app.audit.log("credential.exported", details={"count": len(dump), "path": str(out_path)})


@credential_cmd.command("import")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--data-dir", type=click.Path(path_type=Path),
              default=Path.home() / ".mycelos")
def import_cmd(file: Path, data_dir: Path) -> None:
    """Import credentials from a JSON export file.

    Re-encrypts all credentials with the current master key.
    """
    import json

    app = _get_app(data_dir)

    try:
        dump = json.loads(file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        console.print(f"[red]Failed to read {file}: {e}[/red]")
        raise SystemExit(1)

    if not isinstance(dump, dict):
        console.print("[red]Invalid format — expected JSON object with service names as keys.[/red]")
        raise SystemExit(1)

    count = 0
    for service, cred in dump.items():
        if not isinstance(cred, dict):
            console.print(f"[yellow]Skipping '{service}' — not a valid credential object.[/yellow]")
            continue
        app.credentials.store_credential(service, cred)
        preview = ""
        if "api_key" in cred:
            key = cred["api_key"]
            preview = f" ({key[:4]}...{key[-4:]})" if len(key) > 8 else ""
        console.print(f"  [green]+[/green] {service}{preview}")
        count += 1

    console.print(f"\n[green]Imported {count} credential(s).[/green]")
    console.print(f"[dim]Tip: delete {file} now — it contains plaintext keys.[/dim]")
    app.audit.log("credential.imported", details={"count": count, "source": str(file)})


@credential_cmd.command("services")
def services_cmd() -> None:
    """Show known services and their setup hints."""
    table = Table(title="Known Services")
    table.add_column("Service", style="cyan")
    table.add_column("Name", style="white")
    table.add_column("Help", style="dim")

    for key, info in _SERVICE_HINTS.items():
        table.add_row(key, info["name"], info.get("help", ""))

    console.print(table)
    console.print("\n[dim]You can store any service name, not just these.[/dim]")
