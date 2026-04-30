"""Integration test: Connector setup -> credential storage -> tool use."""

import pytest

from mycelos.connectors.search_tools import search_news, search_web, search_web_brave


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
def test_news_search_real():
    """Real news search returns results with dates."""
    results = search_news("artificial intelligence", max_results=3)
    assert len(results) > 0
    assert "title" in results[0]
