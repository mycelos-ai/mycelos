"""Mycelos Gateway — FastAPI HTTP server for multi-channel chat."""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI

from mycelos.app import App
from mycelos.chat.service import ChatService
from mycelos.gateway.routes import setup_routes

logger = logging.getLogger("mycelos.gateway")


def _register_telegram_webhook(
    bot_token: str,
    webhook_url: str,
    webhook_secret: str | None = None,
    debug: bool = False,
) -> bool:
    """Register webhook URL with Telegram Bot API.

    Routes through the SecurityProxy when present so the gateway never
    opens a direct socket to api.telegram.org. Falls back to urllib in
    single-container mode.

    Returns True if successful, False otherwise.
    """
    import json as _json

    payload: dict[str, str] = {"url": webhook_url}
    if webhook_secret:
        payload["secret_token"] = webhook_secret

    url = f"https://api.telegram.org/bot{{credential}}/setWebhook"
    try:
        from mycelos.connectors import http_tools as _http_tools
        pc = getattr(_http_tools, "_proxy_client", None)
        if pc is not None:
            resp = pc.http_post(
                url,
                body=payload,
                inline_credential=bot_token,
                inject_as="url_path",
                timeout=10,
            )
            body = resp.get("body", "")
            data = _json.loads(body) if body else {}
        else:
            import urllib.request
            resolved = url.replace("{credential}", bot_token)
            req = urllib.request.Request(
                resolved,
                data=_json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = _json.loads(r.read())

        if data.get("ok"):
            logger.info("Telegram webhook registered: %s", webhook_url)
            return True
        logger.error("Telegram setWebhook failed: %s", data.get("description", "unknown"))
        return False
    except Exception as e:
        logger.error("Telegram webhook registration failed: %s", e)
        return False


def _start_mcp_connectors(mycelos: App, debug: bool = False) -> None:
    """Start MCP servers for active connectors that have recipes.

    Checks which connectors are active, finds their MCP recipes,
    and starts the server subprocesses. Tools are auto-discovered
    and made available to the ChatService via the MCPConnectorManager.
    """
    import shutil

    from mycelos.connectors.mcp_recipes import get_recipe

    # Check Node.js availability
    if not shutil.which("npx"):
        logger.warning(
            "Node.js (npx) not found — MCP connectors disabled. "
            "Install Node.js: https://nodejs.org/"
        )
        return

    try:
        connectors = mycelos.connector_registry.list_connectors(status="active")
    except Exception:
        return

    # In two-container deployment, MCP subprocess management belongs to
    # the proxy container — only the proxy can decrypt the credentials
    # the subprocess needs. When MYCELOS_PROXY_URL is set, route every
    # mcp_start through proxy_client.mcp_start instead of spawning the
    # subprocess locally in the gateway (which would hit
    # NotImplementedError when the MCP client tries to load the
    # credential from the gateway-side DelegatingCredentialProxy).
    from mycelos.connectors import http_tools as _http_tools
    proxy_client = getattr(_http_tools, "_proxy_client", None)
    use_proxy = proxy_client is not None

    mcp_mgr = None
    if not use_proxy:
        mcp_mgr = mycelos.mcp_manager
    started = 0

    for connector in connectors:
        cid = connector["id"]
        ctype = connector.get("connector_type", "")

        # Skip non-MCP connectors (channels, built-in search, etc.)
        if ctype in ("channel", "search", "http", "builtin"):
            continue

        # Check if we have a recipe for this connector
        recipe = get_recipe(cid)

        if recipe:
            # Recipe-based connector
            if recipe.transport == "stdio" and not recipe.command:
                continue
            command = recipe.command
            env_vars = {}
            for cred_spec in recipe.credentials:
                env_var = cred_spec["env_var"]
                env_vars[env_var] = f"credential:{cid}"
            transport = recipe.transport
        elif ctype == "mcp":
            # Custom MCP server — command stored in description as "MCP: <command>"
            desc = connector.get("description", "")
            if desc.startswith("MCP: "):
                command = desc[5:]  # Strip "MCP: " prefix
            else:
                logger.warning("Custom MCP connector '%s' has no command in description", cid)
                continue
            # Load stored credentials — bare connector id is the canonical
            # key; fall back to the legacy 'connector:<id>' one for
            # pre-migration rows so users don't lose config on upgrade.
            env_vars = {}
            try:
                cred = (
                    mycelos.credentials.get_credential(cid)
                    or mycelos.credentials.get_credential(f"connector:{cid}")
                )
                if cred and cred.get("api_key"):
                    env_var_name = cred.get("env_var", f"{cid.upper().replace('-', '_')}_API_KEY")
                    env_vars[env_var_name] = f"credential:{cid}"
            except Exception:
                pass
            transport = "stdio"
        else:
            continue

        try:
            if use_proxy:
                # Shell-split the command. The proxy expects a list of
                # argv strings, same shape the MCP manager uses.
                import shlex
                argv = shlex.split(command) if isinstance(command, str) else list(command)
                resp = proxy_client.mcp_start(
                    connector_id=cid,
                    command=argv,
                    env_vars=env_vars,
                    transport=transport,
                )
                if resp.get("error"):
                    raise RuntimeError(resp["error"])
                tools = resp.get("tools", [])
                # Cache the tool list locally so gateway code that
                # inspects list_tools() still sees what the proxy runs.
                # The actual MCP calls go through proxy_client.mcp_call()
                # at tool-call time — the local manager only serves as a
                # read-only catalog in two-container mode.
                mycelos.mcp_manager.register_remote_session(
                    connector_id=cid,
                    session_id=resp.get("session_id", ""),
                    tools=tools,
                )
            else:
                tools = mcp_mgr.connect(
                    connector_id=cid,
                    command=command,
                    env_vars=env_vars,
                    transport=transport,
                )
            started += 1
            logger.info(
                "MCP server '%s' started: %d tools discovered",
                cid, len(tools),
            )
            if debug:
                for t in tools:
                    logger.debug("  Tool: %s — %s", t["name"], t.get("description", "")[:60])
        except Exception as e:
            logger.warning("Failed to start MCP server '%s': %s", cid, e)

    if started:
        logger.info("MCP: %d connector server(s) started", started)
    elif debug:
        logger.debug("MCP: no connectors to start")


def _start_telegram_channel(mycelos: App, api: FastAPI, debug: bool = False) -> None:
    """Start Telegram channel based on NixOS-style config.

    Reads channel config from the channels table. Mode is either
    'polling' (default, no webhook needed) or 'webhook'.
    Allowlist is enforced from the channels table.
    """
    from mycelos.channels.telegram import load_channel_config, setup_telegram, start_polling

    api.state.telegram_bot = None
    api.state.telegram_mode = None

    # Load channel config from DB
    channel_cfg = load_channel_config(mycelos.storage)
    if not channel_cfg:
        if debug:
            logger.debug("Telegram: no channel config found")
        return

    # Load bot token. In the two-container deployment the gateway cannot
    # decrypt credentials itself — we ask the SecurityProxy to materialize
    # the Telegram token once at startup (bootstrap-window gated,
    # allow-listed, audited). aiogram's authenticated long-poll session
    # needs the raw token; see docs/security/two-container-deployment.md
    # for the rationale. In single-container mode (no proxy), fall back
    # to the local credential store.
    bot_token: str | None = None
    try:
        from mycelos.connectors import http_tools as _http_tools
        pc = getattr(_http_tools, "_proxy_client", None)
        if pc is not None:
            materialized = pc.credential_materialize("telegram")
            if materialized.get("api_key"):
                bot_token = materialized["api_key"]
            else:
                logger.warning(
                    "Telegram credential materialize failed: %s",
                    materialized.get("error", "unknown"),
                )
                return
        else:
            telegram_cred = mycelos.credentials.get_credential("telegram")
            if not telegram_cred or not telegram_cred.get("api_key"):
                logger.warning("Telegram channel configured but no bot token in credentials")
                return
            bot_token = telegram_cred["api_key"]
    except Exception as e:
        logger.warning("Telegram credential lookup failed: %s", e)
        return
    mode = channel_cfg.get("mode", "polling")
    allowed_users = channel_cfg.get("allowed_users", [])
    config = channel_cfg.get("config", {})
    webhook_secret = config.get("webhook_secret")
    webhook_url = config.get("webhook_url")

    # Initialize bot with allowlist
    bot = setup_telegram(
        bot_token,
        api.state.chat_service,
        webhook_secret=webhook_secret,
        allowed_users=allowed_users,
        app=mycelos,
    )
    api.state.telegram_bot = bot
    api.state.telegram_mode = mode

    if mode == "polling":
        # Start long polling in daemon thread — no webhook needed
        start_polling()
        logger.info("Telegram started in polling mode (allowed: %s)",
                     allowed_users if allowed_users else "all")

    elif mode == "webhook":
        # Register webhook with Telegram API
        if webhook_url:
            full_url = f"{webhook_url.rstrip('/')}/telegram/webhook"
            _register_telegram_webhook(
                bot_token=bot_token,
                webhook_url=full_url,
                webhook_secret=webhook_secret,
                debug=debug,
            )
            logger.info("Telegram started in webhook mode: %s", full_url)
        else:
            logger.warning(
                "Telegram webhook mode but no webhook_url in config. "
                "Set it via: /connector setup telegram"
            )
    else:
        logger.error("Unknown Telegram mode: %s", mode)

    if debug:
        logger.debug(
            "Telegram channel: mode=%s, allowed_users=%s",
            mode, allowed_users,
        )


def create_app(
    data_dir: Path | None = None,
    debug: bool = False,
    no_scheduler: bool = False,
    host: str = "127.0.0.1",
    password: str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI gateway application.

    Args:
        data_dir: Mycelos data directory. Defaults to ~/.mycelos.
        debug: Enable debug logging (intents, models, tokens, events).
        no_scheduler: Disable the background Huey scheduler.
        host: Bind address. Used to decide localhost-only restriction.
        password: If set, require Basic Auth with user "mycelos" and this password.
    """
    if data_dir is None:
        data_dir = Path.home() / ".mycelos"

    api = FastAPI(
        title="Mycelos Gateway",
        version="0.1.0",
        description="Security-first agent operating system — HTTP API",
    )

    # Store bind host + password state for security status endpoint and middleware
    api.state.bind_host = host
    api.state.password_protected = bool(password)

    # Localhost restriction — gate all /api/* routes when bound to localhost
    from mycelos.gateway.routes import LocalhostMiddleware
    api.add_middleware(LocalhostMiddleware)

    # CORS — allow Next.js dev server (port 3000) in development
    from fastapi.middleware.cors import CORSMiddleware
    api.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Basic Auth middleware (for network-accessible deployments)
    if password:
        import base64
        import secrets

        class BasicAuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                # Health endpoint is always public (for Docker health checks)
                if request.url.path == "/api/health":
                    return await call_next(request)
                auth = request.headers.get("Authorization", "")
                if auth.startswith("Basic "):
                    try:
                        decoded = base64.b64decode(auth[6:]).decode("utf-8")
                        user, pw = decoded.split(":", 1)
                        if secrets.compare_digest(pw, password):
                            return await call_next(request)
                    except Exception:
                        pass
                return JSONResponse(
                    {"error": "Authentication required"},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="Mycelos"'},
                )

        api.add_middleware(BasicAuthMiddleware)
        logger.info("Basic Auth enabled (user: mycelos)")

    # Startup check: warn about unauthenticated public API
    if host == "0.0.0.0" and not password:
        logger.warning(
            "Gateway binding to 0.0.0.0 — API endpoints are accessible from "
            "any network interface WITHOUT authentication. Consider adding "
            "authentication before exposing to untrusted networks."
        )

    # Configure logging
    log_file = data_dir / "gateway.log"
    fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    datefmt = "%H:%M:%S"

    if debug:
        # Console: show mycelos.* at DEBUG, suppress noisy libs
        logging.basicConfig(level=logging.DEBUG, format=fmt, datefmt=datefmt)
        # File: everything goes to log
        file_handler = logging.FileHandler(log_file, mode="a")
        file_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
        file_handler.setLevel(logging.DEBUG)
        logging.getLogger().addHandler(file_handler)

        logger.setLevel(logging.DEBUG)
        logger.debug("Debug mode enabled — full log: %s", log_file)
    else:
        logging.basicConfig(level=logging.INFO, format=fmt, datefmt=datefmt)
        # File handler for INFO+ even without --debug
        file_handler = logging.FileHandler(log_file, mode="a")
        file_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
        file_handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(file_handler)

    # Suppress noisy loggers on console (they still go to file)
    for noisy in ("huey", "huey.consumer", "huey.consumer.Scheduler",
                   "httpcore", "httpx", "LiteLLM", "litellm"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Initialize Mycelos app
    mycelos = App(data_dir)

    # Seed built-in workflows on every startup (idempotent — INSERT only if missing)
    try:
        from mycelos.workflows.templates import seed_builtin_workflows
        seeded = seed_builtin_workflows(mycelos)
        if seeded:
            logging.getLogger("mycelos.gateway").info(f"Seeded {seeded} built-in workflow(s)")
    except Exception:
        logging.getLogger("mycelos.gateway").exception("Failed to seed built-in workflows")

    # Set language from env or user preference
    from mycelos.i18n import set_language
    lang = os.environ.get("MYCELOS_LANG")
    if not lang:
        try:
            lang = mycelos.memory.get("default", "system", "user.language")
        except Exception:
            pass
    set_language(lang or "en")

    # Load master key
    if not os.environ.get("MYCELOS_MASTER_KEY"):
        key_file = data_dir / ".master_key"
        if key_file.exists():
            os.environ["MYCELOS_MASTER_KEY"] = key_file.read_text().strip()

    # SecurityProxy wiring: three modes.
    #  1. MYCELOS_PROXY_URL set → external proxy container (Phase-1 two-container deployment).
    #     No fork, no master key read in this process.
    #  2. MYCELOS_MASTER_KEY set → fork a local SecurityProxy child (existing single-container path).
    #  3. Neither → run without proxy (legacy fallback; credential access degrades).
    from mycelos.security.proxy_launcher import ProxyLauncher
    from mycelos.security.proxy_client import SecurityProxyClient

    proxy_url = os.environ.get("MYCELOS_PROXY_URL", "").strip()
    if proxy_url:
        proxy_token = os.environ.get("MYCELOS_PROXY_TOKEN", "").strip()
        if not proxy_token:
            raise RuntimeError(
                "MYCELOS_PROXY_URL set but MYCELOS_PROXY_TOKEN is missing — "
                "the gateway cannot authenticate to the external proxy."
            )
        proxy_client = SecurityProxyClient(url=proxy_url, token=proxy_token)
        mycelos.set_proxy_client(proxy_client)
        api.state.proxy_launcher = None
        logger.info("SecurityProxy: external at %s (no local fork)", proxy_url)
    else:
        # Eagerly initialize the credential proxy before starting the SecurityProxy.
        # The ProxyLauncher.start() clears MYCELOS_MASTER_KEY from the parent env after
        # forking the child, so we must instantiate EncryptedCredentialProxy first to
        # cache it in App._credentials before the env var disappears.
        master_key = os.environ.get("MYCELOS_MASTER_KEY", "")
        if master_key:
            try:
                _ = mycelos.credentials  # Eagerly init before proxy clears the env var
                proxy_launcher = ProxyLauncher(data_dir, master_key)
                proxy_launcher.start()
                proxy_client = SecurityProxyClient(socket_path=proxy_launcher.socket_path, token=proxy_launcher.session_token)
                mycelos.set_proxy_client(proxy_client)
                api.state.proxy_launcher = proxy_launcher
                logger.info("SecurityProxy started (pid=%s)", proxy_launcher._process.pid if proxy_launcher._process else "?")
            except Exception as e:
                logger.warning("Failed to start SecurityProxy: %s (running without proxy)", e)
                api.state.proxy_launcher = None
        else:
            logger.warning("No MYCELOS_MASTER_KEY — running without SecurityProxy")
            api.state.proxy_launcher = None

    # Wire http_tools to use proxy when available
    from mycelos.connectors.http_tools import set_proxy_client as set_http_proxy
    if mycelos.proxy_client:
        set_http_proxy(mycelos.proxy_client)

    # Credentials are scoped per-LLM-call via credential proxy — no global env loading
    if debug:
        try:
            services = mycelos.credentials.list_services()
            logger.debug("Credential proxy: %d service(s) available", len(services))
        except Exception:
            logger.debug("Credential proxy: not available (no master key?)")

    # Store on app state for routes
    api.state.mycelos = mycelos
    api.state.chat_service = ChatService(mycelos)
    api.state.start_time = time.time()
    api.state.debug = debug

    # Cache default user for _resolve_user_id and /api/health
    row = mycelos.storage.fetchone(
        "SELECT id, name, email, language, timezone FROM users WHERE status = 'active' ORDER BY created_at LIMIT 1"
    )
    api.state.default_user_id = row["id"] if row else "default"
    api.state.default_user = dict(row) if row else {"id": "default", "name": "Default User"}

    # Startup health check: verify LLM connectivity
    _proxy_ok = mycelos.llm._proxy_client is not None
    logger.info("LLM broker: proxy_client=%s, default_model=%s", _proxy_ok, mycelos.llm.default_model)
    if _proxy_ok:
        try:
            test_result = mycelos.llm.complete(
                [{"role": "user", "content": "ping"}],
                model=mycelos.llm.default_model,
            )
            logger.info("LLM health check: OK (model=%s, tokens=%d)", test_result.model, test_result.total_tokens)
        except Exception as e:
            logger.error("LLM health check: FAILED — %s", e)
    else:
        logger.warning("LLM health check: SKIPPED — no SecurityProxy (credentials may not be available)")

    api.state.no_scheduler = no_scheduler

    setup_routes(api)

    # Start Huey scheduler as daemon thread
    if not getattr(api.state, "no_scheduler", False):
        try:
            from mycelos.scheduler.huey_app import create_huey, start_consumer_thread
            from mycelos.scheduler.jobs import register_periodic_jobs

            huey = create_huey(data_dir)
            register_periodic_jobs(huey, mycelos)
            consumer_thread = start_consumer_thread(huey, workers=1, periodic=True)
            api.state.huey = huey

            # Register background pipeline task
            from mycelos.tasks.pipeline_task import register_pipeline_tasks
            api.state.pipeline_task = register_pipeline_tasks(huey, mycelos)
            api.state.chat_service._pipeline_task = api.state.pipeline_task

            api.state.scheduler_running = True

            if debug:
                logger.debug("Scheduler started (Huey consumer thread)")
        except Exception as e:
            logger.warning("Failed to start scheduler: %s", e)
            api.state.scheduler_running = False
    else:
        api.state.scheduler_running = False
        if debug:
            logger.debug("Scheduler disabled (--no-scheduler)")

    # Start MCP connector servers for active connectors
    _start_mcp_connectors(mycelos, debug=debug)

    # Start Telegram channel from NixOS-style config
    _start_telegram_channel(mycelos, api, debug=debug)

    # Mount web frontend if available
    frontend_dir = Path(__file__).parent.parent / "frontend"
    if frontend_dir.is_dir():
        from mycelos.gateway.spa import SPAStaticFiles
        api.mount("/", SPAStaticFiles(directory=str(frontend_dir), html=True), name="spa")
        logger.info("Web frontend mounted from %s", frontend_dir)
    else:
        if debug:
            logger.debug("No web frontend found at %s", frontend_dir)

    mycelos.audit.log("gateway.started", details={"debug": debug})

    # Restart watchdog — polls for restart.txt, triggers graceful restart
    _start_restart_watchdog(data_dir, api_state=api.state)

    return api


def _start_restart_watchdog(data_dir: Path, interval: float = 5.0, api_state: Any = None) -> None:
    """Start a daemon thread that watches for restart.txt.

    When ~/.mycelos/tmp/restart.txt appears, the entire Gateway process
    replaces itself via os.execv(). This enables remote restart from
    Telegram (/restart) or automated deploys.
    """
    import threading

    restart_dir = data_dir / "tmp"
    restart_dir.mkdir(parents=True, exist_ok=True)
    restart_file = restart_dir / "restart.txt"

    # Clean up on start
    if restart_file.exists():
        restart_file.unlink()
        logger.debug("Cleared stale restart.txt")

    def _watch():
        while True:
            time.sleep(interval)
            if restart_file.exists():
                logger.info("restart.txt detected — restarting Gateway...")
                try:
                    restart_file.unlink()
                except Exception:
                    pass

                # Graceful cleanup before restart
                try:
                    # Restore MYCELOS_MASTER_KEY before restart — ProxyLauncher
                    # deletes it from env after forking the child process
                    proxy_launcher = getattr(api_state, 'proxy_launcher', None) if api_state else None
                    if proxy_launcher:
                        os.environ["MYCELOS_MASTER_KEY"] = proxy_launcher._read_master_key()

                    # Stop SecurityProxy if running
                    if proxy_launcher:
                        try:
                            proxy_launcher.stop()
                        except Exception:
                            pass

                    # Give threads a moment to clean up
                    time.sleep(1)
                except Exception as e:
                    logger.warning("Cleanup before restart failed: %s", e)

                # Replace the current process with a fresh one
                os.execv(sys.executable, [sys.executable] + sys.argv)

    thread = threading.Thread(target=_watch, daemon=True, name="restart-watchdog")
    thread.start()
    logger.debug("Restart watchdog started (polling %s every %.0fs)", restart_file, interval)
