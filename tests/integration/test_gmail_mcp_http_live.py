"""Integration test: Gmail HTTP-MCP connector end-to-end.

Requires `.env.test` with:
  GMAIL_OAUTH_CLIENT_JSON   — contents of client_secret_*.json (single line)
  GMAIL_OAUTH_TOKEN_JSON    — contents of a previously-consented token
                              blob, shape like:
                              {"access_token": "...", "refresh_token": "...",
                               "expires_at": "2026-04-23T10:00:00+00:00",
                               "scope": "...", "token_type": "Bearer"}

Without both, the test skips cleanly.

To populate `.env.test` the first time: start Mycelos locally, run
through the UI consent flow, then extract the stored token from the
DB:
    sqlite3 ~/.mycelos/mycelos.db "SELECT encrypted FROM credentials
      WHERE service='gmail-oauth-token'"
Decrypt externally (see tests/security/README.md — we don't ship a
helper for this).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv_test() -> dict[str, str]:
    env_file = REPO_ROOT / ".env.test"
    if not env_file.exists():
        return {}
    env: dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


@pytest.fixture(scope="module")
def gmail_oauth_env() -> tuple[str, str]:
    client_json = os.environ.get("GMAIL_OAUTH_CLIENT_JSON")
    token_json = os.environ.get("GMAIL_OAUTH_TOKEN_JSON")
    if not client_json or not token_json:
        dotenv = _load_dotenv_test()
        client_json = client_json or dotenv.get("GMAIL_OAUTH_CLIENT_JSON")
        token_json = token_json or dotenv.get("GMAIL_OAUTH_TOKEN_JSON")
    if not client_json or not token_json:
        pytest.skip(
            "GMAIL_OAUTH_CLIENT_JSON and GMAIL_OAUTH_TOKEN_JSON not set "
            "(put them in .env.test)"
        )
    return client_json, token_json


@pytest.mark.integration
def test_gmail_http_mcp_lists_labels(gmail_oauth_env, tmp_path, monkeypatch):
    """End-to-end: seed credentials, connect to the real Gmail MCP
    server, list labels, assert INBOX is present."""
    client_json, token_json = gmail_oauth_env

    monkeypatch.setenv("MYCELOS_MASTER_KEY", "live-integ-" + "x" * 16)
    monkeypatch.setenv("MYCELOS_DATA_DIR", str(tmp_path))

    from mycelos.app import App
    app = App(tmp_path)
    app.initialize()

    cp = app.credentials
    cp.store_credential(
        "gmail-oauth-client",
        {"api_key": client_json},
        user_id="default",
    )
    cp.store_credential(
        "gmail-oauth-token",
        {"api_key": token_json},
        user_id="default",
    )

    from mycelos.connectors.mcp_client import MycelosMCPClient

    client = MycelosMCPClient(
        connector_id="gmail",
        command="",  # HTTP recipes have no command
        credential_proxy=cp,
        transport="http",
    )
    import asyncio
    asyncio.run(client.connect())
    tools = asyncio.run(client._session.list_tools())
    tool_names = {t.name for t in tools.tools}
    assert "list_labels" in tool_names, f"tools: {tool_names}"

    # Smoke-call list_labels.
    result = asyncio.run(client._session.call_tool("list_labels", {}))
    assert result is not None
    text = ""
    for block in result.content:
        if hasattr(block, "text"):
            text += block.text
    assert "INBOX" in text, f"expected INBOX in response: {text[:500]!r}"

    asyncio.run(client.disconnect())
