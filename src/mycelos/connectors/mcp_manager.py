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

    def __init__(self, credential_proxy: Any = None) -> None:
        self._clients: dict[str, MycelosMCPClient] = {}
        self._credential_proxy = credential_proxy
        self._all_tools: dict[str, dict] = {}
        # Dedicated event loop for MCP operations
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._start_loop()

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

        env_vars = {}
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

        tools = self._run_async(_connect())

        self._clients[connector_id] = client

        for tool in tools:
            self._all_tools[tool["name"]] = {
                "client": client,
                "original_name": tool["original_name"],
                "description": tool["description"],
                "input_schema": tool.get("input_schema", {}),
            }

        logger.info("MCP '%s' connected: %d tools", connector_id, len(tools))
        return tools

    def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Call an MCP tool by name. Thread-safe."""
        tool = self._all_tools.get(tool_name)
        if tool is None:
            return {"error": f"MCP tool '{tool_name}' not found"}

        client = tool["client"]

        async def _call():
            return await client.call_tool(tool_name, arguments)

        try:
            return self._run_async(_call())
        except Exception as e:
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
        """List connected connector IDs."""
        return list(self._clients.keys())

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
