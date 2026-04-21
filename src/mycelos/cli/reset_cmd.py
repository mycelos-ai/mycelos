"""mycelos reset — backup everything, then clean slate.

Creates a timestamped .tar.gz backup of the data directory,
then removes all data. The user can delete the backup manually.
"""

from __future__ import annotations

import os
import shutil
import tarfile
from datetime import datetime
from pathlib import Path
from mycelos.cli import default_data_dir

import click
from rich.console import Console

from mycelos.i18n import t

console = Console()


@click.command()
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=default_data_dir,
    help="Data directory to reset",
)
@click.option("--no-backup", is_flag=True, default=False, help="Skip backup (dangerous)")
def reset_cmd(data_dir: Path, no_backup: bool) -> None:
    """Reset Mycelos — backup everything, then start fresh."""
    if not data_dir.exists():
        console.print(f"[yellow]{t('reset.nothing_to_reset', path=data_dir)}[/yellow]")
        return

    # Show what will be deleted
    file_count = sum(1 for _ in data_dir.rglob("*") if _.is_file())
    dir_size = sum(f.stat().st_size for f in data_dir.rglob("*") if f.is_file())
    size_mb = dir_size / (1024 * 1024)

    console.print(f"\n[bold red]Warning: This will delete ALL Mycelos data![/bold red]\n")
    console.print(f"  Directory: {data_dir}")
    console.print(f"  Files: {file_count}")
    console.print(f"  Size: {size_mb:.1f} MB")
    console.print()

    # List key items
    db_path = data_dir / "mycelos.db"
    knowledge_dir = data_dir / "knowledge"
    conversations_dir = data_dir / "conversations"

    if db_path.exists():
        console.print(f"  [dim]Database: mycelos.db[/dim]")
    if knowledge_dir.exists():
        note_count = sum(1 for _ in knowledge_dir.rglob("*.md"))
        console.print(f"  [dim]Knowledge Base: {note_count} notes[/dim]")
    if conversations_dir.exists():
        conv_count = sum(1 for _ in conversations_dir.rglob("*.jsonl"))
        console.print(f"  [dim]Conversations: {conv_count} sessions[/dim]")

    console.print()

    if not click.confirm("Are you sure you want to reset?", default=False):
        console.print(f"[green]{t('reset.cancelled')}[/green]")
        return

    # Backup
    if not no_backup:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_name = f"mycelos-backup-{timestamp}.tar.gz"
        # Write backup next to data dir, or inside it if parent is not writable (e.g. Docker /data)
        backup_path = data_dir.parent / backup_name
        if not os.access(data_dir.parent, os.W_OK):
            backup_path = data_dir / backup_name

        console.print(f"\n  Creating backup: {backup_path}")
        try:
            with tarfile.open(backup_path, "w:gz") as tar:
                tar.add(data_dir, arcname="mycelos-backup")
            console.print(f"  [green]✓ Backup saved ({backup_path.stat().st_size / 1024 / 1024:.1f} MB)[/green]")
            console.print(f"  [dim]Delete it when you no longer need it: rm {backup_path}[/dim]")
        except Exception as e:
            console.print(f"  [red]Backup failed: {e}[/red]")
            if not click.confirm("Continue without backup?", default=False):
                return
    else:
        console.print(f"  [yellow]{t('reset.skipping_backup')}[/yellow]")

    # Delete contents (keep data_dir itself for Docker volume mounts)
    console.print(f"\n  Removing {data_dir} contents...")
    try:
        for item in data_dir.iterdir():
            # Skip backup file we just created
            if not no_backup and item.name.startswith("mycelos-backup-"):
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        console.print(f"  [green]✓ All data removed.[/green]")
    except Exception as e:
        console.print(f"  [red]Failed to remove: {e}[/red]")
        return

    console.print(f"\n  [bold]Fresh start:[/bold] mycelos init --data-dir {data_dir}\n")
