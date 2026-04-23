"""MCP Client — connects to MCP servers with Mycelos's security layer.

Wraps the official MCP Python SDK. Each MCP server runs as an isolated
subprocess with only the credentials it needs (via env vars from
CredentialProxy). Tool discovery is automatic.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
from typing import Any

logger = logging.getLogger("mycelos.mcp")

# Environment variables that could be used to hijack the MCP subprocess.
_BLOCKED_ENV_VARS = frozenset({
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "PYTHONPATH",
    "NODE_OPTIONS",
})


class MycelosMCPClient:
    """Connects to an MCP server and provides tool access.

    Supports two transports:
    - stdio: local subprocess (default)
    - http: remote HTTP endpoint (e.g., GitHub's hosted MCP server)
    """

    # HTTP endpoints for hosted MCP servers
    _HTTP_ENDPOINTS: dict[str, str] = {
        "github": "https://api.githubcopilot.com/mcp/",
    }

    def __init__(
        self,
        connector_id: str,
        command: str | list[str],
        env_vars: dict[str, str] | None = None,
        credential_proxy: Any | None = None,
        transport: str = "stdio",
    ) -> None:
        self.connector_id = connector_id
        self._command = command
        self._env_vars = env_vars or {}
        self._credential_proxy = credential_proxy
        self._transport = transport
        self._session: Any = None
        self._tools: list[dict[str, Any]] = []
        self._context_stack: list[Any] = []  # for cleanup

    async def connect(self) -> None:
        """Connect to the MCP server.

        In two-container deployment (``MYCELOS_PROXY_URL`` set) this
        method refuses to run: the subprocess must live in the proxy
        container, spawned via ``proxy_client.mcp_start``. A direct
        stdio_client / streamablehttp_client would spawn the MCP
        subprocess inside the gateway container — which has no
        internet route AND no credential plaintext — recreating
        the exact hole that bd2fe42 closed for the startup path.
        Fail early and loudly instead of failing late and silently.
        """
        import os
        if os.environ.get("MYCELOS_PROXY_URL"):
            raise RuntimeError(
                "MycelosMCPClient.connect() is not allowed in two-container "
                "mode (MYCELOS_PROXY_URL is set). Route MCP subprocess "
                "management through SecurityProxyClient.mcp_start so the "
                "subprocess and its credentials stay inside the proxy "
                "container."
            )
        if self._transport == "http":
            await self._connect_http()
        else:
            await self._connect_stdio()

    async def _connect_stdio(self) -> None:
        """Connect via stdio (local subprocess)."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = self._build_env()
        # The proxy passes argv as a pre-split list (via proxy_client.mcp_start);
        # the local path passes a shell-string. Accept both so the same
        # code works in single-process and two-container deployments.
        if isinstance(self._command, (list, tuple)):
            parts = list(self._command)
        else:
            parts = shlex.split(self._command)
        if not parts:
            raise RuntimeError(f"Empty MCP command for '{self.connector_id}'")

        server_params = StdioServerParameters(
            command=parts[0],
            args=parts[1:] if len(parts) > 1 else [],
            env=env,
        )

        self._stdio_context = stdio_client(server_params)
        read, write = await self._stdio_context.__aenter__()
        self._context_stack.append(self._stdio_context)
        self._session_context = ClientSession(read, write)
        self._session = await self._session_context.__aenter__()
        self._context_stack.append(self._session_context)
        await self._session.initialize()
        logger.info("MCP server '%s' connected (stdio)", self.connector_id)

    async def _connect_http(self) -> None:
        """Connect via HTTP (hosted MCP endpoint)."""
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        from mycelos.connectors.mcp_recipes import get_recipe

        recipe = get_recipe(self.connector_id)
        url = ""
        if recipe and recipe.http_endpoint:
            url = recipe.http_endpoint
        else:
            url = self._HTTP_ENDPOINTS.get(self.connector_id, "")
        if not url:
            raise ValueError(f"No HTTP endpoint configured for '{self.connector_id}'")

        token = self._resolve_token(recipe)
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        self._http_context = streamablehttp_client(url=url, headers=headers)
        read, write, _ = await self._http_context.__aenter__()
        self._context_stack.append(self._http_context)
        self._session_context = ClientSession(read, write)
        self._session = await self._session_context.__aenter__()
        self._context_stack.append(self._session_context)
        await self._session.initialize()
        logger.info("MCP server '%s' connected (http: %s)", self.connector_id, url)

    def _resolve_token(self, recipe=None) -> str | None:
        """Resolve the API token from credential proxy.

        For oauth_http recipes, runs through oauth_token_manager so
        an expired access_token is refreshed lazily. For other HTTP
        recipes (e.g. GitHub) the token is a plain credential lookup.

        Fail-closed (Constitution Rule 3): credential lookup errors
        propagate so the caller sees an explicit failure instead of
        an unauthenticated request with a confusing downstream 401.
        """
        if not self._credential_proxy:
            return None

        if recipe is not None and getattr(recipe, "setup_flow", "") == "oauth_http":
            from mycelos.security.oauth_token_manager import refresh_if_expired
            return refresh_if_expired(
                recipe=recipe,
                credential_proxy=self._credential_proxy,
                user_id="default",
            )

        # Non-OAuth HTTP recipes (GitHub etc.) use the env_vars path.
        for env_var, source in self._env_vars.items():
            if source.startswith("credential:"):
                service = source[11:]
                cred = self._credential_proxy.get_credential(service)
                if cred and "api_key" in cred:
                    return cred["api_key"]
        return None

    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        for ctx in reversed(self._context_stack):
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass
        self._context_stack.clear()
        self._session = None
        logger.info("MCP server '%s' disconnected", self.connector_id)

    async def discover_tools(self) -> list[dict[str, Any]]:
        """List available tools from the MCP server.

        Returns tool definitions in a format compatible with
        Mycelos's ToolRegistry.
        """
        if not self._session:
            raise RuntimeError("Not connected. Call connect() first.")

        result = await self._session.list_tools()
        self._tools = []

        for tool in result.tools:
            self._tools.append({
                "name": f"{self.connector_id}.{tool.name}",
                "original_name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
            })

        logger.info(
            "MCP server '%s': %d tools discovered",
            self.connector_id, len(self._tools),
        )
        return self._tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool on the MCP server.

        Args:
            tool_name: The tool name (with or without connector prefix).
            arguments: Tool arguments.

        Returns:
            Tool result.
        """
        if not self._session:
            raise RuntimeError("Not connected. Call connect() first.")

        # Strip connector prefix if present
        original_name = tool_name
        if tool_name.startswith(f"{self.connector_id}."):
            original_name = tool_name[len(self.connector_id) + 1:]

        result = await self._session.call_tool(original_name, arguments)

        # Extract content from MCP result
        if hasattr(result, "content") and result.content:
            contents = []
            for item in result.content:
                if hasattr(item, "text"):
                    contents.append(item.text)
                elif hasattr(item, "data"):
                    contents.append(str(item.data))
            return "\n".join(contents) if contents else str(result)

        return str(result)

    def _build_env(self) -> dict[str, str]:
        """Build environment with injected credentials.

        Only the credentials this specific MCP server needs are
        injected — no other secrets are visible to the subprocess.
        """
        # Start with minimal env
        env: dict[str, str] = {}
        for key in ("PATH", "HOME", "LANG", "LC_ALL", "NODE_PATH", "npm_config_cache"):
            if key in os.environ:
                env[key] = os.environ[key]

        # Inject credentials from CredentialProxy (skip blocked vars)
        for env_var, source in self._env_vars.items():
            if env_var in _BLOCKED_ENV_VARS:
                logger.warning(
                    "Blocked dangerous env var '%s' for MCP server '%s'",
                    env_var, self.connector_id,
                )
                continue
            if source.startswith("credential:"):
                service = source[11:]
                if self._credential_proxy:
                    try:
                        cred = self._credential_proxy.get_credential(service)
                        if cred and "api_key" in cred:
                            env[env_var] = cred["api_key"]
                            logger.info(
                                "Credential '%s' loaded for MCP server '%s' (env_var=%s, key_len=%d)",
                                service, self.connector_id, env_var, len(cred["api_key"]),
                            )
                        else:
                            logger.warning(
                                "Credential '%s' not found for MCP server '%s' "
                                "(proxy returned %s)",
                                service, self.connector_id,
                                "None" if cred is None else "dict without api_key",
                            )
                    except Exception as e:
                        logger.warning(
                            "Failed to load credential '%s' for MCP server '%s': %s",
                            service, self.connector_id, e,
                        )
                else:
                    logger.warning(
                        "No credential_proxy available for MCP server '%s' — "
                        "env_var '%s' will not be injected",
                        self.connector_id, env_var,
                    )
            else:
                env[env_var] = source

        return env

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    @property
    def tools(self) -> list[dict[str, Any]]:
        return self._tools


def connect_mcp_sync(
    connector_id: str,
    command: str,
    env_vars: dict[str, str] | None = None,
    credential_proxy: Any = None,
) -> tuple[MycelosMCPClient, list[dict]]:
    """Synchronous wrapper: connect + discover tools.

    For use in non-async contexts (CLI, tests).
    Returns (client, tools).
    """
    client = MycelosMCPClient(connector_id, command, env_vars, credential_proxy)

    async def _connect_and_discover():
        await client.connect()
        tools = await client.discover_tools()
        return tools

    loop = asyncio.new_event_loop()
    try:
        tools = loop.run_until_complete(_connect_and_discover())
        return client, tools
    finally:
        loop.close()
