"""Security invariant: the two-container gateway never reads or creates
the master key.

The gateway is recognisable by MYCELOS_PROXY_URL. There the SecurityProxy
container owns the key (MYCELOS_MASTER_KEY_FILE in its own mount); the
gateway stores and lists credentials through it only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner


@pytest.fixture
def gateway_env(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.delenv("MYCELOS_MASTER_KEY", raising=False)
    monkeypatch.delenv("MYCELOS_MASTER_KEY_FILE", raising=False)
    monkeypatch.setenv("MYCELOS_PROXY_URL", "http://proxy:9110")
    monkeypatch.setenv("MYCELOS_PROXY_TOKEN", "gateway-test-token")
    return tmp_path


def test_ensure_master_key_does_not_create_a_key(gateway_env: Path) -> None:
    from mycelos.setup import ensure_master_key
    import os

    ensure_master_key(gateway_env)
    assert not (gateway_env / ".master_key").exists()
    assert "MYCELOS_MASTER_KEY" not in os.environ


def test_init_does_not_create_a_key(gateway_env: Path) -> None:
    from mycelos.cli.init_cmd import _setup_master_key

    _setup_master_key(gateway_env)
    assert not (gateway_env / ".master_key").exists()


def test_cli_credentials_go_through_the_proxy(gateway_env: Path) -> None:
    from mycelos.app import App
    from mycelos.security.credentials import DelegatingCredentialProxy

    app = App(gateway_env)
    app.initialize()
    assert isinstance(app.credentials, DelegatingCredentialProxy)
    with pytest.raises(NotImplementedError):
        app.credentials.get_credential("anthropic")


def test_credential_export_is_refused_in_the_gateway(gateway_env: Path) -> None:
    from mycelos.app import App
    from mycelos.cli.credential_cmd import credential_cmd

    App(gateway_env).initialize()
    result = CliRunner().invoke(credential_cmd, ["export", "--data-dir", str(gateway_env)])
    assert result.exit_code == 1
    assert "proxy" in result.output.lower()


def test_gateway_server_ignores_a_stray_key_file(gateway_env: Path, monkeypatch) -> None:
    """A key left in the data dir by an old install is not loaded."""
    import os
    from mycelos.app import App
    from mycelos.gateway.server import create_app

    App(gateway_env).initialize()
    (gateway_env / ".master_key").write_text("stray-key")
    create_app(gateway_env, no_scheduler=True)
    assert os.environ.get("MYCELOS_MASTER_KEY") != "stray-key"


def test_master_key_path_honours_the_proxy_mount(monkeypatch, tmp_path: Path) -> None:
    from mycelos.security.master_key import master_key_path

    monkeypatch.delenv("MYCELOS_MASTER_KEY_FILE", raising=False)
    assert master_key_path(tmp_path) == tmp_path / ".master_key"
    monkeypatch.setenv("MYCELOS_MASTER_KEY_FILE", "/secrets/.master_key")
    assert master_key_path(tmp_path) == Path("/secrets/.master_key")
