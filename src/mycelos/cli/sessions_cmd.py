"""mycelos sessions -- inspect and maintain chat sessions."""

from __future__ import annotations

from pathlib import Path
from mycelos.cli import default_data_dir

import click
from rich.console import Console
from rich.table import Table

from mycelos.i18n import t
from mycelos.sessions.store import SessionStore

console = Console()


def _list_recent_sessions(data_dir: Path) -> None:
    store = SessionStore(data_dir / "conversations")
    sessions = store.list_sessions()
    if not sessions:
        console.print(f"[dim]{t('sessions.none')}[/dim]")
        return
    table = Table(title="Recent Sessions")
    table.add_column("ID", style="dim")
    table.add_column("Started")
    table.add_column("Messages")
    table.add_column("User")
    for s in sessions[:20]:
        sid = s.get("session_id", "?")[:8] + "..."
        table.add_row(
            sid,
            s.get("timestamp", "?")[:19],
            str(s.get("message_count", 0)),
            s.get("user_id", "?"),
        )
    console.print(table)
    console.print(f"\n[dim]{t('sessions.resume_latest')}[/dim]")


@click.group("sessions", invoke_without_command=True)
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=default_data_dir,
    show_default=True,
    help="Data directory for Mycelos.",
)
@click.pass_context
def sessions_cmd(ctx: click.Context, data_dir: Path) -> None:
    """Inspect and maintain chat sessions."""
    ctx.ensure_object(dict)
    ctx.obj["data_dir"] = data_dir
    if ctx.invoked_subcommand is None:
        _list_recent_sessions(data_dir)


@sessions_cmd.command("backfill-titles")
@click.pass_context
def backfill_titles(ctx: click.Context) -> None:
    """Assign titles to legacy sessions based on their first user message.

    Untitled sessions get the first 60 characters of the first user
    message as their title (ellipsised). Sessions that already have a
    title are left untouched. Safe to run repeatedly.
    """
    data_dir: Path = ctx.obj["data_dir"]
    store = SessionStore(data_dir / "conversations")
    count = store.backfill_titles_from_first_message()
    if count == 0:
        console.print("[dim]No untitled sessions found — nothing to do.[/dim]")
    else:
        console.print(f"[green]Retitled {count} session(s) from their first user message.[/green]")
