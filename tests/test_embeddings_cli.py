import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from mycelos.app import App
from mycelos.cli.main import cli
from mycelos.knowledge.embeddings import LOCAL_MODEL_NAME


def _init_app(data_dir: Path) -> App:
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-cli-embeddings"
    app = App(data_dir)
    app.initialize()
    return app


def test_setup_refuses_without_confirmation(monkeypatch, tmp_path) -> None:
    """Download is explicit: no --yes and no interactive confirm → no download."""
    data_dir = tmp_path / "data"
    _init_app(data_dir)

    called = False

    def _fake_constructor(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("SentenceTransformer must not be constructed")

    monkeypatch.setattr(
        "mycelos.cli.embeddings_cmd.SentenceTransformer", _fake_constructor
    )

    runner = CliRunner()
    # No --yes, and stdin input "n" declines the interactive confirmation.
    result = runner.invoke(
        cli,
        ["embeddings", "setup", "--data-dir", str(data_dir)],
        input="n\n",
    )

    assert result.exit_code != 0
    assert not called, "downloader was invoked despite refused confirmation"


def test_setup_downloads_to_models_dir(monkeypatch, tmp_path) -> None:
    """--yes downloads into models_dir(), not the global HF cache."""
    data_dir = tmp_path / "data"
    _init_app(data_dir)

    saved_to: list[str] = []

    class _FakeModel:
        def save(self, path):
            saved_to.append(path)

        def encode(self, text, **kwargs):
            return [0.1] * 384

    constructed_with: list[str] = []

    def _fake_constructor(model_name, *args, **kwargs):
        constructed_with.append(model_name)
        return _FakeModel()

    monkeypatch.setattr(
        "mycelos.cli.embeddings_cmd.SentenceTransformer", _fake_constructor
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["embeddings", "setup", "--yes", "--data-dir", str(data_dir)],
    )

    assert result.exit_code == 0, result.output
    assert constructed_with == [LOCAL_MODEL_NAME]

    from mycelos.knowledge.embeddings import models_dir

    expected_target = str(models_dir() / LOCAL_MODEL_NAME.replace("/", "__"))
    assert saved_to == [expected_target]
    assert os.environ.get("SENTENCE_TRANSFORMERS_HOME") == str(models_dir())


def test_status_reports_absent_model(monkeypatch, tmp_path) -> None:
    data_dir = tmp_path / "data"
    _init_app(data_dir)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["embeddings", "status", "--data-dir", str(data_dir)],
    )

    assert result.exit_code == 0, result.output
    assert LOCAL_MODEL_NAME in result.output
    from mycelos.knowledge.embeddings import models_dir

    assert str(models_dir()) in result.output
