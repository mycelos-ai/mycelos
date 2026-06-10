"""Gateway must refuse to start unauthenticated on a non-loopback bind.

This is the OpenClaw "135k exposed instances" failure mode — binding to a
public interface with no auth. We make it opt-out, not opt-in.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def data_dir():
    """An initialized Mycelos data dir so create_app gets past startup."""
    d = Path(tempfile.mkdtemp())
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-bind"
    from mycelos.app import App
    from mycelos.setup import web_init
    app = App(d)
    app.initialize()
    web_init(app, api_key="sk-ant-api03-FAKETESTKEYBINDSEC")
    return d


def test_non_loopback_without_password_refuses_to_start(data_dir):
    from mycelos.gateway.server import create_app, InsecureBindError
    with pytest.raises(InsecureBindError):
        create_app(data_dir, no_scheduler=True, host="0.0.0.0")


def test_non_loopback_with_password_is_allowed(data_dir):
    from mycelos.gateway.server import create_app
    app = create_app(data_dir, no_scheduler=True, host="0.0.0.0", password="s3cret")
    assert app is not None


def test_non_loopback_with_explicit_opt_in_is_allowed(data_dir):
    """An operator can still bind publicly without a password by explicitly
    opting into the risk."""
    from mycelos.gateway.server import create_app
    app = create_app(
        data_dir, no_scheduler=True, host="0.0.0.0", allow_insecure_bind=True
    )
    assert app is not None


def test_loopback_without_password_is_allowed(data_dir):
    from mycelos.gateway.server import create_app
    app = create_app(data_dir, no_scheduler=True, host="127.0.0.1")
    assert app is not None
