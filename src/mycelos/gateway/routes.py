"""Gateway HTTP routes — chat, health, config."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from mycelos.chat.events import ChatEvent
from mycelos.gateway.routers._helpers import (
    ChatRequest,
    ConfirmRequest,
    ConnectorAddRequest,
    CredentialAddRequest,
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

    from mycelos.gateway.routers.models import router as models_router
    api.include_router(models_router)

    # ── Connectors ────────────────────────────────────────────

    def _recipe_payload(recipe) -> dict[str, Any]:
        """Serialize a recipe for the HTTP API (without the resolved setup guide).

        The single-recipe endpoint extends this with `setup_guide` resolved
        via `get_setup_guide(oauth_setup_guide_id)`; the list endpoint
        returns just the metadata.
        """
        return {
            "id": recipe.id,
            "name": recipe.name,
            "description": recipe.description,
            "kind": recipe.kind,
            "command": recipe.command,
            "transport": recipe.transport,
            "category": recipe.category,
            "credentials": list(recipe.credentials),
            "capabilities_preview": list(recipe.capabilities_preview),
            "setup_flow": recipe.setup_flow,
            "oauth_setup_guide_id": recipe.oauth_setup_guide_id,
            "oauth_client_credential_service": recipe.oauth_client_credential_service,
            "oauth_token_credential_service": recipe.oauth_token_credential_service,
            "http_endpoint": recipe.http_endpoint,
            "requires_node": recipe.requires_node,
        }

    @api.get("/api/connectors/lookup-env-vars")
    async def lookup_connector_env_vars(package: str) -> dict:
        """Return env-var hints for a known MCP package.

        Wraps mcp_search.lookup_env_vars so the Custom-MCP setup form
        can prefill its fields when the user types a known package.
        Failures are silenced — registry availability is not the user's
        problem; an empty list lets the user enter vars manually.
        """
        from mycelos.connectors.mcp_search import lookup_env_vars
        try:
            env_vars = lookup_env_vars(package) or []
        except Exception:
            env_vars = []
        return {"env_vars": env_vars}

    @api.get("/api/connectors/recipes")
    async def list_recipes_grouped() -> dict[str, list[dict[str, Any]]]:
        """List all connector recipes grouped by `kind`.

        Returns `{"channels": [...], "mcp": [...]}`. The frontend uses
        this split to render chat channels (Telegram, Slack, ...) and
        MCP connectors (GitHub, Gmail, ...) as separate sections.
        """
        from mycelos.connectors.mcp_recipes import RECIPES

        channels: list[dict[str, Any]] = []
        mcp: list[dict[str, Any]] = []
        for recipe in RECIPES.values():
            payload = _recipe_payload(recipe)
            if recipe.kind == "channel":
                channels.append(payload)
            else:
                mcp.append(payload)
        return {"channels": channels, "mcp": mcp}

    @api.get("/api/connectors/recipes/{recipe_id}")
    async def get_recipe(recipe_id: str) -> dict[str, Any]:
        """Recipe metadata + resolved setup guide in one roundtrip.

        Used by the frontend setup dialog to decide which flow to render
        (plain 'secret' vs. 'oauth_http' wizard) and to show the
        platform-specific preparation steps inline.
        """
        from mycelos.connectors.mcp_recipes import get_recipe as get_r
        from mycelos.connectors.oauth_setup_guides import get_setup_guide

        recipe = get_r(recipe_id)
        if recipe is None:
            raise HTTPException(status_code=404, detail=f"Unknown recipe: {recipe_id}")

        guide = (
            get_setup_guide(recipe.oauth_setup_guide_id)
            if recipe.oauth_setup_guide_id
            else None
        )
        return {**_recipe_payload(recipe), "setup_guide": guide}

    @api.post("/api/connectors/oauth/start")
    async def oauth_start_passthrough(
        request: Request, payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate an OAuth 2.0 Authorization Code Flow URL.

        Body: {recipe_id, origin}. Origin is the browser's
        window.location.origin — used to build the redirect_uri that
        must match what the user registered in Cloud Console.
        """
        import hashlib
        import base64
        import secrets as _secrets
        from datetime import datetime, timedelta, timezone
        import json as _json
        from urllib.parse import urlencode

        from mycelos.connectors.mcp_recipes import get_recipe

        recipe_id = payload.get("recipe_id", "")
        origin = (payload.get("origin") or "").rstrip("/")
        if not origin:
            raise HTTPException(status_code=400, detail="origin is required")

        recipe = get_recipe(recipe_id)
        if recipe is None:
            raise HTTPException(status_code=404, detail=f"Unknown recipe: {recipe_id}")
        if recipe.setup_flow != "oauth_http":
            raise HTTPException(
                status_code=400,
                detail=f"Recipe '{recipe_id}' setup_flow is '{recipe.setup_flow}', not 'oauth_http'",
            )

        mycelos = api.state.mycelos

        # Read ONLY the public `client_id` — never the client_secret.
        # In single-process mode we read the encrypted credential locally
        # and extract just client_id. In two-container mode the gateway
        # can't decrypt, so it asks the proxy via /oauth/public_fields
        # which returns only {client_id}. Either way, client_secret
        # never crosses into the gateway process.
        client_id = ""
        try:
            local_cred = mycelos.credentials.get_credential(
                recipe.oauth_client_credential_service, user_id="default",
            )
            if isinstance(local_cred, dict) and isinstance(local_cred.get("api_key"), str):
                blob = _json.loads(local_cred["api_key"])
                installed = blob.get("installed") or blob.get("web") or {}
                client_id = installed.get("client_id", "") or ""
        except NotImplementedError:
            client_id = ""
        except Exception:
            client_id = ""

        if not client_id:
            proxy_client = getattr(mycelos, "proxy_client", None)
            if proxy_client is not None:
                try:
                    got = proxy_client.oauth_public_fields(
                        recipe.oauth_client_credential_service, user_id="default",
                    )
                    if isinstance(got, dict):
                        client_id = got.get("client_id", "") or ""
                except Exception:
                    pass

        if not client_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"OAuth client credential '{recipe.oauth_client_credential_service}' "
                    "not uploaded. Paste client_secret_*.json first."
                ),
            )

        # Build PKCE pair and state.
        code_verifier = _secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).decode().rstrip("=")
        state = _secrets.token_urlsafe(32)

        redirect_uri = f"{origin}/api/connectors/oauth/callback"

        # Store state (TTL-protected; sweep expired on every call).
        states = getattr(api.state, "oauth_pending_states", None)
        if states is None:
            states = {}
            api.state.oauth_pending_states = states
        now = datetime.now(timezone.utc)
        expiry = (now + timedelta(minutes=10)).isoformat()
        for k in list(states.keys()):
            exp = states[k].get("expires_at", "")
            try:
                if datetime.fromisoformat(exp) < now:
                    states.pop(k, None)
            except Exception:
                states.pop(k, None)

        states[state] = {
            "recipe_id": recipe_id,
            "code_verifier": code_verifier,
            "user_id": "default",
            "origin": origin,
            "expires_at": expiry,
        }

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(recipe.oauth_scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }
        auth_url = f"{recipe.oauth_authorize_url}?{urlencode(params)}"

        return {"auth_url": auth_url, "redirect_uri": redirect_uri}


    @api.get("/api/connectors/oauth/callback")
    async def oauth_callback_passthrough(
        code: str = "",
        state: str = "",
        error: str = "",
    ):
        """Browser lands here after OAuth consent. Validate state,
        exchange the code through the proxy, redirect to the
        connectors page."""
        from fastapi.responses import RedirectResponse

        if error:
            return RedirectResponse(
                url=f"/connectors.html?oauth_error={error}",
                status_code=302,
            )

        mycelos = api.state.mycelos
        states = getattr(api.state, "oauth_pending_states", None) or {}
        entry = states.pop(state, None)
        if entry is None:
            return RedirectResponse(
                url="/connectors.html?oauth_error=invalid_state",
                status_code=302,
            )

        proxy_client = getattr(mycelos, "proxy_client", None)
        if proxy_client is None:
            return RedirectResponse(
                url="/connectors.html?oauth_error=proxy_unavailable",
                status_code=302,
            )

        redirect_uri = f"{entry['origin']}/api/connectors/oauth/callback"
        try:
            result = proxy_client.oauth_callback(
                recipe_id=entry["recipe_id"],
                code=code,
                code_verifier=entry["code_verifier"],
                redirect_uri=redirect_uri,
                user_id=entry["user_id"],
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/connectors.html?oauth_error={str(e)[:120]}",
                status_code=302,
            )

        if result.get("status") != "connected":
            err = (result.get("error") or "exchange_failed")[:120]
            return RedirectResponse(
                url=f"/connectors.html?oauth_error={err}",
                status_code=302,
            )

        # Token stored — now register the connector so it shows up in
        # the UI and becomes usable by agents. Idempotent: if the
        # connector already exists we skip (e.g. user re-consented to
        # refresh scopes).
        try:
            from mycelos.connectors.mcp_recipes import get_recipe
            recipe = get_recipe(entry["recipe_id"])
            existing = mycelos.connector_registry.get(entry["recipe_id"])
            if recipe is not None and existing is None:
                mycelos.connector_registry.register(
                    entry["recipe_id"],
                    recipe.name,
                    "mcp",
                    list(recipe.capabilities_preview or []),
                    description=recipe.description,
                    setup_type="oauth_http",
                )
                mycelos.audit.log(
                    "connector.registered",
                    details={
                        "connector": entry["recipe_id"],
                        "setup_type": "oauth_http",
                    },
                    user_id=entry["user_id"],
                )
            # Connect the live MCP session immediately so the user can
            # start using it without a gateway restart. Token resolution
            # happens inside MycelosMCPClient via oauth_token_manager.
            if recipe is not None and recipe.setup_flow == "oauth_http":
                try:
                    mycelos.mcp_manager.connect(
                        connector_id=entry["recipe_id"],
                        command="",
                        env_vars={},
                        transport="http",
                    )
                    logger.info("MCP session started after OAuth for %s", entry["recipe_id"])
                except Exception as e:
                    # Non-fatal: the startup-path connect will retry on
                    # the next gateway restart.
                    logger.warning(
                        "Post-OAuth MCP connect for '%s' failed: %s",
                        entry["recipe_id"], e,
                    )
        except Exception:
            # Connector-registry failure shouldn't undo the successful
            # token exchange; log and keep going. The user can retry
            # via the UI (which is idempotent).
            logger.exception("connector registration failed for %s", entry["recipe_id"])

        return RedirectResponse(
            url=f"/connectors.html?connected={entry['recipe_id']}",
            status_code=302,
        )

    @api.get("/api/connectors")
    async def list_connectors() -> list[dict[str, Any]]:
        """List all connectors with MCP tool count."""
        mycelos = api.state.mycelos
        connectors = mycelos.connector_registry.list_connectors()
        mcp_mgr = getattr(mycelos, "_mcp_manager", None)
        result = []
        for c in connectors:
            tool_count = 0
            if mcp_mgr:
                prefix = f"{c['id']}."
                tool_count = len([t for t in mcp_mgr.list_tools() if t["name"].startswith(prefix)])
            result.append({**dict(c), "tool_count": tool_count})
        return result

    @api.get("/api/connectors/{connector_id}")
    async def get_connector(connector_id: str) -> dict[str, Any]:
        """Look up a single connector by id. 404 if not registered.

        Used by the frontend's OAuth-dialog polling loop: the dialog
        hits this every ~2.5 seconds after showing the auth URL; a 200
        means the callback handler has registered the connector (i.e.
        the user completed consent) and the dialog flips to Stage 3.
        """
        mycelos = api.state.mycelos
        c = mycelos.connector_registry.get(connector_id)
        if c is None:
            raise HTTPException(status_code=404, detail=f"Unknown connector: {connector_id}")
        return dict(c)

    @api.post("/api/connectors")
    async def add_connector(request: Request, body: ConnectorAddRequest) -> dict[str, Any]:
        """Add a connector (same logic as /connector add slash command)."""
        mycelos = api.state.mycelos

        # Validate command if provided
        if body.command:
            from mycelos.chat.slash_commands import _validate_mcp_command
            validation_error = _validate_mcp_command(body.command)
            if validation_error:
                return JSONResponse({"error": f"Invalid command: {validation_error}"}, status_code=400)

        # Check if connector already exists
        existing = mycelos.connector_registry.get(body.name)
        if existing:
            return JSONResponse({"error": f"Connector '{body.name}' already exists"}, status_code=409)

        # Detect builtin connectors (email, etc.) — these don't need MCP commands
        from mycelos.connectors.mcp_recipes import get_recipe
        recipe = get_recipe(body.name)
        is_builtin = recipe and recipe.transport == "builtin"
        is_channel = recipe and recipe.transport == "channel"
        if is_builtin:
            connector_type = "builtin"
            setup_type = "builtin"
        elif is_channel:
            connector_type = "channel"
            setup_type = "channel"
        else:
            connector_type = "mcp"
            setup_type = "mcp"
        description = recipe.description if recipe else (
            f"MCP: {body.command}" if body.command else f"Connector: {body.name}"
        )

        try:
            mycelos.connector_registry.register(
                body.name, body.name, connector_type, [],
                description=description,
                setup_type=setup_type,
            )
        except Exception as e:
            return JSONResponse({"error": f"Failed to register connector: {e}"}, status_code=500)

        # Store secret if provided. Key stored under the bare connector
        # name — both builtins (telegram, email) and MCP connectors share
        # one namespace. The MCP subsystem substitutes `credential:<id>`
        # in env_vars and the SecurityProxy resolves that via the bare
        # name.
        # env_vars (multi-var) wins over legacy single `secret`. We support
        # both shapes so recipe-setup code (which sends `secret`) keeps working.
        cleaned_env_vars: dict[str, str] | None = None
        if body.env_vars:
            cleaned_env_vars = {
                k: v for k, v in body.env_vars.items() if k.strip()
            }
            if not cleaned_env_vars:
                cleaned_env_vars = None  # all keys were blank — fall through

        logger.info(
            "add_connector: name=%s mode=%s",
            body.name,
            "multi" if cleaned_env_vars else ("secret" if body.secret else "none"),
        )

        if cleaned_env_vars:
            try:
                logger.info(
                    "add_connector: storing multi-var credential service=%s vars=%s",
                    body.name, list(cleaned_env_vars.keys()),
                )
                mycelos.credentials.store_credential(
                    body.name,
                    {
                        "api_key": json.dumps(cleaned_env_vars),
                        "env_var": "__multi__",
                        "connector": body.name,
                    },
                    description=f"Credentials for {body.name}",
                )
                mycelos.audit.log(
                    "credential.stored",
                    details={"connector": body.name, "env_var": "__multi__",
                             "var_names": list(cleaned_env_vars.keys())},
                    user_id=_resolve_user_id(request),
                )
            except Exception as e:
                logger.exception("Credential storage failed for connector %s: %s", body.name, e)
                mycelos.audit.log(
                    "credential.store_failed",
                    details={"connector": body.name, "error": str(e)},
                    user_id=_resolve_user_id(request),
                )
        elif body.secret:
            try:
                # Recipe-declared env_var name (e.g. BRAVE_API_KEY) if the
                # connector is a known MCP recipe; otherwise derive from
                # the name.
                if recipe and recipe.credentials:
                    env_var_name = recipe.credentials[0].get("env_var", "")
                else:
                    env_var_name = f"{body.name.upper().replace('-', '_')}_API_KEY"

                logger.info(
                    "add_connector: storing credential service=%s env_var=%s",
                    body.name, env_var_name,
                )
                mycelos.credentials.store_credential(
                    body.name,
                    {"api_key": body.secret, "env_var": env_var_name},
                    description=f"Credentials for {body.name}",
                )
                mycelos.audit.log(
                    "credential.stored",
                    details={"connector": body.name, "env_var": env_var_name},
                    user_id=_resolve_user_id(request),
                )
            except Exception as e:
                logger.exception("Credential storage failed for connector %s: %s", body.name, e)
                mycelos.audit.log(
                    "credential.store_failed",
                    details={"connector": body.name, "error": str(e)},
                    user_id=_resolve_user_id(request),
                )
        else:
            logger.info("add_connector: no creds provided for %s — skipping store", body.name)

        # Channel connectors also need a row in `channels` so the channel
        # layer (Telegram polling, Slack socket, ...) actually picks them up.
        if is_channel:
            import json as _json
            try:
                mycelos.storage.execute("DELETE FROM channels WHERE id = ?", (body.name,))
                mycelos.storage.execute(
                    """INSERT INTO channels (id, channel_type, mode, status, config, allowed_users)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (body.name, body.name, "polling", "active", "{}", "[]"),
                )
                mycelos.audit.log("channel.configured", details={"channel": body.name})
            except Exception as e:
                logger.exception("channel row insert failed for %s: %s", body.name, e)

        mycelos.audit.log("connector.added", details={"connector": body.name, "command": body.command}, user_id=_resolve_user_id(request))

        # Auto-start MCP server for recipe-based connectors (so no restart
        # needed). In two-container mode the subprocess belongs to the
        # proxy container — it's the only process that can decrypt the
        # credentials the MCP server needs. Route the mcp_start RPC
        # through proxy_client instead of spawning locally.
        if not is_builtin and recipe and recipe.command and recipe.transport == "stdio":
            def _auto_start_recipe() -> None:
                try:
                    env_vars: dict[str, str] = dict(recipe.static_env)
                    for cred_spec in recipe.credentials:
                        env_var = cred_spec["env_var"]
                        env_vars[env_var] = f"credential:{body.name}"

                    from mycelos.connectors import http_tools as _http_tools
                    proxy_client = getattr(_http_tools, "_proxy_client", None)
                    if proxy_client is not None:
                        import shlex
                        argv = shlex.split(recipe.command)
                        resp = proxy_client.mcp_start(
                            connector_id=body.name,
                            command=argv,
                            env_vars=env_vars,
                            transport=recipe.transport,
                        )
                        if resp.get("error"):
                            raise RuntimeError(resp["error"])
                        tools = resp.get("tools", [])
                        mycelos.mcp_manager.register_remote_session(
                            connector_id=body.name,
                            session_id=resp.get("session_id", ""),
                            tools=tools,
                        )
                        tool_count = len(tools)
                    else:
                        tools = mycelos.mcp_manager.connect(
                            connector_id=body.name,
                            command=recipe.command,
                            env_vars=env_vars,
                            transport=recipe.transport,
                        )
                        tool_count = len(tools)
                    logger.info("MCP server '%s' auto-started: %d tools", body.name, tool_count)
                except Exception as e:
                    logger.warning("MCP auto-start failed for '%s': %s", body.name, e)

            threading.Thread(
                target=_auto_start_recipe,
                name=f"mcp-autostart-{body.name}",
                daemon=True,
            ).start()

        if not is_builtin and not recipe and body.command:
            def _auto_start_custom() -> None:
                try:
                    stored = mycelos.credentials.get_credential(body.name)
                    env_vars: dict[str, str] = {}
                    if stored:
                        if stored.get("env_var") == "__multi__":
                            env_vars["__multi__"] = f"credential:{body.name}"
                        elif stored.get("env_var"):
                            env_vars[stored["env_var"]] = f"credential:{body.name}"

                    from mycelos.connectors import http_tools as _http_tools
                    proxy_client = getattr(_http_tools, "_proxy_client", None)
                    import shlex
                    argv = shlex.split(body.command)
                    if proxy_client is not None:
                        resp = proxy_client.mcp_start(
                            connector_id=body.name,
                            command=argv,
                            env_vars=env_vars,
                            transport="stdio",
                        )
                        if resp.get("error"):
                            raise RuntimeError(resp["error"])
                        tools = resp.get("tools", [])
                        mycelos.mcp_manager.register_remote_session(
                            connector_id=body.name,
                            session_id=resp.get("session_id", ""),
                            tools=tools,
                        )
                        tool_count = len(tools)
                    else:
                        tools = mycelos.mcp_manager.connect(
                            connector_id=body.name,
                            command=body.command,
                            env_vars=env_vars,
                            transport="stdio",
                        )
                        tool_count = len(tools)
                    logger.info(
                        "Custom MCP server '%s' auto-started: %d tools",
                        body.name, tool_count,
                    )
                except Exception as e:
                    logger.warning("Custom MCP auto-start failed for '%s': %s", body.name, e)

            threading.Thread(
                target=_auto_start_custom,
                name=f"mcp-autostart-{body.name}",
                daemon=True,
            ).start()

        return {"status": "registered", "connector": body.name}

    @api.delete("/api/connectors/{connector_id}")
    async def remove_connector(request: Request, connector_id: str) -> dict[str, Any]:
        """Remove a connector."""
        mycelos = api.state.mycelos
        existing = mycelos.connector_registry.get(connector_id)
        if not existing:
            return JSONResponse({"error": f"Connector '{connector_id}' not found"}, status_code=404)

        mycelos.connector_registry.remove(connector_id)
        mycelos.audit.log("connector.removed", details={"connector": connector_id}, user_id=_resolve_user_id(request))
        return {"status": "removed", "connector": connector_id}

    @api.get("/api/connectors/{connector_id}/tools")
    async def connector_tools(request: Request, connector_id: str) -> dict[str, Any]:
        """List the MCP tools exposed by one connector, with their
        descriptions and current policy status. Powers the Tool
        Transparency panel in the Connectors page.

        Returns { tools: [{name, description, policy, blocked_reason}], ... }.
        A tool is ``blocked`` when the PolicyEngine would return
        ``"never"`` for it — that's the canonical reason an agent can
        see but not use a tool.
        """
        mycelos = api.state.mycelos
        existing = mycelos.connector_registry.get(connector_id)
        if not existing:
            return JSONResponse({"error": f"Connector '{connector_id}' not found"}, status_code=404)

        user_id = _resolve_user_id(request)
        prefix = f"{connector_id}."
        mcp_mgr = getattr(mycelos, "_mcp_manager", None)
        raw_tools: list[dict[str, Any]] = []
        if mcp_mgr is not None:
            try:
                raw_tools = [t for t in mcp_mgr.list_tools() if t["name"].startswith(prefix)]
            except Exception as e:
                return {"connector": connector_id, "tools": [], "error": str(e)}

        policy = mycelos.policy_engine
        tools_out: list[dict[str, Any]] = []
        for t in raw_tools:
            decision = None
            try:
                decision = policy.evaluate(user_id, None, t["name"])
            except Exception:
                decision = None
            blocked = decision == "never"
            tools_out.append({
                "name": t["name"][len(prefix):],
                "full_name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("input_schema") or {},
                "policy": decision or "default",
                "blocked": blocked,
            })

        return {
            "connector": connector_id,
            "operational_state": existing.get("operational_state"),
            "last_success_at": existing.get("last_success_at"),
            "last_error": existing.get("last_error"),
            "last_error_at": existing.get("last_error_at"),
            "tools": tools_out,
        }

    @api.post("/api/connectors/{connector_id}/tools/{tool_name}/call")
    async def connector_tool_call(
        request: Request,
        connector_id: str,
        tool_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Invoke one MCP tool on a connector. Powers `mycelos
        connector call` and any future UI 'try this tool' button.

        Body: {arguments: {...}}. Returns the raw MCP tool result.
        Failures from the underlying MCP call surface as 502 with the
        error message; a totally missing tool is 404.
        """
        mycelos = api.state.mycelos
        existing = mycelos.connector_registry.get(connector_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Connector '{connector_id}' not found")

        mcp_mgr = getattr(mycelos, "_mcp_manager", None)
        if mcp_mgr is None:
            raise HTTPException(status_code=503, detail="MCP manager not available")

        full_name = f"{connector_id}.{tool_name}"
        arguments = payload.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise HTTPException(status_code=400, detail="arguments must be an object")

        result = mcp_mgr.call_tool(full_name, arguments)
        # call_tool returns either a dict-with-content (success) or a
        # {error: "..."} dict (manager-level failure). Treat the error
        # shape as 502 so curl/CLI users see a non-2xx; otherwise pass
        # through verbatim so the caller can inspect the MCP payload.
        if isinstance(result, dict) and "error" in result and len(result) == 1:
            return JSONResponse({"error": result["error"]}, status_code=502)
        return {"connector": connector_id, "tool": tool_name, "result": result}

    @api.post("/api/connectors/{connector_id}/test")
    async def test_connector(request: Request, connector_id: str) -> dict[str, Any]:
        """Run a live connectivity check on a connector.

        Uses the shape of the connector to pick the right probe:
          * telegram → ``getMe`` via the proxy
          * MCP-backed connectors → ``tools/list`` on the running session
          * everything else → a 'not testable' hint

        Every outcome flows through connector_registry.record_* so the
        panel and Doctor see fresh telemetry immediately.
        """
        mycelos = api.state.mycelos
        existing = mycelos.connector_registry.get(connector_id)
        if not existing:
            return JSONResponse({"error": f"Connector '{connector_id}' not found"}, status_code=404)

        ctype = (existing.get("connector_type") or "").lower()
        user_id = _resolve_user_id(request)

        def _ok(detail: str, **extra) -> dict[str, Any]:
            mycelos.connector_registry.record_success(connector_id)
            mycelos.audit.log(
                "connector.test_ok",
                details={"connector": connector_id, **extra},
                user_id=user_id,
            )
            return {"ok": True, "connector": connector_id, "detail": detail, **extra}

        def _fail(detail: str, **extra) -> dict[str, Any]:
            mycelos.connector_registry.record_failure(connector_id, detail)
            mycelos.audit.log(
                "connector.test_failed",
                details={"connector": connector_id, "error": detail[:200], **extra},
                user_id=user_id,
            )
            return {"ok": False, "connector": connector_id, "detail": detail, **extra}

        # ── Telegram ────────────────────────────────────────────
        if connector_id == "telegram" or ctype in ("telegram", "channel"):
            from mycelos.channels.telegram import call_telegram_api
            data = call_telegram_api(mycelos, "getMe", http_method="GET", timeout=5)
            if data.get("ok"):
                bot = data.get("result", {}) or {}
                return _ok(
                    f"Bot '{bot.get('first_name', '?')}' (@{bot.get('username', '?')}) reachable",
                    bot_username=bot.get("username"),
                    bot_name=bot.get("first_name"),
                )
            return _fail(data.get("description", "unknown error"))

        # ── Built-in connectors (http, search, etc.) ──────────────
        # These are always-on in-process helpers, not MCP sessions.
        # 'Testing' them by walking the MCP-tool list is meaningless —
        # their tools live in the ToolRegistry under their own names
        # (http_get, search_web, ...). Report healthy when registered.
        if ctype in ("http", "search", "builtin"):
            return _ok(f"Built-in {ctype} connector is active")

        # ── MCP-backed ─────────────────────────────────────────
        mcp_mgr = getattr(mycelos, "_mcp_manager", None)
        if mcp_mgr is not None:
            prefix = f"{connector_id}."
            try:
                tools = [t for t in mcp_mgr.list_tools() if t["name"].startswith(prefix)]
            except Exception as e:
                return _fail(f"tools/list failed: {e}")
            if tools:
                return _ok(f"{len(tools)} tool(s) loaded", tool_count=len(tools))

            # No tools loaded — session may have died or never started.
            # Try one reconnect from the recipe before surfacing the
            # "No tools discovered" error. Test-connection now actually
            # heals a dead subprocess instead of just reading stale state.
            #
            # In two-container mode (proxy_client set) the subprocess
            # must live in the proxy container — route through
            # proxy_client.mcp_start. In single-process mode, let the
            # local mcp_manager spawn it directly via its recipe.
            try:
                from mycelos.connectors import http_tools as _http_tools
                from mycelos.connectors.mcp_recipes import get_recipe
                import shlex as _shlex

                proxy_client = getattr(_http_tools, "_proxy_client", None)
                recipe = get_recipe(connector_id)

                if proxy_client is not None and recipe is not None and recipe.setup_flow != "oauth_http":
                    # Subprocess-based recipe → spawn in proxy.
                    env_vars = dict(recipe.static_env or {})
                    for cred_spec in recipe.credentials or []:
                        env_vars[cred_spec["env_var"]] = f"credential:{connector_id}"
                    argv = _shlex.split(recipe.command) if recipe.command else []
                    resp = proxy_client.mcp_start(
                        connector_id=connector_id,
                        command=argv,
                        env_vars=env_vars,
                        transport=recipe.transport,
                    )
                    if resp.get("error"):
                        raise RuntimeError(resp["error"])
                    new_tools = resp.get("tools", [])
                    mycelos.mcp_manager.register_remote_session(
                        connector_id=connector_id,
                        session_id=resp.get("session_id", ""),
                        tools=new_tools,
                    )
                else:
                    # Single-process mode OR oauth_http (no subprocess):
                    # let the local manager handle it.
                    mcp_mgr.reconnect(connector_id)

                tools = [t for t in mcp_mgr.list_tools() if t["name"].startswith(prefix)]
                if tools:
                    return _ok(
                        f"Reconnected; {len(tools)} tool(s) loaded",
                        tool_count=len(tools),
                    )
            except Exception as e:
                return _fail(f"reconnect failed: {e}")
            return _fail(
                "No tools discovered after reconnect. "
                "Check credentials and recipe configuration."
            )

        return {
            "ok": None,
            "connector": connector_id,
            "detail": "No test available for this connector type.",
        }

    # ── Setup / Onboarding ─────────────────────────────────────

    @api.get("/api/setup/status")
    async def setup_status() -> dict[str, Any]:
        """Tell the frontend whether onboarding is still required."""
        from mycelos.setup import is_initialized
        mycelos = api.state.mycelos
        return {"initialized": is_initialized(mycelos)}

    @api.post("/api/setup")
    async def run_setup(body: dict[str, Any]) -> dict[str, Any]:
        """Run the web onboarding flow: credential + provider + models + agents."""
        from mycelos.setup import SetupError, web_init
        mycelos = api.state.mycelos
        try:
            return web_init(
                mycelos,
                api_key=body.get("api_key"),
                provider_id=body.get("provider_id"),
                ollama_url=body.get("ollama_url"),
            )
        except SetupError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.exception("web_init failed")
            raise HTTPException(status_code=500, detail=f"Setup failed: {e}")

    # ── Credentials ────────────────────────────────────────────

    @api.get("/api/credentials")
    async def list_credentials() -> list[dict[str, Any]]:
        """List credentials (service + label only, NO keys)."""
        mycelos = api.state.mycelos
        try:
            creds = mycelos.credentials.list_credentials()
            return creds
        except Exception:
            # Gateway mode — credentials managed by proxy
            services = mycelos.storage.fetchall(
                "SELECT service, label, description, created_at FROM credentials ORDER BY service"
            )
            return [dict(s) for s in services]

    @api.post("/api/credentials")
    async def add_credential(request: Request, body: CredentialAddRequest) -> dict[str, Any]:
        """Store a credential (encrypted)."""
        mycelos = api.state.mycelos
        try:
            mycelos.credentials.store_credential(
                body.service,
                {"api_key": body.secret},
                label=body.label,
                description=body.description,
            )
            mycelos.audit.log("credential.stored", details={"service": body.service, "label": body.label}, user_id=_resolve_user_id(request))
            return {"status": "stored", "service": body.service, "label": body.label}
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @api.delete("/api/credentials/{service}")
    async def delete_credential(request: Request, service: str, label: str = "default") -> dict[str, Any]:
        """Delete a credential."""
        mycelos = api.state.mycelos
        try:
            mycelos.credentials.delete_credential(service, label=label)
            mycelos.audit.log("credential.deleted", details={"service": service, "label": label}, user_id=_resolve_user_id(request))
            return {"status": "deleted", "service": service, "label": label}
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @api.post("/api/credentials/oauth-keys/validate")
    async def validate_oauth_keys(payload: dict[str, Any]) -> dict[str, Any]:
        """Cheap shape-check on uploaded gcp-oauth.keys.json content.

        Returns {ok: bool, kind?: str, error?: str}. Non-200 is reserved
        for framework errors; validation failures are ok=False with a
        human-readable message so the UI can keep showing the dialog.
        """
        import json as _json

        content = payload.get("content", "")
        if not content:
            return {"ok": False, "error": "Empty content — paste the gcp-oauth.keys.json file."}
        try:
            data = _json.loads(content)
        except _json.JSONDecodeError as e:
            return {"ok": False, "error": f"Not valid JSON: {e}"}
        if not isinstance(data, dict):
            return {"ok": False, "error": "Top-level must be a JSON object."}
        if "installed" in data and isinstance(data["installed"], dict):
            inst = data["installed"]
            if "client_id" in inst and "client_secret" in inst:
                return {"ok": True, "kind": "desktop"}
            return {"ok": False, "error": "Missing client_id or client_secret in 'installed' section."}
        if "web" in data:
            return {
                "ok": False,
                "error": (
                    "This looks like a Web-app OAuth credential. Mycelos needs a "
                    "Desktop-app credential. Go back to Cloud Console → Credentials "
                    "→ Create credentials → OAuth client ID → Desktop app."
                ),
            }
        return {
            "ok": False,
            "error": (
                "File doesn't look like a gcp-oauth.keys.json. Expected a top-level "
                "'installed' or 'web' key. Make sure you downloaded the OAuth-client JSON, "
                "not the project's service-account key."
            ),
        }

    # ── Telegram Setup ──────────────────────────────────────────

    def _scrub_token(text: str, token: str) -> str:
        """Remove any occurrence of the bot token from an error message.

        Telegram's API requires the token in the URL path, so if an
        exception includes the request URL (httpx does this for timeouts
        and connection errors), the raw token would leak into the
        response body. Strip it defensively.
        """
        if not token or not text:
            return text
        return text.replace(token, "<redacted>")

    @api.post("/api/telegram/check")
    async def telegram_check(request: Request) -> dict[str, Any]:
        """Check for Telegram bot messages to detect chat ID.

        Validates the token via getMe, then tries getUpdates to find
        the user's chat ID. Handles conflict with running long-polling.
        Routed through the SecurityProxy in two-container mode — the
        gateway never opens a direct socket to api.telegram.org.
        """
        from mycelos.channels.telegram import call_telegram_api_with_token
        mycelos = api.state.mycelos
        body = await request.json()
        token = (body.get("token") or "").strip()
        if not token or ":" not in token:
            return JSONResponse({"error": "Invalid bot token format"}, status_code=400)

        mycelos.audit.log("telegram.setup.check_started", user_id="default", details={})

        # Step 1: Validate token via getMe
        me = call_telegram_api_with_token(
            mycelos, token, "getMe", http_method="GET", timeout=10,
        )
        if not me.get("ok"):
            desc = me.get("description", "Invalid bot token")
            mycelos.audit.log(
                "telegram.setup.check_failed",
                user_id="default",
                details={"stage": "getMe"},
            )
            return {"error": _scrub_token(desc, token)}

        bot_name = me.get("result", {}).get("first_name", "Bot")
        bot_username = me.get("result", {}).get("username", "")

        # Step 2: Try getUpdates to find chat ID
        chat_id = None
        updates_data = call_telegram_api_with_token(
            mycelos, token, "getUpdates",
            payload={"limit": 100, "timeout": 1},
            http_method="GET", timeout=10,
        )

        if not updates_data.get("ok") and "Conflict" in (updates_data.get("description") or ""):
            # Long-polling is running — stop temporarily and retry
            tg_channel = getattr(api.state, "_telegram_channel", None)
            if tg_channel and hasattr(tg_channel, "stop"):
                try:
                    await tg_channel.stop()
                except Exception:
                    pass
            import asyncio
            await asyncio.sleep(1)
            updates_data = call_telegram_api_with_token(
                mycelos, token, "getUpdates",
                payload={"limit": 100, "timeout": 2},
                http_method="GET", timeout=10,
            )
            if tg_channel and hasattr(tg_channel, "start"):
                try:
                    await tg_channel.start()
                except Exception:
                    pass

        # Find any chat ID from updates
        results = updates_data.get("result", []) if updates_data.get("ok") else []
        for update in reversed(results):
            msg = update.get("message") or update.get("my_chat_member", {}).get("chat")
            if msg and isinstance(msg, dict):
                chat = msg.get("chat", msg)
                if isinstance(chat, dict) and chat.get("id"):
                    chat_id = str(chat["id"])
                    break

        mycelos.audit.log(
            "telegram.setup.check_succeeded",
            user_id="default",
            details={"bot_username": bot_username, "chat_id_found": chat_id is not None},
        )
        return {
            "valid": True,
            "bot_name": bot_name,
            "bot_username": bot_username,
            "chat_id": chat_id,
            "updates": len(results),
        }

    @api.post("/api/telegram/verify")
    async def telegram_verify(request: Request) -> dict[str, Any]:
        """Send a test message to verify the chat ID works.

        Routed through the SecurityProxy in two-container mode so the
        gateway never opens a direct socket to api.telegram.org.
        """
        from mycelos.channels.telegram import call_telegram_api_with_token
        mycelos = api.state.mycelos
        body = await request.json()
        token = (body.get("token") or "").strip()
        chat_id = (body.get("chat_id") or "").strip()
        if not token or not chat_id:
            return JSONResponse({"error": "token and chat_id required"}, status_code=400)

        data = call_telegram_api_with_token(
            mycelos, token, "sendMessage",
            payload={
                "chat_id": chat_id,
                "text": "Mycelos connected! This bot is ready to use.",
            },
            timeout=10,
        )

        if not data.get("ok"):
            desc = data.get("description", "Unknown error")
            if "chat not found" in desc.lower() or "CHAT_NOT_FOUND" in desc:
                return {"error": "Chat ID not found. Make sure you sent /start to the bot first."}
            mycelos.audit.log(
                "telegram.setup.verify_failed", user_id="default", details={},
            )
            return {"error": _scrub_token(desc, token)}

        mycelos.audit.log("telegram.setup.verify_succeeded", user_id="default", details={})
        return {"ok": True, "message_id": data.get("result", {}).get("message_id")}

    # ── Memory (key-value) ──────────────────────────────────────

    @api.post("/api/memory")
    async def set_memory(request: Request) -> dict[str, Any]:
        """Set a memory entry."""
        mycelos = api.state.mycelos
        body = await request.json()
        scope = body.get("scope", "system")
        key = body.get("key", "")
        value = body.get("value", "")
        if not key:
            return JSONResponse({"error": "key is required"}, status_code=400)
        user_id = _resolve_user_id(request)
        mycelos.memory.set(user_id, scope, key, value)
        mycelos.audit.log("memory.set", details={"scope": scope, "key": key}, user_id=user_id)
        return {"status": "stored", "scope": scope, "key": key}

    # ── End of API endpoints ───────────────────────────────────

