"""Tests for server-side EU-mode state (persisted + audited)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-eu"
        a = App(Path(tmp))
        a.initialize()
        yield a


def test_eu_mode_defaults_off(app):
    from mycelos.llm.eu_mode import get_eu_mode
    assert get_eu_mode(app, "default") is False


def test_eu_mode_persists(app):
    from mycelos.llm.eu_mode import get_eu_mode, set_eu_mode
    set_eu_mode(app, "default", True)
    assert get_eu_mode(app, "default") is True
    set_eu_mode(app, "default", False)
    assert get_eu_mode(app, "default") is False


def test_set_eu_mode_emits_audit_event(app):
    from mycelos.llm.eu_mode import set_eu_mode
    set_eu_mode(app, "default", True)
    rows = app.storage.fetchall(
        "SELECT event_type FROM audit_events WHERE event_type LIKE 'eu_mode%'"
    )
    assert any("eu_mode" in r["event_type"] for r in rows)
