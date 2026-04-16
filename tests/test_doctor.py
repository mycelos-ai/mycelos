"""Tests for mycelos doctor — diagnostic command."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mycelos.app import App


@pytest.fixture
def app():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-doctor"
        a = App(Path(tmp))
        a.initialize()
        yield a


# ---------------------------------------------------------------------------
# Doctor tools
# ---------------------------------------------------------------------------

class TestDoctorTools:
    """Doctor diagnostic tools work correctly."""

    def test_query_audit(self, app):
        from mycelos.doctor.tools import doctor_query_audit
        app.audit.log("test.event", details={"key": "value"})
        results = doctor_query_audit(app, event_type="test.event", limit=5)
        assert len(results) >= 1
        assert results[0]["event_type"] == "test.event"

    def test_query_audit_empty(self, app):
        from mycelos.doctor.tools import doctor_query_audit
        results = doctor_query_audit(app, event_type="nonexistent", limit=5)
        assert results == []

    def test_config_history(self, app):
        from mycelos.doctor.tools import doctor_config_history
        history = doctor_config_history(app, limit=5)
        assert isinstance(history, list)
        # At least the initial generation from init
        assert len(history) >= 1

    def test_check_reminders(self, app):
        from mycelos.doctor.tools import doctor_check_reminders
        # Create a due task
        kb = app.knowledge_base
        kb.write("Test task", "...", type="task", status="open",
                 due=date.today().isoformat(), reminder=True)
        result = doctor_check_reminders(app)
        assert result["due_count"] >= 1
        assert len(result["tasks"]) >= 1

    def test_check_reminders_none(self, app):
        from mycelos.doctor.tools import doctor_check_reminders
        result = doctor_check_reminders(app)
        assert result["due_count"] == 0

    def test_check_schedules(self, app):
        from mycelos.doctor.tools import doctor_check_schedules
        result = doctor_check_schedules(app)
        assert "schedules" in result
        assert isinstance(result["schedules"], list)

    def test_check_credentials(self, app):
        from mycelos.doctor.tools import doctor_check_credentials
        result = doctor_check_credentials(app)
        assert "services" in result


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    """Basic health checks."""

    def test_run_all_checks(self, app):
        from mycelos.doctor.checks import run_health_checks
        results = run_health_checks(app, gateway_url=None)
        assert isinstance(results, list)
        # Should have storage check at minimum
        assert any(r["category"] == "storage" for r in results)

    def test_gateway_url_none_skips_server_check(self, app):
        """When called from inside the gateway itself, there's no point
        pinging ourselves — the server check must be skipped entirely, not
        reported as an error."""
        from mycelos.doctor.checks import run_health_checks
        results = run_health_checks(app, gateway_url=None)
        assert all(r["category"] != "server" for r in results), (
            "server check must be omitted when gateway_url is None"
        )

    def test_storage_check_ok(self, app):
        from mycelos.doctor.checks import check_storage
        result = check_storage(app)
        assert result["status"] == "ok"
        assert result["category"] == "storage"

    def test_credentials_check(self, app):
        from mycelos.doctor.checks import check_credentials
        result = check_credentials(app)
        assert result["category"] == "credentials"

    def test_telegram_empty_allowlist_is_error(self, app):
        """Doctor flags empty Telegram allowlist as error (fail-closed)."""
        from mycelos.doctor.checks import check_telegram
        app.storage.execute(
            """INSERT INTO channels (id, channel_type, mode, status, config, allowed_users)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("telegram", "telegram", "polling", "active", "{}", "[]"),
        )
        app.credentials.store_credential("telegram", {
            "api_key": "123:ABC", "provider": "telegram",
        })
        result = check_telegram(app)
        assert result["status"] == "error"
        assert "empty allowlist" in result["details"]

    def test_reminder_scheduler_ok_when_recent_tick(self, app):
        """If a reminder.tick audit event exists in the last hour,
        the reminder scheduler is alive."""
        from mycelos.doctor.checks import check_reminder_scheduler
        app.audit.log("reminder.tick", details={"tasks_found": 0})
        result = check_reminder_scheduler(app)
        assert result["category"] == "reminder_scheduler"
        assert result["status"] == "ok"

    def test_reminder_scheduler_warns_when_no_recent_tick(self, app):
        """A fresh DB with no reminder.tick events is a warning — the
        Huey consumer is either disabled or crashed."""
        from mycelos.doctor.checks import check_reminder_scheduler
        result = check_reminder_scheduler(app)
        assert result["category"] == "reminder_scheduler"
        assert result["status"] in ("warn", "warning", "error")

    def test_telegram_with_allowlist_is_ok(self, app):
        """Doctor reports OK when Telegram has users in allowlist."""
        from mycelos.doctor.checks import check_telegram
        app.storage.execute(
            """INSERT INTO channels (id, channel_type, mode, status, config, allowed_users)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("telegram", "telegram", "polling", "active", "{}", "[42]"),
        )
        app.credentials.store_credential("telegram", {
            "api_key": "123:ABC", "provider": "telegram",
        })
        app.memory.set("default", "system", "telegram_chat_id", "42")
        result = check_telegram(app)
        assert result["status"] == "ok"
        assert "1 allowed user" in result["details"]


# ---------------------------------------------------------------------------
# --why mode (LLM diagnosis)
# ---------------------------------------------------------------------------

class TestWhyMode:
    """--why uses LLM agent with AGENT.md context."""

    def test_why_builds_context(self, app):
        from mycelos.doctor.agent import DoctorAgent
        agent = DoctorAgent(app)
        context = agent.build_context("my reminder didn't fire")
        assert "AGENT.md" in context or "Constitution" in context
        assert "reminder" in context.lower()

    def test_why_includes_audit_data(self, app):
        from mycelos.doctor.agent import DoctorAgent
        app.audit.log("reminder.sent", details={"tasks": 1, "sent": 0})
        agent = DoctorAgent(app)
        context = agent.build_context("reminder didn't work")
        assert "reminder.sent" in context

    def test_why_runs_agent(self, app):
        from mycelos.doctor.agent import DoctorAgent
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "The reminder didn't fire because the server was restarted."
        mock_response.total_tokens = 100
        mock_response.tool_calls = None
        mock_llm.complete.return_value = mock_response
        app._llm = mock_llm

        agent = DoctorAgent(app)
        result = agent.diagnose("why didn't my reminder fire?")
        assert "reminder" in result.lower() or mock_llm.complete.called
