"""Tests for `mycelos db audit` filters.

Focus on the filter semantics — making sure --suspicious only matches
security-relevant events, --since parses correctly, and --type supports
comma-separated lists.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from mycelos.app import App
from mycelos.cli.db_cmd import NOISY_EVENT_TYPES, SUSPICIOUS_EVENT_PATTERNS, _parse_since, audit_cmd


@pytest.fixture
def seeded_app(tmp_path: Path, monkeypatch) -> App:
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "test-key-db-audit")
    app = App(tmp_path)
    app.initialize()
    # Seed a mix of suspicious and innocuous events
    app.audit.log("tool.blocked", details={"tool": "github.write"})
    app.audit.log("policy.denied", details={"policy": "http.post"})
    app.audit.log("workflow.registered", details={"id": "x"})
    app.audit.log("config.tamper_detected", details={"gen": 42})
    app.audit.log("some.other.event", details={"k": "v"})
    app.audit.log("gen.flood_blocked", details={"count": 1000})
    app.audit.log("agent.denied", details={"agent": "foo"}, agent_id="foo")
    # Add noisy events that --quiet should filter out
    app.audit.log("reminder.tick", details={"tasks_found": 0})
    app.audit.log("scheduler.tick", details={})
    return app


def _run(app: App, args: list[str]) -> str:
    """Run audit_cmd against the app's data_dir and return stdout."""
    runner = CliRunner()
    result = runner.invoke(audit_cmd, ["--data-dir", str(app.data_dir), *args])
    assert result.exit_code == 0, result.output
    return result.output


def test_suspicious_flag_filters_to_security_events(seeded_app: App) -> None:
    out = _run(seeded_app, ["--suspicious", "--limit", "100"])
    assert "tool.blocked" in out
    assert "policy.denied" in out
    assert "config.tamper_detected" in out
    assert "gen.flood_blocked" in out
    assert "agent.denied" in out
    # Non-suspicious events MUST NOT appear
    assert "workflow.registered" not in out
    assert "some.other.event" not in out


def test_type_filter_supports_comma_list(seeded_app: App) -> None:
    out = _run(seeded_app, ["--type", "tool.blocked,policy.denied", "--limit", "100"])
    assert "tool.blocked" in out
    assert "policy.denied" in out
    assert "config.tamper_detected" not in out


def test_agent_filter(seeded_app: App) -> None:
    out = _run(seeded_app, ["--agent", "foo", "--limit", "100"])
    assert "agent.denied" in out
    assert "tool.blocked" not in out


def test_parse_since_accepts_shorthands() -> None:
    assert _parse_since("30m") is not None
    assert _parse_since("1h") is not None
    assert _parse_since("24h") is not None
    assert _parse_since("7d") is not None
    assert _parse_since(None) is None


def test_parse_since_rejects_bad_format() -> None:
    import click
    with pytest.raises(click.exceptions.BadParameter):
        _parse_since("yesterday")
    with pytest.raises(click.exceptions.BadParameter):
        _parse_since("10")


def test_suspicious_patterns_are_non_empty() -> None:
    """Guard: if the list ever gets accidentally emptied, catch it."""
    assert len(SUSPICIOUS_EVENT_PATTERNS) >= 5
    assert all(isinstance(p, str) and p for p in SUSPICIOUS_EVENT_PATTERNS)


def test_quiet_hides_noise_events(seeded_app: App) -> None:
    out = _run(seeded_app, ["--quiet", "--limit", "100"])
    assert "reminder.tick" not in out
    assert "scheduler.tick" not in out
    # Interesting events still visible
    assert "tool.blocked" in out
    assert "workflow.registered" in out


def test_noisy_event_types_are_defined() -> None:
    assert "reminder.tick" in NOISY_EVENT_TYPES


def test_no_filters_shows_recent(seeded_app: App) -> None:
    """Without filters, the command runs and returns rows without crashing."""
    out = _run(seeded_app, ["--limit", "100"])
    assert "Audit Events" in out
    # All seeded events should show up when limit is high enough
    assert "tool.blocked" in out
    assert "workflow.registered" in out
