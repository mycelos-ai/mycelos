"""GitHub Tools — API access via stored credentials.

Uses the GitHub REST API with the token from the Credential Proxy.
No MCP server needed — direct HTTP calls with proper auth.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("mycelos.github")


def github_api(
    endpoint: str,
    credential_proxy: Any = None,
    method: str = "GET",
    body: dict | None = None,
) -> dict:
    """Call the GitHub REST API with authenticated credentials.

    Args:
        endpoint: API path (e.g., "/user/repos", "/repos/owner/name/issues").
        credential_proxy: CredentialProxy to fetch the token from.
        method: HTTP method (GET, POST, PATCH, DELETE).
        body: Request body for POST/PATCH.

    Returns:
        Dict with 'data' (parsed JSON) or 'error'.
    """
    import httpx
    import json as _json
    from mycelos.connectors import http_tools as _http_tools

    # Get token from credential proxy (bare id since b365963;
    # legacy 'connector:github' as fallback)
    token = None
    if credential_proxy:
        try:
            cred = (
                credential_proxy.get_credential("github")
                or credential_proxy.get_credential("connector:github")
            )
            if cred and cred.get("api_key"):
                token = cred["api_key"]
        except Exception:
            pass

    if not token:
        return {
            "error": "GitHub not configured. Set up with: /connector setup github",
        }

    # Normalize endpoint
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    # Ensure pagination for list endpoints
    if "per_page" not in endpoint and "?" not in endpoint:
        endpoint += "?per_page=30"
    elif "per_page" not in endpoint:
        endpoint += "&per_page=30"
    url = f"https://api.github.com{endpoint}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        if _http_tools._proxy_client is not None:
            if method.upper() == "GET":
                raw = _http_tools._proxy_client.http_get(url, headers=headers, timeout=15)
            elif method.upper() in ("POST", "PATCH"):
                raw = _http_tools._proxy_client.http_post(url, body=body or {}, headers=headers, timeout=15)
            else:
                return {"error": f"Unsupported method: {method}"}

            status = raw.get("status", 0)
            if status == 0:
                return {"error": f"GitHub API proxy error: {raw.get('error', 'unknown')}"}
            body_text = raw.get("body", "")
        else:
            if method.upper() == "GET":
                resp = httpx.get(url, headers=headers, timeout=15)
            elif method.upper() == "POST":
                resp = httpx.post(url, headers=headers, json=body or {}, timeout=15)
            elif method.upper() == "PATCH":
                resp = httpx.patch(url, headers=headers, json=body or {}, timeout=15)
            else:
                return {"error": f"Unsupported method: {method}"}

            status = resp.status_code
            body_text = resp.text

        if status == 401:
            return {"error": "GitHub token invalid or expired. Reconfigure with: /connector setup github"}
        if status == 404:
            return {"error": f"Not found: {endpoint}"}
        if status >= 400:
            return {"error": f"GitHub API error {status}: {body_text[:200]}"}

        data = _json.loads(body_text) if body_text else {}

        # Slim down large responses — GitHub returns ~100 fields per object
        if isinstance(data, list):
            data = [_slim_github_object(item) if isinstance(item, dict) else item for item in data]

        return {"data": data, "status": status, "count": len(data) if isinstance(data, list) else 1}

    except Exception as e:
        return {"error": f"GitHub API call failed: {e}"}


# Fields to keep from GitHub API objects (repos, issues, PRs, etc.)
_KEEP_FIELDS = {
    "id", "name", "full_name", "description", "html_url", "url",
    "private", "fork", "language", "stargazers_count", "forks_count",
    "open_issues_count", "created_at", "updated_at", "pushed_at",
    # Issues/PRs
    "title", "body", "state", "number", "labels", "assignees",
    "milestone", "comments", "pull_request",
    # User
    "login", "type", "avatar_url",
}


def _slim_github_object(obj: dict) -> dict:
    """Keep only useful fields from a GitHub API object."""
    result = {}
    for key, value in obj.items():
        if key in _KEEP_FIELDS:
            # Slim nested objects (e.g., owner)
            if isinstance(value, dict) and "login" in value:
                result[key] = {"login": value["login"]}
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                # Labels, assignees — keep just names
                result[key] = [
                    item.get("name") or item.get("login", str(item)[:50])
                    for item in value[:10]
                ]
            elif isinstance(value, str) and len(value) > 300:
                result[key] = value[:300] + "..."
            else:
                result[key] = value
    # Always include owner if present
    if "owner" in obj and isinstance(obj["owner"], dict):
        result["owner"] = obj["owner"].get("login", "?")
    return result
