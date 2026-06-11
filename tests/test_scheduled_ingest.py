"""Tests for scheduled auto-ingest — the living knowledge loop.

An hourly job runs every registered INGEST_SOURCES connector, but ONLY
when the user opted in (memory key ``auto_ingest_enabled``, default OFF)
and the connector is active in the registry. Errors in one connector
never crash the scheduler loop.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-auto-ingest"
        a = App(Path(tmp))
        a.initialize()
        yield a


class _FakeMcp:
    """Stands in for app.mcp_manager — records calls, returns payloads."""

    def __init__(self, payload):
        self.calls: list = []
        self._payload = payload

    def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return self._payload


class _RaisingMcp:
    def __init__(self):
        self.calls: list = []

    def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        raise RuntimeError("connector exploded")


def _gmail_payload():
    return {"threads": [{
        "id": "auto-1", "subject": "Hello", "snippet": "World",
        "from": "alice@example.com", "date": "2026-06-10T10:00:00Z",
    }]}


def _register_gmail(app, status: str = "active") -> None:
    app.connector_registry.register(
        "gmail", "Gmail", "mcp", ["gmail.read"], description="test",
    )
    if status != "active":
        app.connector_registry.set_status("gmail", status)


class TestAutoIngest:
    def test_disabled_by_default(self, app):
        """Auto-ingest is opt-in. No memory flag → nothing runs."""
        from mycelos.scheduler.jobs import auto_ingest_check
        _register_gmail(app)
        mcp = _FakeMcp(_gmail_payload())
        app._mcp_manager = mcp

        result = auto_ingest_check(app)
        assert result["enabled"] is False
        assert mcp.calls == []
        rows = app.storage.fetchall(
            "SELECT path FROM knowledge_notes WHERE created_by='import'"
        )
        assert rows == []

    def test_runs_when_enabled_and_connector_active(self, app):
        from mycelos.scheduler.jobs import auto_ingest_check
        _register_gmail(app)
        app.memory.set("default", "system", "auto_ingest_enabled", True)
        mcp = _FakeMcp(_gmail_payload())
        app._mcp_manager = mcp

        result = auto_ingest_check(app)
        assert result["enabled"] is True
        assert "gmail" in result["ran"]
        assert len(mcp.calls) == 1
        rows = app.storage.fetchall(
            "SELECT path FROM knowledge_notes WHERE created_by='import'"
        )
        assert len(rows) == 1

    def test_skips_inactive_connector(self, app):
        from mycelos.scheduler.jobs import auto_ingest_check
        _register_gmail(app, status="inactive")
        app.memory.set("default", "system", "auto_ingest_enabled", True)
        mcp = _FakeMcp(_gmail_payload())
        app._mcp_manager = mcp

        result = auto_ingest_check(app)
        assert mcp.calls == []
        assert "gmail" in result["skipped"]

    def test_skips_unregistered_connector(self, app):
        from mycelos.scheduler.jobs import auto_ingest_check
        app.memory.set("default", "system", "auto_ingest_enabled", True)
        mcp = _FakeMcp(_gmail_payload())
        app._mcp_manager = mcp

        result = auto_ingest_check(app)
        assert mcp.calls == []
        assert "gmail" in result["skipped"]

    def test_connector_error_does_not_crash(self, app):
        """One exploding connector must not take the scheduler down."""
        from mycelos.scheduler.jobs import auto_ingest_check
        _register_gmail(app)
        app.memory.set("default", "system", "auto_ingest_enabled", True)
        app._mcp_manager = _RaisingMcp()

        result = auto_ingest_check(app)  # must not raise
        assert "gmail" in result["errors"]

    def test_run_is_audited(self, app):
        from mycelos.scheduler.jobs import auto_ingest_check
        _register_gmail(app)
        app.memory.set("default", "system", "auto_ingest_enabled", True)
        app._mcp_manager = _FakeMcp(_gmail_payload())

        auto_ingest_check(app)
        row = app.storage.fetchone(
            "SELECT details FROM audit_events "
            "WHERE event_type='knowledge.auto_ingest.run'"
        )
        assert row is not None
        details = json.loads(row["details"])
        assert "gmail" in details["ran"]

    def test_idempotent_across_runs(self, app):
        """Hourly re-runs must not duplicate notes (external_id dedup)."""
        from mycelos.scheduler.jobs import auto_ingest_check
        _register_gmail(app)
        app.memory.set("default", "system", "auto_ingest_enabled", True)
        app._mcp_manager = _FakeMcp(_gmail_payload())

        auto_ingest_check(app)
        auto_ingest_check(app)
        rows = app.storage.fetchall(
            "SELECT path FROM knowledge_notes WHERE created_by='import'"
        )
        assert len(rows) == 1
