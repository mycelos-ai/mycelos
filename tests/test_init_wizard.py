"""Tests for the simplified init wizard — single provider, auto model selection.

These tests use Click's CliRunner with enough input for the full wizard flow.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from mycelos.app import App
from mycelos.cli.main import cli

# Skip in CI — interactive init tests need tty input
pytestmark = pytest.mark.skipif(
    os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true",
    reason="Init wizard tests require interactive tty input",
)


def _run_init(
    runner: CliRunner,
    data_dir: str,
    api_key: str = "sk-ant-test-dummy-key-for-tests",
    extra_env: dict[str, str] | None = None,
) -> object:
    """Run mycelos init with given inputs.

    The init wizard flow asks:
      1. API key / provider detection (paste key or enter to pick manually)
      2. If key not recognized: manual provider selection (number)
      3. Connectivity check (may fail with test key — that's OK)
      4. Working directory (Enter accepts default)
      5. Home folder read access? (n)
      6. Documents read access? (n)
      7. Downloads read access? (n)
    """
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = api_key
    if extra_env:
        env.update(extra_env)

    # Input flow:
    # Line 1: API key paste (auto-detected as Anthropic via sk-ant prefix)
    # Line 2: Working directory (Enter = accept default)
    # Line 3: Home folder access? -> n
    # Line 4: Documents? -> n
    # Line 5: Downloads? -> n
    input_text = f"{api_key}\n\n\nn\nn\nn\n"

    return runner.invoke(
        cli,
        ["init", "--data-dir", data_dir],
        input=input_text,
        env=env,
    )


def _run_init_manual_provider(
    runner: CliRunner,
    data_dir: str,
    provider_choice: str = "99",
    extra_env: dict[str, str] | None = None,
) -> object:
    """Run init with manual provider selection (for unknown key/no key)."""
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = "sk-test"
    if extra_env:
        env.update(extra_env)

    # Input flow:
    # Line 1: API key paste -> "skip" (not recognized, falls to manual pick)
    # Line 2: Provider number (e.g., "99" for invalid/none)
    input_text = f"skip\n{provider_choice}\n"

    return runner.invoke(
        cli,
        ["init", "--data-dir", data_dir],
        input=input_text,
        env=env,
    )


def test_init_basic_flow(tmp_path: Path) -> None:
    """Basic init with Anthropic should create DB + auto-selected models."""
    runner = CliRunner()
    data_dir = str(tmp_path / "data")
    result = _run_init(runner, data_dir)

    assert result.exit_code == 0, f"Init failed: {result.output}"
    assert (tmp_path / "data" / "mycelos.db").exists()
    # With dummy key, connection fails but setup completes
    assert "Mycelos initialized" in result.output or "Mycelos is set up" in result.output


def test_init_auto_selects_models(tmp_path: Path) -> None:
    """Init auto-selects models without user interaction."""
    runner = CliRunner()
    data_dir = tmp_path / "data"
    _run_init(runner, str(data_dir))

    app = App(data_dir)
    exec_models = app.model_registry.resolve_models(None, "execution")
    assert len(exec_models) >= 1


def test_init_registers_connectors(tmp_path: Path) -> None:
    """Init should register DuckDuckGo and HTTP connectors."""
    runner = CliRunner()
    data_dir = tmp_path / "data"
    _run_init(runner, str(data_dir))

    app = App(data_dir)
    ddg = app.connector_registry.get("web-search-duckduckgo")
    assert ddg is not None
    assert "search.web" in ddg["capabilities"]

    http = app.connector_registry.get("http")
    assert http is not None


def test_init_creates_generation(tmp_path: Path) -> None:
    """Init should create a config generation with full state snapshot."""
    runner = CliRunner()
    data_dir = tmp_path / "data"
    _run_init(runner, str(data_dir))

    app = App(data_dir)
    gen_id = app.config.get_active_generation_id()
    assert gen_id is not None

    row = app.storage.fetchone(
        "SELECT config_snapshot FROM config_generations WHERE id = ?", (gen_id,)
    )
    snapshot = json.loads(row["config_snapshot"])
    assert snapshot["schema_version"] == 2
    assert "connectors" in snapshot
    assert len(snapshot["connectors"]) >= 2


def test_init_reinit_asks_confirmation(tmp_path: Path) -> None:
    """Reinitializing should ask for confirmation."""
    runner = CliRunner()
    data_dir = str(tmp_path / "data")

    # First init
    _run_init(runner, data_dir)

    # Second init -- say no to reinit
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = "sk-ant-test-dummy-key"
    result = runner.invoke(
        cli,
        ["init", "--data-dir", data_dir],
        input="n\n",
        env=env,
    )
    assert (
        "already initialized" in result.output.lower()
        or "reinitialize" in result.output.lower()
    )


def test_init_no_providers_still_initializes(tmp_path: Path) -> None:
    """Selecting no valid providers should still create the DB."""
    runner = CliRunner()
    data_dir = str(tmp_path / "data")
    result = _run_init_manual_provider(runner, data_dir, provider_choice="99")

    assert result.exit_code == 0, f"Init failed: {result.output}"
    assert (tmp_path / "data" / "mycelos.db").exists()


def test_init_master_key_generated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Init should generate a master key file."""
    runner = CliRunner()
    data_dir = tmp_path / "data"

    monkeypatch.delenv("MYCELOS_MASTER_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-dummy-key")

    runner.invoke(
        cli,
        ["init", "--data-dir", str(data_dir)],
        input="sk-ant-test-dummy-key\n\n\nn\nn\nn\n",
    )

    key_file = data_dir / ".master_key"
    assert key_file.exists()
    assert len(key_file.read_text().strip()) > 20


def test_init_snapshot_contains_models(tmp_path: Path) -> None:
    """The state snapshot should include registered LLM models."""
    runner = CliRunner()
    data_dir = tmp_path / "data"
    _run_init(runner, str(data_dir))

    app = App(data_dir)
    gen_id = app.config.get_active_generation_id()
    row = app.storage.fetchone(
        "SELECT config_snapshot FROM config_generations WHERE id = ?", (gen_id,)
    )
    snapshot = json.loads(row["config_snapshot"])
    assert "llm" in snapshot
    assert len(snapshot["llm"].get("models", {})) > 0


def test_init_snapshot_contains_assignments(tmp_path: Path) -> None:
    """The state snapshot should include model assignments."""
    runner = CliRunner()
    data_dir = tmp_path / "data"
    _run_init(runner, str(data_dir))

    app = App(data_dir)
    gen_id = app.config.get_active_generation_id()
    row = app.storage.fetchone(
        "SELECT config_snapshot FROM config_generations WHERE id = ?", (gen_id,)
    )
    snapshot = json.loads(row["config_snapshot"])
    assignments = snapshot.get("llm", {}).get("assignments", {})
    assert len(assignments) > 0
    assert any("execution" in key for key in assignments)


def test_init_hints_at_model_command(tmp_path: Path) -> None:
    """Init should hint at 'mycelos model' for advanced config."""
    runner = CliRunner()
    data_dir = str(tmp_path / "data")
    result = _run_init(runner, data_dir)

    assert "mycelos model" in result.output


def test_init_no_manual_model_selection(tmp_path: Path) -> None:
    """Init should NOT ask user to pick individual models — auto-select only."""
    runner = CliRunner()
    data_dir = str(tmp_path / "data")
    result = _run_init(runner, data_dir)

    # These prompts should NOT appear in simplified init
    assert "Enable which models" not in result.output
    assert "Use these defaults?" not in result.output
