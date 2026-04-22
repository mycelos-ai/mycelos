"""Integration test for @cocal/google-calendar-mcp.

Spawns the MCP server via npx and verifies that list_calendars and
list_events round-trip. Skips unless GOOGLE_OAUTH_KEYS_JSON and
GOOGLE_CALENDAR_TOKEN_JSON are set in .env.test.
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
def calendar_oauth_files(tmp_path_factory):
    keys = os.environ.get("GOOGLE_OAUTH_KEYS_JSON")
    token = os.environ.get("GOOGLE_CALENDAR_TOKEN_JSON")
    if not keys or not token:
        dotenv = _load_dotenv_test()
        keys = keys or dotenv.get("GOOGLE_OAUTH_KEYS_JSON")
        token = token or dotenv.get("GOOGLE_CALENDAR_TOKEN_JSON")
    if not keys or not token:
        pytest.skip(
            "GOOGLE_OAUTH_KEYS_JSON and GOOGLE_CALENDAR_TOKEN_JSON "
            "not set in .env.test"
        )
    tmp = tmp_path_factory.mktemp("gcal-mcp")
    keys_path = tmp / "gcp-oauth.keys.json"
    token_path = tmp / "token.json"
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
def calendar_mcp(calendar_oauth_files, npx_available):
    keys_path, token_path = calendar_oauth_files
    env = {
        **os.environ,
        "GOOGLE_OAUTH_CREDENTIALS": str(keys_path),
        "GOOGLE_CALENDAR_TOKEN_PATH": str(token_path),
        "NO_COLOR": "1",
        "CI": "1",
    }
    server = _MCPServer(["npx", "-y", "@cocal/google-calendar-mcp"], env)
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
def test_calendar_server_exposes_expected_tools(calendar_mcp):
    resp = calendar_mcp.rpc("tools/list")
    assert "result" in resp, resp
    tools = {t["name"] for t in resp["result"].get("tools", [])}
    expected = {"list_calendars", "list_events"}
    missing = expected - tools
    assert not missing, (
        f"Upstream @cocal/google-calendar-mcp removed {missing}. "
        f"Current tools: {sorted(tools)}"
    )


@pytest.mark.integration
def test_calendar_list_calendars_round_trips(calendar_mcp):
    resp = calendar_mcp.rpc("tools/call", {
        "name": "list_calendars",
        "arguments": {},
    }, timeout=60)
    assert "result" in resp, f"list_calendars failed: {resp}"
    content = resp["result"].get("content", [])
    assert content, "empty content — server did not return a response body"
