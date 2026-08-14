import importlib
import os
import sys
import types
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


def _fake_sentence_transformers_module(fake_constructor) -> types.ModuleType:
    """Build a stand-in ``sentence_transformers`` module for sys.modules.

    embeddings_setup() now does ``from sentence_transformers import
    SentenceTransformer`` lazily inside the function body, so tests must
    patch the importable module rather than a module-level attribute on
    embeddings_cmd (there no longer is one).
    """
    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = fake_constructor
    return fake_module


def test_setup_refuses_without_confirmation(monkeypatch, tmp_path) -> None:
    """Download is explicit: no --yes and no interactive confirm → no download."""
    data_dir = tmp_path / "data"
    _init_app(data_dir)

    called = False

    def _fake_constructor(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("SentenceTransformer must not be constructed")

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        _fake_sentence_transformers_module(_fake_constructor),
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

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        _fake_sentence_transformers_module(_fake_constructor),
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


class _BlockSentenceTransformers:
    """A sys.meta_path finder that makes ``sentence_transformers`` (and its
    submodules) unimportable, simulating a production image built without
    the optional `embeddings` extra (e.g. ``pip install ".[agent-toolkit]"``
    as the Dockerfile does)."""

    def find_module(self, fullname, path=None):
        if fullname == "sentence_transformers" or fullname.startswith(
            "sentence_transformers."
        ):
            return self
        return None

    def load_module(self, fullname):
        raise ImportError(f"{fullname} is blocked for this test")

    def find_spec(self, fullname, path, target=None):
        if fullname == "sentence_transformers" or fullname.startswith(
            "sentence_transformers."
        ):
            raise ImportError(f"{fullname} is blocked for this test")
        return None


def test_cli_main_imports_without_sentence_transformers(monkeypatch, tmp_path) -> None:
    """Regression guard: `mycelos` must not require sentence-transformers
    just to load the CLI, and `embeddings status` must run without it —
    only `embeddings setup` needs the model library, and only at call time.

    sentence-transformers ships solely in the optional `embeddings` extra,
    but the production Dockerfile installs ``.[agent-toolkit]`` only. A
    module-level `from sentence_transformers import ...` anywhere the CLI
    imports unconditionally would make every `mycelos` command — including
    `serve` — die at import time in the shipped image.
    """
    blocker = _BlockSentenceTransformers()

    # Remove any already-imported sentence_transformers (and mycelos modules
    # that transitively import it) so the reload below re-executes their
    # top-level import statements under the blocked import machinery.
    removed_modules = {
        name: mod
        for name, mod in list(sys.modules.items())
        if name == "sentence_transformers"
        or name.startswith("sentence_transformers.")
        or name == "mycelos.cli.main"
        or name == "mycelos.cli.embeddings_cmd"
    }
    for name in removed_modules:
        del sys.modules[name]

    sys.meta_path.insert(0, blocker)
    try:
        import mycelos.cli.main as main_module

        main_module = importlib.reload(main_module)
    finally:
        sys.meta_path.remove(blocker)
        # Clean up so this test cannot poison later tests: drop whatever
        # got imported under the blocked machinery, then restore whatever
        # was there before (if anything) via a normal re-import.
        for name in list(sys.modules):
            if name == "mycelos.cli.main" or name == "mycelos.cli.embeddings_cmd":
                del sys.modules[name]
        for name, mod in removed_modules.items():
            sys.modules.setdefault(name, mod)
        import mycelos.cli.main  # noqa: F401 — restore a normal import state

    # `embeddings status` must not need the library either — it only
    # reports presence/absence on disk.
    data_dir = tmp_path / "data"
    _init_app(data_dir)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["embeddings", "status", "--data-dir", str(data_dir)],
    )
    assert result.exit_code == 0, result.output
