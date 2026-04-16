"""Tests for JSON-RPC IPC protocol."""

import json

import pytest

from mycelos.execution.ipc import (
    parse_request, make_error, make_result,
    RPCRequest, RPCResponse,
    PARSE_ERROR, AUTH_FAILED, PERMISSION_DENIED,
)


def test_parse_valid_request() -> None:
    line = json.dumps({
        "jsonrpc": "2.0",
        "method": "tool.call",
        "params": {"tool": "email.read", "args": {"limit": 10}},
        "id": 1,
        "auth": "token-abc123",
    })
    req = parse_request(line)
    assert req.method == "tool.call"
    assert req.params["tool"] == "email.read"
    assert req.id == 1
    assert req.auth == "token-abc123"


def test_parse_request_without_params() -> None:
    line = json.dumps({"jsonrpc": "2.0", "method": "ping", "id": 2})
    req = parse_request(line)
    assert req.method == "ping"
    assert req.params == {}
    assert req.auth == ""


def test_parse_invalid_json() -> None:
    with pytest.raises(ValueError, match="Invalid JSON"):
        parse_request("not json at all")


def test_parse_missing_method() -> None:
    with pytest.raises(ValueError, match="Missing method"):
        parse_request(json.dumps({"jsonrpc": "2.0", "id": 1}))


def test_parse_wrong_version() -> None:
    with pytest.raises(ValueError, match="jsonrpc version"):
        parse_request(json.dumps({"jsonrpc": "1.0", "method": "test", "id": 1}))


def test_success_response_serialization() -> None:
    resp = make_result(1, {"emails": ["a@b.com"]})
    data = json.loads(resp.to_json())
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 1
    assert data["result"] == {"emails": ["a@b.com"]}
    assert "error" not in data


def test_error_response_serialization() -> None:
    resp = make_error(1, AUTH_FAILED, "Token expired")
    data = json.loads(resp.to_json())
    assert data["error"]["code"] == AUTH_FAILED
    assert data["error"]["message"] == "Token expired"
    assert "result" not in data


def test_response_is_error_flag() -> None:
    ok = make_result(1, "data")
    assert ok.is_error is False
    err = make_error(1, -32600, "bad")
    assert err.is_error is True
