"""mycelos model — interactive model and agent-assignment management.

Provides a drill-down menu to:
  - List configured models and their tiers
  - Add new providers and models
  - Remove models
  - View agents and their model assignments
  - Change per-agent model assignments
  - Run plausibility checks and connectivity tests
"""

import os
from pathlib import Path

import click
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
from mycelos.llm.smart_defaults import AGENT_ROLES, compute_smart_defaults
from mycelos.llm.validation import (
    check_model_connectivity,
    validate_model_config,
)

console = Console()


def _load_app(data_dir: Path) -> App:
    """Load the app, ensuring it's initialized."""
    db_path = data_dir / "mycelos.db"
    if not db_path.exists():
        console.print(f"[red]{t('model.not_initialized')}[/red]")
        raise SystemExit(1)
    return App(data_dir)


def _get_registered_models(app: App) -> list[dict]:
    """Get all models from the registry."""
    return app.model_registry.list_models()


def _model_dicts_to_infos(model_dicts: list[dict]) -> list[ModelInfo]:
    """Convert model registry dicts to ModelInfo objects."""
    return [
        ModelInfo(
            id=m["id"],
            name=m["id"],
            tier=m.get("tier", "sonnet"),
            provider=m.get("provider", "unknown"),
            input_cost_per_1k=m.get("input_cost_per_1k"),
            output_cost_per_1k=m.get("output_cost_per_1k"),
            max_context=m.get("max_context"),
        )
        for m in model_dicts
    ]


# ---------------------------------------------------------------------------
# mycelos model  (group)
# ---------------------------------------------------------------------------


@click.group()
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=Path.home() / ".mycelos",
    help="Data directory for Mycelos",
)
@click.pass_context
def model_group(ctx: click.Context, data_dir: Path) -> None:
    """Manage LLM models and agent assignments."""
    ctx.ensure_object(dict)
    ctx.obj["data_dir"] = data_dir


# ---------------------------------------------------------------------------
# mycelos model list
# ---------------------------------------------------------------------------


@model_group.command("list")
@click.pass_context
def model_list(ctx: click.Context) -> None:
    """Show all configured models and their tiers."""
    app = _load_app(ctx.obj["data_dir"])
    models = _get_registered_models(app)

    if not models:
        console.print(f"[yellow]{t('model.no_models')}[/yellow]")
        return

    table = Table(title="Configured Models")
    table.add_column("Model", style="bold")
    table.add_column("Provider")
    table.add_column("Tier")
    table.add_column("Cost (in/out per 1K)")
    table.add_column("Context")
    table.add_column("Status")

    for m in models:
        cost_in = m.get("input_cost_per_1k")
        cost_out = m.get("output_cost_per_1k")
        if cost_in is not None and cost_in > 0:
            cost = f"${cost_in:.4f}/${cost_out:.4f}"
        else:
            cost = "free"
        ctx_str = f"{m['max_context']:,}" if m.get("max_context") else "?"
        status = m.get("status", "available")
        table.add_row(m["id"], m["provider"], m["tier"], cost, ctx_str, status)

    console.print(table)

    # Show additional services (embeddings, STT)
    try:
        services = app.credentials.list_services()
        extra = []
        if "openai" in services:
            extra.append("  Embeddings: [green]OpenAI text-embedding-3-small[/green]")
            extra.append("  Speech-to-Text: [green]OpenAI Whisper[/green]")
        elif "gemini" in services:
            extra.append("  Embeddings: [green]Google Gemini[/green]")
            extra.append("  Speech-to-Text: [green]Google Gemini[/green]")
        else:
            extra.append("  Embeddings: [dim]local model (sentence-transformers)[/dim]")
            extra.append("  Speech-to-Text: [dim]not configured[/dim] — add OpenAI: mycelos credential store openai")
        if extra:
            console.print()
            console.print("[bold]Additional Services:[/bold]")
            for line in extra:
                console.print(line)
    except Exception:
        pass

    # Quick validation summary
    infos = _model_dicts_to_infos(models)
    report = validate_model_config(infos)
    if report.issues:
        console.print()
        for issue in report.issues:
            prefix = "[yellow]⚠[/yellow]" if issue.level == "warning" else "[blue]ℹ[/blue]"
            console.print(f"  {prefix} {issue.message}")
            if issue.suggestion:
                console.print(f"    [dim]{issue.suggestion}[/dim]")


# ---------------------------------------------------------------------------
# mycelos model add
# ---------------------------------------------------------------------------


@model_group.command("add")
@click.pass_context
def model_add(ctx: click.Context) -> None:
    """Add a new LLM provider and its models."""
    app = _load_app(ctx.obj["data_dir"])

    # Show provider menu
    console.print(f"\n[bold]{t('model.pick_provider')}[/bold]\n")
    provider_list = list(PROVIDERS.values())
    for i, p in enumerate(provider_list, 1):
        key_info = f"(API Key: {p.env_var})" if p.requires_key else "(no key needed)"
        console.print(f"  ({i}) {p.name}  [dim]{key_info}[/dim]")

    console.print()
    choice = click.prompt("Provider", default="1")

    try:
        idx = int(choice.strip()) - 1
        if not (0 <= idx < len(provider_list)):
            console.print(f"[yellow]{t('model.invalid_selection')}[/yellow]")
            return
    except ValueError:
        console.print(f"[yellow]{t('model.invalid_selection')}[/yellow]")
        return

    provider = provider_list[idx]

    # Get API key if needed
    if provider.requires_key:
        api_key = _prompt_api_key(app, provider)
        if not api_key:
            console.print(f"[yellow]{t('model.skipping_provider', provider=provider.name)}[/yellow]")
            return
    elif provider.id == "ollama":
        _add_ollama_models(app, provider)
        return
    elif provider.id == "custom":
        _add_custom_model(app)
        return

    # Discover models
    models = get_provider_models(provider.id)
    if not models:
        console.print(f"[yellow]{t('model.no_models_for_provider', provider=provider.name)}[/yellow]")
        return

    # Let user select which models to enable
    selected = _interactive_model_select(models)
    if not selected:
        console.print(f"[yellow]{t('model.no_models_selected')}[/yellow]")
        return

    # Register
    for m in selected:
        app.model_registry.add_model(
            model_id=m.id, provider=m.provider, tier=m.tier,
            input_cost_per_1k=m.input_cost_per_1k,
            output_cost_per_1k=m.output_cost_per_1k,
            max_context=m.max_context,
        )
        console.print(f"  [green]+ {m.name}[/green] ({m.tier})")

    # Re-run validation
    all_models = _model_dicts_to_infos(_get_registered_models(app))
    report = validate_model_config(all_models)
    if not report.has_warnings:
        console.print(f"\n[green]{t('model.config_looks_good')}[/green]")

    # Offer to update defaults
    if click.confirm(f"\n{t('model.update_defaults')}", default=True):
        defaults = compute_smart_defaults(all_models)
        _apply_defaults_silent(app, defaults)
        console.print(f"[green]{t('model.defaults_updated')}[/green]")

    _create_generation(app, f"Added {len(selected)} model(s) from {provider.name}")


# ---------------------------------------------------------------------------
# mycelos model remove
# ---------------------------------------------------------------------------


@model_group.command("remove")
@click.argument("model_id")
@click.pass_context
def model_remove(ctx: click.Context, model_id: str) -> None:
    """Remove a model from the registry."""
    app = _load_app(ctx.obj["data_dir"])

    existing = app.model_registry.get_model(model_id)
    if not existing:
        console.print(f"[red]{t('model.model_not_found', model_id=model_id)}[/red]")
        return

    if click.confirm(f"Remove '{model_id}'? This will also remove its agent assignments."):
        app.model_registry.remove_model(model_id)
        console.print(f"[green]{t('model.removed', model_id=model_id)}[/green]")
        _create_generation(app, f"Removed model {model_id}")


# ---------------------------------------------------------------------------
# mycelos model test
# ---------------------------------------------------------------------------


@model_group.command("test")
@click.argument("model_id", required=False)
@click.pass_context
def model_test(ctx: click.Context, model_id: str | None) -> None:
    """Test connectivity for one or all configured models."""
    app = _load_app(ctx.obj["data_dir"])

    if model_id:
        existing = app.model_registry.get_model(model_id)
        if not existing:
            console.print(f"[red]{t('model.model_not_found', model_id=model_id)}[/red]")
            return
        models_to_test = _model_dicts_to_infos([existing])
    else:
        models_to_test = _model_dicts_to_infos(_get_registered_models(app))

    if not models_to_test:
        console.print(f"[yellow]{t('model.no_models_to_test')}[/yellow]")
        return

    console.print(f"\n[bold]{t('model.testing')}[/bold]\n")

    for model in models_to_test:
        result = check_model_connectivity(model, app.credentials)
        if result.reachable:
            console.print(f"  [green]✓ {model.id}[/green]")
        else:
            console.print(f"  [red]✗ {model.id}: {result.error}[/red]")


# ---------------------------------------------------------------------------
# mycelos model agents
# ---------------------------------------------------------------------------


@model_group.command("agents")
@click.pass_context
def model_agents(ctx: click.Context) -> None:
    """Show agents and their current model assignments. Drill in to change."""
    app = _load_app(ctx.obj["data_dir"])

    # Get all agents
    agents = app.agent_registry.list_agents()
    if not agents:
        console.print(f"[yellow]{t('model.no_agents')}[/yellow]")
        return

    # Get all available models for assignment
    all_model_dicts = _get_registered_models(app)
    if not all_model_dicts:
        console.print(f"[yellow]{t('model.no_models_for_agents')}[/yellow]")
        return

    while True:
        # Show agent overview table
        console.print("\n[bold]--- Agent Model Assignments ---[/bold]\n")

        table = Table()
        table.add_column("#", style="dim", width=3)
        table.add_column("Agent", style="bold")
        table.add_column("Type")
        table.add_column("Primary Model")
        table.add_column("Fallback(s)")

        for i, agent in enumerate(agents, 1):
            agent_id = agent["id"]
            exec_models = app.model_registry.resolve_models(agent_id, "execution")
            primary = exec_models[0] if exec_models else "---"
            fallbacks = ", ".join(exec_models[1:]) if len(exec_models) > 1 else "---"
            table.add_row(
                str(i), agent["name"], agent.get("agent_type", ""),
                primary, fallbacks,
            )

        # Also show system defaults
        sys_exec = app.model_registry.resolve_models(None, "execution")
        sys_class = app.model_registry.resolve_models(None, "classification")
        table.add_row(
            "", "[dim]System (execution)[/dim]", "",
            sys_exec[0] if sys_exec else "---",
            ", ".join(sys_exec[1:]) if len(sys_exec) > 1 else "---",
        )
        table.add_row(
            "", "[dim]System (classification)[/dim]", "",
            sys_class[0] if sys_class else "---",
            ", ".join(sys_class[1:]) if len(sys_class) > 1 else "---",
        )

        console.print(table)

        # Drill-in menu
        console.print(f"\n  {t('model.enter_agent_number')}")
        choice = click.prompt("Agent #", default="q")

        if choice.strip().lower() == "q":
            break

        try:
            idx = int(choice.strip()) - 1
            if not (0 <= idx < len(agents)):
                console.print(f"[yellow]{t('model.invalid_selection')}[/yellow]")
                continue
        except ValueError:
            console.print(f"[yellow]{t('model.invalid_selection')}[/yellow]")
            continue

        agent = agents[idx]
        _edit_agent_models(app, agent, all_model_dicts)


# ---------------------------------------------------------------------------
# mycelos model check
# ---------------------------------------------------------------------------


@model_group.command("check")
@click.pass_context
def model_check(ctx: click.Context) -> None:
    """Run plausibility check on current model configuration."""
    app = _load_app(ctx.obj["data_dir"])
    models = _model_dicts_to_infos(_get_registered_models(app))

    if not models:
        console.print(f"[yellow]{t('model.no_models_check')}[/yellow]")
        return

    report = validate_model_config(models)

    console.print("\n[bold]--- Configuration Check ---[/bold]\n")

    # Tier overview
    tiers: dict[str, list[str]] = {}
    for m in models:
        tiers.setdefault(m.tier, []).append(m.id)

    table = Table(title="Model Summary")
    table.add_column("Tier", style="bold")
    table.add_column("Models")
    table.add_column("Count", justify="right")
    for tier in ("opus", "sonnet", "haiku"):
        if tier in tiers:
            table.add_row(tier, ", ".join(tiers[tier]), str(len(tiers[tier])))
    console.print(table)

    if report.issues:
        console.print()
        for issue in report.issues:
            prefix = "[yellow]⚠[/yellow]" if issue.level == "warning" else "[blue]ℹ[/blue]"
            console.print(f"  {prefix} {issue.message}")
            if issue.suggestion:
                console.print(f"    [dim]{issue.suggestion}[/dim]")
    else:
        console.print("\n  [green]Configuration looks good![/green]")

    # Offer connectivity test
    if click.confirm("\nTest model connectivity?", default=True):
        console.print()
        for model in models:
            result = check_model_connectivity(model, app.credentials)
            if result.reachable:
                console.print(f"  [green]✓ {model.id}[/green]")
            else:
                console.print(f"  [red]✗ {model.id}: {result.error}[/red]")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _prompt_api_key(app: App, provider: ProviderConfig) -> str | None:
    """Prompt for API key and store it encrypted."""
    env_var = provider.env_var
    existing_key = os.environ.get(env_var) if env_var else None

    if existing_key:
        console.print(f"[green]{t('init.env_found', env_var=env_var)}[/green]")
        api_key = existing_key
    else:
        api_key = click.prompt(
            t("init.api_key_enter", provider=provider.name),
            default="skip",
            hide_input=True,
        )
        if api_key == "skip":
            return None
        os.environ[env_var] = api_key

    app.credentials.store_credential(provider.id, {
        "api_key": api_key,
        "env_var": env_var,
        "provider": provider.id,
    })
    app.audit.log("credential.stored", details={"service": provider.id})
    return api_key


def _interactive_model_select(models: list[ModelInfo]) -> list[ModelInfo]:
    """Show models in a table and let user select which to enable."""
    table = Table(title="Available Models")
    table.add_column("#", style="dim", width=3)
    table.add_column("Model", style="bold")
    table.add_column("Tier")
    table.add_column("Cost (in/out per 1K)")
    table.add_column("Context")

    for i, m in enumerate(models, 1):
        cost = "free" if (m.input_cost_per_1k or 0) == 0 else (
            f"${m.input_cost_per_1k:.4f}/{m.output_cost_per_1k:.4f}"
        )
        ctx = f"{m.max_context:,}" if m.max_context else "?"
        table.add_row(str(i), m.name, m.tier, cost, ctx)

    console.print(table)

    choice = click.prompt(
        "Enable which models? (comma-separated, or 'all')",
        default="all",
    )

    if choice.strip().lower() == "all":
        return models

    selected: list[ModelInfo] = []
    for part in choice.split(","):
        try:
            idx = int(part.strip()) - 1
            if 0 <= idx < len(models):
                selected.append(models[idx])
        except ValueError:
            pass

    return selected if selected else models


def _add_ollama_models(app: App, provider: ProviderConfig) -> None:
    """Add Ollama models interactively."""
    from mycelos.llm.ollama import (
        classify_ollama_tier,
        discover_ollama_models,
        is_ollama_running,
    )

    url = click.prompt("Ollama URL", default=provider.default_url or "http://localhost:11434")

    if not is_ollama_running(url):
        console.print(f"[yellow]{t('init.ollama_unreachable', url=url)}[/yellow]")
        return

    ollama_models = discover_ollama_models(url)
    if not ollama_models:
        console.print(f"[yellow]{t('model.no_models_selected')}[/yellow]")
        return

    models: list[ModelInfo] = []
    for m in ollama_models:
        tier = classify_ollama_tier(m)
        models.append(ModelInfo(
            id=m.id, name=m.name, tier=tier, provider="ollama",
            input_cost_per_1k=0.0, output_cost_per_1k=0.0,
        ))

    selected = _interactive_model_select(models)
    for m in selected:
        app.model_registry.add_model(
            model_id=m.id, provider=m.provider, tier=m.tier,
            input_cost_per_1k=0.0, output_cost_per_1k=0.0,
        )
        console.print(f"  [green]+ {m.name}[/green]")

    _create_generation(app, f"Added {len(selected)} Ollama model(s)")


def _add_custom_model(app: App) -> None:
    """Add a custom OpenAI-compatible model."""
    url = click.prompt("Server URL (e.g. http://localhost:8080)")
    model_id = click.prompt("Model ID")
    api_key = click.prompt("API Key (optional, press Enter to skip)", default="", hide_input=True)

    if api_key:
        app.credentials.store_credential("custom", {
            "api_key": api_key,
            "api_base": url,
            "provider": "custom",
        })

    full_id = f"custom/{model_id}"
    app.model_registry.add_model(
        model_id=full_id, provider="custom", tier="sonnet",
        input_cost_per_1k=0.0, output_cost_per_1k=0.0,
    )
    console.print(f"  [green]+ {full_id}[/green]")
    _create_generation(app, f"Added custom model {full_id}")


def _edit_agent_models(app: App, agent: dict, all_model_dicts: list[dict]) -> None:
    """Let user edit model assignments for a specific agent."""
    agent_id = agent["id"]
    agent_name = agent["name"]

    console.print(f"\n[bold]--- {agent_name} ---[/bold]\n")

    # Show current assignments
    exec_models = app.model_registry.resolve_models(agent_id, "execution")
    class_models = app.model_registry.resolve_models(agent_id, "classification")

    console.print(f"  Execution:       {', '.join(exec_models) if exec_models else '(system default)'}")
    console.print(f"  Classification:  {', '.join(class_models) if class_models else '(system default)'}")

    # Show available models
    console.print(f"\n  Available models:")
    for i, m in enumerate(all_model_dicts, 1):
        console.print(f"    ({i}) {m['id']}  [dim]{m['tier']}, {m['provider']}[/dim]")

    # Edit execution models
    console.print()
    choice = click.prompt(
        f"  Execution models for {agent_name} (comma-separated #, or Enter to keep)",
        default="",
    )

    if choice.strip():
        selected_ids = _parse_model_selection(choice, all_model_dicts)
        if selected_ids:
            app.model_registry.set_agent_models(agent_id, selected_ids, "execution")
            console.print(f"  [green]Updated: {', '.join(selected_ids)}[/green]")
            _create_generation(app, f"Changed models for {agent_name}")


def _parse_model_selection(choice: str, all_model_dicts: list[dict]) -> list[str]:
    """Parse comma-separated model selection into model IDs."""
    selected: list[str] = []
    for part in choice.split(","):
        try:
            idx = int(part.strip()) - 1
            if 0 <= idx < len(all_model_dicts):
                mid = all_model_dicts[idx]["id"]
                if mid not in selected:
                    selected.append(mid)
        except ValueError:
            pass
    return selected


def _apply_defaults_silent(app: App, defaults: dict[str, list[str]]) -> None:
    """Apply smart defaults without interactive prompts."""
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
            agent_assignments.setdefault(agent, {})[purpose] = model_ids

    if system_defaults:
        app.model_registry.set_system_defaults(system_defaults)

    for agent_id, purposes in agent_assignments.items():
        for purpose, model_ids in purposes.items():
            app.model_registry.set_agent_models(agent_id, model_ids, purpose)


def _create_generation(app: App, description: str) -> None:
    """Create a new config generation after a model change."""
    try:
        app.config.apply_from_state(
            state_manager=app.state_manager,
            description=description,
            trigger="model_cmd",
        )
    except Exception as e:
        # Non-fatal, but log it (Constitution Rule 1: audit everything)
        import logging
        logging.getLogger("mycelos.cli").warning("Config generation failed: %s", e)
        try:
            app.audit.log("config.generation_failed", details={"error": str(e), "description": description})
        except Exception:
            pass
