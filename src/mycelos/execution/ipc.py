"""JSON-RPC IPC Protocol for agent <-> gateway communication.

Agents send JSON-RPC requests on STDOUT, receive responses on STDIN.
The gateway reads agent STDOUT, processes requests, writes responses to agent STDIN.

Message format (JSON-RPC 2.0):
  Request:  {"jsonrpc": "2.0", "method": "tool.call", "params": {...}, "id": 1, "auth": "<token>"}
  Response: {"jsonrpc": "2.0", "result": {...}, "id": 1}
  Error:    {"jsonrpc": "2.0", "error": {"code": -32600, "message": "..."}, "id": 1}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Standard JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# Custom error codes
AUTH_FAILED = -32000
PERMISSION_DENIED = -32001
RATE_LIMITED = -32002


@dataclass(frozen=True)
class RPCRequest:
    """Parsed JSON-RPC request from an agent."""

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: int | str | None = None
    auth: str = ""


@dataclass(frozen=True)
class RPCResponse:
    """JSON-RPC response to send back to agent."""

    id: int | str | None = None
    result: Any = None
    error_code: int | None = None
    error_message: str | None = None

    @property
    def is_error(self) -> bool:
        """Return True if this response represents an error."""
        return self.error_code is not None

    def to_json(self) -> str:
        """Serialize response to a JSON string."""
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": self.id}
        if self.is_error:
            msg["error"] = {"code": self.error_code, "message": self.error_message}
        else:
            msg["result"] = self.result
        return json.dumps(msg)


def parse_request(line: str) -> RPCRequest:
    """Parse a JSON-RPC request line.

    Args:
        line: A single line of JSON text representing a JSON-RPC 2.0 request.

    Returns:
        A parsed RPCRequest dataclass.

    Raises:
        ValueError: If the JSON is malformed or missing required fields.
    """
    try:
        msg = json.loads(line)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e

    if not isinstance(msg, dict):
        raise ValueError("Request must be a JSON object")
    if msg.get("jsonrpc") != "2.0":
        raise ValueError("Missing or invalid jsonrpc version")
    if "method" not in msg:
        raise ValueError("Missing method field")

    return RPCRequest(
        method=msg["method"],
        params=msg.get("params", {}),
        id=msg.get("id"),
        auth=msg.get("auth", ""),
    )


def make_error(request_id: int | str | None, code: int, message: str) -> RPCResponse:
    """Create an error RPCResponse.

    Args:
        request_id: The id from the original request.
        code: JSON-RPC error code.
        message: Human-readable error description.
    """
    return RPCResponse(id=request_id, error_code=code, error_message=message)


def make_result(request_id: int | str | None, result: Any) -> RPCResponse:
    """Create a success RPCResponse.

    Args:
        request_id: The id from the original request.
        result: The result payload.
    """
    return RPCResponse(id=request_id, result=result)
