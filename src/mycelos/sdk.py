"""Mycelos Agent SDK — the interface agents use to call tools.

Usage inside agent code:
    from mycelos.sdk import run, progress
    result = run(tool="email.read", args={"limit": 50})
    progress("Processing email 3 of 17...")
"""

from __future__ import annotations

import itertools
import json
import os
import sys
from typing import Any

_request_counter = itertools.count(1)


def run(tool: str, args: dict[str, Any] | None = None) -> Any:
    """Call a tool through the Mycelos Security Layer."""
    request_id = next(_request_counter)

    token = os.environ.get("MYCELOS_SESSION_TOKEN", "")
    request = {
        "jsonrpc": "2.0",
        "method": "tool.call",
        "params": {"tool": tool, "args": args or {}},
        "id": request_id,
        "auth": token,
    }
    sys.stdout.write(json.dumps(request) + "\n")
    sys.stdout.flush()

    response_line = sys.stdin.readline()
    if not response_line:
        raise RuntimeError("No response from gateway (STDIN closed)")

    response = json.loads(response_line)
    if "error" in response:
        error = response["error"]
        raise RuntimeError(f"Tool call failed [{error.get('code')}]: {error.get('message')}")

    return response.get("result")


def progress(text: str) -> None:
    """Send a progress update to the gateway (non-blocking, fire and forget).

    Progress notifications are JSON-RPC notifications (no id, no response expected).
    """
    notification = {
        "jsonrpc": "2.0",
        "method": "progress",
        "params": {"text": text},
    }
    sys.stdout.write(json.dumps(notification) + "\n")
    sys.stdout.flush()
