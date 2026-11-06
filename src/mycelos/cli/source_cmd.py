"""Local source commands. JSON output also supports assisted setup."""
import json
from pathlib import Path

import click

from mycelos.app import App
from mycelos.cli import default_data_dir
from mycelos.knowledge.local_sources import LocalSourceService


def _print(action):
    try:
        click.echo(json.dumps(action(), ensure_ascii=False, indent=2, default=str))
    except (ValueError, OSError, RuntimeError) as error:
        raise click.ClickException(str(error)) from error


@click.group("source")
@click.option("--data-dir", type=click.Path(path_type=Path), default=default_data_dir)
@click.pass_context
def source_cmd(ctx, data_dir):
    """Lokale Quellen hinzufügen, prüfen und erschließen."""
    data_dir.mkdir(parents=True, exist_ok=True)
    app = App(data_dir)
    app.storage.initialize()
    ctx.obj = LocalSourceService(app)


@source_cmd.command("add")
@click.option("--name", required=True)
@click.option("--kind", type=click.Choice(["file", "folder", "git", "mercurial", "codex", "claude", "obsidian"]), required=True)
@click.option("--path", "root", type=click.Path(exists=True), required=True)
@click.option("--purpose", default="")
@click.option("--mode", type=click.Choice(["analyze", "direct"]), default="analyze")
@click.option("--model", default=None)
@click.option("--workflow-file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_obj
def add(service, **kwargs):
    """Quelle speichern. Der Import beginnt erst mit dem Importauftrag."""
    def save():
        path = kwargs.pop("workflow_file", None)
        if path:
            kwargs["workflow"] = json.loads(path.read_text())
        return service.add(**kwargs)
    _print(save)


@source_cmd.command("list")
@click.pass_obj
def list_sources(service):
    _print(service.list)


@source_cmd.command("preview")
@click.argument("source_id")
@click.pass_obj
def preview(service, source_id):
    """Bestand und Textbeispiele ohne Modellaufruf zeigen."""
    _print(lambda: service.preview(source_id))


@source_cmd.command("import")
@click.argument("source_id")
@click.pass_obj
def ingest(service, source_id):
    """Quelle mit ihrem gespeicherten Verfahren verarbeiten."""
    _print(lambda: service.ingest(source_id))


@source_cmd.command("probe")
@click.argument("source_id")
@click.pass_obj
def probe(service, source_id):
    """Eine Textprobe mit dem gewählten Modell analysieren, ohne Wissen zu speichern."""
    _print(lambda: service.probe(source_id))


@source_cmd.command("pause")
@click.argument("source_id")
@click.option("--resume", is_flag=True)
@click.pass_obj
def pause(service, source_id, resume):
    _print(lambda: service.pause(source_id, not resume))


@source_cmd.command("search")
@click.argument("query")
@click.pass_obj
def search(service, query):
    """Wissenseinträge aus lokalen Quellen mit Herkunft finden."""
    _print(lambda: service.search(query))


@source_cmd.command("evidence")
@click.argument("item_id")
@click.pass_obj
def evidence(service, item_id):
    """Den gespeicherten Originaltext eines Quellenobjekts lesen."""
    _print(lambda: service.evidence(item_id))


@source_cmd.command("templates")
@click.pass_obj
def reference_templates(service):
    """Anpassbare Referenzworkflows als JSON ausgeben."""
    from mycelos.knowledge.source_workflows import templates
    _print(templates)


@source_cmd.command("inspect")
@click.option("--kind", type=click.Choice(["file", "folder", "git", "mercurial", "codex", "claude", "obsidian"]), required=True)
@click.option("--path", "root", type=click.Path(exists=True), required=True)
@click.option("--workflow-file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_obj
def inspect_source(service, kind, root, workflow_file):
    """Eine Quelle vor dem Speichern ohne Sprachmodell sichten."""
    _print(lambda: service.inspect(kind, root, json.loads(workflow_file.read_text()) if workflow_file else None))


@source_cmd.command("check")
@click.argument("source_id")
@click.pass_obj
def check_source(service, source_id):
    """Änderungen ohne Modellaufruf und ohne Embeddings vormerken."""
    from mycelos.knowledge.source_checks import SourceCheckService
    _print(lambda: SourceCheckService(service.app, service.user_id).check(source_id))


@source_cmd.command("check-status")
@click.argument("source_id")
@click.pass_obj
def check_status(service, source_id):
    from mycelos.knowledge.source_checks import SourceCheckService
    _print(lambda: SourceCheckService(service.app, service.user_id).status(source_id))


@source_cmd.command("schedule")
@click.argument("source_id")
@click.option("--minutes", type=click.Choice(['0', '15', '60', '360', '1440']), required=True)
@click.pass_obj
def schedule_source(service, source_id, minutes):
    """Zeitplan der Quellenprüfung speichern. 0 bedeutet nur auf Auftrag."""
    from mycelos.knowledge.source_checks import SourceCheckService
    _print(lambda: SourceCheckService(service.app, service.user_id).configure(source_id, int(minutes)))


@source_cmd.command("pending")
@click.argument("source_id")
@click.pass_obj
def pending_source(service, source_id):
    """Vorgemerkte Änderungen ohne Inhaltsanalyse zeigen."""
    from mycelos.knowledge.source_checks import SourceCheckService
    _print(lambda: SourceCheckService(service.app, service.user_id).pending(source_id))
