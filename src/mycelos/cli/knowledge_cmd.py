"""mycelos knowledge commands."""

from pathlib import Path

import click
from rich.console import Console

from mycelos.app import App
from mycelos.cli import default_data_dir
from mycelos.i18n import t

console = Console()


@click.group()
def knowledge_cmd():
    """Knowledge base — export, import, maintenance."""
    pass


@knowledge_cmd.command("export")
@click.option(
    "--okf",
    "okf_dir",
    type=click.Path(path_type=Path),
    required=True,
    help="Write an OKF bundle into this directory.",
)
@click.option("--force", is_flag=True, help="Overwrite a non-empty target directory.")
@click.option("--data-dir", type=click.Path(path_type=Path), default=default_data_dir)
def knowledge_export(okf_dir: Path, force: bool, data_dir: Path):
    """Export the knowledge tree as an OKF bundle.

    OKF is a boundary format: notes are serialized at the boundary while the
    internal Note + SQLite index stay authoritative. Exports all non-archived
    notes.
    """
    from mycelos.knowledge.okf_export import build_okf_bundle

    if okf_dir.exists() and any(okf_dir.iterdir()) and not force:
        console.print(f"[red]{t('knowledge.export.dir_not_empty', path=okf_dir)}[/red]")
        raise SystemExit(1)

    app = App(data_dir)
    kb = app.knowledge_base

    notes = [
        n for n in kb.list_notes(limit=5000)
        if n.get("status") != "archived"
    ]
    bundle = build_okf_bundle(notes, kb.read)

    okf_dir.mkdir(parents=True, exist_ok=True)
    for relpath, contents in bundle.items():
        # Paths come from the DB (already slugified), but guard defensively.
        if relpath.startswith("/") or ".." in relpath.split("/"):
            continue
        target = okf_dir / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")

    app.audit.log("knowledge.export", details={"format": "okf", "count": len(notes)})
    console.print(
        f"[green]{t('knowledge.export.done', count=len(notes), path=okf_dir)}[/green]"
    )
