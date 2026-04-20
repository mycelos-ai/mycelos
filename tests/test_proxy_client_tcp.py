"""SecurityProxyClient must accept either socket_path= (UDS) or url= (TCP)."""

from __future__ import annotations

import pytest

from mycelos.security.proxy_client import SecurityProxyClient


def test_client_accepts_url_kwarg():
    c = SecurityProxyClient(url="http://proxy:9110", token="t")
    assert c.base_url == "http://proxy:9110"


def test_client_accepts_socket_path_kwarg():
    c = SecurityProxyClient(socket_path="/tmp/proxy.sock", token="t")
    assert c.base_url.startswith("http")


def test_client_rejects_both_transports():
    with pytest.raises(ValueError, match="exactly one of"):
        SecurityProxyClient(socket_path="/tmp/x", url="http://y", token="t")


def test_client_rejects_neither():
    with pytest.raises(ValueError, match="exactly one of"):
        SecurityProxyClient(token="t")
