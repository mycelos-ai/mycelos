"""The /oauth/stream/{session_id} WebSocket streams subprocess I/O
between the browser and the spawned auth process. Frame shape:
{type: 'stdout'|'stderr'|'stdin'|'done', data: str, exit_code?: int}.

'cat' is the faithful stand-in for the real OAuth subprocess — it
reads stdin and echoes it to stdout, which is exactly the shape of
an interactive 'paste the callback URL here' prompt.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def proxy_app(tmp_path: Path, monkeypatch):
    from mycelos.app import App
    from mycelos.security.proxy_server import create_proxy_app
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "phase-1b-test-key-" + "x" * 16)
    monkeypatch.setenv("MYCELOS_PROXY_TOKEN", "test-token")
    monkeypatch.setenv("MYCELOS_DB_PATH", str(tmp_path / "mycelos.db"))
    app = App(tmp_path)
    app.initialize()
    proxy = create_proxy_app()
    client = TestClient(proxy)
    client.headers.update({"Authorization": "Bearer test-token"})
    return client


def _auth_headers():
    return {"Authorization": "Bearer test-token"}


def test_websocket_unknown_session_closes_immediately(proxy_app):
    """An unknown session id must close the WS rather than hanging."""
    # TestClient raises WebSocketDisconnect when the server closes the
    # connection without accepting, or closes right after accepting.
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises((WebSocketDisconnect, Exception)):
        with proxy_app.websocket_connect(
            "/oauth/stream/oauth-nonexistent",
            headers=_auth_headers(),
        ) as ws:
            # If the server closed on accept, this receive raises.
            ws.receive_text()


def test_websocket_streams_stdout_from_subprocess(proxy_app):
    """Spawn 'npx --help' (finishes quickly, prints to stdout). Connect
    the WS and expect at least one stdout frame and a done frame."""
    if shutil.which("npx") is None:
        pytest.skip("npx not on PATH")

    # Start a session
    resp = proxy_app.post("/oauth/start", json={
        "oauth_cmd": "npx --help",
        "env_vars": {},
    })
    assert resp.status_code == 200, resp.text
    sid = resp.json()["session_id"]

    saw_stdout = False
    exit_code = None
    try:
        with proxy_app.websocket_connect(
            f"/oauth/stream/{sid}",
            headers=_auth_headers(),
        ) as ws:
            # Read until 'done' frame or timeout on receive.
            for _ in range(200):
                try:
                    raw = ws.receive_text()
                except Exception:
                    break
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if frame.get("type") == "stdout" and frame.get("data"):
                    saw_stdout = True
                if frame.get("type") == "done":
                    exit_code = frame.get("exit_code")
                    break
    finally:
        proxy_app.post("/oauth/stop", json={"session_id": sid})

    assert saw_stdout, "expected at least one stdout frame from 'npx --help'"
    assert exit_code is not None, "expected a done frame with exit_code"
