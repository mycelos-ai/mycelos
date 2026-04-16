"""Integration test: Full security pipeline — Token -> Tool -> Sanitize.

Tests the complete flow: issue token -> make tool call through ExecutionRuntime
-> validate token -> check scope -> execute tool -> sanitize response.
"""

import json
import time

import pytest

from mycelos.execution.runtime import ExecutionRuntime
from mycelos.execution.tools import ToolDefinition, ToolRegistry
from mycelos.security.capabilities import CapabilityTokenManager
from mycelos.security.policies import PolicyEngine
from mycelos.security.sanitizer import ResponseSanitizer
from mycelos.storage.database import SQLiteStorage


@pytest.fixture
def security_pipeline(tmp_path):
    """Full security pipeline with real SQLite."""
    storage = SQLiteStorage(tmp_path / "test.db")
    storage.initialize()
    tokens = CapabilityTokenManager(storage)
    policy = PolicyEngine(storage)
    registry = ToolRegistry()
    sanitizer = ResponseSanitizer()

    # Register a tool that returns sensitive data
    def leaky_tool(query: str) -> str:
        return f"Result for {query}. Debug: api_key=sk-ant-secret123456789012345678"

    registry.register(
        ToolDefinition(
            name="search.web",
            description="Search",
            handler=leaky_tool,
            required_capability="search.web",
        )
    )

    # Register a blocked tool
    registry.register(
        ToolDefinition(
            name="shell.exec",
            description="Shell",
            handler=lambda cmd: "executed",
            required_capability="shell.exec",
        )
    )

    runtime = ExecutionRuntime(tokens, registry, sanitizer, policy_engine=policy)
    return runtime, tokens, policy


@pytest.mark.integration
def test_full_pipeline_token_to_sanitized_response(security_pipeline):
    """Token -> Scope check -> Execute -> Sanitize credentials from output."""
    runtime, tokens, _ = security_pipeline
    token = tokens.issue(agent_id="search-agent", scope=["search.web"], ttl_seconds=60)

    req = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "tool.call",
            "params": {"tool": "search.web", "args": {"query": "AI news"}},
            "id": 1,
            "auth": token,
        }
    )
    resp = runtime.handle_request(req)

    assert not resp.is_error
    assert "AI news" in resp.result  # result preserved
    assert "sk-ant-" not in resp.result  # credential redacted!
    assert "[REDACTED]" in resp.result


@pytest.mark.integration
def test_pipeline_blocks_wrong_scope(security_pipeline):
    """Token with search scope cannot call shell.exec."""
    runtime, tokens, _ = security_pipeline
    token = tokens.issue(agent_id="agent", scope=["search.web"], ttl_seconds=60)

    req = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "tool.call",
            "params": {"tool": "shell.exec", "args": {"cmd": "ls"}},
            "id": 1,
            "auth": token,
        }
    )
    resp = runtime.handle_request(req)
    assert resp.is_error


@pytest.mark.integration
def test_pipeline_policy_blocks_never(security_pipeline):
    """Even with valid token, 'never' policy blocks execution."""
    runtime, tokens, policy = security_pipeline
    policy.set_policy("default", "blocked-agent", "shell.exec", "never")
    token = tokens.issue(
        agent_id="blocked-agent", scope=["shell.exec"], ttl_seconds=60
    )

    req = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "tool.call",
            "params": {"tool": "shell.exec", "args": {"cmd": "ls"}},
            "id": 1,
            "auth": token,
        }
    )
    resp = runtime.handle_request(req)
    assert resp.is_error
    assert (
        "never" in resp.error_message.lower()
        or "denied" in resp.error_message.lower()
    )


@pytest.mark.integration
def test_pipeline_expired_token_rejected(security_pipeline):
    """Expired token is rejected."""
    runtime, tokens, _ = security_pipeline
    token = tokens.issue(agent_id="agent", scope=["search.web"], ttl_seconds=0)
    time.sleep(0.05)

    req = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "tool.call",
            "params": {"tool": "search.web", "args": {"query": "test"}},
            "id": 1,
            "auth": token,
        }
    )
    resp = runtime.handle_request(req)
    assert resp.is_error
