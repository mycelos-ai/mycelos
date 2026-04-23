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


def test_websocket_persists_token_on_clean_exit(proxy_app, tmp_path, monkeypatch):
    """When a recipe-dispatched subprocess exits 0 and wrote a token
    file to <HOME>/.gmail-mcp/credentials.json, the proxy must store
    it as oauth_token_credential_service and purge the tmp dir."""
    import json as _json

    # Seed keys credential.
    proxy_app.post("/credential/store", json={
        "service": "gmail-oauth-keys",
        "label": "default",
        "payload": {"api_key": '{"installed": {"client_id": "c"}}'},
        "description": "test",
    })

    from mycelos.security import proxy_server as ps
    monkeypatch.setattr(ps, "OAUTH_TMP_ROOT", tmp_path)
    # Widen the allowlist so our test command (python) goes through.
    monkeypatch.setattr(ps, "_OAUTH_ALLOWED_HEADS", ("npx", "python"))

    # Swap the recipe's oauth_cmd for a short-lived python one-liner
    # that writes a token file into $HOME/.gmail-mcp/ and exits 0.
    from mycelos.connectors.mcp_recipes import RECIPES
    original_cmd = RECIPES["gmail"].oauth_cmd
    test_script = (
        "import os, pathlib; "
        "p = pathlib.Path(os.environ['HOME']) / '.gmail-mcp'; "
        "p.mkdir(parents=True, exist_ok=True); "
        "(p / 'credentials.json').write_text('{\"access_token\": \"fake\"}');"
    )
    # dataclasses.replace would make a copy; the materializer looks up
    # the recipe by id each time, so we mutate in place and restore
    # at the end.
    object.__setattr__(RECIPES["gmail"], "oauth_cmd", f'python -c "{test_script}"')

    try:
        resp = proxy_app.post("/oauth/start", json={"recipe_id": "gmail"})
        assert resp.status_code == 200, resp.text
        sid = resp.json()["session_id"]

        with proxy_app.websocket_connect(
            f"/oauth/stream/{sid}",
            headers={"Authorization": "Bearer test-token"},
        ) as ws:
            # Read frames until 'done'.
            for _ in range(200):
                try:
                    raw = ws.receive_text()
                except Exception:
                    break
                try:
                    frame = _json.loads(raw)
                except _json.JSONDecodeError:
                    continue
                if frame.get("type") == "done":
                    assert frame["exit_code"] == 0, f"unexpected exit: {frame}"
                    break
    finally:
        object.__setattr__(RECIPES["gmail"], "oauth_cmd", original_cmd)

    # Token credential was stored.
    lst = proxy_app.get("/credential/list").json()
    services = [c["service"] for c in lst.get("credentials", [])]
    assert "gmail-oauth-token" in services, f"expected token credential, got: {services}"

    # Tmp dir was purged.
    assert not list(tmp_path.glob("mycelos-oauth-*"))
