"""Browser security headers for every gateway response.

The Web UI renders text from outside sources (notes, imported mail, web
pages, LLM replies). The sanitizer in ``frontend/shared/safe-html.js`` is
the primary defence against script injection; these headers are the
second line:

- ``Content-Security-Policy`` limits where scripts, styles, frames and
  network requests may go. Script and connect sources are restricted to
  the gateway itself plus the few hosts the UI really loads from, so an
  injected script cannot pull code from, or send data to, an arbitrary
  server with ``fetch``. The pages still use inline scripts and Alpine.js
  (which evaluates expressions), so ``'unsafe-inline'`` and
  ``'unsafe-eval'`` stay until those are moved into files.
- ``X-Frame-Options`` / ``frame-ancestors`` stop other sites from framing
  the UI (clickjacking).
- ``X-Content-Type-Options: nosniff`` stops content-type guessing for
  uploaded files.
- ``Referrer-Policy`` keeps note paths out of the Referer sent to links.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

# Hosts the bundled pages load from. Keep this list short and explicit.
_SCRIPT_HOSTS = ("https://cdn.tailwindcss.com",)
_STYLE_HOSTS = ("https://fonts.googleapis.com",)
_FONT_HOSTS = ("https://fonts.gstatic.com",)
# MCP registry search runs in the browser on the connectors page.
_CONNECT_HOSTS = ("https://registry.modelcontextprotocol.io",)


def build_csp() -> str:
    """Return the Content-Security-Policy header value."""
    directives = [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' " + " ".join(_SCRIPT_HOSTS),
        "style-src 'self' 'unsafe-inline' " + " ".join(_STYLE_HOSTS),
        "font-src 'self' data: " + " ".join(_FONT_HOSTS),
        # Notes and replies may show remote images; they cannot run code.
        "img-src 'self' data: blob: https:",
        "media-src 'self' blob:",
        "connect-src 'self' " + " ".join(_CONNECT_HOSTS),
        "frame-src 'self' blob:",
        "worker-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'self'",
    ]
    return "; ".join(directives)


_STATIC_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", build_csp().encode("ascii")),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
)


class SecurityHeadersMiddleware:
    """Pure ASGI middleware — safe for streaming (SSE) responses."""

    def __init__(self, app: Callable[..., Awaitable[Any]]) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {name.lower() for name, _ in headers}
                for name, value in _STATIC_HEADERS:
                    # A route that sets its own policy (e.g. a download
                    # with a stricter CSP) keeps it.
                    if name not in present:
                        headers.append((name, value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
