"""DelegatingCredentialProxy: gateway's thin wrapper when the proxy owns writes."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mycelos.security.credentials import DelegatingCredentialProxy


def test_store_delegates_to_proxy():
    client = MagicMock()
    client.credential_store.return_value = {"status": "stored"}
    wrapper = DelegatingCredentialProxy(storage=MagicMock(), proxy_client=client)
    wrapper.store_credential("x", {"api_key": "k"})
    client.credential_store.assert_called_once_with(
        "x", {"api_key": "k"}, label="default", description=None,
    )


def test_store_with_description_forwards_it():
    client = MagicMock()
    wrapper = DelegatingCredentialProxy(storage=MagicMock(), proxy_client=client)
    wrapper.store_credential("x", {"api_key": "k"}, label="prod", description="production key")
    client.credential_store.assert_called_once_with(
        "x", {"api_key": "k"}, label="prod", description="production key",
    )


def test_delete_delegates_to_proxy():
    client = MagicMock()
    wrapper = DelegatingCredentialProxy(storage=MagicMock(), proxy_client=client)
    wrapper.delete_credential("x", label="default")
    client.credential_delete.assert_called_once_with("x", "default")


def test_list_delegates_to_proxy():
    client = MagicMock()
    client.credential_list.return_value = [
        {"service": "anthropic", "label": "default"},
        {"service": "openai", "label": "default"},
    ]
    wrapper = DelegatingCredentialProxy(storage=MagicMock(), proxy_client=client)
    items = wrapper.list_credentials()
    assert len(items) == 2
    assert items[0]["service"] == "anthropic"


def test_list_services_dedupes():
    client = MagicMock()
    client.credential_list.return_value = [
        {"service": "anthropic", "label": "default"},
        {"service": "anthropic", "label": "prod"},
        {"service": "openai", "label": "default"},
    ]
    wrapper = DelegatingCredentialProxy(storage=MagicMock(), proxy_client=client)
    assert wrapper.list_services() == ["anthropic", "openai"]


def test_rotate_delegates():
    client = MagicMock()
    wrapper = DelegatingCredentialProxy(storage=MagicMock(), proxy_client=client)
    wrapper.mark_security_rotated("x")
    client.credential_rotate.assert_called_once_with("x", "default")


def test_get_credential_raises_not_implemented():
    client = MagicMock()
    wrapper = DelegatingCredentialProxy(storage=MagicMock(), proxy_client=client)
    with pytest.raises(NotImplementedError, match="plaintext"):
        wrapper.get_credential("x")
    # The proxy client was NOT called — no plaintext RPC exists.
    client.credential_store.assert_not_called()
    assert not any(c.startswith("credential_") and "get" in c for c in dir(client) if c.startswith("credential_"))
