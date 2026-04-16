"""Tests for YAML workflow seed import at init."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from mycelos.app import App
from mycelos.cli.main import cli
from mycelos.workflows.workflow_registry import WorkflowRegistry


def _run_init(runner: CliRunner, data_dir: str) -> object:
    """Run mycelos init with standard Anthropic provider selection."""
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = "sk-test-workflow-import"
    return runner.invoke(
        cli,
        ["init", "--data-dir", data_dir],
        input="1\nall\ny\n",
        env=env,
    )


def test_init_imports_news_summary_workflow(tmp_path: Path) -> None:
    """Init should import the news-summary seed workflow."""
    runner = CliRunner()
    result = _run_init(runner, str(tmp_path / "data"))
    assert result.exit_code == 0, f"Failed: {result.output}"

    app = App(tmp_path / "data")
    wf = app.workflow_registry.get("news-summary")
    assert wf is not None
    assert wf["name"] == "news-summary"
    # After workflow refactor: plan is required, steps are optional
    assert wf.get("plan") or len(wf.get("steps", [])) > 0


def test_init_imports_create_agent_workflow(tmp_path: Path) -> None:
    """Init should import the create-agent seed workflow."""
    runner = CliRunner()
    result = _run_init(runner, str(tmp_path / "data"))
    assert result.exit_code == 0, f"Failed: {result.output}"

    app = App(tmp_path / "data")
    wf = app.workflow_registry.get("create-agent")
    assert wf is not None
    assert any(s["id"] == "generate-code" for s in wf["steps"])
    assert any(s["id"] == "audit" for s in wf["steps"])


def test_workflows_in_config_snapshot(tmp_path: Path) -> None:
    """Imported workflows should appear in the config generation snapshot."""
    runner = CliRunner()
    _run_init(runner, str(tmp_path / "data"))

    app = App(tmp_path / "data")
    gen_id = app.config.get_active_generation_id()
    row = app.storage.fetchone(
        "SELECT config_snapshot FROM config_generations WHERE id = ?", (gen_id,)
    )
    snapshot = json.loads(row["config_snapshot"])
    assert "workflows" in snapshot
    assert len(snapshot["workflows"]) >= 1


def test_reinit_updates_existing_workflows(tmp_path: Path) -> None:
    """Reimporting should update version, not fail."""
    runner = CliRunner()
    _run_init(runner, str(tmp_path / "data"))

    app = App(tmp_path / "data")
    wf1 = app.workflow_registry.get("news-summary")
    version1 = wf1["version"] if wf1 else 0

    # Reinit -- say yes to reinitialize
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = "sk-test"
    runner.invoke(
        cli,
        ["init", "--data-dir", str(tmp_path / "data")],
        input="y\n1\nall\ny\n",
        env=env,
    )

    app2 = App(tmp_path / "data")
    wf2 = app2.workflow_registry.get("news-summary")
    assert wf2 is not None
    assert wf2["version"] >= version1


def test_workflow_registry_from_yaml_directly(tmp_path: Path) -> None:
    """WorkflowRegistry.import_from_yaml should work with the seed files."""
    app = App(tmp_path)
    app.initialize()
    registry = WorkflowRegistry(app.storage)

    # Import the news-summary YAML
    yaml_path = Path(__file__).parent.parent / "artifacts" / "workflows" / "news-summary.yaml"
    if yaml_path.exists():
        wf_id = registry.import_from_yaml(yaml_path)
        assert wf_id == "news-summary"
        wf = registry.get(wf_id)
        assert wf is not None
        # news-summary uses plan (not steps) after workflow refactor
        assert wf.get("plan") or len(wf.get("steps", [])) > 0


def test_create_agent_workflow_has_seven_steps(tmp_path: Path) -> None:
    """The create-agent workflow should have all 7 pipeline steps (incl. gherkin)."""
    app = App(tmp_path)
    app.initialize()
    registry = WorkflowRegistry(app.storage)

    yaml_path = Path(__file__).parent.parent / "artifacts" / "workflows" / "create-agent.yaml"
    assert yaml_path.exists(), f"create-agent.yaml not found at {yaml_path}"

    wf_id = registry.import_from_yaml(yaml_path)
    wf = registry.get(wf_id)
    assert wf is not None
    step_ids = [s["id"] for s in wf["steps"]]
    assert step_ids == [
        "feasibility",
        "generate-gherkin",
        "generate-code",
        "generate-tests",
        "run-tests",
        "audit",
        "register",
    ]


def test_create_agent_workflow_scope_and_tags(tmp_path: Path) -> None:
    """The create-agent workflow should have correct scope and tags."""
    app = App(tmp_path)
    app.initialize()
    registry = WorkflowRegistry(app.storage)

    yaml_path = Path(__file__).parent.parent / "artifacts" / "workflows" / "create-agent.yaml"
    wf_id = registry.import_from_yaml(yaml_path)
    wf = registry.get(wf_id)
    assert wf is not None
    assert "agent.register" in wf["scope"]
    assert "sandbox.execute" in wf["scope"]
    assert "system" in wf["tags"]
    assert "agent-creation" in wf["tags"]
