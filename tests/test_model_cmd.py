"""Tests for mycelos model — model management and agent assignment command."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from mycelos.app import App
from mycelos.cli.main import cli


def _init_mycelos(tmp_path: Path) -> tuple[CliRunner, Path]:
    """Initialize Mycelos programmatically (bypasses interactive init for CI)."""
    runner = CliRunner()
    data_dir = tmp_path / "data"
    os.environ["MYCELOS_MASTER_KEY"] = "test-model-cmd-key"

    app = App(data_dir)
    app.initialize()

    # Register test models + system agents (simulates what interactive init does)
    app.model_registry.add_model(
        model_id="anthropic/claude-sonnet-4-6", provider="anthropic",
        tier="sonnet", input_cost_per_1k=0.003, output_cost_per_1k=0.015,
        max_context=200000,
    )
    app.model_registry.add_model(
        model_id="anthropic/claude-haiku-4-5", provider="anthropic",
        tier="haiku", input_cost_per_1k=0.001, output_cost_per_1k=0.005,
        max_context=200000,
    )

    # Register system agents
    from mycelos.cli.init_cmd import _register_system_agents
    _register_system_agents(app)

    return runner, data_dir


class TestModelList:
    """Tests for `mycelos model list`."""

    def test_list_shows_models(self, tmp_path: Path) -> None:
        """Should show all configured models in a table."""
        runner, data_dir = _init_mycelos(tmp_path)
        env = os.environ.copy()
        env["ANTHROPIC_API_KEY"] = "sk-test-dummy"
        result = runner.invoke(
            cli, ["model", "--data-dir", str(data_dir), "list"],
            env=env,
        )
        assert result.exit_code == 0, f"Failed: {result.output}"
        # Should show model IDs and tiers
        assert "anthropic" in result.output.lower() or "claude" in result.output.lower()

    def test_list_empty_shows_hint(self, tmp_path: Path) -> None:
        """With no models, should hint at `mycelos model add`."""
        runner = CliRunner()
        data_dir = tmp_path / "data"
        # Initialize without provider (programmatic — no interactive prompts)
        os.environ["MYCELOS_MASTER_KEY"] = "test-model-empty-key"
        app = App(data_dir)
        app.initialize()

        env = os.environ.copy()
        env["ANTHROPIC_API_KEY"] = "sk-test"
        result = runner.invoke(
            cli, ["model", "--data-dir", str(data_dir), "list"],
            env=env,
        )
        assert "mycelos model add" in result.output


class TestModelRemove:
    """Tests for `mycelos model remove`."""

    def test_remove_existing_model(self, tmp_path: Path) -> None:
        """Should remove a model and its assignments."""
        runner, data_dir = _init_mycelos(tmp_path)
        env = os.environ.copy()
        env["ANTHROPIC_API_KEY"] = "sk-test-dummy"

        app = App(data_dir)
        models = app.model_registry.list_models()
        if not models:
            pytest.skip("No models registered during init")

        model_id = models[0]["id"]
        result = runner.invoke(
            cli, ["model", "--data-dir", str(data_dir), "remove", model_id],
            input="y\n", env=env,
        )
        assert result.exit_code == 0
        assert "Removed" in result.output

        # Verify it's gone
        app2 = App(data_dir)
        assert app2.model_registry.get_model(model_id) is None

    def test_remove_nonexistent_model(self, tmp_path: Path) -> None:
        """Should show error for unknown model ID."""
        runner, data_dir = _init_mycelos(tmp_path)
        env = os.environ.copy()
        env["ANTHROPIC_API_KEY"] = "sk-test-dummy"

        result = runner.invoke(
            cli, ["model", "--data-dir", str(data_dir), "remove", "nonexistent-model"],
            env=env,
        )
        assert "not found" in result.output.lower()


class TestModelTest:
    """Tests for `mycelos model test`."""

    def test_test_all_models(self, tmp_path: Path) -> None:
        """Should test all configured models."""
        runner, data_dir = _init_mycelos(tmp_path)
        env = os.environ.copy()
        env["ANTHROPIC_API_KEY"] = "sk-test-dummy"

        # Mock the broker so we don't make real API calls
        mock_broker_cls = MagicMock()
        mock_broker_cls.return_value.complete.return_value = MagicMock(content="OK")

        with patch("mycelos.llm.broker.LiteLLMBroker", mock_broker_cls):
            result = runner.invoke(
                cli, ["model", "--data-dir", str(data_dir), "test"],
                env=env,
            )

        assert result.exit_code == 0

    def test_test_nonexistent_model(self, tmp_path: Path) -> None:
        """Should show error for unknown model ID."""
        runner, data_dir = _init_mycelos(tmp_path)
        env = os.environ.copy()
        env["ANTHROPIC_API_KEY"] = "sk-test-dummy"

        result = runner.invoke(
            cli, ["model", "--data-dir", str(data_dir), "test", "nonexistent"],
            env=env,
        )
        assert "not found" in result.output.lower()


class TestModelAgents:
    """Tests for `mycelos model agents`."""

    def test_agents_shows_table(self, tmp_path: Path) -> None:
        """Should show agent table with model assignments."""
        runner, data_dir = _init_mycelos(tmp_path)
        env = os.environ.copy()
        env["ANTHROPIC_API_KEY"] = "sk-test-dummy"

        # Quit immediately with 'q'
        result = runner.invoke(
            cli, ["model", "--data-dir", str(data_dir), "agents"],
            input="q\n", env=env,
        )
        assert result.exit_code == 0
        # Should show system agent names
        assert "Builder" in result.output or "builder" in result.output.lower() or "Mycelos" in result.output

    def test_agents_drill_down_and_keep(self, tmp_path: Path) -> None:
        """Entering an agent number, then Enter to keep defaults, then quit."""
        runner, data_dir = _init_mycelos(tmp_path)
        env = os.environ.copy()
        env["ANTHROPIC_API_KEY"] = "sk-test-dummy"

        # Select agent 1, press Enter to keep, then quit
        result = runner.invoke(
            cli, ["model", "--data-dir", str(data_dir), "agents"],
            input="1\n\nq\n", env=env,
        )
        assert result.exit_code == 0


class TestModelCheck:
    """Tests for `mycelos model check`."""

    def test_check_shows_summary(self, tmp_path: Path) -> None:
        """Should show tier summary and validation."""
        runner, data_dir = _init_mycelos(tmp_path)
        env = os.environ.copy()
        env["ANTHROPIC_API_KEY"] = "sk-test-dummy"

        # Skip connectivity test
        result = runner.invoke(
            cli, ["model", "--data-dir", str(data_dir), "check"],
            input="n\n", env=env,
        )
        assert result.exit_code == 0
        assert "Configuration Check" in result.output


class TestModelNotInitialized:
    """Tests for running model commands before init."""

    def test_model_list_before_init(self, tmp_path: Path) -> None:
        """Should show error when Mycelos is not initialized."""
        runner = CliRunner()
        data_dir = str(tmp_path / "nonexistent")
        result = runner.invoke(
            cli, ["model", "--data-dir", data_dir, "list"],
        )
        assert result.exit_code != 0 or "not initialized" in result.output.lower()
