"""Register all built-in connectors as tools in the ToolRegistry."""

from __future__ import annotations

from typing import Any

from mycelos.connectors.google_tools import (
    calendar_list,
    calendar_today,
    drive_list,
    gmail_labels,
    gmail_search,
)
from mycelos.connectors.http_tools import http_get, http_post
from mycelos.connectors.search_tools import search_news, search_web, search_web_brave
from mycelos.execution.tools import ToolDefinition, ToolRegistry


def register_builtin_tools(
    registry: ToolRegistry, credential_proxy: Any = None
) -> None:
    """Register all built-in HTTP and Search tools.

    Args:
        registry: The tool registry to register tools into.
        credential_proxy: Optional credential proxy for injecting API keys
            via closures. When provided, tools that require API keys will
            have them captured at registration time so agents never see them.
    """
    registry.register(
        ToolDefinition(
            name="http.get",
            description="Make an HTTP GET request to a URL",
            handler=http_get,
            required_capability="http.get",
        )
    )
    registry.register(
        ToolDefinition(
            name="http.post",
            description="Make an HTTP POST request",
            handler=http_post,
            required_capability="http.post",
        )
    )
    registry.register(
        ToolDefinition(
            name="search.web",
            description="Search the web using DuckDuckGo",
            handler=search_web,
            required_capability="search.web",
        )
    )

    # Brave Search: key injected via closure if credential proxy is available.
    # Key namespace unified in b365963 — bare id; legacy prefixed key kept as
    # fallback so old installs don't lose their config.
    if credential_proxy:
        cred = (
            credential_proxy.get_credential("web-search-brave")
            or credential_proxy.get_credential("connector:web-search-brave")
        )
        if cred and cred.get("api_key"):
            brave_key = cred["api_key"]

            def brave_search_with_key(
                query: str, max_results: int = 5
            ) -> list[dict[str, Any]]:
                return search_web_brave(
                    query, api_key=brave_key, max_results=max_results
                )

            registry.register(
                ToolDefinition(
                    name="search.web.brave",
                    description="Search the web using Brave Search API",
                    handler=brave_search_with_key,
                    required_capability="search.web",
                )
            )

    registry.register(
        ToolDefinition(
            name="search.news",
            description="Search news using DuckDuckGo",
            handler=search_news,
            required_capability="search.news",
        )
    )

    # Google tools via gog CLI (no credential proxy needed — gog handles OAuth)
    registry.register(
        ToolDefinition(
            name="google.gmail.search",
            description="Search Gmail for emails",
            handler=gmail_search,
            required_capability="google.gmail.read",
        )
    )
    registry.register(
        ToolDefinition(
            name="google.gmail.labels",
            description="List Gmail labels",
            handler=gmail_labels,
            required_capability="google.gmail.read",
        )
    )
    registry.register(
        ToolDefinition(
            name="google.calendar.list",
            description="List upcoming calendar events",
            handler=calendar_list,
            required_capability="google.calendar.read",
        )
    )
    registry.register(
        ToolDefinition(
            name="google.calendar.today",
            description="List today's events",
            handler=calendar_today,
            required_capability="google.calendar.read",
        )
    )
    registry.register(
        ToolDefinition(
            name="google.drive.list",
            description="List Google Drive files",
            handler=drive_list,
            required_capability="google.drive.read",
        )
    )
