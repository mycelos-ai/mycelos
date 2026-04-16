"""mycelos init — simple setup wizard for non-technical users.

Asks for exactly ONE provider, auto-selects a capable + cheap model,
registers system agents with smart defaults, and hints at `mycelos model`
for advanced configuration.
"""

import logging
import os
import secrets
from pathlib import Path

import click

logger = logging.getLogger("mycelos.init")
from rich.console import Console
from rich.table import Table

from mycelos.app import App
from mycelos.i18n import t
from mycelos.llm.providers import (
    PROVIDERS,
    ModelInfo,
    ProviderConfig,
    get_provider_models,
)
from mycelos.llm.smart_defaults import compute_smart_defaults
from mycelos.llm.validation import validate_model_config
from mycelos.setup import SYSTEM_AGENTS

console = Console()


@click.command()
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=Path.home() / ".mycelos",
    help="Data directory for Mycelos",
)
def init_cmd(data_dir: Path) -> None:
    """Initialize Mycelos — quick setup in under a minute."""
    console.print()
    console.print(f"[bold]{t('init.welcome')}[/bold]")
    console.print(f"[dim]{t('init.subtitle')}[/dim]\n")

    # Check if already initialized
    db_path = data_dir / "mycelos.db"
    if db_path.exists():
        console.print(f"[yellow]{t('init.already_initialized', path=data_dir)}[/yellow]")
        if not click.confirm(t("init.reinitialize"), default=False):
            return

    # Step 1: Master Key
    _setup_master_key(data_dir)

    # Step 2: Initialize DB
    app = App(data_dir)
    app.initialize()

    # Step 3: Auto-detect provider from key/URL
    console.print(
        f"[bold]{t('init.provider_intro')}[/bold]\n"
        f"[dim]{t('init.provider_intro_detail')}[/dim]\n"
    )
    provider, api_key = _auto_detect_or_pick(app)
    if provider is None:
        console.print(f"[yellow]{t('init.no_provider_selected')}[/yellow]")
        _finalize(app, data_dir, [])
        return

    # Step 4: Auto-select best capable + cheap model for this provider
    all_models = _auto_select_models(provider)
    if not all_models:
        console.print(f"[yellow]{t('init.no_models_for', provider=provider.name)}[/yellow]")
        _finalize(app, data_dir, [])
        return

    # Step 5: Register models
    for model in all_models:
        app.model_registry.add_model(
            model_id=model.id,
            provider=model.provider,
            tier=model.tier,
            input_cost_per_1k=model.input_cost_per_1k,
            output_cost_per_1k=model.output_cost_per_1k,
            max_context=model.max_context,
        )

    # Step 6: Quick plausibility check (no interactive prompts)
    report = validate_model_config(all_models)
    _show_model_summary(all_models, report)

    # Step 7: Register agents + smart defaults (automatic, no user interaction)
    _register_system_agents(app)
    defaults = compute_smart_defaults(all_models)
    _apply_defaults(app, defaults)

    # Step 8: Connectivity check — retry loop if key is invalid
    connection_ok = False
    max_retries = 3
    for attempt in range(max_retries):
        console.print(f"\n  {t('init.testing_connection')}")
        try:
            from mycelos.llm.broker import LiteLLMBroker
            broker = LiteLLMBroker(
                default_model=all_models[0].id if all_models else provider.default_model,
                credential_proxy=app.credentials,
                storage=app.storage,
            )
            success, msg = _check_connectivity(broker)
            if success:
                console.print(f"  [green]✓ {t('init.connection_ok')}[/green]")
                connection_ok = True
                break
            else:
                # Clean up the error message
                clean_msg = msg
                import re
                for pattern in [r'"message":"([^"]+)"', r'Error:\s*(.+?)(?:\.|$)']:
                    match = re.search(pattern, msg)
                    if match:
                        clean_msg = match.group(1)
                        break
                console.print(f"\n  [bold red]✗ {t('init.connection_failed', error=clean_msg)}[/bold red]")

                if attempt < max_retries - 1:
                    console.print(f"    {t('init.connection_key_invalid')}")
                    retry = click.confirm(f"    {t('init.connection_retry')}", default=True)
                    if retry:
                        new_key = click.prompt(f"    {t('init.connection_new_key')}", hide_input=False)
                        new_key = new_key.strip()
                        if new_key:
                            app.credentials.store_credential(provider.id, {
                                "api_key": new_key,
                                "env_var": provider.env_var,
                            })
                            if provider.env_var:
                                os.environ[provider.env_var] = new_key
                            continue
                    break
                else:
                    console.print(f"    {t('init.connection_fix_later', provider_id=provider.id)}")
        except Exception as e:
            console.print(f"  [yellow]✗ {t('init.connection_error', error=e)}[/yellow]")
            break

    # Filesystem permissions intentionally skipped — users grant access later
    # via `mycelos connector` / web UI when actually needed. Makes init fully
    # non-interactive after the API key prompt and works identically in Docker
    # (where host paths are mounts anyway).

    # Step 9: Built-in Connectors + Workflows
    _register_builtin_connectors(app)
    _import_seed_workflows(app)

    # Finalize
    _finalize(app, data_dir, all_models, connection_ok=connection_ok)


def _pick_provider(app: App) -> tuple[ProviderConfig | None, str | None]:
    """Let user pick one LLM provider and enter the API key."""
    console.print(f"[bold]{t('init.provider_pick')}[/bold]\n")

    provider_list = list(PROVIDERS.values())
    for i, p in enumerate(provider_list, 1):
        key_info = f"(API Key: {p.env_var})" if p.requires_key else "(no key needed)"
        console.print(f"  ({i}) {p.name}  [dim]{key_info}[/dim]")

    console.print()
    choice = click.prompt(t("init.provider_label"), default="1")

    try:
        idx = int(choice.strip()) - 1
        if not (0 <= idx < len(provider_list)):
            return None, None
    except ValueError:
        return None, None

    provider = provider_list[idx]

    # Handle API key
    if provider.requires_key:
        api_key = _get_api_key(app, provider)
        if not api_key:
            return None, None
        return provider, api_key

    if provider.id == "ollama":
        # For Ollama we don't need a key but return the provider
        return provider, None

    return provider, None


def _auto_detect_or_pick(app: App) -> tuple[ProviderConfig | None, str | None]:
    """Auto-detect provider from key prefix, fall back to manual selection."""
    from mycelos.cli.detect_provider import detect_provider

    console.print(f"[bold]{t('init.api_key_prompt')}[/bold]")
    input_str = click.prompt("", hide_input=False)

    detection = detect_provider(input_str)

    if detection.provider and not detection.is_url:
        # Key detected — store credential
        api_key = input_str.strip()
        console.print(f"  [green]✓ {t('init.provider_detected', provider=detection.provider.title())}[/green]")
        app.credentials.store_credential(detection.provider, {
            "api_key": api_key,
            "env_var": detection.env_var,
        })
        # Set env var so LiteLLMBroker can find it for connectivity check
        if detection.env_var:
            os.environ[detection.env_var] = api_key
        app.audit.log("credential.stored", details={"service": detection.provider})
        # Find the matching ProviderConfig
        provider = PROVIDERS.get(detection.provider)
        return provider, api_key

    elif detection.is_url:
        # Ollama URL
        console.print(f"  [green]✓ {t('init.provider_detected_url', url=detection.server_url)}[/green]")
        console.print(f"  [dim]{t('init.ollama_model_discovery')}[/dim]")
        # Store as ollama config
        app.memory.set("default", "system", "ollama_url", detection.server_url)
        provider = PROVIDERS.get("ollama")
        return provider, None

    else:
        # Unknown — fall back to manual selection
        console.print(f"  [yellow]{t('init.provider_unknown')}[/yellow]")
        return _pick_provider(app)


def _check_connectivity(broker) -> tuple[bool, str]:
    """Send a tiny test prompt to verify the API works."""
    try:
        response = broker.complete([
            {"role": "user", "content": "Say 'hello' in one word."}
        ])
        if response and response.content:
            return True, response.content.strip()
    except Exception as e:
        return False, str(e)[:200]
    return False, "No response"


def _get_api_key(app: App, provider: ProviderConfig) -> str | None:
    """Get API key from env or prompt user."""
    env_var = provider.env_var
    existing_key = os.environ.get(env_var) if env_var else None

    if existing_key:
        console.print(f"[green]{t('init.env_found', env_var=env_var)}[/green]")
        api_key = existing_key
    else:
        console.print(f"[yellow]{t('init.env_missing', env_var=env_var)}[/yellow]")
        api_key = click.prompt(
            t("init.api_key_enter", provider=provider.name),
            default="skip",
            hide_input=True,
        )
        if api_key == "skip":
            return None
        api_key = api_key.strip()  # Strip trailing whitespace from paste
        os.environ[env_var] = api_key

    # Store encrypted
    app.credentials.store_credential(provider.id, {
        "api_key": api_key,
        "env_var": env_var,
        "provider": provider.id,
    })
    app.audit.log("credential.stored", details={"service": provider.id})
    return api_key


def _auto_select_models(provider: ProviderConfig) -> list[ModelInfo]:
    """Auto-select the best capable (sonnet) + cheap (haiku) model for a provider.

    For Ollama, delegates to the Ollama discovery flow.
    """
    if provider.id == "ollama":
        return _setup_ollama(provider)

    models = get_provider_models(provider.id)
    if not models:
        return []

    # Pick one model per tier: opus (powerful), sonnet (balanced), haiku (fast & cheap)
    selected: list[ModelInfo] = []
    seen_tiers: set[str] = set()

    for tier in ("opus", "sonnet", "haiku"):
        model = next((m for m in models if m.tier == tier), None)
        if model and model.id not in {s.id for s in selected}:
            selected.append(model)
            seen_tiers.add(tier)

    # Fallback: if no tier match, just take the first model
    if not selected and models:
        selected.append(models[0])

    return selected


def _setup_ollama(provider: ProviderConfig) -> list[ModelInfo]:
    """Set up Ollama: check connection + discover local models."""
    from mycelos.llm.ollama import (
        classify_ollama_tier,
        discover_ollama_models,
        is_ollama_running,
    )

    url = click.prompt(t("init.ollama_url_label"), default=provider.default_url or "http://localhost:11434")

    if not is_ollama_running(url):
        console.print(f"[yellow]{t('init.ollama_unreachable', url=url)}[/yellow]")
        return []

    console.print(f"[green]{t('init.ollama_running')}[/green]")
    ollama_models = discover_ollama_models(url)

    if not ollama_models:
        console.print(f"[yellow]{t('init.ollama_no_models')}[/yellow]")
        return []

    models: list[ModelInfo] = []
    for m in ollama_models:
        tier = classify_ollama_tier(m)
        models.append(ModelInfo(
            id=m.id, name=m.name, tier=tier, provider="ollama",
            input_cost_per_1k=0.0, output_cost_per_1k=0.0,
        ))
    return models


def _show_model_summary(
    models: list[ModelInfo], report: object,
) -> None:
    """Show a brief summary of what was auto-configured."""
    console.print(f"\n[bold]{t('init.models_title')}[/bold]\n")

    for m in models:
        tier_label = {"opus": "powerful", "sonnet": "capable", "haiku": "fast & cheap"}.get(m.tier, m.tier)
        console.print(f"  [green]{m.name}[/green]  [dim]({tier_label})[/dim]")

    # Show warnings from validation (but don't ask interactive questions)
    if hasattr(report, "issues") and report.issues:
        console.print()
        for issue in report.issues:
            if issue.level == "warning":
                console.print(f"  [yellow]{issue.message}[/yellow]")
                if issue.suggestion:
                    console.print(f"    [dim]{issue.suggestion}[/dim]")


def _register_system_agents(app: App) -> None:
    """Register the built-in system agents."""
    for agent in SYSTEM_AGENTS:
        existing = app.agent_registry.get(agent["id"])
        if existing is None:
            app.agent_registry.register(
                agent["id"], agent["name"], agent["agent_type"],
                agent["capabilities"], "system",
            )
            app.agent_registry.set_status(agent["id"], "active")


def _apply_defaults(app: App, defaults: dict[str, list[str]]) -> None:
    """Write model assignments to the model registry."""
    system_defaults: dict[str, list[str]] = {}
    agent_assignments: dict[str, dict[str, list[str]]] = {}

    for role, model_ids in defaults.items():
        if not model_ids:
            continue
        parts = role.split(":", 1)
        agent = parts[0]
        purpose = parts[1] if len(parts) > 1 else "execution"

        if agent == "system":
            system_defaults[purpose] = model_ids
        else:
            if agent not in agent_assignments:
                agent_assignments[agent] = {}
            agent_assignments[agent][purpose] = model_ids

    if system_defaults:
        app.model_registry.set_system_defaults(system_defaults)

    for agent_id, purposes in agent_assignments.items():
        for purpose, model_ids in purposes.items():
            app.model_registry.set_agent_models(agent_id, model_ids, purpose)


def _register_builtin_connectors(app: App) -> None:
    """Register DuckDuckGo and HTTP as built-in connectors."""
    try:
        app.connector_registry.register(
            "web-search-duckduckgo", "DuckDuckGo", "search",
            ["search.web", "search.news"],
            description="Search the web -- no API key needed",
            setup_type="none",
        )
        app.policy_engine.set_policy("default", None, "search.web", "always")
        app.policy_engine.set_policy("default", None, "search.news", "always")
    except Exception as e:
        logger.debug("Failed to register DuckDuckGo connector: %s", e, exc_info=True)

    try:
        app.connector_registry.register(
            "http", "HTTP", "http",
            ["http.get", "http.post"],
            description="Fetch web pages and call APIs",
            setup_type="none",
        )
        app.policy_engine.set_policy("default", None, "http.get", "always")
        app.policy_engine.set_policy("default", None, "http.post", "always")
    except Exception as e:
        logger.debug("Failed to register HTTP connector: %s", e, exc_info=True)

    # Default policies for safe tools
    for tool in ("note.write", "note.read", "note.search", "note.list", "note.update", "note.link"):
        app.policy_engine.set_policy("default", None, tool, "always")

    app.audit.log("connectors.builtin_registered")


def _setup_filesystem_permissions(app: App, data_dir: Path) -> None:
    """Ask the user about filesystem access permissions.

    Security-first: nothing is accessible by default.
    The user explicitly grants read/write access to directories.
    """
    console.print(f"\n[bold]{t('init.filesystem_title')}[/bold]")
    console.print(f"  {t('init.filesystem_intro')}\n")

    # Suggest working directory
    home = Path.home()
    suggested_workdir = data_dir / "workspace"
    console.print(f"  [cyan]{t('init.filesystem_workdir_label')}[/cyan] — {t('init.filesystem_workdir_desc')}")
    console.print(f"  {t('init.filesystem_workdir_suggested', path=suggested_workdir)}")

    workdir_input = click.prompt(
        t("init.filesystem_workdir_prompt"),
        default=str(suggested_workdir),
    ).strip()
    workdir = Path(workdir_input).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    # Mount working directory with full access
    try:
        app.mount_registry.add(str(workdir), "read_write")
        console.print(f"  [green]✓[/green] {t('init.filesystem_workdir_set', path=workdir)}")
    except Exception as e:
        logger.debug("Failed to mount working directory %s: %s", workdir, e, exc_info=True)

    # Offer additional read access
    console.print(f"\n  [cyan]{t('init.filesystem_additional_label')}[/cyan] — {t('init.filesystem_additional_desc')}")
    console.print(f"  {t('init.filesystem_additional_explain')}")
    console.print(f"  [dim]{t('init.filesystem_additional_warn')}[/dim]\n")

    if click.confirm(t("init.filesystem_grant_home"), default=False):
        try:
            app.mount_registry.add(str(home), "read")
            console.print(f"  [green]✓[/green] {t('init.filesystem_home_set', path=home)}")
        except Exception as e:
            logger.debug("Failed to mount home %s: %s", home, e, exc_info=True)
    else:
        # Offer specific folders
        console.print(f"  [dim]{t('init.filesystem_add_later')}[/dim]")
        if click.confirm(t("init.filesystem_grant_documents"), default=False):
            docs = home / "Documents"
            if docs.exists():
                try:
                    app.mount_registry.add(str(docs), "read")
                    console.print(f"  [green]✓[/green] {t('init.filesystem_documents_set', path=docs)}")
                except Exception as e:
                    logger.debug("Failed to mount Documents %s: %s", docs, e, exc_info=True)

        if click.confirm(t("init.filesystem_grant_downloads"), default=False):
            downloads = home / "Downloads"
            if downloads.exists():
                try:
                    app.mount_registry.add(str(downloads), "read")
                    console.print(f"  [green]✓[/green] {t('init.filesystem_downloads_set', path=downloads)}")
                except Exception as e:
                    logger.debug("Failed to mount Downloads %s: %s", downloads, e, exc_info=True)

    # Store working directory in memory
    app.memory.set("default", "system", "workspace.path", str(workdir), created_by="init")
    app.audit.log("filesystem.permissions_configured", details={
        "working_directory": str(workdir),
    })


def _import_seed_workflows(app: App) -> None:
    """Import seed workflow definitions from YAML into DB."""
    search_paths = [
        Path(__file__).parent.parent.parent.parent / "artifacts" / "workflows",
        app.data_dir / "workflows",
    ]

    for workflows_dir in search_paths:
        if not workflows_dir.exists():
            continue
        for yaml_file in sorted(workflows_dir.glob("*.yaml")):
            try:
                app.workflow_registry.import_from_yaml(yaml_file)
            except Exception:
                pass


def _finalize(app: App, data_dir: Path, models: list[ModelInfo], connection_ok: bool = False) -> None:
    """Create first generation and show completion message."""
    # Register agents even with no models (needed for later model assignment)
    if not models:
        _register_system_agents(app)

    _register_builtin_connectors(app)
    _import_seed_workflows(app)

    app.config.apply_from_state(
        state_manager=app.state_manager,
        description="Initial setup",
        trigger="init",
    )

    _conn_ok = connection_ok

    # Welcome box — positioning statement
    from rich.panel import Panel

    welcome_lines = (
        f"  {t('init.welcome_box.line1')}\n"
        f"  {t('init.welcome_box.line2')}\n\n"
        f"  • {t('init.welcome_box.bullet1')}\n"
        f"  • {t('init.welcome_box.bullet2')}\n"
        f"  • {t('init.welcome_box.bullet3')}\n"
        f"  • {t('init.welcome_box.bullet4')}"
    )
    console.print()
    console.print(Panel(welcome_lines, title=f"[bold]{t('init.welcome_box.title')}[/bold]", border_style="cyan"))

    if _conn_ok:
        console.print(f"\n[bold green]✓ {t('init.ready')}[/bold green]\n")
    else:
        console.print(f"\n[bold yellow]⚠ {t('init.setup_warning')}[/bold yellow]")
        console.print(f"  [dim]{t('init.setup_warning_detail')}[/dim]\n")

    if models:
        console.print(f"  {t('init.models_configured', count=len(models))}")
    console.print(f"  {t('init.data_dir', path=data_dir)}")
    console.print()

    if _conn_ok:
        console.print(f"  [bold]{t('init.next_serve')}[/bold]")
        console.print(f"  [dim]  {t('init.next_serve_detail')}[/dim]")
    else:
        provider_id = models[0].provider if models else "anthropic"
        console.print(f"  [bold]{t('init.next_fix_key', provider_id=provider_id)}[/bold]")

    console.print(f"  [bold]{t('init.next_model')}[/bold]")
    console.print(f"  [bold]{t('init.next_credential')}[/bold]")
    console.print()


def _setup_master_key(data_dir: Path) -> None:
    """Generate or load master key."""
    master_key = os.environ.get("MYCELOS_MASTER_KEY")
    key_file = data_dir / ".master_key"
    if not master_key:
        if key_file.exists():
            master_key = key_file.read_text().strip()
            os.environ["MYCELOS_MASTER_KEY"] = master_key
        else:
            master_key = secrets.token_urlsafe(32)
            data_dir.mkdir(parents=True, exist_ok=True)
            key_file.write_text(master_key)
            key_file.chmod(0o600)
            os.environ["MYCELOS_MASTER_KEY"] = master_key
            console.print(f"[green]✓ {t('init.master_key_generated_short')}[/green]")
            console.print()
            console.print(
                f"  [bold yellow]⚠ {t('init.master_key_backup_warning')}[/bold yellow]\n"
                f"  [bold]{key_file}[/bold]\n"
                f"  [dim]{t('init.master_key_backup_detail')}[/dim]"
            )
            console.print()
