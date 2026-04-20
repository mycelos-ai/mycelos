"""Tests for HTTP and Search connectors."""

from __future__ import annotations

import importlib
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from mycelos.connectors.http_tools import http_get, http_post
from mycelos.connectors.registry import register_builtin_tools
from mycelos.connectors.search_tools import search_news, search_web
from mycelos.execution.tools import ToolRegistry


@pytest.fixture(autouse=True)
def _skip_url_validation():
    """Skip DNS validation in unit tests (CI may not resolve external hosts)."""
    with patch("mycelos.connectors.http_tools._validate_url"), \
         patch("mycelos.connectors.http_tools._proxy_client", None):
        yield


# ── HTTP Tools ──


def test_http_get_success() -> None:
    """http_get returns status, headers, body."""
    with patch("mycelos.connectors.http_tools.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.text = "<html>Hello</html>"
        mock_resp.url = "https://example.com"
        mock_httpx.get.return_value = mock_resp

        result = http_get("https://example.com")
        assert result["status"] == 200
        assert "Hello" in result["body"]


def test_http_get_timeout() -> None:
    """http_get returns error on timeout."""
    with patch("mycelos.connectors.http_tools.httpx") as mock_httpx:
        mock_httpx.get.side_effect = httpx.TimeoutException("timeout")
        mock_httpx.TimeoutException = httpx.TimeoutException
        mock_httpx.RequestError = httpx.RequestError

        result = http_get("https://example.com", timeout=1)
        assert result["status"] == 0
        assert "timed out" in result["error"]


def test_http_get_request_error() -> None:
    """http_get returns error on connection failure."""
    with patch("mycelos.connectors.http_tools.httpx") as mock_httpx:
        mock_httpx.get.side_effect = httpx.RequestError("connection refused")
        mock_httpx.TimeoutException = httpx.TimeoutException
        mock_httpx.RequestError = httpx.RequestError

        result = http_get("https://nonexistent.invalid")
        assert result["status"] == 0
        assert "error" in result


def test_http_get_truncates_large_body() -> None:
    """http_get caps response body at 50k chars."""
    with patch("mycelos.connectors.http_tools.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.text = "x" * 100_000
        mock_resp.url = "https://example.com"
        mock_httpx.get.return_value = mock_resp

        result = http_get("https://example.com")
        assert len(result["body"]) == 50_000


def test_http_post_json() -> None:
    """http_post sends JSON body."""
    with patch("mycelos.connectors.http_tools.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.headers = {}
        mock_resp.text = '{"created": true}'
        mock_resp.url = "https://api.example.com"
        mock_httpx.post.return_value = mock_resp

        result = http_post("https://api.example.com", body={"key": "val"})
        assert result["status"] == 201
        mock_httpx.post.assert_called_once()
        call_kwargs = mock_httpx.post.call_args
        assert call_kwargs.kwargs.get("json") == {"key": "val"} or call_kwargs[1].get(
            "json"
        ) == {"key": "val"}


def test_http_post_string_body() -> None:
    """http_post sends string body as content."""
    with patch("mycelos.connectors.http_tools.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.text = "ok"
        mock_resp.url = "https://api.example.com"
        mock_httpx.post.return_value = mock_resp

        result = http_post("https://api.example.com", body="raw data")
        assert result["status"] == 200


def test_http_post_timeout() -> None:
    """http_post returns error on timeout."""
    with patch("mycelos.connectors.http_tools.httpx") as mock_httpx:
        mock_httpx.post.side_effect = httpx.TimeoutException("timeout")
        mock_httpx.TimeoutException = httpx.TimeoutException
        mock_httpx.RequestError = httpx.RequestError

        result = http_post("https://example.com", body={"key": "val"})
        assert result["status"] == 0
        assert "timed out" in result["error"]


# ── Search Tools ──


def test_search_web_returns_results() -> None:
    """search_web returns list of results with title, url, snippet."""
    with patch("ddgs.DDGS") as MockDDGS:
        mock_instance = MagicMock()
        mock_instance.__enter__ = MagicMock(return_value=mock_instance)
        mock_instance.__exit__ = MagicMock(return_value=False)
        mock_instance.text.return_value = [
            {
                "title": "Result 1",
                "href": "https://example.com/1",
                "body": "Snippet 1",
            },
            {
                "title": "Result 2",
                "href": "https://example.com/2",
                "body": "Snippet 2",
            },
        ]
        MockDDGS.return_value = mock_instance

        results = search_web("test query", max_results=2)
        assert len(results) == 2
        assert results[0]["title"] == "Result 1"
        assert results[0]["url"] == "https://example.com/1"
        assert results[1]["snippet"] == "Snippet 2"


def test_search_news_returns_results() -> None:
    """search_news returns list with date and source."""
    with patch("ddgs.DDGS") as MockDDGS:
        mock_instance = MagicMock()
        mock_instance.__enter__ = MagicMock(return_value=mock_instance)
        mock_instance.__exit__ = MagicMock(return_value=False)
        mock_instance.news.return_value = [
            {
                "title": "News 1",
                "url": "https://news.com/1",
                "body": "Breaking",
                "date": "2026-03-20",
                "source": "CNN",
            },
        ]
        MockDDGS.return_value = mock_instance

        results = search_news("breaking news", max_results=1)
        assert len(results) == 1
        assert results[0]["source"] == "CNN"
        assert results[0]["date"] == "2026-03-20"


def test_search_web_handles_exception() -> None:
    """search_web returns error dict on unexpected exceptions."""
    with patch("ddgs.DDGS") as MockDDGS:
        MockDDGS.side_effect = RuntimeError("network error")

        results = search_web("test")
        assert len(results) == 1
        assert "error" in results[0]
        assert "Search failed" in results[0]["error"]


def test_search_news_handles_exception() -> None:
    """search_news returns error dict on unexpected exceptions."""
    with patch("ddgs.DDGS") as MockDDGS:
        MockDDGS.side_effect = RuntimeError("network error")

        results = search_news("test")
        assert len(results) == 1
        assert "error" in results[0]


def test_search_handles_import_error() -> None:
    """search_web returns error if duckduckgo-search not installed."""
    with patch.dict("sys.modules", {"ddgs": None}):
        from mycelos.connectors import search_tools

        importlib.reload(search_tools)
        results = search_tools.search_web("test")
        assert len(results) == 1
        assert "error" in results[0]


# ── Registry ──


def test_register_builtin_tools() -> None:
    """All 4 built-in tools are registered."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    tools = registry.list_tools()
    names = {t["name"] for t in tools}
    assert "http.get" in names
    assert "http.post" in names
    assert "search.web" in names
    assert "search.news" in names


def test_builtin_tools_have_capabilities() -> None:
    """Each tool has a required_capability."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    for tool_info in registry.list_tools():
        assert tool_info["capability"], f"Tool {tool_info['name']} has no capability"


def test_registry_tools_are_callable() -> None:
    """Each registered tool handler can be called."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    for tool_info in registry.list_tools():
        tool_def = registry.get(tool_info["name"])
        assert tool_def is not None
        assert callable(tool_def.handler)


# ── Through ExecutionRuntime (Security Pipeline) ──


def test_http_tool_through_security_pipeline() -> None:
    """http.get flows through the full security pipeline."""
    from mycelos.execution.runtime import ExecutionRuntime
    from mycelos.security.capabilities import CapabilityTokenManager
    from mycelos.security.sanitizer import ResponseSanitizer
    from mycelos.storage.database import SQLiteStorage

    tmp = Path(tempfile.mkdtemp())
    storage = SQLiteStorage(tmp / "test.db")
    storage.initialize()

    tokens = CapabilityTokenManager(storage)
    registry = ToolRegistry()
    register_builtin_tools(registry)
    sanitizer = ResponseSanitizer()
    runtime = ExecutionRuntime(tokens, registry, sanitizer)

    # Issue token with http.get capability
    token = tokens.issue(agent_id="test-agent", scope=["http.get"], ttl_seconds=60)

    # Make request through pipeline (mock the actual HTTP call)
    with patch("mycelos.connectors.http_tools.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/plain"}
        mock_resp.text = "Hello from security pipeline"
        mock_resp.url = "https://example.com"
        mock_httpx.get.return_value = mock_resp
        mock_httpx.TimeoutException = Exception
        mock_httpx.RequestError = Exception

        req = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "tool.call",
                "params": {
                    "tool": "http.get",
                    "args": {"url": "https://example.com"},
                },
                "id": 1,
                "auth": token,
            }
        )
        resp = runtime.handle_request(req)

    assert not resp.is_error
    assert resp.result["status"] == 200


def test_search_tool_blocked_without_capability() -> None:
    """search.web is blocked if token doesn't have the capability."""
    from mycelos.execution.runtime import ExecutionRuntime
    from mycelos.security.capabilities import CapabilityTokenManager
    from mycelos.security.sanitizer import ResponseSanitizer
    from mycelos.storage.database import SQLiteStorage

    tmp = Path(tempfile.mkdtemp())
    storage = SQLiteStorage(tmp / "test.db")
    storage.initialize()

    tokens = CapabilityTokenManager(storage)
    registry = ToolRegistry()
    register_builtin_tools(registry)
    runtime = ExecutionRuntime(tokens, registry, ResponseSanitizer())

    # Issue token WITHOUT search capability
    token = tokens.issue(agent_id="agent", scope=["http.get"], ttl_seconds=60)

    req = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "tool.call",
            "params": {"tool": "search.web", "args": {"query": "test"}},
            "id": 1,
            "auth": token,
        }
    )
    resp = runtime.handle_request(req)
    assert resp.is_error  # Permission denied -- no search.web in scope


# ── Proxy routing: search_tools ──


def test_search_web_brave_uses_proxy_when_available(monkeypatch) -> None:
    """search_web_brave routes through _proxy_client when set."""
    from unittest.mock import MagicMock
    from mycelos.connectors import http_tools

    mock_pc = MagicMock()
    mock_pc.http_get.return_value = {
        "status": 200,
        "body": '{"web": {"results": [{"title": "T", "url": "https://x.com", "description": "D"}]}}',
        "headers": {},
        "url": "https://api.search.brave.com/res/v1/web/search",
    }
    monkeypatch.setattr(http_tools, "_proxy_client", mock_pc)

    from mycelos.connectors.search_tools import search_web_brave
    results = search_web_brave("test query", api_key="test-key", max_results=1)

    mock_pc.http_get.assert_called_once()
    assert len(results) == 1
    assert results[0]["title"] == "T"


def test_search_web_brave_uses_direct_httpx_when_no_proxy(monkeypatch) -> None:
    """search_web_brave falls back to direct httpx when _proxy_client is None."""
    from unittest.mock import MagicMock
    from mycelos.connectors import http_tools
    monkeypatch.setattr(http_tools, "_proxy_client", None)

    import httpx
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"web": {"results": []}}
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: mock_resp)

    from mycelos.connectors.search_tools import search_web_brave
    results = search_web_brave("test", api_key="key")

    assert isinstance(results, list)


# ── Proxy routing: github_tools ──


def test_github_api_get_uses_proxy_when_available(monkeypatch) -> None:
    """github_api GET routes through _proxy_client when set."""
    from unittest.mock import MagicMock
    from mycelos.connectors import http_tools

    mock_pc = MagicMock()
    mock_pc.http_get.return_value = {
        "status": 200,
        "body": '[{"id": 1, "name": "myrepo", "full_name": "user/myrepo"}]',
        "headers": {},
        "url": "https://api.github.com/user/repos",
    }
    monkeypatch.setattr(http_tools, "_proxy_client", mock_pc)

    mock_cred_proxy = MagicMock()
    mock_cred_proxy.get_credential.return_value = {"api_key": "ghp_token"}

    from mycelos.connectors.github_tools import github_api
    result = github_api("/user/repos", credential_proxy=mock_cred_proxy, method="GET")

    mock_pc.http_get.assert_called_once()
    assert "data" in result


def test_github_api_post_uses_proxy_when_available(monkeypatch) -> None:
    """github_api POST routes through _proxy_client when set."""
    from unittest.mock import MagicMock
    from mycelos.connectors import http_tools

    mock_pc = MagicMock()
    mock_pc.http_post.return_value = {
        "status": 201,
        "body": '{"id": 42, "number": 1, "title": "Issue title"}',
        "headers": {},
        "url": "https://api.github.com/repos/user/repo/issues",
    }
    monkeypatch.setattr(http_tools, "_proxy_client", mock_pc)

    mock_cred_proxy = MagicMock()
    mock_cred_proxy.get_credential.return_value = {"api_key": "ghp_token"}

    from mycelos.connectors.github_tools import github_api
    result = github_api(
        "/repos/user/repo/issues",
        credential_proxy=mock_cred_proxy,
        method="POST",
        body={"title": "Issue title"},
    )

    mock_pc.http_post.assert_called_once()
    assert "data" in result


def test_github_api_get_uses_direct_httpx_when_no_proxy(monkeypatch) -> None:
    """github_api falls back to direct httpx when _proxy_client is None."""
    from unittest.mock import MagicMock
    from mycelos.connectors import http_tools
    monkeypatch.setattr(http_tools, "_proxy_client", None)

    import httpx
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "[]"
    mock_resp.json.return_value = []
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: mock_resp)

    mock_cred_proxy = MagicMock()
    mock_cred_proxy.get_credential.return_value = {"api_key": "ghp_token"}

    from mycelos.connectors.github_tools import github_api
    result = github_api("/user/repos", credential_proxy=mock_cred_proxy, method="GET")

    assert "data" in result


# ── Proxy routing: mcp_search ──


def test_mcp_search_uses_proxy_when_available(monkeypatch) -> None:
    """search_mcp_servers routes through _proxy_client when set."""
    from unittest.mock import MagicMock
    from mycelos.connectors import http_tools

    mock_pc = MagicMock()
    mock_pc.http_get.return_value = {
        "status": 200,
        "body": '{"servers": [{"server": {"name": "github-mcp", "description": "GitHub connector", "repository": {"url": "https://github.com/x/y"}, "packages": []}}]}',
        "headers": {},
        "url": "https://registry.modelcontextprotocol.io/v0/servers",
    }
    monkeypatch.setattr(http_tools, "_proxy_client", mock_pc)

    from mycelos.connectors.mcp_search import search_mcp_servers
    results = search_mcp_servers("github")

    mock_pc.http_get.assert_called_once()
    assert len(results) == 1
    assert results[0]["name"] == "github-mcp"


def test_mcp_search_uses_direct_httpx_when_no_proxy(monkeypatch) -> None:
    """search_mcp_servers falls back to direct httpx when _proxy_client is None."""
    from unittest.mock import MagicMock
    from mycelos.connectors import http_tools
    monkeypatch.setattr(http_tools, "_proxy_client", None)

    import httpx
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = lambda: None
    mock_resp.json.return_value = {"servers": []}
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: mock_resp)

    from mycelos.connectors.mcp_search import search_mcp_servers
    results = search_mcp_servers("anything")

    assert results == []
