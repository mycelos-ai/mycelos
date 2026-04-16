"""Search Tools -- web search via DuckDuckGo and Brave Search.

Returns structured search results with title, URL, and snippet.
"""

from __future__ import annotations

from typing import Any


def search_web(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search the web using DuckDuckGo. Returns list of {title, url, snippet}."""
    try:
        from ddgs import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("href", r.get("link", "")),
                "snippet": r.get("body", r.get("snippet", "")),
            }
            for r in results
        ]
    except ImportError:
        return [
            {
                "error": "ddgs not installed. Run: pip install ddgs"
            }
        ]
    except Exception as e:
        return [{"error": f"Search failed: {str(e)}"}]


def search_news(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search news using DuckDuckGo. Returns list of {title, url, snippet, date}."""
    try:
        from ddgs import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.news(query, max_results=max_results))
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", r.get("link", "")),
                "snippet": r.get("body", ""),
                "date": r.get("date", ""),
                "source": r.get("source", ""),
            }
            for r in results
        ]
    except ImportError:
        return [{"error": "ddgs not installed"}]
    except Exception as e:
        return [{"error": f"News search failed: {str(e)}"}]


def search_web_brave(
    query: str, api_key: str, max_results: int = 5
) -> list[dict[str, Any]]:
    """Search the web using Brave Search API.

    Args:
        query: Search query string.
        api_key: Brave Search API subscription token.
        max_results: Maximum number of results to return.

    Returns:
        List of dicts with title, url, and snippet keys.
    """
    try:
        import httpx

        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={
                "X-Subscription-Token": api_key,
                "Accept": "application/json",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return [{"error": f"Brave API returned {resp.status_code}"}]
        data = resp.json()
        results = data.get("web", {}).get("results", [])
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("description", ""),
            }
            for r in results
        ]
    except ImportError:
        return [{"error": "httpx not installed. Run: pip install httpx"}]
    except Exception as e:
        return [{"error": f"Brave search failed: {str(e)}"}]
