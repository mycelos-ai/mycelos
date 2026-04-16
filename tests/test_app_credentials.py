"""Tests for App credential proxy integration."""

from pathlib import Path
import os

import pytest

from mycelos.app import App


def test_app_has_credentials_property(tmp_data_dir: Path) -> None:
    """App should expose a credentials property."""
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-for-app"
    try:
        app = App(tmp_data_dir)
        app.initialize()
        assert hasattr(app, "credentials")
        cred = app.credentials
        assert cred is not None
        assert hasattr(cred, "store_credential")
        assert hasattr(cred, "get_credential")
    finally:
        del os.environ["MYCELOS_MASTER_KEY"]


def test_app_credentials_roundtrip(tmp_data_dir: Path) -> None:
    """Credentials stored via App are retrievable."""
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-for-app"
    try:
        app = App(tmp_data_dir)
        app.initialize()
        app.credentials.store_credential("test_svc", {"token": "abc123"})
        result = app.credentials.get_credential("test_svc")
        assert result is not None
        assert result["token"] == "abc123"
    finally:
        del os.environ["MYCELOS_MASTER_KEY"]


def test_app_no_master_key_raises(tmp_data_dir: Path) -> None:
    """Accessing credentials without MYCELOS_MASTER_KEY raises a clear error."""
    os.environ.pop("MYCELOS_MASTER_KEY", None)
    app = App(tmp_data_dir)
    app.initialize()
    with pytest.raises(RuntimeError, match="MYCELOS_MASTER_KEY"):
        _ = app.credentials


