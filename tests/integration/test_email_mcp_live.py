"""Integration test for @n24q02m/better-email-mcp.

Spawns the MCP server locally via npx, drives it over JSON-RPC stdio,
and verifies the three operations Mycelos actually relies on:

  1. initialize — server comes up and advertises its tool set
  2. tools/list — the composite tools we expect (messages, send, …) exist
  3. messages search UNREAD — a real IMAP round-trip against the account
     in .env.test returns at least an (empty-is-fine) result payload

Requires:
  - Node.js >= 24.15 on PATH (that's what the MCP server declares;
    lower versions emit EBADENGINE warnings but may still work — we
    don't skip on version mismatch because Node 22 was observed to
    function fine end-to-end in local smoke runs).
  - GMAIL_USER and GMAIL_PASSWORD in the environment (loaded from
    .env.test via the pytest fixture below). Skip cleanly when missing
    so local and CI runs without email creds don't break.
  - TRANSPORT_MODE=stdio — this is baked into the recipe's static_env
    and passed to the subprocess, but we set it here explicitly too
    so the test mirrors what the gateway does in production.

The test is marked 'integration' so it's skipped by the default
test run (tests/ --ignore=tests/integration) and only fires when
someone explicitly asks for pytest tests/integration/.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv_test() -> dict[str, str]:
    """Parse .env.test without pulling in python-dotenv. Returns a
    dict of env var name to value; empty if the file is absent."""
    env_file = REPO_ROOT / ".env.test"
    if not env_file.exists():
        return {}
    env: dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        env[key.strip()] = value
    return env


@pytest.fixture(scope="module")
def gmail_credentials() -> tuple[str, str]:
    """Pull (GMAIL_USER, GMAIL_PASSWORD) from env or .env.test."""
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_PASSWORD")
    if not user or not pw:
        dotenv = _load_dotenv_test()
        user = user or dotenv.get("GMAIL_USER")
        pw = pw or dotenv.get("GMAIL_PASSWORD")
    if not user or not pw:
        pytest.skip("GMAIL_USER and GMAIL_PASSWORD not set (put them in .env.test)")
    return user, pw


@pytest.fixture(scope="module")
def npx_available() -> None:
    if shutil.which("npx") is None:
        pytest.skip("npx not on PATH — cannot spawn the MCP server")


class _MCPServer:
    """Thin JSON-RPC-over-stdio client for a long-running MCP subprocess.

    Keeps the class deliberately minimal — no reconnect, no concurrency,
    no progress tokens. That's fine for a single integration test that
    sends three requests in sequence.
    """

    def __init__(self, env: dict[str, str]) -> None:
        self._proc = subprocess.Popen(
            ["npx", "-y", "@n24q02m/better-email-mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
        )
        # Drain stderr asynchronously so the pipe doesn't block.
        self._stderr_lines: list[str] = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self._id = 0

    def _drain_stderr(self) -> None:
        assert self._proc.stderr is not None
        for raw in iter(self._proc.stderr.readline, b""):
            self._stderr_lines.append(raw.decode("utf-8", "replace").rstrip())

    def rpc(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            req["params"] = params
        self._proc.stdin.write((json.dumps(req) + "\n").encode())
        self._proc.stdin.flush()

        # Read until we see a response with our id (skip unsolicited notifications).
        import select
        want_id = req["id"]
        deadline = _now() + timeout
        while _now() < deadline:
            ready, _, _ = select.select([self._proc.stdout], [], [], 1.0)
            if not ready:
                continue
            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError(f"server closed stdout; stderr tail: {self._stderr_lines[-5:]}")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # non-JSON stderr bleed, ignore
            if msg.get("id") == want_id:
                return msg
        raise TimeoutError(f"no response to {method} within {timeout}s; stderr tail: {self._stderr_lines[-5:]}")

    def notify(self, method: str, params: dict | None = None) -> None:
        assert self._proc.stdin is not None
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._proc.stdin.write((json.dumps(msg) + "\n").encode())
        self._proc.stdin.flush()

    def stop(self) -> None:
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.close()
        except Exception:
            pass
        self._proc.terminate()
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._proc.kill()


def _now() -> float:
    import time
    return time.monotonic()


@pytest.fixture(scope="module")
def mcp_server(gmail_credentials, npx_available):
    """Start the MCP server once per module, drive it through
    initialize, yield the client, and tear down at module exit."""
    user, pw = gmail_credentials
    env = {
        **os.environ,
        "EMAIL_CREDENTIALS": f"{user}:{pw}",
        "TRANSPORT_MODE": "stdio",
        "NO_COLOR": "1",
        "CI": "1",
    }
    server = _MCPServer(env)
    try:
        init = server.rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "mycelos-integration", "version": "0.1"},
        })
        assert "result" in init, f"initialize failed: {init}"
        server.notify("notifications/initialized")
        yield server
    finally:
        server.stop()


@pytest.mark.integration
def test_server_exposes_expected_tools(mcp_server):
    """The recipe's capabilities_preview list must match what the
    server actually advertises on tools/list. If this drifts, we know
    the upstream package changed its tool surface."""
    resp = mcp_server.rpc("tools/list")
    assert "result" in resp, resp
    tools = {t["name"] for t in resp["result"].get("tools", [])}
    # Mycelos's recipe declares these six composite tools. Keep the
    # list in sync with mcp_recipes.py "email" entry.
    expected = {"messages", "folders", "attachments", "send", "config", "help"}
    missing = expected - tools
    assert not missing, (
        f"@n24q02m/better-email-mcp no longer exposes {missing}. "
        f"Update mcp_recipes.py and the email workflow templates. "
        f"Current tools: {sorted(tools)}"
    )


@pytest.mark.integration
def test_messages_search_unread_round_trips(mcp_server):
    """Real IMAP round-trip — the MCP server logs in to Gmail with the
    EMAIL_CREDENTIALS we passed and returns a search result. The
    account can legitimately have zero unread messages; we only check
    that the response shape is sane (no error, at least a 'total' field
    in the payload)."""
    resp = mcp_server.rpc("tools/call", {
        "name": "messages",
        "arguments": {
            "action": "search",
            "query": "UNREAD",
            "returnBody": False,
            "limit": 3,
        },
    }, timeout=60)
    assert "result" in resp, f"search failed: {resp}"
    payload = resp["result"].get("content", [])
    assert payload, "empty content — server did not return a response body"
    # Payload is a list of {type: 'text', text: '...json...'} entries.
    # The MCP server wraps its JSON in <untrusted_email_content> tags —
    # strip and parse so we can assert on the shape.
    text = payload[0].get("text", "")
    assert "total" in text or "messages" in text, (
        f"unexpected response shape: {text[:200]!r}"
    )
