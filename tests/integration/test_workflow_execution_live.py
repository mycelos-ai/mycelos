"""Live integration tests for workflow execution and scheduling.

Tests:
1. Workflow execution with real tools (search_web → note_write)
2. Scheduling: create → trigger → verify execution
3. Builder reuses existing workflow (requires LLM)

Run: pytest -m integration tests/integration/test_workflow_execution_live.py -v -s
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def app(integration_app):
    """Use the isolated integration_app fixture (temp DB + creds from .env.test)."""
    return integration_app


@pytest.mark.integration
class TestWorkflowExecution:
    """Test workflow execution with real tools."""

    def test_search_web_via_workflow_agent(self, app):
        """A workflow with search_web executes via WorkflowAgent."""
        from mycelos.workflows.agent import WorkflowAgent

        workflow_def = {
            "plan": (
                "Search the web for the given topic and return the top 3 results. "
                "Call search_web with the query from the inputs."
            ),
            "model": "haiku",
            "allowed_tools": ["search_web"],
        }

        agent = WorkflowAgent(
            app=app,
            workflow_def=workflow_def,
            run_id="live-search-test",
            max_rounds=5,
        )
        result = agent.execute(inputs={"query": "Python programming"})

        print(f"\nSearch result: status={result.status}", file=sys.stderr)
        print(f"  Result length: {len(result.result)}", file=sys.stderr)

        assert result.status == "completed", f"Workflow failed: {result.error}"


@pytest.mark.integration
class TestSchedulingEndToEnd:
    """Test scheduled workflow execution."""

    def test_schedule_triggers_due_workflow(self, app):
        """Create a workflow + schedule, verify it triggers when due."""
        import uuid

        workflow_id = f"test-sched-{uuid.uuid4().hex[:8]}"
        task_id = f"sched-{uuid.uuid4().hex[:8]}"

        # 1. Create a simple workflow
        try:
            app.workflow_registry.register(
                workflow_id=workflow_id,
                name="Test Scheduled Workflow",
                steps=[
                    {"id": "search", "action": "search_web", "description": "Search for test"},
                ],
                description="Test scheduled execution",
                plan="Run a single search_web call for 'test' and return the first result.",
                model="anthropic/claude-haiku-4-5",
                allowed_tools=["search_web"],
            )
        except Exception:
            pass  # May already exist

        # 2. Insert a scheduled task with next_run in the past (immediately due)
        past = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
        app.storage.execute(
            "INSERT OR REPLACE INTO scheduled_tasks (id, workflow_id, schedule, next_run, status) VALUES (?, ?, ?, ?, ?)",
            (task_id, workflow_id, "*/5 * * * *", past, "active"),
        )

        # 3. Verify it's due
        due = app.schedule_manager.get_due_tasks()
        due_ids = [t["id"] for t in due]
        assert task_id in due_ids, f"Task not in due list: {due_ids}"

        # 4. Execute scheduled workflows
        from mycelos.scheduler.jobs import check_scheduled_workflows
        executed = check_scheduled_workflows(app)

        print(f"\nExecuted: {executed}", file=sys.stderr)
        assert task_id in executed, f"Task not executed: {executed}"

        # 5. Verify execution results
        task = app.schedule_manager.get(task_id)
        assert task is not None
        assert task["run_count"] >= 1, f"Run count not incremented: {task['run_count']}"
        assert task["last_run"] is not None, "last_run not set"

        print(f"  run_count: {task['run_count']}", file=sys.stderr)
        print(f"  last_run: {task['last_run']}", file=sys.stderr)
        print(f"  next_run: {task['next_run']}", file=sys.stderr)

        # 6. Verify audit event
        events = app.audit.query(event_type="scheduled.executed")
        matching = [e for e in events if task_id in str(e.get("details", ""))]
        assert len(matching) > 0, "No audit event for scheduled execution"

        # Cleanup
        app.storage.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
        try:
            app.workflow_registry.remove(workflow_id)
        except Exception:
            pass

    def test_paused_schedule_not_triggered(self, app):
        """A paused scheduled task should not be executed."""
        import uuid

        workflow_id = f"test-paused-{uuid.uuid4().hex[:8]}"
        task_id = f"paused-{uuid.uuid4().hex[:8]}"

        try:
            app.workflow_registry.register(
                workflow_id=workflow_id,
                name="Paused Test",
                steps=[{"id": "s1", "action": "search_web"}],
            )
        except Exception:
            pass

        past = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
        app.storage.execute(
            "INSERT OR REPLACE INTO scheduled_tasks (id, workflow_id, schedule, next_run, status) VALUES (?, ?, ?, ?, ?)",
            (task_id, workflow_id, "*/5 * * * *", past, "paused"),
        )

        from mycelos.scheduler.jobs import check_scheduled_workflows
        executed = check_scheduled_workflows(app)

        assert task_id not in executed, "Paused task should not execute"

        # Cleanup
        app.storage.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))


@pytest.mark.integration
class TestBuilderReusesWorkflow:
    """Builder should prefer existing workflows over creating new ones."""

    def test_builder_mentions_existing_workflow(self, app):
        """When a matching workflow exists, Builder should reference it.

        Scenario: 'research-summary' workflow already exists (built-in).
        User asks "I want to research a topic and save a summary."
        Builder should call list_tools, find the workflow, and mention it.
        """
        from mycelos.chat.service import ChatService

        # Ensure research-summary workflow exists
        existing = app.workflow_registry.get("research-summary")
        if not existing:
            pytest.skip("research-summary workflow not registered")

        svc = ChatService(app)
        session_id = svc.create_session()
        app.memory.set("default", "system", "user.name", "Stefan", created_by="test")

        # Handoff to builder
        svc._execute_handoff(session_id, "builder", "Research workflow")
        assert svc._get_active_agent(session_id) == "builder"

        # Ask Builder to set up research automation
        events = svc.handle_message(
            message=(
                "Bitte richte mir eine Automatisierung ein: Ich möchte regelmäßig "
                "ein Thema im Web recherchieren und die Ergebnisse als Zusammenfassung "
                "in meiner Knowledge Base speichern. Bitte prüfe zuerst ob wir dafür "
                "schon einen passenden Workflow haben."
            ),
            session_id=session_id,
        )

        # Collect response
        step_ids = [e.data.get("step_id", "") for e in events if e.type == "step-progress"]
        text_content = " ".join(
            e.data.get("content", "") for e in events if e.type in ("text", "system-response")
        )

        tools_called = [s for s in step_ids if s]
        print(f"\nTools called: {tools_called}", file=sys.stderr)
        print(f"Response (first 400): {text_content[:400]}", file=sys.stderr)

        # Builder should reference the existing research-summary workflow in the response
        # (Either via list_tools or because it's already in the planner context)
        assert "research-summary" in text_content.lower() or "research summary" in text_content.lower(), \
            f"Builder didn't mention existing research-summary workflow. Response: {text_content[:400]}"

        # Response should mention "research" or the existing workflow
        response_lower = text_content.lower()
        mentions_existing = any(
            kw in response_lower
            for kw in ["research", "summary", "workflow", "bereits", "existing", "vorhanden"]
        )
        print(f"Mentions existing: {mentions_existing}", file=sys.stderr)

        # Builder should NOT have created a new agent
        assert "create_agent" not in tools_called, \
            f"Builder created an agent instead of using workflow! Called: {tools_called}"

        assert len(text_content) > 50, "Builder response too short"
