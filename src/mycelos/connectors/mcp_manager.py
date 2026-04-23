"""MCP Connector Manager — manages running MCP servers and bridges tools.

Starts MCP servers as subprocesses, discovers their tools, and makes
them available to Mycelos agents via the ChatService tool system.

Uses a dedicated event loop thread so MCP sessions stay alive.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from mycelos.connectors.mcp_client import MycelosMCPClient
from mycelos.connectors.mcp_recipes import RECIPES, get_recipe

logger = logging.getLogger("mycelos.mcp")


class MCPConnectorManager:
    """Manages running MCP server connections.

    All async MCP operations run in a dedicated event loop thread.
    This ensures MCP sessions (asyncio streams) stay alive between
    connect() and call_tool().
    """

    def __init__(
        self,
        credential_proxy: Any = None,
        connector_registry: Any = None,
    ) -> None:
        self._clients: dict[str, MycelosMCPClient] = {}
        self._credential_proxy = credential_proxy
        # Optional — if provided, connect/disconnect/call_tool record
        # success/failure telemetry so the UI + Doctor reflect reality.
        # Injected lazily from app.py so tests can still construct a
        # bare manager without a DB.
        self._connector_registry = connector_registry
        self._all_tools: dict[str, dict] = {}
        # Dedicated event loop for MCP operations
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._start_loop()

    def set_connector_registry(self, registry: Any) -> None:
        """Inject the connector registry after construction.

        App.py constructs the mcp_manager before the registry in some
        code paths; this lets the composition root wire them up
        without a circular import.
        """
        self._connector_registry = registry

    def _record_success(self, connector_id: str) -> None:
        if self._connector_registry is not None:
            try:
                self._connector_registry.record_success(connector_id)
            except Exception:
                logger.debug("record_success failed for %s", connector_id, exc_info=True)

    def _record_failure(self, connector_id: str, error: str) -> None:
        if self._connector_registry is not None:
            try:
                self._connector_registry.record_failure(connector_id, error)
            except Exception:
                logger.debug("record_failure failed for %s", connector_id, exc_info=True)

    def _start_loop(self) -> None:
        """Start a dedicated event loop in a daemon thread."""
        self._loop = asyncio.new_event_loop()

        def _run():
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()

        self._thread = threading.Thread(target=_run, daemon=True, name="mcp-event-loop")
        self._thread.start()

    def _run_async(self, coro) -> Any:
        """Run a coroutine in the dedicated MCP event loop. Thread-safe."""
        if self._loop is None or self._loop.is_closed():
            raise RuntimeError("MCP event loop not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=30)

    def connect_recipe(self, recipe_id: str) -> list[dict]:
        """Connect to an MCP server from a predefined recipe."""
        recipe = get_recipe(recipe_id)
        if recipe is None:
            raise ValueError(f"Unknown recipe: {recipe_id}")

        env_vars = dict(recipe.static_env)  # non-secret env baked into the recipe
        for cred in recipe.credentials:
            env_vars[cred["env_var"]] = f"credential:{recipe_id}"

        return self.connect(
            connector_id=recipe_id,
            command=recipe.command,
            env_vars=env_vars,
        )

    def connect(
        self,
        connector_id: str,
        command: str,
        env_vars: dict[str, str] | None = None,
        transport: str = "stdio",
    ) -> list[dict]:
        """Connect to an MCP server and discover its tools."""
        # Purge any stale state for this id (we might be reconnecting
        # after a dead session) so tool-lookup doesn't resolve to a
        # zombie client.
        old_tools = [n for n, t in self._all_tools.items() if t.get("client") and getattr(t["client"], "connector_id", "") == connector_id]
        for n in old_tools:
            self._all_tools.pop(n, None)

        client = MycelosMCPClient(
            connector_id=connector_id,
            command=command,
            env_vars=env_vars,
            credential_proxy=self._credential_proxy,
            transport=transport,
        )

        async def _connect():
            await client.connect()
            return await client.discover_tools()

        try:
            tools = self._run_async(_connect())
        except Exception as e:
            self._record_failure(connector_id, f"connect failed: {e}")
            raise

        self._clients[connector_id] = client

        for tool in tools:
            self._all_tools[tool["name"]] = {
                "client": client,
                "original_name": tool["original_name"],
                "description": tool["description"],
                "input_schema": tool.get("input_schema", {}),
            }

        self._record_success(connector_id)
        logger.info("MCP '%s' connected: %d tools", connector_id, len(tools))
        return tools

    def reconnect(self, connector_id: str) -> list[dict]:
        """Tear down the existing client for this connector (if any) and
        re-spawn it using the recipe's command + credentials. Returns
        the freshly-discovered tool list.

        Raises if the connector has no recipe (custom MCP connectors
        store their command in the registry row rather than a recipe
        — those use reconnect_from_registry() below).
        """
        # Drop stale client first so tool-lookup doesn't hit zombies.
        old = self._clients.pop(connector_id, None)
        if old is not None:
            try:
                self._run_async(old.disconnect())
            except Exception:
                pass
        # Strip orphan tool entries from _all_tools.
        for name in [n for n, t in list(self._all_tools.items())
                     if t.get("client") is old]:
            self._all_tools.pop(name, None)

        return self.connect_recipe(connector_id)

    def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Call an MCP tool by name. Thread-safe.

        Two paths:
          - Local subprocess: go through the in-process client
          - Remote session (Phase 1b, subprocess lives in the proxy):
            call proxy_client.mcp_call with the stored session id

        Self-healing: if the tool isn't known (stale/dead session) or
        the underlying client raises, we attempt one reconnect using
        the recipe and retry the call. Transparent to the caller as
        long as the recipe + credentials are still valid.
        """
        tool = self._all_tools.get(tool_name)

        # Try a lazy reconnect when the tool is missing but we can
        # guess which connector it belongs to from the prefix.
        if tool is None:
            connector_id = tool_name.split(".", 1)[0] if "." in tool_name else ""
            if connector_id and connector_id not in self._clients:
                try:
                    logger.info("MCP tool '%s' not known; attempting reconnect of '%s'",
                                tool_name, connector_id)
                    self.reconnect(connector_id)
                    tool = self._all_tools.get(tool_name)
                except Exception as e:
                    logger.warning("Auto-reconnect of '%s' failed: %s", connector_id, e)

        if tool is None:
            return {"error": f"MCP tool '{tool_name}' not found"}

        if tool.get("_remote"):
            # Strip the connector prefix — the proxy expects the bare
            # tool name (what the MCP server itself knows).
            session_id = tool.get("_session_id") or ""
            bare_name = tool_name.split(".", 1)[-1] if "." in tool_name else tool_name
            from mycelos.connectors import http_tools as _http_tools
            proxy_client = getattr(_http_tools, "_proxy_client", None)
            if proxy_client is None:
                return {"error": "Remote MCP tool requires proxy_client (not configured)"}
            try:
                result = proxy_client.mcp_call(
                    session_id=session_id,
                    tool=bare_name,
                    arguments=arguments,
                )
                connector_id = getattr(tool.get("client"), "connector_id", "")
                if connector_id:
                    self._record_success(connector_id)
                return result
            except Exception as e:
                connector_id = getattr(tool.get("client"), "connector_id", "")
                if connector_id:
                    self._record_failure(connector_id, f"remote call failed: {e}")
                return {"error": f"Remote MCP tool call failed: {e}"}

        client = tool["client"]
        connector_id = getattr(client, "connector_id", "")

        async def _call():
            return await client.call_tool(tool_name, arguments)

        try:
            result = self._run_async(_call())
            if connector_id:
                self._record_success(connector_id)
            return result
        except Exception as e:
            # First error: maybe the session died. Try one reconnect +
            # retry before giving up.
            if connector_id:
                try:
                    logger.info("MCP tool call '%s' raised; attempting reconnect of '%s'",
                                tool_name, connector_id)
                    self.reconnect(connector_id)
                    retry_tool = self._all_tools.get(tool_name)
                    if retry_tool is not None:
                        retry_client = retry_tool["client"]

                        async def _retry():
                            return await retry_client.call_tool(tool_name, arguments)

                        result = self._run_async(_retry())
                        self._record_success(connector_id)
                        return result
                except Exception as retry_err:
                    self._record_failure(connector_id, f"call+reconnect failed: {retry_err}")
                    return {"error": f"MCP tool call failed: {retry_err}"}

                self._record_failure(connector_id, f"call failed: {e}")
            return {"error": f"MCP tool call failed: {e}"}

    def disconnect_all(self) -> None:
        """Disconnect all MCP servers."""
        for cid, client in list(self._clients.items()):
            try:
                self._run_async(client.disconnect())
            except Exception:
                pass
        self._clients.clear()
        self._all_tools.clear()

    def list_connected(self) -> list[str]:
        """List connected connector IDs — local clients and remote
        sessions registered via register_remote_session combined."""
        local = set(self._clients.keys())
        remote = set(getattr(self, "_remote_sessions", {}).keys())
        return sorted(local | remote)

    def register_remote_session(
        self, connector_id: str, session_id: str, tools: list[dict]
    ) -> None:
        """Register an MCP session that actually runs in the proxy
        container. The gateway doesn't own the subprocess, but the
        tools catalog still needs to be visible locally so Agents
        (and list_tools()) know what's available. Tool calls route
        through proxy_client.mcp_call at execution time.
        """
        if not hasattr(self, "_remote_sessions"):
            self._remote_sessions: dict[str, str] = {}
        self._remote_sessions[connector_id] = session_id
        for tool in tools:
            name = tool.get("name") if isinstance(tool, dict) else None
            if not name:
                continue
            # Prefix with connector id (matches the local-client shape)
            full_name = name if name.startswith(f"{connector_id}.") else f"{connector_id}.{name}"
            self._all_tools[full_name] = {
                "description": tool.get("description", "") if isinstance(tool, dict) else "",
                "input_schema": tool.get("input_schema", {}) if isinstance(tool, dict) else {},
                "_remote": True,
                "_session_id": session_id,
            }

    def list_tools(self) -> list[dict]:
        """List all available MCP tools with schemas."""
        return [
            {
                "name": name,
                "description": info["description"],
                "input_schema": info.get("input_schema", {}),
            }
            for name, info in self._all_tools.items()
        ]

    @property
    def tool_count(self) -> int:
        return len(self._all_tools)
