"""Connectors endpoints — recipes, OAuth flow, registration, tools, test."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from mycelos.gateway.routers._helpers import (
    ConnectorAddRequest,
    resolve_user_id,
)

logger = logging.getLogger("mycelos.gateway")

router = APIRouter()


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


@router.get("/api/connectors/lookup-env-vars")
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


@router.get("/api/connectors/recipes")
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


@router.get("/api/connectors/recipes/{recipe_id}")
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


@router.post("/api/connectors/oauth/start")
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

    mycelos = request.app.state.mycelos

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
    states = getattr(request.app.state, "oauth_pending_states", None)
    if states is None:
        states = {}
        request.app.state.oauth_pending_states = states
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


@router.get("/api/connectors/oauth/callback")
async def oauth_callback_passthrough(
    request: Request,
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

    mycelos = request.app.state.mycelos
    states = getattr(request.app.state, "oauth_pending_states", None) or {}
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


@router.get("/api/connectors")
async def list_connectors(request: Request) -> list[dict[str, Any]]:
    """List all connectors with MCP tool count."""
    mycelos = request.app.state.mycelos
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


@router.get("/api/connectors/{connector_id}")
async def get_connector(request: Request, connector_id: str) -> dict[str, Any]:
    """Look up a single connector by id. 404 if not registered.

    Used by the frontend's OAuth-dialog polling loop: the dialog
    hits this every ~2.5 seconds after showing the auth URL; a 200
    means the callback handler has registered the connector (i.e.
    the user completed consent) and the dialog flips to Stage 3.
    """
    mycelos = request.app.state.mycelos
    c = mycelos.connector_registry.get(connector_id)
    if c is None:
        raise HTTPException(status_code=404, detail=f"Unknown connector: {connector_id}")
    return dict(c)


@router.post("/api/connectors")
async def add_connector(request: Request, body: ConnectorAddRequest) -> dict[str, Any]:
    """Add a connector (same logic as /connector add slash command)."""
    mycelos = request.app.state.mycelos

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
                user_id=resolve_user_id(request),
            )
        except Exception as e:
            logger.exception("Credential storage failed for connector %s: %s", body.name, e)
            mycelos.audit.log(
                "credential.store_failed",
                details={"connector": body.name, "error": str(e)},
                user_id=resolve_user_id(request),
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
                user_id=resolve_user_id(request),
            )
        except Exception as e:
            logger.exception("Credential storage failed for connector %s: %s", body.name, e)
            mycelos.audit.log(
                "credential.store_failed",
                details={"connector": body.name, "error": str(e)},
                user_id=resolve_user_id(request),
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

    mycelos.audit.log("connector.added", details={"connector": body.name, "command": body.command}, user_id=resolve_user_id(request))

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


@router.delete("/api/connectors/{connector_id}")
async def remove_connector(request: Request, connector_id: str) -> dict[str, Any]:
    """Remove a connector."""
    mycelos = request.app.state.mycelos
    existing = mycelos.connector_registry.get(connector_id)
    if not existing:
        return JSONResponse({"error": f"Connector '{connector_id}' not found"}, status_code=404)

    mycelos.connector_registry.remove(connector_id)
    mycelos.audit.log("connector.removed", details={"connector": connector_id}, user_id=resolve_user_id(request))
    return {"status": "removed", "connector": connector_id}


@router.get("/api/connectors/{connector_id}/tools")
async def connector_tools(request: Request, connector_id: str) -> dict[str, Any]:
    """List the MCP tools exposed by one connector, with their
    descriptions and current policy status. Powers the Tool
    Transparency panel in the Connectors page.

    Returns { tools: [{name, description, policy, blocked_reason}], ... }.
    A tool is ``blocked`` when the PolicyEngine would return
    ``"never"`` for it — that's the canonical reason an agent can
    see but not use a tool.
    """
    mycelos = request.app.state.mycelos
    existing = mycelos.connector_registry.get(connector_id)
    if not existing:
        return JSONResponse({"error": f"Connector '{connector_id}' not found"}, status_code=404)

    user_id = resolve_user_id(request)
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


@router.post("/api/connectors/{connector_id}/tools/{tool_name}/call")
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
    mycelos = request.app.state.mycelos
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


@router.post("/api/connectors/{connector_id}/test")
async def test_connector(request: Request, connector_id: str) -> dict[str, Any]:
    """Run a live connectivity check on a connector.

    Uses the shape of the connector to pick the right probe:
      * telegram → ``getMe`` via the proxy
      * MCP-backed connectors → ``tools/list`` on the running session
      * everything else → a 'not testable' hint

    Every outcome flows through connector_registry.record_* so the
    panel and Doctor see fresh telemetry immediately.
    """
    mycelos = request.app.state.mycelos
    existing = mycelos.connector_registry.get(connector_id)
    if not existing:
        return JSONResponse({"error": f"Connector '{connector_id}' not found"}, status_code=404)

    ctype = (existing.get("connector_type") or "").lower()
    user_id = resolve_user_id(request)

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
            elif proxy_client is not None and recipe is None:
                # Custom MCP in two-container mode: rebuild command +
                # env_vars from the registry row (same shape as the
                # boot path in gateway/server.py) and spawn in proxy.
                existing_row = mycelos.connector_registry.get(connector_id) or {}
                desc = existing_row.get("description", "")
                if not desc.startswith("MCP: "):
                    raise RuntimeError(
                        f"Custom MCP connector '{connector_id}' has no command "
                        "in registry description"
                    )
                command_str = desc[len("MCP: "):]
                env_vars = {}
                try:
                    cred = mycelos.credentials.get_credential(connector_id)
                    if cred and cred.get("api_key"):
                        env_var_name = cred.get(
                            "env_var",
                            f"{connector_id.upper().replace('-', '_')}_API_KEY",
                        )
                        env_vars[env_var_name] = f"credential:{connector_id}"
                except Exception:
                    pass
                argv = _shlex.split(command_str)
                resp = proxy_client.mcp_start(
                    connector_id=connector_id,
                    command=argv,
                    env_vars=env_vars,
                    transport="stdio",
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
                # let the local manager handle it. reconnect() falls
                # back to registry lookup for custom (recipe-less) MCPs.
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
