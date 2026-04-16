"""Web tools — search and HTTP access."""

from __future__ import annotations

import re
from typing import Any

from mycelos.tools.registry import ToolPermission

# --- Tool Schemas ---

SEARCH_WEB_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": "Search the web for information. Uses Brave Search if configured, otherwise DuckDuckGo.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}

SEARCH_NEWS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_news",
        "description": "Search for recent news articles on a topic.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The news search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}

HTTP_GET_SCHEMA = {
    "type": "function",
    "function": {
        "name": "http_get",
        "description": (
            "Fetch a web page or API endpoint. Returns the content in the requested format. "
            "Use format='markdown' for readable text, 'json' for structured data, "
            "or 'html' for raw HTML."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch",
                },
                "format": {
                    "type": "string",
                    "enum": ["html", "markdown", "json"],
                    "description": (
                        "Output format: 'html' (raw), 'markdown' (clean text), "
                        "'json' (structured data). Default: 'html'"
                    ),
                    "default": "html",
                },
            },
            "required": ["url"],
        },
    },
}


# --- Format Conversion Helpers ---

def _html_to_markdown(html: str) -> str:
    """Convert HTML to clean markdown text."""
    # Remove script/style tags
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL)
    # Convert headers
    text = re.sub(
        r"<h(\d)[^>]*>(.*?)</h\d>",
        lambda m: "#" * int(m.group(1)) + " " + m.group(2),
        text,
    )
    # Convert links
    text = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r"[\2](\1)", text)
    # Convert bold/strong
    text = re.sub(r"<(b|strong)[^>]*>(.*?)</\1>", r"**\2**", text)
    # Convert italic/em
    text = re.sub(r"<(i|em)[^>]*>(.*?)</\1>", r"*\2*", text)
    # Convert list items
    text = re.sub(r"<li[^>]*>(.*?)</li>", r"- \1\n", text, flags=re.DOTALL)
    # Convert paragraphs and breaks
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<p[^>]*>", "\n\n", text)
    text = re.sub(r"</p>", "", text)
    # Remove remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode HTML entities
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    text = text.replace("&nbsp;", " ")
    # Clean up whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _html_to_json(html: str) -> dict[str, Any]:
    """Extract structured data from HTML."""
    # Extract title
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""

    # Extract headings
    headings = re.findall(r"<h[1-6][^>]*>(.*?)</h[1-6]>", html, re.DOTALL)
    headings = [re.sub(r"<[^>]+>", "", h).strip() for h in headings]

    # Extract text content (strip tags)
    text = _html_to_markdown(html)

    # Extract links
    links = re.findall(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html)
    links_clean = [
        {"url": url, "text": re.sub(r"<[^>]+>", "", text).strip()}
        for url, text in links[:50]
        if url.startswith("http")
    ]

    return {
        "title": title,
        "headings": headings[:30],
        "text": text[:5000],
        "links": links_clean,
    }


# --- Tool Execution ---

def execute_search_web(args: dict, context: dict) -> Any:
    """Execute search_web tool."""
    from mycelos.connectors.search_tools import search_web

    return search_web(
        query=args.get("query", ""),
        max_results=args.get("max_results", 5),
    )


def execute_search_news(args: dict, context: dict) -> Any:
    """Execute search_news tool."""
    from mycelos.connectors.search_tools import search_news

    return search_news(
        query=args.get("query", ""),
        max_results=args.get("max_results", 5),
    )


def execute_http_get(args: dict, context: dict) -> Any:
    """Execute http_get tool with format conversion."""
    from mycelos.connectors.http_tools import http_get

    result = http_get(url=args.get("url", ""))

    # Check for error
    if result.get("error"):
        return result

    fmt = args.get("format", "html")
    body = result.get("body", "")

    if fmt == "markdown":
        return {
            "status": result.get("status"),
            "url": result.get("url"),
            "content": _html_to_markdown(body),
        }
    elif fmt == "json":
        structured = _html_to_json(body)
        structured["status"] = result.get("status")
        structured["url"] = result.get("url")
        return structured
    else:
        # html (default) — return as-is (original behavior)
        return result


# --- Registration ---

def register(registry: type) -> None:
    """Register all web tools."""
    registry.register("search_web", SEARCH_WEB_SCHEMA, execute_search_web, ToolPermission.STANDARD, concurrent_safe=True, category="knowledge_read")
    registry.register("search_news", SEARCH_NEWS_SCHEMA, execute_search_news, ToolPermission.STANDARD, concurrent_safe=True, category="knowledge_read")
    registry.register("http_get", HTTP_GET_SCHEMA, execute_http_get, ToolPermission.STANDARD, concurrent_safe=True, category="knowledge_read")
