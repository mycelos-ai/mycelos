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
import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("mycelos.gateway")

SESSION_COOKIE = "mycelos_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600  # one week (in-process)
# "Remember this device": the token's SHA-256 hash is persisted (system
# memory scope) so the device stays signed in across gateway restarts.
# Only the hash is stored — a DB leak never yields a usable token.
REMEMBER_TTL_DAYS = 90
_DEVICES_KEY = "auth_devices"

# Paths reachable without authentication: the health check (Docker),
# the login flow itself, translations, the static assets the login page
# needs, and the Telegram webhook. The webhook is called by Telegram's
# servers (no session/cookie), authenticated by its own secret-token
# header (verify_webhook_secret) — gating it behind the login wall would
# silently break webhook-mode delivery.
_PUBLIC_PATHS = {
    "/api/health", "/login", "/api/auth/login", "/api/i18n",
    "/telegram/webhook",
}
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

    def admit(self, token: str) -> None:
        """Re-cache an externally validated token (remembered device)."""
        self._tokens[token] = time.time() + SESSION_TTL_SECONDS

    def revoke(self, token: str | None) -> None:
        if token:
            self._tokens.pop(token, None)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _load_devices(mycelos) -> dict:
    try:
        devices = mycelos.memory.get("default", "system", _DEVICES_KEY)
        return devices if isinstance(devices, dict) else {}
    except Exception:
        return {}


def _remember_device(mycelos, token: str) -> None:
    devices = _load_devices(mycelos)
    expires = (datetime.now() + timedelta(days=REMEMBER_TTL_DAYS)).isoformat()
    devices[_token_hash(token)] = expires
    # Prune expired entries while we're here.
    now = datetime.now().isoformat()
    devices = {h: exp for h, exp in devices.items() if exp > now}
    mycelos.memory.set("default", "system", _DEVICES_KEY, devices)


def _device_remembered(mycelos, token: str) -> bool:
    devices = _load_devices(mycelos)
    expires = devices.get(_token_hash(token))
    return bool(expires) and expires > datetime.now().isoformat()


def _forget_device(mycelos, token: str) -> None:
    devices = _load_devices(mycelos)
    if devices.pop(_token_hash(token), None) is not None:
        mycelos.memory.set("default", "system", _DEVICES_KEY, devices)


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
        remember = bool(body.get("remember"))
        resp = JSONResponse({"ok": True, "next": _safe_next(body.get("next"))})
        if remember:
            # Persistent device: long-lived cookie + server-side token hash
            # so the session survives gateway restarts.
            _remember_device(request.app.state.mycelos, token)
            resp.set_cookie(
                SESSION_COOKIE, token,
                httponly=True, samesite="lax",
                max_age=REMEMBER_TTL_DAYS * 24 * 3600, path="/",
            )
        else:
            # Browser-session cookie (no Max-Age): dies with the browser,
            # and the in-process token dies with the gateway.
            resp.set_cookie(
                SESSION_COOKIE, token,
                httponly=True, samesite="lax", path="/",
            )
        try:
            request.app.state.mycelos.audit.log(
                "auth.login", details={"remember": remember},
            )
        except Exception:
            pass
        return resp

    @router.post("/api/auth/logout")
    async def logout(request: Request):
        token = request.cookies.get(SESSION_COOKIE)
        sessions.revoke(token)
        if token:
            _forget_device(request.app.state.mycelos, token)
        try:
            request.app.state.mycelos.audit.log("auth.logout")
        except Exception:
            pass
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    api.include_router(router)

    class AuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            path = request.url.path
            if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
                return await call_next(request)
            token = request.cookies.get(SESSION_COOKIE)
            if sessions.validate(token):
                return await call_next(request)
            # Remembered device: the in-process token is gone (restart), but
            # the persisted hash is valid — re-admit and re-cache.
            if token and _device_remembered(request.app.state.mycelos, token):
                sessions.admit(token)
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
