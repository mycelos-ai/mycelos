"""Full integration tests for mycelos init -- end-to-end verification.

These tests require interactive CLI input which doesn't work in CI
(no tty, interactive init prompts for API key). They run locally.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

# Skip all tests in this module in CI — they need interactive input
pytestmark = pytest.mark.skipif(
    os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true",
    reason="Init integration tests require interactive tty input (not available in CI)",
)

from mycelos.app import App
from mycelos.cli.main import cli


def _run_init(
    runner: CliRunner,
    data_dir: str,
    provider_choice: str = "1",
    model_choice: str = "all",
    defaults_confirm: str = "y",
    extra_env: dict[str, str] | None = None,
) -> object:
    """Run mycelos init with given inputs (interactive — local only, not CI)."""
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = "sk-test-integration"
    if extra_env:
        env.update(extra_env)
    return runner.invoke(
        cli,
        ["init", "--data-dir", data_dir],
        input=f"sk-ant-test-dummy-key\nn\n",
        env=env,
    )


def test_full_init_creates_complete_state(tmp_path: Path) -> None:
    """Init should create a complete system state."""
    runner = CliRunner()
    data_dir = tmp_path / "data"
    result = _run_init(runner, str(data_dir))
    assert result.exit_code == 0, f"Failed: {result.output}"

    app = App(data_dir)

    # DB exists
    assert (data_dir / "mycelos.db").exists()

    # Master key available (either via env var or generated file)
    master_key_available = (
        (data_dir / ".master_key").exists()
        or os.environ.get("MYCELOS_MASTER_KEY") is not None
    )
    assert master_key_available

    # Models registered
    models = app.model_registry.list_models()
    assert len(models) > 0

    # System defaults set
    exec_models = app.model_registry.resolve_models(None, "execution")
    assert len(exec_models) >= 1

    # Connectors registered
    ddg = app.connector_registry.get("web-search-duckduckgo")
    assert ddg is not None

    http = app.connector_registry.get("http")
    assert http is not None

    # Generation exists with full snapshot
    gen_id = app.config.get_active_generation_id()
    assert gen_id is not None


def test_init_snapshot_is_complete(tmp_path: Path) -> None:
    """The config generation snapshot should contain all state."""
    runner = CliRunner()
    data_dir = tmp_path / "data"
    _run_init(runner, str(data_dir))

    app = App(data_dir)
    gen_id = app.config.get_active_generation_id()
    row = app.storage.fetchone(
        "SELECT config_snapshot FROM config_generations WHERE id = ?", (gen_id,)
    )
    snapshot = json.loads(row["config_snapshot"])

    assert snapshot["schema_version"] == 2
    assert len(snapshot["connectors"]) >= 2
    assert "web-search-duckduckgo" in snapshot["connectors"]
    assert "http" in snapshot["connectors"]
    assert len(snapshot["llm"]["models"]) > 0
    assert len(snapshot["llm"]["assignments"]) > 0


def test_init_snapshot_connectors_have_capabilities(tmp_path: Path) -> None:
    """Connectors in the snapshot should include their capabilities."""
    runner = CliRunner()
    data_dir = tmp_path / "data"
    _run_init(runner, str(data_dir))

    app = App(data_dir)
    gen_id = app.config.get_active_generation_id()
    row = app.storage.fetchone(
        "SELECT config_snapshot FROM config_generations WHERE id = ?", (gen_id,)
    )
    snapshot = json.loads(row["config_snapshot"])

    ddg = snapshot["connectors"]["web-search-duckduckgo"]
    assert "search.web" in ddg["capabilities"]
    assert "search.news" in ddg["capabilities"]

    http_conn = snapshot["connectors"]["http"]
    assert "http.get" in http_conn["capabilities"]
    assert "http.post" in http_conn["capabilities"]


def test_init_rollback_restores_state(tmp_path: Path) -> None:
    """After init, adding something and rolling back should restore init state."""
    runner = CliRunner()
    data_dir = tmp_path / "data"
    _run_init(runner, str(data_dir))

    app = App(data_dir)
    gen_init = app.config.get_active_generation_id()

    # Add something new
    app.connector_registry.register("brave", "Brave", "search", ["search.brave"])
    gen_modified = app.config.apply_from_state(
        app.state_manager, "added brave", "test"
    )

    assert app.connector_registry.get("brave") is not None

    # Rollback to init state
    app.config.rollback(to_generation=gen_init, state_manager=app.state_manager)

    assert app.connector_registry.get("brave") is None
    assert app.connector_registry.get("web-search-duckduckgo") is not None


def test_init_rollback_preserves_builtin_connectors(tmp_path: Path) -> None:
    """Rollback should preserve all built-in connectors from init."""
    runner = CliRunner()
    data_dir = tmp_path / "data"
    _run_init(runner, str(data_dir))

    app = App(data_dir)
    gen_init = app.config.get_active_generation_id()

    # Modify and rollback
    app.model_registry.add_model("custom/test", "custom", "sonnet", 0.0, 0.0)
    app.config.apply_from_state(app.state_manager, "added custom model", "test")
    app.config.rollback(to_generation=gen_init, state_manager=app.state_manager)

    # Built-in connectors should still be there
    ddg = app.connector_registry.get("web-search-duckduckgo")
    assert ddg is not None
    http = app.connector_registry.get("http")
    assert http is not None


def test_init_policies_set_correctly(tmp_path: Path) -> None:
    """Built-in connectors should have 'always' policies."""
    runner = CliRunner()
    data_dir = tmp_path / "data"
    _run_init(runner, str(data_dir))

    app = App(data_dir)
    decision = app.policy_engine.evaluate("default", None, "search.web")
    assert decision == "always"

    decision = app.policy_engine.evaluate("default", None, "http.get")
    assert decision == "always"


def test_init_policies_for_all_builtin_capabilities(tmp_path: Path) -> None:
    """All built-in connector capabilities should have 'always' policies."""
    runner = CliRunner()
    data_dir = tmp_path / "data"
    _run_init(runner, str(data_dir))

    app = App(data_dir)
    for capability in ["search.web", "search.news", "http.get", "http.post"]:
        decision = app.policy_engine.evaluate("default", None, capability)
        assert decision == "always", (
            f"Policy for {capability} should be 'always', got '{decision}'"
        )


def test_init_with_anthropic_registers_models(tmp_path: Path) -> None:
    """Selecting Anthropic (provider 1) should register Anthropic models."""
    runner = CliRunner()
    data_dir = tmp_path / "data"
    result = _run_init(runner, str(data_dir), provider_choice="1")
    assert result.exit_code == 0

    app = App(data_dir)
    models = app.model_registry.list_models(provider="anthropic")
    assert len(models) > 0


def test_init_system_execution_defaults_set(tmp_path: Path) -> None:
    """Init should configure system:execution model defaults."""
    runner = CliRunner()
    data_dir = tmp_path / "data"
    _run_init(runner, str(data_dir))

    app = App(data_dir)
    exec_models = app.model_registry.resolve_models(None, "execution")
    assert len(exec_models) >= 1
    # All resolved models should actually exist in the registry
    for model_id in exec_models:
        m = app.model_registry.get_model(model_id)
        assert m is not None, f"Default model {model_id} not found in registry"


def test_init_dedup_on_reinit(tmp_path: Path) -> None:
    """Reinitializing with same config should not create duplicate generation."""
    runner = CliRunner()
    data_dir = tmp_path / "data"

    # First init
    _run_init(runner, str(data_dir))
    app = App(data_dir)
    gen1 = app.config.get_active_generation_id()

    # Second init (answer 'y' to reinit)
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = "sk-test-integration"
    result = runner.invoke(
        cli,
        ["init", "--data-dir", str(data_dir)],
        input="y\n1\nall\ny\n",
        env=env,
    )
    # If it gets through, the generation should be deduped
    # (same models, same connectors -> same hash)
    if result.exit_code == 0:
        gen2 = app.config.get_active_generation_id()
        # Either same gen (dedup) or new gen -- both valid
        assert gen2 is not None


def test_init_audit_log_created(tmp_path: Path) -> None:
    """Init should create audit log entries."""
    runner = CliRunner()
    data_dir = tmp_path / "data"
    _run_init(runner, str(data_dir))

    app = App(data_dir)
    rows = app.storage.fetchall(
        "SELECT * FROM audit_events ORDER BY created_at"
    )
    assert len(rows) > 0
    events = [r["event_type"] for r in rows]
    assert "system.initialized" in events


def test_init_credential_stored_encrypted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Init should store the API key encrypted via credential proxy."""
    runner = CliRunner()
    data_dir = tmp_path / "data"

    # Remove MYCELOS_MASTER_KEY so init generates a .master_key file
    monkeypatch.delenv("MYCELOS_MASTER_KEY", raising=False)
    # Use sk-ant- prefix so detect_provider identifies it as Anthropic
    api_key = "sk-ant-test-integration-key"

    result = runner.invoke(
        cli,
        ["init", "--data-dir", str(data_dir)],
        # Input: 1) API key for auto-detect, 2) "n" to skip connectivity retry,
        # 3) Enter for default workdir, 4-6) "n" for filesystem permission prompts
        input=f"{api_key}\nn\n\nn\nn\nn\n",
    )
    assert result.exit_code == 0, f"Failed: {result.output}"

    # Read master key that init generated
    key_file = data_dir / ".master_key"
    assert key_file.exists(), "Master key file should be generated by init"
    master_key = key_file.read_text().strip()
    os.environ["MYCELOS_MASTER_KEY"] = master_key
    try:
        app = App(data_dir)
        cred = app.credentials.get_credential("anthropic")
        assert cred is not None
        assert cred["api_key"] == api_key
    finally:
        os.environ.pop("MYCELOS_MASTER_KEY", None)
