"""Tests for the Execution Runtime — full pipeline."""

import json
from pathlib import Path

import pytest

from mycelos.storage.database import SQLiteStorage
from mycelos.execution.ipc import AUTH_FAILED, PERMISSION_DENIED, METHOD_NOT_FOUND
from mycelos.execution.runtime import ExecutionRuntime
from mycelos.execution.tools import ToolRegistry, ToolDefinition
from mycelos.security.capabilities import CapabilityTokenManager
from mycelos.security.policies import PolicyEngine
from mycelos.security.sanitizer import ResponseSanitizer


@pytest.fixture
def storage(db_path: Path) -> SQLiteStorage:
    s = SQLiteStorage(db_path)
    s.initialize()
    return s


@pytest.fixture
def runtime(storage: SQLiteStorage) -> tuple[ExecutionRuntime, CapabilityTokenManager]:
    tokens = CapabilityTokenManager(storage)
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="math.add",
        description="Add numbers",
        handler=lambda a, b: a + b,
        required_capability="math.add",
    ))
    registry.register(ToolDefinition(
        name="echo",
        description="Echo text",
        handler=lambda text: text,
        required_capability="echo",
    ))
    sanitizer = ResponseSanitizer()
    rt = ExecutionRuntime(tokens, registry, sanitizer)
    return rt, tokens


def make_request(method: str, params: dict, token: str = "", req_id: int = 1) -> str:
    return json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": req_id, "auth": token})


def test_full_pipeline_success(runtime: tuple) -> None:
    """Full pipeline: valid token -> tool call -> success."""
    rt, tokens = runtime
    token_id = tokens.issue(agent_id="agent", scope=["math.add"], ttl_seconds=300)
    req = make_request("tool.call", {"tool": "math.add", "args": {"a": 2, "b": 3}}, token_id)
    resp = rt.handle_request(req)
    assert not resp.is_error
    assert resp.result == 5


def test_missing_auth_token(runtime: tuple) -> None:
    rt, _ = runtime
    req = make_request("tool.call", {"tool": "math.add", "args": {"a": 1, "b": 2}}, "")
    resp = rt.handle_request(req)
    assert resp.is_error
    assert resp.error_code == AUTH_FAILED


def test_invalid_token(runtime: tuple) -> None:
    rt, _ = runtime
    req = make_request("tool.call", {"tool": "math.add", "args": {"a": 1, "b": 2}}, "fake-token")
    resp = rt.handle_request(req)
    assert resp.is_error
    assert resp.error_code == PERMISSION_DENIED


def test_scope_mismatch(runtime: tuple) -> None:
    rt, tokens = runtime
    token_id = tokens.issue(agent_id="agent", scope=["echo"], ttl_seconds=300)
    req = make_request("tool.call", {"tool": "math.add", "args": {"a": 1, "b": 2}}, token_id)
    resp = rt.handle_request(req)
    assert resp.is_error
    assert resp.error_code == PERMISSION_DENIED


def test_unknown_tool(runtime: tuple) -> None:
    rt, tokens = runtime
    token_id = tokens.issue(agent_id="agent", scope=["whatever"], ttl_seconds=300)
    req = make_request("tool.call", {"tool": "nonexistent", "args": {}}, token_id)
    resp = rt.handle_request(req)
    assert resp.is_error
    assert resp.error_code == METHOD_NOT_FOUND


def test_response_sanitization(runtime: tuple) -> None:
    """Credentials in tool output are sanitized."""
    rt, tokens = runtime
    token_id = tokens.issue(agent_id="agent", scope=["echo"], ttl_seconds=300)
    req = make_request("tool.call", {"tool": "echo", "args": {"text": "key: sk-ant-secret12345678901234567890"}}, token_id)
    resp = rt.handle_request(req)
    assert not resp.is_error
    assert "sk-ant-" not in resp.result
    assert "[REDACTED]" in resp.result


def test_tools_list(runtime: tuple) -> None:
    rt, _ = runtime
    req = make_request("tools.list", {})
    resp = rt.handle_request(req)
    assert not resp.is_error
    assert len(resp.result) == 2


def test_invalid_json(runtime: tuple) -> None:
    rt, _ = runtime
    resp = rt.handle_request("not json")
    assert resp.is_error


def test_policy_engine_blocks_never(storage: SQLiteStorage) -> None:
    """Policy Engine blocks tools with 'never' policy even with valid token."""
    tokens = CapabilityTokenManager(storage)
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="dangerous",
        description="Dangerous tool",
        handler=lambda: None,
        required_capability="shell.exec",
    ))
    sanitizer = ResponseSanitizer()
    policy = PolicyEngine(storage)
    policy.set_policy("default", "agent-x", "shell.exec", "never")
    runtime = ExecutionRuntime(tokens, registry, sanitizer, policy_engine=policy)

    token = tokens.issue(agent_id="agent-x", scope=["shell.exec"], ttl_seconds=60)
    req = json.dumps({
        "jsonrpc": "2.0", "method": "tool.call",
        "params": {"tool": "dangerous", "args": {}},
        "id": 1, "auth": token,
    })
    resp = runtime.handle_request(req)
    assert resp.is_error
    assert "never" in resp.error_message.lower() or "denied" in resp.error_message.lower()
