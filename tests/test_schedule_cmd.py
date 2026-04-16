"""Tests for mycelos schedule CLI commands."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from mycelos.app import App
from mycelos.cli.main import cli


def _init_app(tmp_path: Path) -> App:
    """Initialize an App instance for testing."""
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-schedule-cmd"
    app = App(tmp_path)
    app.initialize()
    # Register a test workflow
    app.workflow_registry.register("news-summary", "News", [{"id": "s1"}])
    return app


def test_schedule_list_empty(tmp_path: Path) -> None:
    """List with no scheduled tasks shows helpful message."""
    _init_app(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["schedule", "list", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "No scheduled tasks" in result.output


def test_schedule_add(tmp_path: Path) -> None:
    """Add a scheduled task for an existing workflow."""
    _init_app(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "schedule", "add", "news-summary",
        "--cron", "0 8 * * *",
        "--data-dir", str(tmp_path),
    ])
    assert result.exit_code == 0
    assert "Scheduled task created" in result.output


def test_schedule_add_with_input(tmp_path: Path) -> None:
    """Add a task with JSON input and budget."""
    _init_app(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "schedule", "add", "news-summary",
        "--cron", "*/5 * * * *",
        "--input", '{"query": "AI news"}',
        "--budget", "0.50",
        "--data-dir", str(tmp_path),
    ])
    assert result.exit_code == 0
    assert "Scheduled task created" in result.output
    assert "$0.50" in result.output


def test_schedule_add_missing_workflow(tmp_path: Path) -> None:
    """Add with nonexistent workflow shows error."""
    _init_app(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, [
        "schedule", "add", "nonexistent",
        "--cron", "0 8 * * *",
        "--data-dir", str(tmp_path),
    ])
    assert "not found" in result.output


def test_schedule_list_after_add(tmp_path: Path) -> None:
    """List shows tasks that were added."""
    app = _init_app(tmp_path)
    app.schedule_manager.add("news-summary", "0 8 * * *")

    runner = CliRunner()
    result = runner.invoke(cli, ["schedule", "list", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "news-summary" in result.output


def test_schedule_pause_resume(tmp_path: Path) -> None:
    """Pause and resume a scheduled task by prefix."""
    app = _init_app(tmp_path)
    task_id = app.schedule_manager.add("news-summary", "0 8 * * *")

    runner = CliRunner()
    # Pause
    result = runner.invoke(cli, ["schedule", "pause", task_id[:8], "--data-dir", str(tmp_path)])
    assert "Paused" in result.output

    # Resume
    result = runner.invoke(cli, ["schedule", "resume", task_id[:8], "--data-dir", str(tmp_path)])
    assert "Resumed" in result.output


def test_schedule_delete(tmp_path: Path) -> None:
    """Delete a scheduled task with confirmation."""
    app = _init_app(tmp_path)
    task_id = app.schedule_manager.add("news-summary", "0 8 * * *")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["schedule", "delete", task_id[:8], "--data-dir", str(tmp_path)],
        input="y\n",
    )
    assert "Deleted" in result.output


def test_schedule_help() -> None:
    """Schedule group shows help text."""
    runner = CliRunner()
    result = runner.invoke(cli, ["schedule", "--help"])
    assert result.exit_code == 0
    assert "schedule" in result.output.lower()
