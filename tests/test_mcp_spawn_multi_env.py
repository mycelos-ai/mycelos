"""MCP spawn injects all keys from a __multi__ credential blob into the subprocess env."""

from __future__ import annotations

from unittest.mock import MagicMock


def test_multi_var_credential_expands_into_env() -> None:
    """A credential with env_var='__multi__' and JSON blob in api_key
    expands into one env entry per blob key."""
    from mycelos.connectors.mcp_client import MycelosMCPClient
    import json

    cred_proxy = MagicMock()
    cred_proxy.get_credential.return_value = {
        "api_key": json.dumps({"API_KEY": "ctx_abc", "WORKSPACE": "ws_42"}),
        "env_var": "__multi__",
        "connector": "context7",
    }

    client = MycelosMCPClient(
        connector_id="context7",
        command="npx -y @upstash/context7-mcp",
        env_vars={"__multi__": "credential:context7"},
        credential_proxy=cred_proxy,
    )
    env = client._build_env()

    assert env.get("API_KEY") == "ctx_abc"
    assert env.get("WORKSPACE") == "ws_42"
    assert "__multi__" not in env, "sentinel must not leak into the spawn env"


def test_legacy_single_var_credential_still_works() -> None:
    """Recipe-style single-var credential keeps its existing behavior."""
    from mycelos.connectors.mcp_client import MycelosMCPClient

    cred_proxy = MagicMock()
    cred_proxy.get_credential.return_value = {
        "api_key": "secret123",
        "env_var": "BRAVE_API_KEY",
        "connector": "brave-search",
    }

    client = MycelosMCPClient(
        connector_id="brave-search",
        command="npx -y @brave/brave-search-mcp-server",
        env_vars={"BRAVE_API_KEY": "credential:brave-search"},
        credential_proxy=cred_proxy,
    )
    env = client._build_env()
    assert env.get("BRAVE_API_KEY") == "secret123"


def test_multi_var_blocked_keys_are_dropped() -> None:
    """Even via __multi__, blocked env vars must not be injected."""
    from mycelos.connectors.mcp_client import MycelosMCPClient, _BLOCKED_ENV_VARS
    import json

    blocked = next(iter(_BLOCKED_ENV_VARS))  # any blocked name

    cred_proxy = MagicMock()
    cred_proxy.get_credential.return_value = {
        "api_key": json.dumps({"SAFE": "ok", blocked: "BAD"}),
        "env_var": "__multi__",
        "connector": "evil",
    }

    client = MycelosMCPClient(
        connector_id="evil",
        command="npx -y something",
        env_vars={"__multi__": "credential:evil"},
        credential_proxy=cred_proxy,
    )
    env = client._build_env()
    assert env.get("SAFE") == "ok"
    assert blocked not in env, f"blocked var {blocked!r} must be dropped even from multi-blob"


def test_multi_var_malformed_json_skipped() -> None:
    """Bad JSON in api_key — log and skip injection, do not crash."""
    from mycelos.connectors.mcp_client import MycelosMCPClient

    cred_proxy = MagicMock()
    cred_proxy.get_credential.return_value = {
        "api_key": "{ this is not json",
        "env_var": "__multi__",
        "connector": "broken",
    }

    client = MycelosMCPClient(
        connector_id="broken",
        command="npx -y something",
        env_vars={"__multi__": "credential:broken"},
        credential_proxy=cred_proxy,
    )
    env = client._build_env()
    assert "__multi__" not in env
