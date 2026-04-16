"""Tests for scheduler integration with Gateway."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_scheduler() -> TestClient:
    """Create a test client with scheduler disabled."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-scheduler-int"
        from mycelos.app import App

        app = App(Path(tmp))
        app.initialize()

        from mycelos.gateway.server import create_app

        fastapi_app = create_app(Path(tmp), no_scheduler=True, host="0.0.0.0")
        yield TestClient(fastapi_app)


def test_health_shows_scheduler_status(client_with_scheduler: TestClient) -> None:
    """Health endpoint includes scheduler field."""
    resp = client_with_scheduler.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "scheduler" in data


def test_health_scheduler_disabled(client_with_scheduler: TestClient) -> None:
    """When no_scheduler=True, scheduler reports False."""
    resp = client_with_scheduler.get("/api/health")
    data = resp.json()
    assert data["scheduler"] is False


def test_create_app_no_scheduler_flag() -> None:
    """create_app accepts no_scheduler parameter."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-scheduler-flag"
        from mycelos.app import App

        app = App(Path(tmp))
        app.initialize()

        from mycelos.gateway.server import create_app

        fastapi_app = create_app(Path(tmp), no_scheduler=True, host="0.0.0.0")
        assert fastapi_app.state.no_scheduler is True
        assert fastapi_app.state.scheduler_running is False


def test_serve_cmd_exists() -> None:
    """serve_cmd should exist and accept --no-scheduler."""
    from mycelos.cli.serve_cmd import serve_cmd

    assert serve_cmd is not None
    # Check that --no-scheduler is a registered parameter
    param_names = [p.name for p in serve_cmd.params]
    assert "no_scheduler" in param_names
