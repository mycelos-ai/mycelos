"""Tests for the Agent SDK."""

import json
import io
from unittest.mock import patch

import pytest

from mycelos.sdk import progress, run


def test_sdk_run_sends_jsonrpc() -> None:
    mock_response = json.dumps({"jsonrpc": "2.0", "result": {"emails": [1, 2, 3]}, "id": 1})
    with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout, \
         patch("sys.stdin", io.StringIO(mock_response + "\n")), \
         patch.dict("os.environ", {"MYCELOS_SESSION_TOKEN": "test-token"}):
        result = run(tool="email.read", args={"limit": 10})
    assert result == {"emails": [1, 2, 3]}
    sent = json.loads(mock_stdout.getvalue().strip())
    assert sent["jsonrpc"] == "2.0"
    assert sent["method"] == "tool.call"
    assert sent["params"]["tool"] == "email.read"
    assert sent["auth"] == "test-token"


def test_sdk_run_error_raises() -> None:
    mock_response = json.dumps({
        "jsonrpc": "2.0",
        "error": {"code": -32001, "message": "Permission denied"},
        "id": 1,
    })
    with patch("sys.stdout", new_callable=io.StringIO), \
         patch("sys.stdin", io.StringIO(mock_response + "\n")), \
         patch.dict("os.environ", {"MYCELOS_SESSION_TOKEN": "tok"}):
        with pytest.raises(RuntimeError, match="Permission denied"):
            run(tool="shell.exec", args={"cmd": "ls"})


def test_sdk_run_no_response_raises() -> None:
    with patch("sys.stdout", new_callable=io.StringIO), \
         patch("sys.stdin", io.StringIO("")), \
         patch.dict("os.environ", {"MYCELOS_SESSION_TOKEN": "tok"}):
        with pytest.raises(RuntimeError, match="No response"):
            run(tool="test.op")


def test_sdk_progress_sends_notification() -> None:
    """progress() sends a JSON-RPC notification (no id, no response expected)."""
    with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        progress("Processing step 2 of 5...")

    sent = json.loads(mock_stdout.getvalue().strip())
    assert sent["jsonrpc"] == "2.0"
    assert sent["method"] == "progress"
    assert sent["params"]["text"] == "Processing step 2 of 5..."
    assert "id" not in sent  # notifications have no id
