"""Live integration tests for all workflow templates.

Tests each workflow template end-to-end with real services.
Skips workflows whose prerequisites aren't met.

Requires .env.test with:
  ANTHROPIC_API_KEY=sk-ant-...
  GMAIL_USER=your@gmail.com
  GMAIL_PASSWORD=app-password

Run with: pytest -m integration tests/integration/test_workflow_templates_live.py -v
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from mycelos.app import App


TEMPLATES_DIR = Path(__file__).parent.parent.parent / "artifacts" / "workflows"


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-wf-live"
        a = App(Path(tmp))
        a.initialize()

        # Store Anthropic credential and register models
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            a.credentials.store_credential("anthropic", {
                "api_key": api_key, "env_var": "ANTHROPIC_API_KEY",
            })
            # Register models + system agents + smart defaults
            a.model_registry.add_model("anthropic/claude-haiku-4-5", "anthropic", "haiku")
            a.model_registry.add_model("anthropic/claude-sonnet-4-6", "anthropic", "sonnet")
            a.model_registry.set_system_defaults({
                "execution": ["anthropic/claude-sonnet-4-6"],
                "classification": ["anthropic/claude-haiku-4-5"],
            })

        # Store Gmail credential if available
        gmail_user = os.environ.get("GMAIL_USER")
        gmail_pw = os.environ.get("GMAIL_PASSWORD")
        if gmail_user and gmail_pw:
            cred_data = json.dumps({
                "imap_server": "imap.gmail.com", "imap_port": 993,
                "smtp_server": "smtp.gmail.com", "smtp_port": 587,
                "email": gmail_user, "password": gmail_pw,
            })
            a.credentials.store_credential("email", {"api_key": cred_data})
            a.connector_registry.register("email", "Email", "builtin", ["email.read", "email.send"])

        yield a


def _load_template(name: str) -> dict:
    path = TEMPLATES_DIR / f"{name}.yaml"
    return yaml.safe_load(path.read_text())


def _check_prerequisites(app: App, template: dict) -> str | None:
    """Check if template prerequisites are met. Returns skip reason or None."""
    requires = template.get("requires", {})

    for conn in requires.get("connectors", []):
        existing = app.connector_registry.get(conn)
        if not existing or existing.get("status") != "active":
            return f"Connector '{conn}' not configured"

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return "ANTHROPIC_API_KEY not set"

    return None


def _run_workflow_agent(app: App, template: dict, inputs: dict | None = None) -> dict:
    """Run a workflow template via WorkflowAgent."""
    from mycelos.workflows.agent import WorkflowAgent

    workflow_def = {
        "plan": template["plan"],
        "model": template.get("model", "haiku"),
        "allowed_tools": template.get("allowed_tools", []),
    }

    agent = WorkflowAgent(
        app=app,
        workflow_def=workflow_def,
        run_id=f"test-{template['name']}",
        max_rounds=10,
    )

    result = agent.execute(inputs=inputs)
    return {
        "status": result.status,
        "result": result.result,
        "error": result.error,
        "rounds": len([m for m in result.conversation if m.get("role") == "assistant"]),
        "tokens": result.total_tokens,
    }


# ---------------------------------------------------------------------------
# Workflow Template Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestCheckReminders:
    """check-reminders: find overdue/due tasks."""

    def test_runs_successfully(self, app):
        template = _load_template("check-reminders")
        skip = _check_prerequisites(app, template)
        if skip:
            pytest.skip(skip)

        # Create a due task
        kb = app.knowledge_base
        from datetime import date
        kb.write("Test reminder task", "...", type="task", status="open",
                 due=date.today().isoformat(), reminder=True)

        result = _run_workflow_agent(app, template)
        assert result["status"] == "completed", f"Failed: {result['error']}"
        assert "test reminder" in result["result"].lower() or result["result"]


@pytest.mark.integration
class TestNoteIntake:
    """note-intake: classify and store a note."""

    def test_runs_successfully(self, app):
        template = _load_template("note-intake")
        skip = _check_prerequisites(app, template)
        if skip:
            pytest.skip(skip)

        result = _run_workflow_agent(app, template, inputs={
            "text": "Morgen muss ich Milch und Brot kaufen"
        })
        assert result["status"] == "completed", f"Failed: {result['error']}"


@pytest.mark.integration
class TestNewsSummary:
    """news-summary: search news and summarize."""

    def test_runs_successfully(self, app):
        template = _load_template("news-summary")
        skip = _check_prerequisites(app, template)
        if skip:
            pytest.skip(skip)

        result = _run_workflow_agent(app, template, inputs={
            "query": "artificial intelligence"
        })
        assert result["status"] == "completed", f"Failed: {result['error']}"
        assert len(result["result"]) > 50  # Should have some content


@pytest.mark.integration
class TestTopicMonitor:
    """topic-monitor: search web + news for a topic."""

    def test_runs_successfully(self, app):
        template = _load_template("topic-monitor")
        skip = _check_prerequisites(app, template)
        if skip:
            pytest.skip(skip)

        result = _run_workflow_agent(app, template, inputs={
            "query": "Python 3.13"
        })
        assert result["status"] == "completed", f"Failed: {result['error']}"


@pytest.mark.integration
class TestWeeklyReview:
    """weekly-review: summarize activity."""

    def test_runs_successfully(self, app):
        template = _load_template("weekly-review")
        skip = _check_prerequisites(app, template)
        if skip:
            pytest.skip(skip)

        # Create some notes to review
        kb = app.knowledge_base
        kb.write("Project meeting notes", "Discussed roadmap", type="note")
        kb.write("Fix login bug", "Done", type="task", status="done")

        result = _run_workflow_agent(app, template)
        assert result["status"] == "completed", f"Failed: {result['error']}"


@pytest.mark.integration
class TestEmailDigest:
    """email-digest: summarize unread emails (requires email connector)."""

    def test_runs_successfully(self, app):
        template = _load_template("email-digest")
        skip = _check_prerequisites(app, template)
        if skip:
            pytest.skip(skip)

        result = _run_workflow_agent(app, template)
        assert result["status"] == "completed", f"Failed: {result['error']}"
        # Should mention email content or "no new emails"
        assert result["result"]


@pytest.mark.integration
class TestEmailUrgentNotify:
    """email-urgent-notify: check for urgent emails."""

    def test_runs_successfully(self, app):
        template = _load_template("email-urgent-notify")
        skip = _check_prerequisites(app, template)
        if skip:
            pytest.skip(skip)

        result = _run_workflow_agent(app, template)
        assert result["status"] == "completed", f"Failed: {result['error']}"


@pytest.mark.integration
class TestEmailToKnowledge:
    """email-to-knowledge: save important emails as notes."""

    def test_runs_successfully(self, app):
        template = _load_template("email-to-knowledge")
        skip = _check_prerequisites(app, template)
        if skip:
            pytest.skip(skip)

        result = _run_workflow_agent(app, template)
        assert result["status"] == "completed", f"Failed: {result['error']}"


# ---------------------------------------------------------------------------
# Summary test
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestAllTemplatesLoadable:
    """Verify all templates can be loaded and have valid structure."""

    def test_all_templates_parse(self):
        for yf in sorted(TEMPLATES_DIR.glob("*.yaml")):
            data = yaml.safe_load(yf.read_text())
            assert "name" in data, f"{yf.name} missing 'name'"
            assert "plan" in data, f"{yf.name} missing 'plan'"
            assert "requires" in data, f"{yf.name} missing 'requires'"
