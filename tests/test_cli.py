import os
from click.testing import CliRunner
from pathlib import Path

from mycelos.app import App
from mycelos.cli.main import cli


def _run_init(runner: CliRunner, data_dir: str) -> object:
    """Run mycelos init with default inputs (provider=1/anthropic, all models, confirm defaults).

    Sets ANTHROPIC_API_KEY in env so init skips the API key prompt.
    Input provides: provider choice (1) + model choice (all) + defaults confirm (y).
    """
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = "sk-test-dummy-key"
    return runner.invoke(
        cli,
        ["init", "--data-dir", data_dir],
        input="1\nall\ny\n",  # Provider 1 + all models + confirm defaults
        env=env,
    )


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Mycelos" in result.output


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    from mycelos import __version__
    assert __version__ in result.output


def test_init_creates_database(tmp_path):
    runner = CliRunner()
    result = _run_init(runner, str(tmp_path / "data"))
    assert result.exit_code == 0, f"Init failed: {result.output}"
    assert (tmp_path / "data" / "mycelos.db").exists()


def test_config_list_after_init(tmp_path):
    runner = CliRunner()
    data_dir = str(tmp_path / "data")
    _run_init(runner, data_dir)
    result = runner.invoke(cli, ["config", "list", "--data-dir", data_dir])
    assert result.exit_code == 0
    # The init wizard creates generations with descriptions like
    # "initial config" (from app.initialize) or "Initial setup" (from wizard)
    output_lower = result.output.lower()
    assert "initial" in output_lower


def test_init_creates_initial_config(tmp_path):
    runner = CliRunner()
    data_dir = tmp_path / "data"
    _run_init(runner, str(data_dir))

    app = App(data_dir)
    config = app.config.get_active_config()
    assert config is not None
    # After multi-provider wizard, the active config is a state snapshot
    assert config.get("schema_version") == 2 or config.get("version") == "0.1.0"


def test_memory_accessible_after_init(tmp_path):
    runner = CliRunner()
    data_dir = tmp_path / "data"
    _run_init(runner, str(data_dir))

    app = App(data_dir)
    app.memory.set("default", "system", "test.key", "test_value")
    assert app.memory.get("default", "system", "test.key") == "test_value"
