"""mycelos embeddings — install and inspect the local embedding model."""

from __future__ import annotations

import os
from pathlib import Path

import click
from rich.console import Console
from sentence_transformers import SentenceTransformer

from mycelos.app import App
from mycelos.cli import default_data_dir
from mycelos.i18n import t
from mycelos.knowledge.embeddings import (
    LOCAL_MODEL_DIMENSION,
    LOCAL_MODEL_NAME,
    local_model_present,
    models_dir,
)

console = Console()

APPROX_MODEL_SIZE_MB = 120


def _target_dir() -> Path:
    return models_dir() / LOCAL_MODEL_NAME.replace("/", "__")


def _bind_data_dir(data_dir: Path) -> None:
    """Point models_dir()/default_data_dir() at --data-dir for this process.

    models_dir() resolves purely from $MYCELOS_DATA_DIR (see Task 2), so a
    CLI invocation with an explicit --data-dir must mirror it into the env
    var — otherwise `embeddings setup/status --data-dir X` would silently
    read/write the model under the default ~/.mycelos instead of X.
    """
    os.environ["MYCELOS_DATA_DIR"] = str(data_dir)


@click.group()
def embeddings_cmd():
    """Local embedding model — setup and status."""
    pass


@embeddings_cmd.command("setup")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
@click.option("--data-dir", type=click.Path(path_type=Path), default=default_data_dir)
def embeddings_setup(yes: bool, data_dir: Path) -> None:
    """Download the local embedding model into the Mycelos data directory."""
    _bind_data_dir(data_dir)
    target = _target_dir()

    console.print(f"\n[bold]{t('embeddings.setup.title')}[/bold]\n")
    console.print(f"  {t('embeddings.setup.model', model=LOCAL_MODEL_NAME)}")
    console.print(f"  {t('embeddings.setup.size', size=APPROX_MODEL_SIZE_MB)}")
    console.print(f"  {t('embeddings.setup.target', path=target)}", soft_wrap=True)
    console.print()

    if not yes and not click.confirm(t("embeddings.setup.confirm"), default=False):
        console.print(f"[yellow]{t('embeddings.setup.cancelled')}[/yellow]")
        raise SystemExit(1)

    # Point the sentence-transformers cache at our own models_dir so
    # nothing lands in the global (per-user) HF cache.
    os.environ["SENTENCE_TRANSFORMERS_HOME"] = str(models_dir())
    models_dir().mkdir(parents=True, exist_ok=True)

    console.print(f"[dim]{t('embeddings.setup.downloading')}[/dim]")
    try:
        model = SentenceTransformer(LOCAL_MODEL_NAME)
        model.save(str(target))
    except Exception as e:
        console.print(f"[red]{t('embeddings.setup.download_failed', error=e)}[/red]")
        raise SystemExit(1)

    console.print(f"[dim]{t('embeddings.setup.verifying')}[/dim]")
    try:
        probe = model.encode("query: mycelos")
        if probe is None or len(probe) == 0:
            raise RuntimeError("empty probe encode result")
    except Exception as e:
        console.print(f"[red]{t('embeddings.setup.verify_failed', error=e)}[/red]")
        raise SystemExit(1)

    console.print(f"\n[green]{t('embeddings.setup.done', path=target)}[/green]", soft_wrap=True)
    console.print(f"[dim]{t('embeddings.setup.reembed_note')}[/dim]\n")


@embeddings_cmd.command("status")
@click.option("--data-dir", type=click.Path(path_type=Path), default=default_data_dir)
def embeddings_status(data_dir: Path) -> None:
    """Show which embedding provider is selected and whether the local model is present."""
    _bind_data_dir(data_dir)
    present = local_model_present()
    present_label = (
        t("embeddings.status.present_true")
        if present
        else t("embeddings.status.present_false")
    )

    provider_name = "none"
    counts: tuple[int, int] | None = None
    app = _try_load_app(data_dir)
    if app is not None:
        try:
            provider_name = app.knowledge_base._embedding_provider.name
        except Exception:
            provider_name = "none"
        counts = _vector_vs_note_counts(app)

    console.print(f"\n[bold]{t('embeddings.status.title')}[/bold]\n")
    console.print(f"  {t('embeddings.status.provider', provider=provider_name)}")
    console.print(f"  {t('embeddings.status.model', model=LOCAL_MODEL_NAME)}")
    console.print(f"  {t('embeddings.status.present', present=present_label)}")
    console.print(f"  {t('embeddings.status.dir', path=models_dir())}", soft_wrap=True)
    console.print(f"  {t('embeddings.status.dimension', dimension=LOCAL_MODEL_DIMENSION)}")

    if counts is not None:
        vectors, notes = counts
        console.print(
            f"  {t('embeddings.status.counts', vectors=vectors, notes=notes)}"
        )
    console.print()


def _try_load_app(data_dir: Path) -> App | None:
    """Load the App when the data dir is reachable, else None.

    Loads the master key from disk the same way `doctor_cmd` does, so
    `embeddings status` works standalone without requiring the caller to
    export MYCELOS_MASTER_KEY first.
    """
    if not data_dir.exists():
        return None
    try:
        if not os.environ.get("MYCELOS_MASTER_KEY"):
            key_file = data_dir / ".master_key"
            if key_file.exists():
                os.environ["MYCELOS_MASTER_KEY"] = key_file.read_text().strip()
        return App(data_dir)
    except Exception:
        return None


def _vector_vs_note_counts(app: App) -> tuple[int, int] | None:
    """Return (vector_row_count, note_count) when the KB is reachable, else None."""
    try:
        note_row = app.storage.fetchone(
            "SELECT COUNT(*) AS c FROM knowledge_notes WHERE status != 'archived'"
        )
        notes = note_row["c"] if note_row else 0
        try:
            vec_row = app.storage.fetchone("SELECT COUNT(*) AS c FROM knowledge_vec")
            vectors = vec_row["c"] if vec_row else 0
        except Exception:
            vectors = 0
        return vectors, notes
    except Exception:
        return None
