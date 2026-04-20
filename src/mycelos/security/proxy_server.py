"""SecurityProxy Server — FastAPI app that handles all external network access.

Runs in a child process. All outbound HTTP flows through here.
Every request requires Bearer token auth (MYCELOS_PROXY_TOKEN).
SSRF validation blocks private IPs and metadata endpoints.
Credential injection is done here — credentials never leave this process.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("mycelos.proxy")
from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from mycelos.speech.transcription import SttError, SttRequest, SttService

# litellm is an optional dependency — imported at module level so tests can patch it.
# In environments without litellm installed, the /llm/complete endpoint will return 500.
try:
    import litellm  # noqa: F401
except ImportError:
    litellm = None  # type: ignore[assignment]

# SSRF validation — single source of truth in ssrf.py
from mycelos.security.ssrf import validate_url as _validate_url


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class HttpProxyRequest(BaseModel):
    method: str = "GET"
    url: str
    headers: dict[str, str] = {}
    body: str | dict | None = None  # Accept dict (JSON body) or string
    timeout: int = 30
    inject_credential: str | None = None
    inject_as: str | None = None  # "bearer" | "header:{name}"


class HttpProxyResponse(BaseModel):
    status: int
    headers: dict[str, str] = {}
    body: str = ""
    url: str = ""
    error: str | None = None


class McpStartRequest(BaseModel):
    connector_id: str
    command: list[str]
    env_vars: dict[str, str] = {}
    transport: str = "stdio"


class McpCallRequest(BaseModel):
    session_id: str
    tool: str
    arguments: dict = {}


class McpStopRequest(BaseModel):
    session_id: str


class LlmCompleteRequest(BaseModel):
    model: str
    messages: list[dict]
    tools: list[dict] | None = None
    stream: bool = False
    purpose: str = "chat"


class CredentialBootstrapRequest(BaseModel):
    service: str


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------

def _write_audit(
    storage: Any,
    event_type: str,
    user_id: str,
    details: dict | None = None,
) -> None:
    """Log audit events from the proxy process.

    The proxy runs as a separate process. Writing to the same SQLite DB
    from two processes causes 'database is locked' errors. Instead of
    writing to DB, we log to the proxy logger. The gateway process handles
    all DB audit writes.
    """
    import logging as _log
    _log.getLogger("mycelos.proxy").debug("audit: %s user=%s %s", event_type, user_id, json.dumps(details or {}))


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_proxy_app() -> FastAPI:
    """Create and return the SecurityProxy FastAPI application.

    Reads configuration from environment variables:
    - MYCELOS_PROXY_TOKEN: Required bearer token for all requests
    - MYCELOS_MASTER_KEY: Master key for credential decryption
    - MYCELOS_DB_PATH: Path to the SQLite database for audit logging
    """
    proxy_token = os.environ.get("MYCELOS_PROXY_TOKEN", "")
    master_key = os.environ.get("MYCELOS_MASTER_KEY", "")
    db_path_str = os.environ.get("MYCELOS_DB_PATH", "")

    # LLM provider → credential service name mapping
    _PROVIDER_MAP = {
        "anthropic": "anthropic",
        "openai": "openai",
        "gemini": "gemini",
        "openrouter": "openrouter",
    }

    # Mutable state container — avoids closures over app.state before app is created
    _state: dict[str, Any] = {
        "_storage": None,
        "_credential_proxy": None,
        "_mcp_manager": None,
        "_stt_service": None,
        # Maps session_id → connector_id for MCP sessions started via /mcp/start
        "_mcp_sessions": {},
        # Tracks which credentials have already been bootstrapped this session
        "_bootstrapped": set(),
        "start_time": time.time(),
    }

    # Thread-safe lock for bootstrap one-shot consumption (H-03 fix)
    _bootstrap_lock = threading.Lock()

    # ---------------------------------------------------------------------------
    # Storage and credential proxy (lazy — created once on first use)
    # ---------------------------------------------------------------------------

    def _get_storage(read_only: bool = True) -> Any | None:
        """Get a storage connection for the proxy process.

        By default opens a read-only connection (safe for credential reads,
        no cross-process locking). Pass read_only=False to get a writable
        connection for credential writes (Phase 1b: proxy owns all writes).

        Existing callers that pass no argument remain unchanged.
        """
        if not db_path_str:
            return None
        if _state["_storage"] is None or _state.get("_storage_read_only") != read_only:
            import sqlite3 as _sql3
            if read_only:
                conn = _sql3.connect(
                    f"file:{db_path_str}?mode=ro",
                    uri=True,
                    timeout=5,
                )
            else:
                conn = _sql3.connect(db_path_str, timeout=5)
            conn.row_factory = _sql3.Row
            conn.execute("PRAGMA busy_timeout=3000")

            # Wrap in a minimal object that matches what EncryptedCredentialProxy needs
            class _Storage:
                def __init__(self, c):
                    self._conn = c
                def fetchone(self, sql, params=()):
                    return dict(self._conn.execute(sql, params).fetchone() or {}) or None
                def fetchall(self, sql, params=()):
                    return [dict(r) for r in self._conn.execute(sql, params).fetchall()]
                def execute(self, sql, params=()):
                    cur = self._conn.execute(sql, params)
                    if not read_only:
                        self._conn.commit()  # autocommit each statement in RW mode
                    return cur
                def _get_connection(self):
                    return self._conn

            _state["_storage"] = _Storage(conn)
            _state["_storage_read_only"] = read_only
        return _state["_storage"]

    def _get_credential_proxy() -> Any | None:
        if not master_key:
            return None
        storage = _get_storage()
        if storage is None:
            return None
        if _state["_credential_proxy"] is None:
            from mycelos.security.credentials import EncryptedCredentialProxy
            _state["_credential_proxy"] = EncryptedCredentialProxy(storage, master_key)
        return _state["_credential_proxy"]

    def _get_mcp_manager() -> Any:
        if _state["_mcp_manager"] is None:
            from mycelos.connectors.mcp_manager import MCPConnectorManager
            _state["_mcp_manager"] = MCPConnectorManager(
                credential_proxy=_get_credential_proxy()
            )
        return _state["_mcp_manager"]

    def _get_stt_service() -> SttService:
        if _state["_stt_service"] is None:
            credential_proxy = _get_credential_proxy()

            def _lookup(service_name: str, user_id: str = "default") -> dict | None:
                if not credential_proxy:
                    return None
                try:
                    cred = credential_proxy.get_credential(service_name, user_id=user_id)
                    # Fall back to "default" user — API keys are system-wide
                    if cred is None and user_id != "default":
                        cred = credential_proxy.get_credential(service_name, user_id="default")
                    return cred
                except Exception as exc:
                    logger.warning("STT credential lookup failed for %s: %s", service_name, exc)
                    return None  # Caller will deny — SttError raised in _create_backend

            _state["_stt_service"] = SttService(credential_lookup=_lookup)
        return _state["_stt_service"]

    # ---------------------------------------------------------------------------
    # Lifespan — log proxy.started
    # ---------------------------------------------------------------------------

    @asynccontextmanager
    async def lifespan(a: FastAPI):  # noqa: ANN001
        storage = _get_storage()
        if storage:
            _write_audit(storage, "proxy.started", "system", {"db_path": db_path_str})
        yield

    # ---------------------------------------------------------------------------
    # App creation
    # ---------------------------------------------------------------------------

    app = FastAPI(
        title="Mycelos SecurityProxy",
        version="0.1.0",
        description="Internal security proxy — all external network access flows here",
        lifespan=lifespan,
    )

    # Store start time on app.state for convenience
    app.state.start_time = _state["start_time"]

    # ---------------------------------------------------------------------------
    # Auth helper — applied in each route handler
    # ---------------------------------------------------------------------------

    def _check_auth(request: Request) -> tuple[bool, str]:
        """Return (authorized, user_id). Logs auth failures."""
        auth_header = request.headers.get("Authorization", "")
        user_id = request.headers.get("X-User-Id", "default")

        if not auth_header.startswith("Bearer "):
            storage = _get_storage()
            if storage:
                _write_audit(storage, "proxy.auth_failed", user_id, {
                    "reason": "missing_token",
                    "path": str(request.url.path),
                })
            return False, user_id

        token = auth_header[len("Bearer "):]
        if not hmac.compare_digest(token, proxy_token):
            storage = _get_storage()
            if storage:
                _write_audit(storage, "proxy.auth_failed", user_id, {
                    "reason": "wrong_token",
                    "path": str(request.url.path),
                })
            return False, user_id

        return True, user_id

    # ---------------------------------------------------------------------------
    # GET /healthz — unauthenticated liveness probe for Docker/K8s
    # ---------------------------------------------------------------------------

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # ---------------------------------------------------------------------------
    # GET /health — authenticated, includes operational detail
    # ---------------------------------------------------------------------------

    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
        authorized, _user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        uptime = time.time() - _state["start_time"]
        return JSONResponse({
            "status": "ok",
            "uptime_seconds": round(uptime, 2),
            "mcp_sessions": len(_state["_mcp_sessions"]),
        })

    # ---------------------------------------------------------------------------
    # POST /http
    # ---------------------------------------------------------------------------

    @app.post("/http")
    async def http_proxy(request: Request) -> JSONResponse:
        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        body_data = await request.json()
        req = HttpProxyRequest(**body_data)

        storage = _get_storage()

        # SSRF validation
        try:
            _validate_url(req.url)
        except ValueError as e:
            if storage:
                _write_audit(storage, "proxy.ssrf_blocked", user_id, {
                    "url": req.url,
                    "reason": str(e),
                })
            return JSONResponse({
                "status": 0,
                "error": f"URL blocked: {e}",
            })

        # Build outbound headers
        outbound_headers = dict(req.headers)

        # Credential injection — happens inside proxy, never exposed to callers
        if req.inject_credential:
            credential_proxy = _get_credential_proxy()
            if credential_proxy:
                try:
                    cred = credential_proxy.get_credential(req.inject_credential, user_id=user_id)
                    if cred and cred.get("api_key"):
                        api_key = cred["api_key"]
                        inject_as = req.inject_as or "bearer"
                        if inject_as == "bearer":
                            outbound_headers["Authorization"] = f"Bearer {api_key}"
                        elif inject_as.startswith("header:"):
                            header_name = inject_as[len("header:"):]
                            outbound_headers[header_name] = api_key
                except Exception:
                    return JSONResponse(
                        {"error": f"Credential injection failed for '{req.inject_credential}' — denied (fail-closed)"},
                        status_code=502,
                    )

        # Audit the outbound request (no credential values logged)
        if storage:
            _write_audit(storage, "proxy.http_request", user_id, {
                "method": req.method,
                "url": req.url,
                "inject_credential": req.inject_credential,
            })

        # Make the outbound request
        try:
            kwargs: dict[str, Any] = {
                "headers": outbound_headers,
                "timeout": req.timeout,
                "follow_redirects": False,
            }
            if req.body is not None:
                if isinstance(req.body, dict):
                    kwargs["json"] = req.body
                else:
                    kwargs["content"] = req.body

            response = httpx.request(req.method, req.url, **kwargs)

            return JSONResponse({
                "status": response.status_code,
                "headers": dict(response.headers),
                "body": response.text[:50_000],
                "url": str(response.url),
            })

        except httpx.TimeoutException:
            return JSONResponse({
                "status": 0,
                "error": f"Request timed out after {req.timeout}s",
            })
        except httpx.RequestError as e:
            logger.error("HTTP proxy request failed: %s", e)
            return JSONResponse({
                "status": 0,
                "error": "HTTP request failed. Check server logs for details.",
            })

    # ---------------------------------------------------------------------------
    # POST /mcp/start
    # ---------------------------------------------------------------------------

    @app.post("/mcp/start")
    async def mcp_start(request: Request) -> JSONResponse:
        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        agent_id = request.headers.get("X-Agent-Id", "")
        body_data = await request.json()
        req = McpStartRequest(**body_data)

        storage = _get_storage()
        t_start = time.time()

        # Resolve credential:X references in env_vars before passing to manager
        resolved_env: dict[str, str] = {}
        credential_proxy = _get_credential_proxy()
        for key, val in req.env_vars.items():
            if val.startswith("credential:") and credential_proxy:
                service_name = val[len("credential:"):]
                try:
                    cred = credential_proxy.get_credential(service_name, user_id=user_id)
                    if cred and cred.get("api_key"):
                        resolved_env[key] = cred["api_key"]
                    else:
                        return JSONResponse(
                            {"error": f"Credential '{service_name}' not found for env var '{key}' — denied (fail-closed)"},
                            status_code=502,
                        )
                except Exception:
                    return JSONResponse(
                        {"error": f"Credential lookup failed for '{service_name}' — denied (fail-closed)"},
                        status_code=502,
                    )
            else:
                resolved_env[key] = val

        try:
            mcp = _get_mcp_manager()
            tools = mcp.connect(
                connector_id=req.connector_id,
                command=req.command,
                env_vars=resolved_env,
                transport=req.transport,
            )
        except Exception as e:
            logger.error("MCP start failed for connector '%s': %s", req.connector_id, e)
            return JSONResponse(
                {"error": "MCP connector start failed. Check server logs for details.", "status": 0},
                status_code=500,
            )

        import secrets
        session_id = f"mcp-{req.connector_id}-{secrets.token_hex(6)}"
        _state["_mcp_sessions"][session_id] = req.connector_id

        duration = time.time() - t_start
        if storage:
            _write_audit(storage, "proxy.mcp_started", user_id, {
                "connector_id": req.connector_id,
                "command": req.command,
                "transport": req.transport,
                "agent_id": agent_id,
                "duration": round(duration, 3),
            })

        return JSONResponse({"session_id": session_id, "tools": tools})

    # ---------------------------------------------------------------------------
    # POST /mcp/call
    # ---------------------------------------------------------------------------

    @app.post("/mcp/call")
    async def mcp_call(request: Request) -> JSONResponse:
        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        agent_id = request.headers.get("X-Agent-Id", "")
        body_data = await request.json()
        req = McpCallRequest(**body_data)

        storage = _get_storage()

        if req.session_id not in _state["_mcp_sessions"]:
            return JSONResponse({
                "error": "MCP session not found",
                "session_id": req.session_id,
                "status": 0,
            })

        t_start = time.time()
        try:
            mcp = _get_mcp_manager()
            result = mcp.call_tool(req.tool, req.arguments)
        except Exception as e:
            logger.error("MCP call failed for tool '%s' in session '%s': %s", req.tool, req.session_id, e)
            return JSONResponse(
                {"error": "MCP tool call failed. Check server logs for details.", "status": 0},
                status_code=500,
            )

        duration = time.time() - t_start
        if storage:
            _write_audit(storage, "proxy.mcp_call", user_id, {
                "session_id": req.session_id,
                "tool": req.tool,
                "agent_id": agent_id,
                "duration": round(duration, 3),
            })

        return JSONResponse({"result": result})

    # ---------------------------------------------------------------------------
    # POST /mcp/stop
    # ---------------------------------------------------------------------------

    @app.post("/mcp/stop")
    async def mcp_stop(request: Request) -> JSONResponse:
        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        body_data = await request.json()
        req = McpStopRequest(**body_data)

        storage = _get_storage()

        # Remove from sessions map regardless of whether it exists
        _state["_mcp_sessions"].pop(req.session_id, None)

        if storage:
            _write_audit(storage, "proxy.mcp_stopped", user_id, {
                "session_id": req.session_id,
            })

        return JSONResponse({"ok": True})

    # ---------------------------------------------------------------------------
    # POST /llm/complete
    # ---------------------------------------------------------------------------

    @app.post("/llm/complete")
    async def llm_complete(request: Request) -> Any:
        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        agent_id = request.headers.get("X-Agent-Id", "")
        body_data = await request.json()
        req = LlmCompleteRequest(**body_data)

        storage = _get_storage()

        # Resolve API key from credential proxy — pass directly, never set in os.environ
        api_key: str | None = None
        provider = req.model.split("/")[0] if "/" in req.model else ""
        credential_name = _PROVIDER_MAP.get(provider)
        if credential_name:
            credential_proxy = _get_credential_proxy()
            if credential_proxy:
                try:
                    cred = credential_proxy.get_credential(credential_name, user_id=user_id)
                    if cred and cred.get("api_key"):
                        api_key = cred["api_key"]
                except Exception as e:
                    logger.warning("Credential lookup failed for LLM provider %s: %s", credential_name, e)

        if litellm is None:
            return JSONResponse({"error": "litellm not installed"}, status_code=500)

        t_start = time.time()

        kwargs: dict[str, Any] = {
            "model": req.model,
            "messages": req.messages,
            "stream": req.stream,
        }
        if req.tools:
            kwargs["tools"] = req.tools
        if api_key:
            kwargs["api_key"] = api_key

        def _sanitize_llm_error(e: Exception) -> str:
            """Strip potential credentials from LLM error messages."""
            from mycelos.security.sanitizer import ResponseSanitizer
            return ResponseSanitizer().sanitize_text(str(e))

        if req.stream:
            def _stream_chunks():
                try:
                    for chunk in litellm.completion(**kwargs):
                        delta = chunk.choices[0].delta if chunk.choices else None
                        content_piece = ""
                        tool_calls_piece = None
                        if delta:
                            content_piece = delta.content or ""
                            raw_tc = getattr(delta, "tool_calls", None)
                            if raw_tc:
                                try:
                                    tool_calls_piece = [
                                        {"index": getattr(tc, "index", 0),
                                         "id": getattr(tc, "id", None),
                                         "type": getattr(tc, "type", "function"),
                                         "function": {"name": getattr(tc.function, "name", None),
                                                      "arguments": getattr(tc.function, "arguments", "")}}
                                        for tc in raw_tc
                                    ]
                                except (AttributeError, TypeError):
                                    tool_calls_piece = None
                        payload = json.dumps({
                            "content": content_piece,
                            "tool_calls": tool_calls_piece,
                        })
                        yield f"data: {payload}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'error': _sanitize_llm_error(e)})}\n\n"

            return StreamingResponse(_stream_chunks(), media_type="text/event-stream")

        # Non-streaming
        try:
            response = litellm.completion(**kwargs)
        except Exception as e:
            return JSONResponse({"error": _sanitize_llm_error(e)}, status_code=500)

        duration = time.time() - t_start

        choice = response.choices[0] if response.choices else None
        content = choice.message.content if choice else None
        # tool_calls may be Pydantic objects — convert to dicts for JSON serialization
        raw_tool_calls = getattr(choice.message, "tool_calls", None) if choice else None
        tool_calls = None
        if raw_tool_calls:
            try:
                tool_calls = [
                    {"id": tc.id, "type": tc.type,
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in raw_tool_calls
                ]
            except (AttributeError, TypeError):
                tool_calls = [str(tc) for tc in raw_tool_calls]

        usage = response.usage if hasattr(response, "usage") else None
        usage_dict: dict[str, int] = {}
        cost = 0.0
        input_tokens = 0
        output_tokens = 0
        if usage:
            input_tokens = getattr(usage, "prompt_tokens", 0) or 0
            output_tokens = getattr(usage, "completion_tokens", 0) or 0
            usage_dict = {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": getattr(usage, "total_tokens", input_tokens + output_tokens) or 0,
            }
        try:
            cost = litellm.completion_cost(completion_response=response)
        except Exception:
            cost = 0.0

        # LLM usage and audit are logged by the gateway process (LLMBroker)
        # to avoid cross-process database locking. The proxy only returns
        # the usage data in the response — the caller logs it.

        return JSONResponse({
            "content": content,
            "tool_calls": tool_calls,
            "usage": usage_dict,
            "model": req.model,
            "cost": cost,
        })

    # ---------------------------------------------------------------------------
    # POST /credential/bootstrap
    # ---------------------------------------------------------------------------

    _BOOTSTRAP_WINDOW_SECONDS = 10

    @app.post("/credential/bootstrap")
    async def credential_bootstrap(request: Request) -> JSONResponse:
        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        body_data = await request.json()
        req = CredentialBootstrapRequest(**body_data)

        storage = _get_storage()

        # Check window — only first 10 seconds after startup
        elapsed = time.time() - _state["start_time"]
        if elapsed > _BOOTSTRAP_WINDOW_SECONDS:
            return JSONResponse(
                {"error": "Bootstrap window closed", "elapsed": round(elapsed, 1)},
                status_code=403,
            )

        # Each credential can only be bootstrapped once per session.
        # Thread-safe check-and-add to prevent concurrent race past the duplicate check.
        with _bootstrap_lock:
            if req.service in _state["_bootstrapped"]:
                return JSONResponse(
                    {"error": "Credential already bootstrapped this session", "service": req.service},
                    status_code=403,
                )
            _state["_bootstrapped"].add(req.service)

        credential_proxy = _get_credential_proxy()
        if not credential_proxy:
            return JSONResponse({"error": "Credential proxy unavailable"}, status_code=500)

        try:
            cred = credential_proxy.get_credential(req.service, user_id=user_id)
        except Exception as e:
            logger.error("Credential bootstrap failed for service '%s': %s", req.service, e)
            return JSONResponse(
                {"error": "Credential lookup failed. Check server logs for details."},
                status_code=404,
            )

        if not cred:
            return JSONResponse({"error": "Credential not found", "service": req.service}, status_code=404)

        if storage:
            _write_audit(storage, "proxy.credential_bootstrap", user_id, {
                "service": req.service,
            })

        # Never return plaintext credentials (Constitution Rule 4)
        return JSONResponse({"service": req.service, "status": "available"})

    # ---------------------------------------------------------------------------
    # POST /credential/store
    # ---------------------------------------------------------------------------

    @app.post("/credential/store")
    async def credential_store(request: Request) -> JSONResponse:
        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        body = await request.json()
        service = (body.get("service") or "").strip()
        label = (body.get("label") or "default").strip() or "default"
        payload = body.get("payload")
        description = body.get("description")
        if not service or not isinstance(payload, dict) or not payload:
            return JSONResponse({"error": "service + payload dict required"}, status_code=400)
        storage = _get_storage(read_only=False)
        if storage is None:
            return JSONResponse({"error": "storage unavailable"}, status_code=500)
        if not master_key:
            return JSONResponse({"error": "master key not available"}, status_code=500)
        from mycelos.security.credentials import EncryptedCredentialProxy
        ecp = EncryptedCredentialProxy(storage, master_key)
        ecp.store_credential(service, payload, user_id=user_id, label=label, description=description)
        return JSONResponse({"status": "stored", "service": service, "label": label})

    # ---------------------------------------------------------------------------
    # DELETE /credential/{service}/{label}
    # ---------------------------------------------------------------------------

    @app.delete("/credential/{service}/{label}")
    async def credential_delete(service: str, label: str, request: Request) -> JSONResponse:
        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        storage = _get_storage(read_only=False)
        if storage is None:
            return JSONResponse({"error": "storage unavailable"}, status_code=500)
        if not master_key:
            return JSONResponse({"error": "master key not available"}, status_code=500)
        from mycelos.security.credentials import EncryptedCredentialProxy
        ecp = EncryptedCredentialProxy(storage, master_key)
        ecp.delete_credential(service, user_id=user_id, label=label)
        return JSONResponse({"status": "deleted", "service": service, "label": label})

    # ---------------------------------------------------------------------------
    # GET /credential/list
    # ---------------------------------------------------------------------------

    @app.get("/credential/list")
    async def credential_list(request: Request) -> JSONResponse:
        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        storage = _get_storage(read_only=False)
        if storage is None:
            return JSONResponse({"credentials": []})
        if not master_key:
            return JSONResponse({"credentials": []})
        from mycelos.security.credentials import EncryptedCredentialProxy
        ecp = EncryptedCredentialProxy(storage, master_key)
        items = ecp.list_credentials(user_id=user_id)
        return JSONResponse({"credentials": items})

    # ---------------------------------------------------------------------------
    # POST /credential/rotate
    # ---------------------------------------------------------------------------

    @app.post("/credential/rotate")
    async def credential_rotate(request: Request) -> JSONResponse:
        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        body = await request.json()
        service = (body.get("service") or "").strip()
        label = (body.get("label") or "default").strip() or "default"
        if not service:
            return JSONResponse({"error": "service required"}, status_code=400)
        storage = _get_storage(read_only=False)
        if storage is None:
            return JSONResponse({"error": "storage unavailable"}, status_code=500)
        if not master_key:
            return JSONResponse({"error": "master key not available"}, status_code=500)
        from mycelos.security.credentials import EncryptedCredentialProxy
        ecp = EncryptedCredentialProxy(storage, master_key)
        ecp.mark_security_rotated(service)
        return JSONResponse({"status": "rotated", "service": service, "label": label})

    # ---------------------------------------------------------------------------
    # POST /stt/transcribe
    # ---------------------------------------------------------------------------

    _STT_MAX_BYTES = 25 * 1024 * 1024  # 25 MB — Whisper API limit

    @app.post("/stt/transcribe")
    async def stt_transcribe(
        request: Request,
        audio: UploadFile,
        language: str = Form(default="auto"),
        model: str = Form(default="whisper-1"),
        provider: str = Form(default=""),
    ) -> JSONResponse:
        authorized, user_id = _check_auth(request)
        if not authorized:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        storage = _get_storage()

        # Read file and validate size
        audio_bytes = await audio.read()
        if len(audio_bytes) > _STT_MAX_BYTES:
            return JSONResponse(
                {"error": f"Audio file exceeds 25 MB limit ({len(audio_bytes)} bytes)"},
                status_code=413,
            )

        stt_service = _get_stt_service()
        resolved_provider = stt_service.resolve_provider(provider if provider else None)
        stt_request = SttRequest(
            audio=audio_bytes,
            filename=audio.filename or "audio",
            mime_type=audio.content_type or "application/octet-stream",
            model=model,
            language=language,
        )

        try:
            result = stt_service.transcribe(
                stt_request,
                provider=provider if provider else None,
                user_id=user_id,
            )
        except SttError as exc:
            logger.error("STT transcription failed: %s", exc)
            exc_str = str(exc).lower()
            status = 400
            if "timed out" in exc_str:
                status = 504
            elif "request failed" in exc_str:
                status = 502
            from mycelos.security.sanitizer import ResponseSanitizer
            sanitized_msg = ResponseSanitizer().sanitize_text(str(exc))
            return JSONResponse({"error": sanitized_msg}, status_code=status)

        text = result.text
        detected_language = result.language or (language if language != "auto" else "")
        duration = result.duration_seconds

        if storage:
            _write_audit(storage, "proxy.stt_transcribe", user_id, {
                "provider": resolved_provider,
                "model": model,
                "language": language,
                "detected_language": detected_language,
                "duration_seconds": duration,
                "audio_bytes": len(audio_bytes),
                "filename": audio.filename,
            })

        return JSONResponse({
            "text": text,
            "language": detected_language,
            "duration_seconds": duration,
        })

    return app
