"""mycelos schedule -- manage scheduled workflow tasks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from mycelos.app import App
from mycelos.i18n import t

console = Console()


@click.group()
def schedule_cmd() -> None:
    """Manage scheduled workflow tasks (cron jobs)."""
    pass


@schedule_cmd.command("list")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def schedule_list(data_dir: Path) -> None:
    """List all scheduled tasks."""
    app = App(data_dir)
    tasks = app.schedule_manager.list_tasks()

    if not tasks:
        console.print(t("schedule.no_tasks"))
        return

    table = Table(title="Scheduled Tasks")
    table.add_column("ID", style="dim", width=8)
    table.add_column("Workflow", style="bold")
    table.add_column("Schedule")
    table.add_column("Status")
    table.add_column("Last Run", style="dim")
    table.add_column("Next Run")
    table.add_column("Runs", justify="right")

    for task in tasks:
        table.add_row(
            task["id"][:8],
            task["workflow_id"],
            task["schedule"],
            task["status"],
            (task.get("last_run") or "\u2014")[:16],
            (task.get("next_run") or "\u2014")[:16],
            str(task.get("run_count", 0)),
        )

    console.print(table)


@schedule_cmd.command("add")
@click.argument("workflow_id")
@click.option("--cron", required=True, help='Cron expression, e.g. "0 8 * * *"')
@click.option("--input", "input_json", default=None, help='JSON input, e.g. \'{"query":"AI"}\'')
@click.option("--budget", type=float, default=None, help="Max cost per run in $")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def schedule_add(
    workflow_id: str,
    cron: str,
    input_json: str | None,
    budget: float | None,
    data_dir: Path,
) -> None:
    """Add a scheduled task for a workflow."""
    app = App(data_dir)

    # Verify workflow exists
    wf = app.workflow_registry.get(workflow_id)
    if wf is None:
        console.print(f"[red]{t('schedule.workflow_not_found', id=workflow_id)}[/red]")
        console.print(t("schedule.available_workflows"))
        for w in app.workflow_registry.list_workflows():
            console.print(f"  - {w['id']}")
        return

    inputs = json.loads(input_json) if input_json else None

    task_id = app.schedule_manager.add(
        workflow_id=workflow_id,
        schedule=cron,
        inputs=inputs,
        budget_per_run=budget,
    )

    task = app.schedule_manager.get(task_id)
    console.print(f"[green]{t('schedule.created', id=task_id[:8])}[/green]")
    console.print(f"  Workflow: {workflow_id}")
    console.print(f"  Schedule: {cron}")
    console.print(f"  {t('schedule.next_run', time=task['next_run'][:16] if task else '?')}")
    if budget:
        console.print(f"  {t('schedule.budget', amount=f'{budget:.2f}')}")


@schedule_cmd.command("pause")
@click.argument("task_id")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def schedule_pause(task_id: str, data_dir: Path) -> None:
    """Pause a scheduled task."""
    app = App(data_dir)
    task = _find_task(app, task_id)
    if task:
        app.schedule_manager.pause(task["id"])
        console.print(f"[yellow]{t('schedule.paused', id=task['id'][:8], workflow=task['workflow_id'])}[/yellow]")


@schedule_cmd.command("resume")
@click.argument("task_id")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def schedule_resume(task_id: str, data_dir: Path) -> None:
    """Resume a paused scheduled task."""
    app = App(data_dir)
    task = _find_task(app, task_id)
    if task:
        app.schedule_manager.resume(task["id"])
        console.print(f"[green]{t('schedule.resumed', id=task['id'][:8], workflow=task['workflow_id'])}[/green]")


@schedule_cmd.command("delete")
@click.argument("task_id")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def schedule_delete(task_id: str, data_dir: Path) -> None:
    """Delete a scheduled task."""
    app = App(data_dir)
    task = _find_task(app, task_id)
    if task:
        if click.confirm(f"Delete task {task['id'][:8]} ({task['workflow_id']})?"):
            app.schedule_manager.delete(task["id"])
            console.print(f"[red]{t('schedule.deleted')}[/red]")


@schedule_cmd.command("run")
@click.argument("task_id")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def schedule_run(task_id: str, data_dir: Path) -> None:
    """Run a scheduled task immediately (for testing)."""
    app = App(data_dir)
    task = _find_task(app, task_id)
    if task:
        console.print(t("schedule.running_now", workflow=task["workflow_id"]))
        from datetime import datetime, timezone

        from mycelos.scheduler.jobs import check_scheduled_workflows

        # Force next_run to now so it becomes due
        app.storage.execute(
            "UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), task["id"]),
        )
        executed = check_scheduled_workflows(app)
        if task["id"] in executed:
            console.print(f"[green]{t('schedule.run_done')}[/green]")
        else:
            console.print(f"[yellow]{t('schedule.run_failed')}[/yellow]")


def _find_task(app: App, task_id_prefix: str) -> dict[str, Any] | None:
    """Find a task by ID or prefix."""
    # Try exact match
    task = app.schedule_manager.get(task_id_prefix)
    if task:
        return task
    # Try prefix match
    all_tasks = app.schedule_manager.list_tasks()
    for st in all_tasks:
        if st["id"].startswith(task_id_prefix):
            return st
    console.print(f"[red]{t('schedule.not_found', id=task_id_prefix)}[/red]")
    return None
