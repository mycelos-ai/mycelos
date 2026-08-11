import os
from pathlib import Path

from click.testing import CliRunner

from mycelos.app import App
from mycelos.cli.main import cli


def _init_app(data_dir: Path) -> App:
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-cli-export"
    app = App(data_dir)
    app.initialize()
    return app


def test_export_writes_bundle_to_dir(tmp_path):
    data_dir = tmp_path / "data"
    app = _init_app(data_dir)
    app.knowledge_base.write(title="Coffee", content="Dark roast.", type="note")

    out = tmp_path / "bundle"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["knowledge", "export", "--okf", str(out), "--data-dir", str(data_dir)],
    )

    assert result.exit_code == 0, result.output
    assert (out / "index.md").exists()
    assert (out / "notes" / "coffee.md").exists()
    assert "Dark roast." in (out / "notes" / "coffee.md").read_text(encoding="utf-8")


def test_export_refuses_nonempty_dir_without_force(tmp_path):
    data_dir = tmp_path / "data"
    _init_app(data_dir)

    out = tmp_path / "bundle"
    out.mkdir()
    (out / "existing.txt").write_text("keep me", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["knowledge", "export", "--okf", str(out), "--data-dir", str(data_dir)],
    )

    assert result.exit_code != 0
    # The pre-existing file is untouched.
    assert (out / "existing.txt").read_text(encoding="utf-8") == "keep me"


def test_export_force_overwrites_nonempty_dir(tmp_path):
    data_dir = tmp_path / "data"
    app = _init_app(data_dir)
    app.knowledge_base.write(title="Coffee", content="body", type="note")

    out = tmp_path / "bundle"
    out.mkdir()
    (out / "existing.txt").write_text("old", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["knowledge", "export", "--okf", str(out), "--force", "--data-dir", str(data_dir)],
    )

    assert result.exit_code == 0, result.output
    assert (out / "index.md").exists()
