"""Integration test: Connector setup -> credential storage -> tool use."""

import pytest

from mycelos.connectors.registry import register_builtin_tools
from mycelos.connectors.search_tools import search_news, search_web, search_web_brave
from mycelos.execution.tools import ToolRegistry


@pytest.mark.integration
def test_duckduckgo_search_real():
    """Real DuckDuckGo search returns results."""
    results = search_web("Python programming", max_results=3)
    assert len(results) > 0
    assert "title" in results[0]
    assert "url" in results[0]


@pytest.mark.integration
def test_brave_search_with_credential(require_brave_key, integration_app):
    """Brave Search works with credential from Credential Proxy."""
    app = integration_app
    # Credential should already be stored by the fixture
    cred = app.credentials.get_credential("connector:web-search-brave")
    assert cred is not None
    assert "api_key" in cred

    # Use the key to search
    results = search_web_brave(
        "hello world", api_key=cred["api_key"], max_results=2
    )
    assert len(results) > 0
    assert "error" not in results[0]


@pytest.mark.integration
def test_brave_tool_registered_with_closure(require_brave_key, integration_app):
    """Brave Search tool registered via closure — agent never sees API key."""
    app = integration_app
    registry = ToolRegistry()
    register_builtin_tools(registry, credential_proxy=app.credentials)

    # Brave tool should be registered
    tool = registry.get("search.web.brave")
    assert tool is not None

    # Call without api_key in args — closure provides it
    result = tool.handler(query="test query", max_results=1)
    assert isinstance(result, list)
    assert len(result) > 0


@pytest.mark.integration
def test_news_search_real():
    """Real news search returns results with dates."""
    results = search_news("artificial intelligence", max_results=3)
    assert len(results) > 0
    assert "title" in results[0]
