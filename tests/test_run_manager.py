"""Tests for WorkflowRunManager methods."""

from __future__ import annotations

import os

import pytest


class TestListScheduled:
    def test_returns_empty_when_no_scheduled_tasks(self, tmp_path):
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-list-sched-empty"
        from mycelos.app import App
        app = App(tmp_path)
        app.initialize()
        assert app.workflow_run_manager.list_scheduled() == []

    def test_returns_scheduled_tasks_with_workflow_name(self, tmp_path):
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-list-sched-rows"
        from mycelos.app import App
        app = App(tmp_path)
        app.initialize()
        app.workflow_registry.register(
            workflow_id="wf-daily",
            name="Daily News",
            steps=[{"id": "s1", "action": "search_web", "description": "x"}],
            description="daily news summary",
            plan="Search the web and write a note.",
            model="anthropic/claude-haiku-4-5",
            allowed_tools=["search_web"],
        )
        app.storage.execute(
            "INSERT INTO scheduled_tasks (id, workflow_id, schedule, next_run, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("sched-1", "wf-daily", "0 8 * * *", "2026-04-09T08:00:00Z", "active"),
        )
        rows = app.workflow_run_manager.list_scheduled()
        assert len(rows) == 1
        assert rows[0]["id"] == "sched-1"
        assert rows[0]["workflow_id"] == "wf-daily"
        assert rows[0]["workflow_name"] == "Daily News"
        assert rows[0]["schedule"] == "0 8 * * *"
        assert rows[0]["next_run"] == "2026-04-09T08:00:00Z"

    def test_skips_paused_scheduled_tasks(self, tmp_path):
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-list-sched-paused"
        from mycelos.app import App
        app = App(tmp_path)
        app.initialize()
        app.workflow_registry.register(
            workflow_id="wf-paused",
            name="Paused WF",
            steps=[],
            plan="Do nothing.",
            model="anthropic/claude-haiku-4-5",
            allowed_tools=[],
        )
        app.storage.execute(
            "INSERT INTO scheduled_tasks (id, workflow_id, schedule, next_run, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("sched-2", "wf-paused", "0 8 * * *", "2026-04-09T08:00:00Z", "paused"),
        )
        assert app.workflow_run_manager.list_scheduled() == []
