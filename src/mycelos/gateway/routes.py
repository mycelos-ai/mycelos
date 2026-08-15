"""Gateway HTTP routes orchestrator + middleware.

Per-domain handlers live under ``mycelos.gateway.routers.*``. ``setup_routes``
mounts each domain router on the FastAPI app. Middleware classes
(``LocalhostMiddleware``, ``CSRFMiddleware``) and the test-facing helper
re-exports stay here for back-compat.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from mycelos.chat.events import ChatEvent
from mycelos.gateway.routers._helpers import (
    ChatRequest,
    ConfirmRequest,
    get_doc as _get_doc,
    list_docs as _list_docs,
    parse_frontmatter as _parse_frontmatter,
    resolve_user_id as _resolve_user_id,
    sse_error as _sse_error,
)

logger = logging.getLogger("mycelos.gateway")

_LOCALHOST_ADDRS = ("127.0.0.1", "::1")


class LocalhostMiddleware(BaseHTTPMiddleware):
    """Restrict /api/* routes to localhost unless the server binds to 0.0.0.0."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        # Only gate /api/* paths
        if request.url.path.startswith("/api/"):
            bind_host = getattr(request.app.state, "bind_host", "127.0.0.1")
            # If bound to localhost only, enforce the check
            if bind_host in _LOCALHOST_ADDRS:
                client_host = request.client.host if request.client else None
                if client_host not in _LOCALHOST_ADDRS:
                    return JSONResponse(
                        status_code=403,
                        content={"error": "API is only accessible from localhost"},
                    )
        return await call_next(request)


# Methods that a malicious cross-origin site could use to mutate state.
# GET/HEAD/OPTIONS are considered safe by the HTTP spec (server must not
# mutate state on them), so we only gate the dangerous verbs. OPTIONS
# specifically must pass through because browsers use it for CORS preflight.
_CSRF_GUARDED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Allowed Origin values compared against the Origin / Referer header.
# Built from bind host + env override at app startup and stashed on
# `app.state.csrf_allowed_origins` — this set can stay empty at import
# time and still match correctly at request time.
_LOCAL_ORIGIN_PREFIXES = ("http://localhost:", "http://127.0.0.1:", "http://[::1]:")


class CSRFMiddleware(BaseHTTPMiddleware):
    """Reject cross-origin browser requests that change state.

    Threat: a user has Mycelos running on localhost (or a LAN IP) and
    opens a malicious website in the same browser. Without this
    middleware, that page's JavaScript can POST to /api/connectors/gmail/
    tools/search_threads/call and exfiltrate email through the user's
    own open session — classic CSRF.

    Defense: for POST / PUT / PATCH / DELETE requests we require either
    - no `Origin` or `Referer` header at all (curl, mycelos CLI, server-
      to-server scripts — not browser-initiated), or
    - an `Origin` / `Referer` whose scheme+host+port matches one of the
      allowed origins (the gateway's own bind host + anything in
      MYCELOS_ALLOWED_ORIGINS).

    GET / HEAD / OPTIONS are passed through unchanged. OPTIONS
    specifically MUST pass so CORS preflight works.
    """

    def __init__(self, app, allowed_origins: set[str] | None = None) -> None:
        super().__init__(app)
        # Normalize to scheme://host[:port] with no trailing slash.
        self._static_allowed = {o.rstrip("/") for o in (allowed_origins or set())}

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if request.method not in _CSRF_GUARDED_METHODS:
            return await call_next(request)
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        origin = request.headers.get("origin") or ""
        referer = request.headers.get("referer") or ""

        # No Origin AND no Referer → not a browser-initiated request.
        # curl, httpx, the Mycelos CLI, etc. — allow through. This is
        # the intentional escape hatch for CLI / scripting; attackers
        # can't strip Origin from a real browser fetch.
        if not origin and not referer:
            return await call_next(request)

        allowed = set(self._static_allowed)
        allowed.update(getattr(request.app.state, "csrf_allowed_origins", set()) or set())

        # The Host header tells us the URL the browser is actually
        # talking to. If Origin matches that host exactly, the request
        # is same-origin by definition — no matter what we bind to or
        # what hostname our container thinks it has. This is the only
        # reliable check inside Docker / behind a reverse proxy where
        # `socket.gethostname()` returns a synthetic id.
        host_header = (request.headers.get("host") or "").strip().lower()

        def _origin_ok(value: str) -> bool:
            if not value:
                return False
            # Normalize: take scheme://host:port, drop path.
            try:
                from urllib.parse import urlparse
                p = urlparse(value)
                if not p.scheme or not p.netloc:
                    return False
                normalized = f"{p.scheme}://{p.netloc}"
                netloc_lower = p.netloc.lower()
            except Exception:
                return False
            if normalized in allowed:
                return True
            # Same-origin: Origin's host:port matches the request's Host
            # header. Catches `http://pi5.local:9100` posts to itself.
            if host_header and netloc_lower == host_header:
                return True
            # Always-accept localhost regardless of port — single-process
            # dev setup cycles ports a lot.
            return any(normalized.startswith(p) for p in _LOCAL_ORIGIN_PREFIXES)

        # Origin wins over Referer (Origin is set by the browser on
        # cross-origin POST/fetch and is harder for a page to forge).
        if origin:
            if not _origin_ok(origin):
                return JSONResponse(
                    {"error": "Cross-origin request blocked (CSRF)"},
                    status_code=403,
                )
        elif referer:
            if not _origin_ok(referer):
                return JSONResponse(
                    {"error": "Cross-origin request blocked (CSRF)"},
                    status_code=403,
                )

        return await call_next(request)


def setup_routes(api: FastAPI) -> None:
    """Register all gateway routes."""

    from mycelos.gateway.routers.chat import router as chat_router
    api.include_router(chat_router)

    from mycelos.gateway.routers.config import router as config_router
    api.include_router(config_router)

    from mycelos.gateway.routers.docs import router as docs_router
    api.include_router(docs_router)

    from mycelos.gateway.routers.cost import router as cost_router
    api.include_router(cost_router)

    from mycelos.gateway.routers.ui_theme import router as ui_theme_router
    api.include_router(ui_theme_router)

    from mycelos.gateway.routers.telegram_webhook import router as telegram_webhook_router
    api.include_router(telegram_webhook_router)

    from mycelos.gateway.routers.schedules import router as schedules_router
    api.include_router(schedules_router)

    from mycelos.gateway.routers.channels import router as channels_router
    api.include_router(channels_router)

    from mycelos.gateway.routers.agents import router as agents_router
    api.include_router(agents_router)

    from mycelos.gateway.routers.admin import router as admin_router
    api.include_router(admin_router)

    from mycelos.gateway.routers.audit import router as audit_router
    api.include_router(audit_router)

    from mycelos.gateway.routers.sessions import router as sessions_router
    api.include_router(sessions_router)

    from mycelos.gateway.routers.workflows import router as workflows_router
    api.include_router(workflows_router)

    from mycelos.gateway.routers.media import router as media_router
    api.include_router(media_router)

    from mycelos.gateway.routers.knowledge import router as knowledge_router
    api.include_router(knowledge_router)

    from mycelos.gateway.routers.briefing import router as briefing_router
    api.include_router(briefing_router)

    from mycelos.gateway.routers.models import router as models_router
    api.include_router(models_router)

    from mycelos.gateway.routers.setup import router as setup_router
    api.include_router(setup_router)

    from mycelos.gateway.routers.connectors import router as connectors_router
    api.include_router(connectors_router)

    from mycelos.gateway.routers.sources import router as sources_router
    api.include_router(sources_router)

    from mycelos.gateway.routers.inbox import router as inbox_router
    api.include_router(inbox_router)

    # ── End of API endpoints ───────────────────────────────────

