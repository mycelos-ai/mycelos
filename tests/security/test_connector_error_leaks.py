"""Security tests for connector error paths (Rule 3 fail-closed + Rule 4 no leak).

Covers:
- http_tools error responses must not echo credentials from the input URL (Rule 4).
- mcp_client._resolve_token must propagate credential lookup errors (Rule 3).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest


def test_http_get_error_scrubs_inline_url_credentials(monkeypatch) -> None:
    """A URL with user:pass@host that fails must not echo the credential in the error."""
    from mycelos.connectors import http_tools

    # Force a RequestError with the full URL (what httpx would echo)
    def fake_get(url, **kwargs):
        raise httpx.RequestError(
            f"DNS lookup failed for {url}",
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(http_tools.httpx, "get", fake_get)
    monkeypatch.setattr(http_tools, "_validate_url", lambda u: None)

    result = http_tools.http_get("https://user:sup3rsecret@nosuchhost.example/api")
    assert result["status"] == 0
    assert "sup3rsecret" not in result["error"]
    assert "user:sup3rsecret" not in result["error"]


def test_http_post_error_scrubs_inline_url_credentials(monkeypatch) -> None:
    """Same for POST."""
    from mycelos.connectors import http_tools

    def fake_post(url, **kwargs):
        raise httpx.RequestError(
            f"connection refused to {url}",
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(http_tools.httpx, "post", fake_post)
    monkeypatch.setattr(http_tools, "_validate_url", lambda u: None)

    result = http_tools.http_post(
        "https://admin:topsecret@unreachable.example/webhook", body={"k": "v"}
    )
    assert result["status"] == 0
    assert "topsecret" not in result["error"]


def test_http_get_error_scrubs_bearer_token_in_message(monkeypatch) -> None:
    """Known credential patterns in the raw exception text must be redacted."""
    from mycelos.connectors import http_tools

    def fake_get(url, **kwargs):
        raise httpx.RequestError(
            "upstream returned: Authorization: Bearer sk-ant-verylongkey12345678901234567890",
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(http_tools.httpx, "get", fake_get)
    monkeypatch.setattr(http_tools, "_validate_url", lambda u: None)

    result = http_tools.http_get("https://example.com/")
    assert "sk-ant-verylongkey" not in result["error"]


def test_mcp_resolve_token_propagates_credential_store_error() -> None:
    """Credential lookup errors must surface (fail-closed), not silently return None.

    A silent None would cause the MCP call to run unauthenticated and produce a
    confusing 401 instead of an explicit credential-store failure.
    """
    from mycelos.connectors.mcp_client import MycelosMCPClient

    proxy = MagicMock()
    proxy.get_credential.side_effect = RuntimeError("credential store unavailable")

    client = MycelosMCPClient.__new__(MycelosMCPClient)
    client._credential_proxy = proxy
    client._env_vars = {"GITHUB_TOKEN": "credential:github"}

    with pytest.raises(RuntimeError, match="credential store unavailable"):
        client._resolve_token()


def test_mcp_resolve_token_returns_none_when_no_credential_configured() -> None:
    """Missing credential (get_credential returns None) is still a legitimate no-op."""
    from mycelos.connectors.mcp_client import MycelosMCPClient

    proxy = MagicMock()
    proxy.get_credential.return_value = None

    client = MycelosMCPClient.__new__(MycelosMCPClient)
    client._credential_proxy = proxy
    client._env_vars = {"GITHUB_TOKEN": "credential:github"}

    assert client._resolve_token() is None


def test_mcp_resolve_token_returns_api_key_when_configured() -> None:
    """Happy path: credential exists, api_key is returned."""
    from mycelos.connectors.mcp_client import MycelosMCPClient

    proxy = MagicMock()
    proxy.get_credential.return_value = {"api_key": "gh_real_token_here"}

    client = MycelosMCPClient.__new__(MycelosMCPClient)
    client._credential_proxy = proxy
    client._env_vars = {"GITHUB_TOKEN": "credential:github"}

    assert client._resolve_token() == "gh_real_token_here"
