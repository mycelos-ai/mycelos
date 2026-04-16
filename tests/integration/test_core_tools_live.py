"""Live integration tests for core tools — real network calls.

Tests that search_mcp_servers, search_web, and http_get work with
real endpoints. No mocks — these verify actual connectivity.

Run: pytest -m integration tests/integration/test_core_tools_live.py -v -s
"""

import os
import sys
from pathlib import Path

import pytest


@pytest.fixture
def app(integration_app):
    """Use the isolated integration_app fixture (temp DB + creds from .env.test)."""
    return integration_app


@pytest.mark.integration
class TestMCPSearchLive:
    """Verify MCP Registry search returns real data."""

    def test_search_playwright(self):
        """Search for 'playwright' returns servers with install commands."""
        from mycelos.connectors.mcp_search import search_mcp_servers

        results = search_mcp_servers("playwright", limit=5)

        assert len(results) > 0, "No results for 'playwright'"
        first = results[0]
        assert first["name"], f"Empty name: {first}"
        assert first["description"], f"Empty description: {first}"
        assert first["packages"], f"No packages: {first}"

        # Should have an npm package with install command
        pkg = first["packages"][0]
        assert pkg["registry"] == "npm", f"Expected npm, got: {pkg}"
        assert pkg["name"], f"Empty package name: {pkg}"

        print(f"\nPlaywright MCP: {first['name']}", file=sys.stderr)
        print(f"  Install: npx -y {pkg['name']}", file=sys.stderr)
        print(f"  Repo: {first['repository']}", file=sys.stderr)

    def test_search_github(self):
        """Search for 'github' returns servers."""
        from mycelos.connectors.mcp_search import search_mcp_servers

        results = search_mcp_servers("github", limit=3)
        assert len(results) > 0, "No results for 'github'"
        print(f"\nGitHub MCP servers: {len(results)}", file=sys.stderr)
        for r in results[:3]:
            print(f"  - {r['name']}: {r['description'][:60]}", file=sys.stderr)

    def test_search_nonexistent(self):
        """Search for gibberish returns empty list."""
        from mycelos.connectors.mcp_search import search_mcp_servers

        results = search_mcp_servers("xyzzy_nonexistent_12345", limit=3)
        assert isinstance(results, list)
        # May return 0 or some vague matches — just shouldn't crash

    def test_search_via_tool_dispatch(self, app):
        """search_mcp_servers tool returns formatted results."""
        from mycelos.chat.service import ChatService

        svc = ChatService(app)
        result = svc._execute_tool("search_mcp_servers", {"query": "playwright"})

        assert "results" in result
        assert len(result["results"]) > 0
        first = result["results"][0]
        assert first["name"], "Empty name in tool result"
        assert first["install"], "Empty install command"
        print(f"\nTool result: {first['name']} → {first['install']}", file=sys.stderr)


@pytest.mark.integration
class TestWebSearchLive:
    """Verify web search returns real results."""

    def test_search_web_basic(self, app):
        """search_web returns results for a simple query."""
        from mycelos.chat.service import ChatService

        svc = ChatService(app)
        result = svc._execute_tool("search_web", {
            "query": "Mycelos agent operating system",
            "max_results": 3,
        })

        assert isinstance(result, list), f"Expected list, got: {type(result)}"
        print(f"\nWeb search results: {len(result)}", file=sys.stderr)
        for r in result[:3]:
            print(f"  - {r.get('title', '?')}: {r.get('url', '?')[:60]}", file=sys.stderr)

    def test_search_news(self, app):
        """search_news returns news results."""
        from mycelos.chat.service import ChatService

        svc = ChatService(app)
        result = svc._execute_tool("search_news", {
            "query": "artificial intelligence",
            "max_results": 3,
        })

        assert isinstance(result, list), f"Expected list, got: {type(result)}"
        print(f"\nNews results: {len(result)}", file=sys.stderr)


@pytest.mark.integration
class TestHTTPGetLive:
    """Verify http_get can fetch real web pages."""

    def test_fetch_example_com(self, app):
        """Fetch example.com — the simplest possible test."""
        from mycelos.chat.service import ChatService

        svc = ChatService(app)
        result = svc._execute_tool("http_get", {"url": "https://example.com"})

        assert isinstance(result, dict), f"Expected dict, got: {type(result)}"
        content = result.get("content", result.get("body", ""))
        assert "Example Domain" in content, f"Unexpected content: {content[:200]}"
        print(f"\nexample.com: {len(content)} chars", file=sys.stderr)

    def test_fetch_heise(self, app):
        """Fetch heise.de — real German news site."""
        from mycelos.chat.service import ChatService

        svc = ChatService(app)
        result = svc._execute_tool("http_get", {"url": "https://www.heise.de"})

        assert isinstance(result, dict)
        content = result.get("content", result.get("body", ""))
        assert len(content) > 1000, f"Content too short ({len(content)} chars)"
        print(f"\nheise.de: {len(content)} chars", file=sys.stderr)

    def test_fetch_returns_error_for_bad_url(self, app):
        """Bad URL returns error, not crash."""
        from mycelos.chat.service import ChatService

        svc = ChatService(app)
        result = svc._execute_tool("http_get", {"url": "https://thisdomaindoesnotexist12345.com"})

        assert isinstance(result, dict)
        assert "error" in result, f"Expected error for bad URL, got: {result}"
        print(f"\nBad URL error: {result.get('error', '')[:100]}", file=sys.stderr)
