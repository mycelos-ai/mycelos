"""HTTP Tools — http.get, http.post through the Security Layer.

These tools execute in the Gateway process (not in the agent sandbox).
All requests are logged and responses are sanitized.
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_TIMEOUT = 30

# Module-level proxy client reference (set by gateway)
_proxy_client = None


def set_proxy_client(client) -> None:
    """Inject SecurityProxyClient for delegation.

    When set, http_get() and http_post() delegate to the proxy process
    instead of making direct calls. This is used in Gateway mode.
    For CLI mode, _proxy_client remains None and direct calls are made.
    """
    global _proxy_client
    _proxy_client = client


from mycelos.security.sanitizer import ResponseSanitizer
from mycelos.security.ssrf import validate_url as _validate_url  # Single source of truth

_sanitizer = ResponseSanitizer()


def _scrub(text: str) -> str:
    """Strip credentials from error messages before returning to agents (Rule 4)."""
    return _sanitizer.sanitize_text(text)


def http_get(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """HTTP GET request. Returns {status, headers, body}."""
    if _proxy_client is not None:
        return _proxy_client.http_get(url, headers=headers, timeout=timeout)
    try:
        _validate_url(url)
        response = httpx.get(
            url, headers=headers or {}, timeout=timeout, follow_redirects=False
        )
        return {
            "status": response.status_code,
            "headers": dict(response.headers),
            "body": response.text[:50_000],  # Cap at 50k chars
            "url": str(response.url),
        }
    except ValueError as e:
        return {"error": _scrub(f"URL blocked: {e}"), "status": 0}
    except httpx.TimeoutException:
        return {"error": f"Request timed out after {timeout}s", "status": 0}
    except httpx.RequestError as e:
        return {"error": _scrub(str(e)), "status": 0}


def http_post(
    url: str,
    body: dict[str, Any] | str | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """HTTP POST request. Returns {status, headers, body}."""
    if _proxy_client is not None:
        return _proxy_client.http_post(url, body=body, headers=headers, timeout=timeout)
    try:
        _validate_url(url)
        kwargs: dict[str, Any] = {
            "headers": headers or {},
            "timeout": timeout,
            "follow_redirects": False,
        }
        if isinstance(body, dict):
            kwargs["json"] = body
        elif isinstance(body, str):
            kwargs["content"] = body
        response = httpx.post(url, **kwargs)
        return {
            "status": response.status_code,
            "headers": dict(response.headers),
            "body": response.text[:50_000],
            "url": str(response.url),
        }
    except ValueError as e:
        return {"error": _scrub(f"URL blocked: {e}"), "status": 0}
    except httpx.TimeoutException:
        return {"error": f"Request timed out after {timeout}s", "status": 0}
    except httpx.RequestError as e:
        return {"error": _scrub(str(e)), "status": 0}
