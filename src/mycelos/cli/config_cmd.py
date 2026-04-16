"""mycelos config commands."""

import json
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from mycelos.app import App
from mycelos.i18n import t

console = Console()


@click.group()
def config_group():
    """Manage system configuration (NixOS-style generations)."""
    pass


@config_group.command("list")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def config_list(data_dir: Path):
    """List all config generations."""
    app = App(data_dir)

    gens = app.config.list_generations()
    if not gens:
        console.print(t("config.no_generations"))
        return

    table = Table(title="Config Generations")
    table.add_column("ID", style="cyan")
    table.add_column("Created", style="dim")
    table.add_column("Hash", style="dim")
    table.add_column("Description")
    table.add_column("Active", style="green")

    for gen in gens:
        table.add_row(
            str(gen.id),
            gen.created_at[:19],
            gen.config_hash,
            gen.description or "",
            "<<<" if gen.is_active else "",
        )

    console.print(table)


@config_group.command("show")
@click.argument("generation_id", type=int, required=False)
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON snapshot")
def config_show(generation_id: int | None, data_dir: Path, as_json: bool):
    """Show config state. Without ID shows the active generation."""
    app = App(data_dir)

    if generation_id:
        try:
            row = app.storage.fetchone(
                "SELECT config_snapshot FROM config_generations WHERE id = ?",
                (generation_id,),
            )
            if not row:
                console.print(f"[red]{t('config.not_found', id=generation_id)}[/red]")
                return
            snapshot = json.loads(row["config_snapshot"])
        except Exception as e:
            console.print(f"[red]{t('common.error')}: {e}[/red]")
            return
    else:
        try:
            snapshot = app.config.get_active_config()
        except Exception:
            console.print(f"[red]{t('config.no_active')}[/red]")
            return

    if as_json:
        console.print_json(json.dumps(snapshot, indent=2, ensure_ascii=False))
        return

    # Pretty-print the state
    gen_id = generation_id or app.config.get_active_generation_id()
    console.print(f"\n[bold]{t('config.generation', id=gen_id)}[/bold]")
    console.print(f"[dim]{t('config.schema_version', version=snapshot.get('schema_version', '?'))}[/dim]\n")

    # LLM Models
    llm = snapshot.get("llm", {})
    models = llm.get("models", {})
    if models:
        table = Table(title="LLM Models")
        table.add_column("Model", style="bold")
        table.add_column("Provider")
        table.add_column("Tier")
        table.add_column("Cost (in/out per 1K)")
        table.add_column("Context")

        for mid, info in sorted(models.items()):
            cost_in = info.get("input_cost_per_1k")
            cost_out = info.get("output_cost_per_1k")
            cost_str = (
                "free" if (cost_in or 0) == 0
                else f"${cost_in:.4f}/${cost_out:.4f}"
            )
            ctx = f"{info.get('max_context', 0):,}" if info.get("max_context") else "?"
            table.add_row(mid, info.get("provider", "?"), info.get("tier", "?"), cost_str, ctx)

        console.print(table)

    # LLM Assignments
    assignments = llm.get("assignments", {})
    if assignments:
        table = Table(title="Model Assignments")
        table.add_column("Role", style="bold")
        table.add_column("Primary")
        table.add_column("Fallback(s)")

        for role, model_ids in sorted(assignments.items()):
            primary = model_ids[0] if model_ids else "—"
            fallbacks = ", ".join(model_ids[1:]) if len(model_ids) > 1 else "—"
            table.add_row(role, primary, fallbacks)

        console.print(table)

    # Connectors
    connectors = snapshot.get("connectors", {})
    if connectors:
        table = Table(title="Connectors")
        table.add_column("ID", style="bold")
        table.add_column("Name")
        table.add_column("Type")
        table.add_column("Status")
        table.add_column("Capabilities")

        for cid, info in sorted(connectors.items()):
            caps = ", ".join(info.get("capabilities", []))
            table.add_row(
                cid, info.get("name", "?"), info.get("connector_type", "?"),
                info.get("status", "?"), caps,
            )

        console.print(table)

    # Agents
    agents = snapshot.get("agents", {})
    if agents:
        table = Table(title="Agents")
        table.add_column("ID", style="bold")
        table.add_column("Name")
        table.add_column("Type")
        table.add_column("Status")
        table.add_column("Capabilities")
        table.add_column("Code")

        for aid, info in sorted(agents.items()):
            caps = ", ".join(info.get("capabilities", []))
            code = info.get("code_hash", "")[:12] + "..." if info.get("code_hash") else "—"
            table.add_row(
                aid, info.get("name", "?"), info.get("agent_type", "?"),
                info.get("status", "?"), caps, code,
            )

        console.print(table)

    # Policies
    policies = snapshot.get("policies", {})
    if policies:
        table = Table(title="Policies")
        table.add_column("Resource", style="bold")
        table.add_column("Decision")

        for key, decision in sorted(policies.items()):
            table.add_row(key, decision)

        console.print(table)

    # Credentials (show service names only, not values)
    credentials = snapshot.get("credentials", {})
    if credentials:
        console.print(f"\n[bold]{t('config.credentials')}[/bold] {', '.join(sorted(credentials.keys()))}")
        rotated = [s for s, info in credentials.items() if info.get("security_rotated")]
        if rotated:
            console.print(f"[yellow]{t('config.rotated', services=', '.join(rotated))}[/yellow]")

    console.print()


@config_group.command("diff")
@click.argument("gen_a", type=int)
@click.argument("gen_b", type=int)
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def config_diff(gen_a: int, gen_b: int, data_dir: Path):
    """Show differences between two config generations."""
    app = App(data_dir)

    try:
        result = app.config.diff(gen_a, gen_b)
    except Exception as e:
        console.print(f"[red]{t('common.error')}: {e}[/red]")
        return

    if result.is_empty():
        console.print(t("config.identical", a=gen_a, b=gen_b))
        return

    console.print(f"\n[bold]{t('config.diff_title', a=gen_a, b=gen_b)}[/bold]\n")

    if result.added:
        for key, val in sorted(result.added.items()):
            val_str = json.dumps(val, ensure_ascii=False, indent=2) if not isinstance(val, str) else val
            console.print(f"  [green]+ {key}[/green]: {val_str}")

    if result.removed:
        for key, val in sorted(result.removed.items()):
            val_str = json.dumps(val, ensure_ascii=False, indent=2) if not isinstance(val, str) else val
            console.print(f"  [red]- {key}[/red]: {val_str}")

    if result.changed:
        for key, (old, new) in sorted(result.changed.items()):
            old_str = json.dumps(old, ensure_ascii=False, indent=2) if not isinstance(old, str) else old
            new_str = json.dumps(new, ensure_ascii=False, indent=2) if not isinstance(new, str) else new
            console.print(f"  [yellow]~ {key}[/yellow]:")
            console.print(f"    [red]old:[/red] {old_str}")
            console.print(f"    [green]new:[/green] {new_str}")

    total = len(result.added) + len(result.removed) + len(result.changed)
    console.print(
        f"\n{len(result.added)} {t('config.added')}, "
        f"{len(result.removed)} {t('config.removed')}, "
        f"{len(result.changed)} {t('config.changed')}"
    )


@config_group.command("rollback")
@click.argument("generation_id", type=int, required=False)
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def config_rollback(generation_id: int | None, data_dir: Path):
    """Roll back to a previous config generation."""
    app = App(data_dir)

    try:
        rolled_back = app.config.rollback(
            to_generation=generation_id,
            state_manager=app.state_manager,
        )
        app.audit.log("config.rollback", details={"to_generation": rolled_back})
        console.print(f"[green]{t('config.rollback_success', id=rolled_back)}[/green]")
        console.print(f"[dim]{t('config.rollback_restored')}[/dim]")
    except Exception as e:
        console.print(f"[red]{t('common.error')}: {e}[/red]")
