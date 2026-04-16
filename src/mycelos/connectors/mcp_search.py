"""MCP Server Search — finds MCP servers from the official registry.

Uses the official MCP Registry API (no auth required) to search for
servers by keyword. The Creator-Agent and Planner can use this to
find connectors they need.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("mycelos.mcp")

REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"


def search_mcp_servers(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search the official MCP Registry for servers.

    Args:
        query: Search term (e.g., "filesystem", "github", "email").
        limit: Maximum results to return.

    Returns:
        List of server dicts with name, description, repository, packages.
    """
    import httpx

    try:
        resp = httpx.get(
            REGISTRY_URL,
            params={"search": query, "limit": limit},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        servers = data.get("servers", [])
        results = []
        for entry in servers:
            # API wraps each server in a "server" key
            s = entry.get("server", entry) if isinstance(entry, dict) else entry
            repo = s.get("repository", {})
            repo_url = repo.get("url", "") if isinstance(repo, dict) else str(repo)
            packages = s.get("packages", [])
            # Collect env vars from all packages
            env_vars = []
            for p in packages:
                for ev in p.get("environmentVariables", []):
                    env_vars.append({
                        "name": ev.get("name", ""),
                        "required": ev.get("isRequired", False),
                        "secret": ev.get("isSecret", False),
                        "description": ev.get("description", ""),
                    })

            results.append({
                "name": s.get("name", ""),
                "description": s.get("description", ""),
                "repository": repo_url,
                "packages": [
                    {
                        "registry": p.get("registryType", p.get("registry", "")),
                        "name": p.get("identifier", p.get("name", "")),
                    }
                    for p in packages
                ],
                "transport": [
                    p.get("transport", {}).get("type", "")
                    for p in packages if p.get("transport")
                ],
                "env_vars": env_vars,
            })
        return results

    except Exception as e:
        logger.warning("MCP registry search failed: %s", e)
        return []


def lookup_env_vars(package_name: str) -> list[dict]:
    """Look up required environment variables for an MCP package.

    Searches the registry for the package and returns its env var specs.
    Useful when registering a connector to know what secrets it needs.

    Returns list of: {"name": "CONTEXT7_API_KEY", "required": True, "secret": True, "description": "..."}
    """
    # Extract the package base name for search (e.g., "@upstash/context7-mcp" → "context7")
    search_term = package_name.split("/")[-1].replace("-mcp", "").replace("@", "")
    results = search_mcp_servers(search_term, limit=5)

    for r in results:
        for pkg in r.get("packages", []):
            if pkg.get("name") == package_name or package_name in pkg.get("name", ""):
                return r.get("env_vars", [])

    return []


def format_search_results(results: list[dict]) -> str:
    """Format search results as readable text for chat or slash command."""
    if not results:
        return "No MCP servers found for that query."

    lines = [f"**MCP Servers Found** ({len(results)})\n"]
    for r in results:
        lines.append(f"  **{r['name']}**")
        if r["description"]:
            lines.append(f"  {r['description'][:100]}")
        if r["packages"]:
            pkg = r["packages"][0]
            install = f"npx -y {pkg['name']}" if pkg["registry"] == "npm" else pkg["name"]
            lines.append(f"  Install: `{install}`")
        if r["repository"]:
            lines.append(f"  [dim]{r['repository']}[/dim]")
        lines.append("")

    lines.append("Add one with: `/connector add-custom <name> <install command>`")
    return "\n".join(lines)
