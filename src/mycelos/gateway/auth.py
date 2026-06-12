"""Gateway authentication — session-cookie login with Basic-Auth fallback.

When a password is configured, browsers get a proper login page
(GET /login) and a session cookie instead of the raw Basic-Auth popup
(which demands a username nothing ever checks). Basic Auth keeps working
for scripts, curl, and existing integrations.

Sessions are in-process random tokens with a TTL — no DB table, gone on
restart, which is the right trade-off for a single-user gateway.
"""
from __future__ import annotations

import base64
import logging
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("mycelos.gateway")

SESSION_COOKIE = "mycelos_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600  # one week

# Paths reachable without authentication: the health check (Docker),
# the login flow itself, translations, and the static assets the login
# page needs. Everything else is gated.
_PUBLIC_PATHS = {"/api/health", "/login", "/api/auth/login", "/api/i18n"}
_PUBLIC_PREFIXES = ("/shared/", "/assets/")

_LOGIN_PAGE = Path(__file__).resolve().parent.parent / "frontend" / "pages" / "login.html"


class SessionStore:
    """In-process session tokens with expiry."""

    def __init__(self) -> None:
        self._tokens: dict[str, float] = {}

    def issue(self) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens[token] = time.time() + SESSION_TTL_SECONDS
        return token

    def validate(self, token: str | None) -> bool:
        if not token:
            return False
        expires = self._tokens.get(token)
        if expires is None:
            return False
        if expires < time.time():
            self._tokens.pop(token, None)
            return False
        return True

    def revoke(self, token: str | None) -> None:
        if token:
            self._tokens.pop(token, None)


def _safe_next(value) -> str:
    """Only same-origin relative paths — never an open redirect."""
    if isinstance(value, str) and value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


def _basic_auth_ok(request: Request, password: str) -> bool:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth[6:]).decode("utf-8")
        _user, pw = decoded.split(":", 1)
        return secrets.compare_digest(pw, password)
    except Exception:
        return False


def install_auth(api: FastAPI, password: str) -> None:
    """Wire the login routes and the auth middleware into the gateway."""
    sessions = SessionStore()
    api.state.session_store = sessions

    router = APIRouter()

    @router.get("/login")
    async def login_page(request: Request):
        return FileResponse(_LOGIN_PAGE, media_type="text/html")

    @router.post("/api/auth/login")
    async def login(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        supplied = body.get("password") or ""
        if not isinstance(supplied, str) or not secrets.compare_digest(supplied, password):
            return JSONResponse({"error": "Wrong password"}, status_code=401)
        token = sessions.issue()
        resp = JSONResponse({"ok": True, "next": _safe_next(body.get("next"))})
        resp.set_cookie(
            SESSION_COOKIE, token,
            httponly=True, samesite="lax", max_age=SESSION_TTL_SECONDS, path="/",
        )
        return resp

    @router.post("/api/auth/logout")
    async def logout(request: Request):
        sessions.revoke(request.cookies.get(SESSION_COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    api.include_router(router)

    class AuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            path = request.url.path
            if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
                return await call_next(request)
            if sessions.validate(request.cookies.get(SESSION_COOKIE)):
                return await call_next(request)
            if _basic_auth_ok(request, password):
                return await call_next(request)
            # Browser navigation gets the login page; API callers get 401
            # with the Basic challenge so curl/scripts keep working.
            accepts_html = "text/html" in request.headers.get("Accept", "")
            if request.method == "GET" and accepts_html:
                return RedirectResponse(f"/login?next={path}", status_code=302)
            return JSONResponse(
                {"error": "Authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Mycelos"'},
            )

    api.add_middleware(AuthMiddleware)
    logger.info("Authentication enabled (session login + Basic Auth fallback)")
