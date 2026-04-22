"""Integration test for @gongrzhe/server-gmail-autoauth-mcp.

Spawns the MCP server via npx, drives it over JSON-RPC stdio, and
verifies that Mycelos still talks to it correctly. This catches
breaking upstream changes (renamed tools, new required env vars)
before they hit a user.

Requires:
  - Node.js + npx on PATH
  - GMAIL_OAUTH_KEYS_JSON in .env.test — the contents of
    gcp-oauth.keys.json as a single-line JSON string
  - GMAIL_TOKEN_JSON in .env.test — the contents of the per-user
    token file (credentials.json from ~/.gmail-mcp/), also as a
    single-line JSON string

Without both env vars the test skips cleanly. The shape mirrors
test_email_mcp_live.py — same _MCPServer helper, same assertions
style — so maintenance patterns carry over.
"""
from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv_test() -> dict[str, str]:
    env_file = REPO_ROOT / ".env.test"
    if not env_file.exists():
        return {}
    env: dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


@pytest.fixture(scope="module")
def gmail_oauth_files(tmp_path_factory):
    """Materialize the keys JSON + token JSON to disk in a temp dir."""
    keys = os.environ.get("GMAIL_OAUTH_KEYS_JSON")
    token = os.environ.get("GMAIL_TOKEN_JSON")
    if not keys or not token:
        dotenv = _load_dotenv_test()
        keys = keys or dotenv.get("GMAIL_OAUTH_KEYS_JSON")
        token = token or dotenv.get("GMAIL_TOKEN_JSON")
    if not keys or not token:
        pytest.skip(
            "GMAIL_OAUTH_KEYS_JSON and GMAIL_TOKEN_JSON not set in .env.test"
        )
    tmp = tmp_path_factory.mktemp("gmail-mcp")
    keys_path = tmp / "gcp-oauth.keys.json"
    token_path = tmp / "credentials.json"
    keys_path.write_text(keys)
    token_path.write_text(token)
    return keys_path, token_path


@pytest.fixture(scope="module")
def npx_available() -> None:
    if shutil.which("npx") is None:
        pytest.skip("npx not on PATH")


class _MCPServer:
    def __init__(self, cmd: list[str], env: dict[str, str]) -> None:
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
        )
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
        want_id = req["id"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self._proc.stdout], [], [], 1.0)
            if not ready:
                continue
            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError(
                    f"server closed stdout; stderr tail: {self._stderr_lines[-5:]}"
                )
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want_id:
                return msg
        raise TimeoutError(
            f"no response to {method} within {timeout}s; stderr tail: {self._stderr_lines[-5:]}"
        )

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


@pytest.fixture(scope="module")
def gmail_mcp(gmail_oauth_files, npx_available):
    keys_path, token_path = gmail_oauth_files
    env = {
        **os.environ,
        "GMAIL_OAUTH_PATH": str(keys_path),
        "GMAIL_CREDENTIALS_PATH": str(token_path),
        "NO_COLOR": "1",
        "CI": "1",
    }
    server = _MCPServer(["npx", "-y", "@gongrzhe/server-gmail-autoauth-mcp"], env)
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
def test_gmail_server_exposes_expected_tools(gmail_mcp):
    """Keep the recipe's capabilities_preview in sync with reality.
    If upstream drops or renames a tool, we hear about it here."""
    resp = gmail_mcp.rpc("tools/list")
    assert "result" in resp, resp
    tools = {t["name"] for t in resp["result"].get("tools", [])}
    # A minimal subset we absolutely need. Upstream may add more —
    # that's fine. This only catches *removed* ones.
    expected = {"search_emails", "send_email"}
    missing = expected - tools
    assert not missing, (
        f"Upstream @gongrzhe/server-gmail-autoauth-mcp removed {missing}. "
        f"Update mcp_recipes.py + prompts. Current tools: {sorted(tools)}"
    )


@pytest.mark.integration
def test_gmail_search_round_trips(gmail_mcp):
    """Real API round-trip — an empty 'in:inbox' search returns a shape
    we can parse. Doesn't assert on message count (account may be empty)."""
    resp = gmail_mcp.rpc("tools/call", {
        "name": "search_emails",
        "arguments": {"query": "in:inbox", "maxResults": 1},
    }, timeout=60)
    assert "result" in resp, f"search_emails failed: {resp}"
    content = resp["result"].get("content", [])
    assert content, "empty content — server did not return a response body"
    text = content[0].get("text", "")
    assert text.strip(), "response text was empty"
