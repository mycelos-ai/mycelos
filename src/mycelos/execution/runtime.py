"""Execution Runtime — orchestrates the full agent execution pipeline.

Pipeline: Agent Request -> IPC Parse -> Token Validate -> Policy Check ->
          Tool Dispatch -> Response Sanitize -> Agent Response
"""

from __future__ import annotations

from typing import Any

from mycelos.execution.ipc import (
    RPCRequest,
    RPCResponse,
    parse_request,
    make_result,
    make_error,
    AUTH_FAILED,
    PERMISSION_DENIED,
    METHOD_NOT_FOUND,
    INTERNAL_ERROR,
)
from mycelos.execution.tools import ToolRegistry
from mycelos.security.capabilities import CapabilityTokenManager
from mycelos.security.policies import PolicyEngine
from mycelos.security.sanitizer import ResponseSanitizer


class ExecutionRuntime:
    """Processes agent JSON-RPC requests through the security pipeline."""

    def __init__(
        self,
        token_manager: CapabilityTokenManager,
        tool_registry: ToolRegistry,
        sanitizer: ResponseSanitizer,
        policy_engine: PolicyEngine | None = None,
    ) -> None:
        self._tokens = token_manager
        self._tools = tool_registry
        self._sanitizer = sanitizer
        self._policy_engine = policy_engine

    def handle_request(self, raw_line: str) -> RPCResponse:
        """Handle a single JSON-RPC request from an agent.

        Args:
            raw_line: Raw JSON-RPC 2.0 string from the agent's stdout.

        Returns:
            An RPCResponse with either a result or an error.
        """
        try:
            request = parse_request(raw_line)
        except ValueError as e:
            return make_error(None, -32700, str(e))

        if request.method == "tool.call":
            return self._handle_tool_call(request)
        elif request.method == "tools.list":
            return make_result(request.id, self._tools.list_tools())
        else:
            return make_error(
                request.id, METHOD_NOT_FOUND, f"Unknown method: {request.method}"
            )

    def _handle_tool_call(self, request: RPCRequest) -> RPCResponse:
        """Handle a tool.call request through the security pipeline.

        Steps:
            1. Validate auth token presence
            2. Look up tool in registry
            3. Validate token scope against tool's required capability
            4. Execute tool handler
            5. Sanitize response output
        """
        tool_name = request.params.get("tool", "")
        args = request.params.get("args", {})

        # 1. Validate auth token
        if not request.auth:
            return make_error(request.id, AUTH_FAILED, "Missing auth token")

        # 2. Find tool
        tool_def = self._tools.get(tool_name)
        if tool_def is None:
            return make_error(
                request.id, METHOD_NOT_FOUND, f"Tool '{tool_name}' not found"
            )

        # 3. Validate token against the tool's required capability
        cap_validation = self._tokens.validate(request.auth, tool_def.required_capability)
        if not cap_validation.valid:
            return make_error(request.id, PERMISSION_DENIED, cap_validation.reason)

        # 3b. Check PolicyEngine (never policy blocks even with valid token)
        if self._policy_engine and cap_validation.agent_id:
            decision = self._policy_engine.evaluate(
                "default", cap_validation.agent_id, tool_def.required_capability
            )
            if decision == "never":
                return make_error(
                    request.id,
                    PERMISSION_DENIED,
                    f"Policy denied: {tool_def.required_capability} is set to 'never' for agent '{cap_validation.agent_id}'",
                )

        # 4. Execute tool
        try:
            result = self._tools.call(tool_name, args)
        except Exception as e:
            return make_error(request.id, INTERNAL_ERROR, str(e))

        # 5. Sanitize response
        result = self._sanitize_result(result)

        return make_result(request.id, result)

    def _sanitize_result(self, result: Any) -> Any:
        """Sanitize a tool result, handling strings, dicts, and lists."""
        if isinstance(result, str):
            return self._sanitizer.sanitize_text(result)
        elif isinstance(result, dict):
            return self._sanitize_dict(result)
        elif isinstance(result, list):
            return [self._sanitize_result(item) for item in result]
        return result

    def _sanitize_dict(self, d: dict[str, Any]) -> dict[str, Any]:
        """Recursively sanitize string values in a dict."""
        return {k: self._sanitize_result(v) for k, v in d.items()}
