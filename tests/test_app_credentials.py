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


def test_app_credentials_uses_delegating_wrapper_with_external_proxy(tmp_path, monkeypatch):
    """MYCELOS_PROXY_URL + wired proxy_client → DelegatingCredentialProxy, NOT EncryptedCredentialProxy."""
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "ignored-in-two-container-mode-but-needed-for-init")
    monkeypatch.setenv("MYCELOS_PROXY_URL", "http://proxy.internal:9110")
    monkeypatch.setenv("MYCELOS_PROXY_TOKEN", "tok")

    from mycelos.app import App
    from mycelos.security.credentials import DelegatingCredentialProxy, EncryptedCredentialProxy
    from mycelos.security.proxy_client import SecurityProxyClient

    app = App(tmp_path)
    app.initialize()

    # Wire a proxy client — mimics what gateway/server.py does at boot
    app.set_proxy_client(SecurityProxyClient(url="http://proxy.internal:9110", token="tok"))

    creds = app.credentials
    assert isinstance(creds, DelegatingCredentialProxy), \
        f"expected DelegatingCredentialProxy, got {type(creds).__name__}"
    # Explicit: NOT the in-process proxy
    assert not isinstance(creds, EncryptedCredentialProxy)


def test_app_credentials_uses_encrypted_in_single_container(tmp_path, monkeypatch):
    """No MYCELOS_PROXY_URL → legacy in-process EncryptedCredentialProxy."""
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "single-container-key-" + "x" * 20)
    monkeypatch.delenv("MYCELOS_PROXY_URL", raising=False)

    from mycelos.app import App
    from mycelos.security.credentials import EncryptedCredentialProxy

    app = App(tmp_path)
    app.initialize()
    assert isinstance(app.credentials, EncryptedCredentialProxy)


def test_app_credentials_without_wired_proxy_client_falls_back(tmp_path, monkeypatch):
    """If MYCELOS_PROXY_URL is set but set_proxy_client wasn't called yet,
    do NOT return DelegatingCredentialProxy (it would have a None client).
    Fall back to EncryptedCredentialProxy — the caller gets the old
    behavior until the proxy client actually arrives.

    This handles the startup ordering quirk: in gateway/server.py,
    set_proxy_client is called AFTER some App init paths may have
    already touched app.credentials. The fallback avoids a NoneType
    AttributeError."""
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "key-" + "x" * 24)
    monkeypatch.setenv("MYCELOS_PROXY_URL", "http://proxy.internal:9110")

    from mycelos.app import App
    from mycelos.security.credentials import EncryptedCredentialProxy

    app = App(tmp_path)
    app.initialize()
    # NOTE: no set_proxy_client call
    assert app._proxy_client is None
    # app.credentials must not explode
    creds = app.credentials
    assert isinstance(creds, EncryptedCredentialProxy)

