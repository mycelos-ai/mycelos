# SecurityProxy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Isolate credentials and external network access in a separate child process, so the Gateway never touches the master key or plaintext credentials.

**Architecture:** The Gateway forks a SecurityProxy child process at startup. The proxy runs a FastAPI app on a Unix Domain Socket, owns the master key, and handles all HTTP, MCP, and LLM calls. The Gateway communicates via a synchronous `SecurityProxyClient` using httpx with Unix socket transport.

**Tech Stack:** FastAPI, uvicorn (--uds), httpx (HTTPTransport(uds=...)), litellm (inside proxy), multiprocessing

**Spec:** `docs/superpowers/specs/2026-03-25-security-proxy-design.md`

---

## File Structure

### New Files
- `src/mycelos/security/proxy_server.py` — SecurityProxy FastAPI app (runs in child process)
- `src/mycelos/security/proxy_client.py` — SecurityProxyClient (used by Gateway)
- `src/mycelos/security/proxy_launcher.py` — Fork/restart logic, health check, session token
- `tests/test_security_proxy.py` — Unit tests for proxy server + client
- `tests/security/test_proxy_isolation.py` — Security invariant tests
- `tests/e2e/test_proxy_integration.py` — E2E: Gateway → Proxy → external mock

### Modified Files
- `src/mycelos/protocols.py` — Add SecurityProxyProtocol
- `src/mycelos/app.py` — Replace `credentials` property with `proxy_client`, remove master key dependency
- `src/mycelos/gateway/server.py` — Start proxy before Gateway, wire proxy_client
- `src/mycelos/connectors/http_tools.py` — Delegate to proxy_client
- `src/mycelos/connectors/mcp_manager.py` — Delegate to proxy_client (or replace)
- `src/mycelos/llm/broker.py` — Delegate to proxy_client for LLM calls
- `src/mycelos/channels/telegram.py` — Use bootstrapped token from proxy

---

## Task 1: SecurityProxy Server — Core + Health + Auth

**Files:**
- Create: `src/mycelos/security/proxy_server.py`
- Create: `tests/test_security_proxy.py`

- [ ] **Step 1: Write failing tests for proxy server**

```python
# tests/test_security_proxy.py
"""Tests for SecurityProxy server — auth, health, HTTP proxy."""

import os
import tempfile
import time
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import httpx

SESSION_TOKEN = "test-session-token-12345678901234567890123456789012"


@pytest.fixture
def proxy_app():
    """Create a SecurityProxy FastAPI app for testing."""
    os.environ["MYCELOS_PROXY_TOKEN"] = SESSION_TOKEN
    os.environ["MYCELOS_MASTER_KEY"] = "test-master-key"
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_DB_PATH"] = str(Path(tmp) / "test.db")
        from mycelos.security.proxy_server import create_proxy_app
        app = create_proxy_app()
        # Initialize DB for audit writes
        from mycelos.storage.database import SQLiteStorage
        storage = SQLiteStorage(Path(tmp) / "test.db")
        storage.initialize()
        yield app


@pytest.fixture
def client(proxy_app):
    """httpx test client for the proxy."""
    from starlette.testclient import TestClient
    return TestClient(proxy_app)


class TestAuth:
    def test_health_returns_ok(self, client):
        resp = client.get("/health", headers={"Authorization": f"Bearer {SESSION_TOKEN}"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_rejects_missing_token(self, client):
        resp = client.get("/health")
        assert resp.status_code == 401

    def test_rejects_wrong_token(self, client):
        resp = client.get("/health", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401


class TestHttpProxy:
    def test_blocks_private_ip(self, client):
        resp = client.post("/http", json={
            "method": "GET",
            "url": "http://169.254.169.254/latest/meta-data/",
        }, headers={
            "Authorization": f"Bearer {SESSION_TOKEN}",
            "X-User-Id": "default",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == 0
        assert "blocked" in data.get("error", "").lower()

    def test_http_get_with_mock(self, client):
        with patch("mycelos.security.proxy_server.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.headers = {"content-type": "text/html"}
            mock_resp.text = "<html>OK</html>"
            mock_resp.url = "https://example.com"
            mock_httpx.request.return_value = mock_resp
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.RequestError = httpx.RequestError

            resp = client.post("/http", json={
                "method": "GET",
                "url": "https://example.com",
                "timeout": 10,
            }, headers={
                "Authorization": f"Bearer {SESSION_TOKEN}",
                "X-User-Id": "default",
            })
            assert resp.status_code == 200
            assert resp.json()["status"] == 200

    def test_inject_as_custom_header(self, client):
        """Credential injection with header:{name} mode."""
        with patch("mycelos.security.proxy_server.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.headers = {}
            mock_resp.text = "ok"
            mock_resp.url = "https://api.example.com"
            mock_httpx.request.return_value = mock_resp
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.RequestError = httpx.RequestError

            resp = client.post("/http", json={
                "method": "GET",
                "url": "https://api.example.com",
                "inject_credential": "test-service",
                "inject_as": "header:X-Api-Key",
            }, headers={
                "Authorization": f"Bearer {SESSION_TOKEN}",
                "X-User-Id": "default",
            })
            assert resp.status_code == 200

    def test_ssrf_blocked_creates_audit_event(self, client, proxy_app):
        """SSRF block logs proxy.ssrf_blocked audit event."""
        client.post("/http", json={
            "method": "GET",
            "url": "http://169.254.169.254/",
        }, headers={
            "Authorization": f"Bearer {SESSION_TOKEN}",
            "X-User-Id": "default",
        })
        # Verify audit event was written
        from mycelos.storage.database import SQLiteStorage
        db = SQLiteStorage(Path(os.environ["MYCELOS_DB_PATH"]))
        events = db.fetchall(
            "SELECT * FROM audit_events WHERE event_type = 'proxy.ssrf_blocked'"
        )
        assert len(events) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_security_proxy.py -v`
Expected: ImportError

- [ ] **Step 3: Implement proxy_server.py**

Create `src/mycelos/security/proxy_server.py` — FastAPI app with:
- `create_proxy_app()` factory function — reads `MYCELOS_PROXY_TOKEN` and `MYCELOS_PROXY_SOCKET` from env
- Auth middleware: validates `Authorization: Bearer {token}` against `MYCELOS_PROXY_TOKEN`
- `GET /health` — returns `{"status": "ok", "uptime_seconds": N, "mcp_sessions": 0}`
- `POST /http` — accepts `{method, url, headers, body, timeout, inject_credential, inject_as}`, runs SSRF validation (move `_validate_url` from http_tools.py), makes outbound request via httpx, returns `{status, headers, body, url}`
- Credential injection: if `inject_credential` set, look up via `EncryptedCredentialProxy` and inject as `"bearer"` (Authorization header) or `"header:{name}"` (custom header). No query param injection (Constitution Rule 4).
- Audit logging: every request logs to SQLite `audit_events` table with user_id from `X-User-Id` header. Events: `proxy.http_request`, `proxy.ssrf_blocked`, `proxy.auth_failed`, `proxy.started` (at boot)

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_security_proxy.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/security/proxy_server.py tests/test_security_proxy.py
git commit -m "feat: SecurityProxy server — auth, health, HTTP proxy with SSRF filter"
```

---

## Task 2: SecurityProxyClient + Protocol

**Files:**
- Create: `src/mycelos/security/proxy_client.py`
- Modify: `src/mycelos/protocols.py`
- Modify: `tests/test_security_proxy.py`

- [ ] **Step 1: Write failing tests for client**

Add to `tests/test_security_proxy.py`:

```python
class TestProxyClient:
    def test_client_sends_auth_header(self):
        """Client includes Bearer token in every request."""
        from mycelos.security.proxy_client import SecurityProxyClient
        with patch("mycelos.security.proxy_client.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.post.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"status": 200, "body": "ok", "headers": {}, "url": "https://example.com"}),
            )
            mock_cls.return_value = mock_client

            proxy = SecurityProxyClient("/tmp/fake.sock", "my-token")
            proxy.http_get("https://example.com", user_id="stefan")

            # Verify POST /http was called with correct body
            call_args = mock_client.post.call_args
            assert call_args[1]["json"]["url"] == "https://example.com"
            assert call_args[1]["json"]["method"] == "GET"
            assert "stefan" in str(call_args[1].get("headers", {}))

    def test_client_raises_on_connection_failure(self):
        """ProxyUnavailableError on connection failure."""
        from mycelos.security.proxy_client import SecurityProxyClient, ProxyUnavailableError
        with patch("mycelos.security.proxy_client.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.post.side_effect = httpx.ConnectError("refused")
            mock_cls.return_value = mock_client

            proxy = SecurityProxyClient("/tmp/fake.sock", "token")
            with pytest.raises(ProxyUnavailableError):
                proxy.http_get("https://example.com")
```

- [ ] **Step 2: Add SecurityProxyProtocol to protocols.py**

```python
@runtime_checkable
class SecurityProxyProtocol(Protocol):
    def http_get(self, url: str, headers: dict | None = None,
                 credential: str | None = None, timeout: int = 30,
                 user_id: str = "default", agent_id: str | None = None) -> dict: ...
    def http_post(self, url: str, body: Any = None,
                  headers: dict | None = None, credential: str | None = None,
                  timeout: int = 30, user_id: str = "default",
                  agent_id: str | None = None) -> dict: ...
    def mcp_start(self, connector_id: str, command: list[str],
                  env_vars: dict, transport: str = "stdio",
                  user_id: str = "default") -> dict: ...
    def mcp_call(self, session_id: str, tool: str, arguments: dict,
                 user_id: str = "default", agent_id: str | None = None) -> dict: ...
    def mcp_stop(self, session_id: str) -> None: ...
    def llm_complete(self, model: str, messages: list[dict],
                     tools: list[dict] | None = None, stream: bool = False,
                     user_id: str = "default", agent_id: str | None = None,
                     purpose: str = "chat") -> Any: ...
    def health(self) -> dict: ...
```

- [ ] **Step 3: Implement SecurityProxyClient**

Create `src/mycelos/security/proxy_client.py`:
- `SecurityProxyClient(socket_path, token)` — creates `httpx.Client` with `httpx.HTTPTransport(uds=socket_path)`
- All methods are synchronous
- Each method sets `Authorization`, `X-User-Id`, `X-Agent-Id` headers
- `http_get/post` → `POST /http` with method field
- `llm_complete` with `stream=True` → uses `client.stream()` and returns iterator
- `ProxyUnavailableError` exception for connection failures

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_security_proxy.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/security/proxy_client.py src/mycelos/protocols.py tests/test_security_proxy.py
git commit -m "feat: SecurityProxyClient + SecurityProxyProtocol"
```

---

## Task 3: Proxy Launcher — Fork, Health Check, Auto-Restart

**Files:**
- Create: `src/mycelos/security/proxy_launcher.py`
- Modify: `tests/test_security_proxy.py`

- [ ] **Step 1: Write failing tests**

```python
class TestProxyLauncher:
    def test_generate_session_token(self):
        from mycelos.security.proxy_launcher import generate_session_token
        token = generate_session_token()
        assert len(token) == 64  # 32 bytes hex

    def test_create_socket_path(self):
        from mycelos.security.proxy_launcher import create_socket_dir
        sock_dir = create_socket_dir()
        assert Path(sock_dir).exists()
        # Cleanup
        import shutil
        shutil.rmtree(sock_dir)

    def test_launcher_lifecycle(self):
        from mycelos.security.proxy_launcher import ProxyLauncher
        launcher = ProxyLauncher.__new__(ProxyLauncher)
        assert launcher  # Basic instantiation
```

- [ ] **Step 2: Implement proxy_launcher.py**

Create `src/mycelos/security/proxy_launcher.py`:
- `generate_session_token()` → `secrets.token_hex(32)`
- `create_socket_dir()` → `tempfile.mkdtemp(prefix="mycelos-sec-")` with `chmod 700`
- `ProxyLauncher` class:
  - `__init__(self, data_dir, master_key)` — stores config
  - `start()` → generates session token, creates socket dir (`tempfile.mkdtemp`, `chmod 700`), forks child process via `multiprocessing.Process`, passes env vars (`MYCELOS_MASTER_KEY`, `MYCELOS_PROXY_TOKEN`, `MYCELOS_PROXY_SOCKET`, `MYCELOS_DB_PATH`), polls `/health` (max 5s)
  - `stop()` → sends SIGTERM, waits, cleans up socket dir
  - `restart()` → stop + start, increments restart counter
  - `ensure_alive()` → checks if process is alive, auto-restarts if dead (max 3), logs `proxy.degraded_mode` audit event after 3 failures
  - `is_running` property
  - `socket_path` property
  - `session_token` property
- Child process target: reads `MYCELOS_PROXY_SOCKET` from env, runs `uvicorn.run(create_proxy_app(), uds=socket_path)`, sets `chmod 600` on socket file after creation
- After fork: launcher clears `MYCELOS_MASTER_KEY` from `os.environ` in the parent (Gateway) process

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_security_proxy.py::TestProxyLauncher -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add src/mycelos/security/proxy_launcher.py tests/test_security_proxy.py
git commit -m "feat: ProxyLauncher — fork, health check, auto-restart"
```

---

## Task 4: MCP + LLM Endpoints on Proxy Server

**Files:**
- Modify: `src/mycelos/security/proxy_server.py`
- Modify: `tests/test_security_proxy.py`

- [ ] **Step 1: Write failing tests for MCP endpoints**

```python
class TestMcpProxy:
    def test_mcp_start_returns_session(self, client):
        """MCP start creates a session (mocked)."""
        with patch("mycelos.security.proxy_server.MCPConnectorManager") as mock_mgr:
            mock_mgr.return_value.connect.return_value = [
                {"name": "list_issues", "description": "List issues"}
            ]
            resp = client.post("/mcp/start", json={
                "connector_id": "github",
                "command": ["echo", "test"],
                "env_vars": {},
                "transport": "stdio",
            }, headers={
                "Authorization": f"Bearer {SESSION_TOKEN}",
                "X-User-Id": "default",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "session_id" in data
            assert "tools" in data

    def test_mcp_stop_returns_ok(self, client):
        resp = client.post("/mcp/stop", json={
            "session_id": "nonexistent",
        }, headers={
            "Authorization": f"Bearer {SESSION_TOKEN}",
            "X-User-Id": "default",
        })
        assert resp.status_code == 200
```

- [ ] **Step 2: Write failing tests for LLM endpoint**

```python
class TestLlmProxy:
    def test_llm_complete_non_streaming(self, client):
        with patch("mycelos.security.proxy_server.litellm") as mock_llm:
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock(message=MagicMock(content="Hello", tool_calls=None))]
            mock_resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
            mock_llm.completion.return_value = mock_resp

            resp = client.post("/llm/complete", json={
                "model": "anthropic/claude-haiku-4-5",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            }, headers={
                "Authorization": f"Bearer {SESSION_TOKEN}",
                "X-User-Id": "default",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "content" in data
            assert data["usage"]["total_tokens"] == 15
```

- [ ] **Step 2b: Write failing test for streaming LLM**

```python
    def test_llm_complete_streaming(self, client):
        with patch("mycelos.security.proxy_server.litellm") as mock_llm:
            # Mock streaming response
            chunks = [
                MagicMock(choices=[MagicMock(delta=MagicMock(content="Hel", tool_calls=None))]),
                MagicMock(choices=[MagicMock(delta=MagicMock(content="lo!", tool_calls=None))]),
            ]
            mock_llm.completion.return_value = iter(chunks)

            resp = client.post("/llm/complete", json={
                "model": "anthropic/claude-haiku-4-5",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            }, headers={
                "Authorization": f"Bearer {SESSION_TOKEN}",
                "X-User-Id": "default",
            })
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")
            body = resp.text
            assert "Hel" in body
            assert "lo!" in body
```

- [ ] **Step 3: Implement MCP endpoints on proxy_server.py**

Add to proxy_server.py:
- `POST /mcp/start` — creates MCPConnectorManager session inside proxy, resolves credentials from env_vars, returns session_id + tools
- `POST /mcp/call` — calls tool on existing session, returns result
- `POST /mcp/stop` — stops session, returns `{"ok": true}`
- MCP sessions stored in `_mcp_sessions: dict[str, MCPConnectorManager]`

- [ ] **Step 4: Implement LLM endpoint on proxy_server.py**

Add to proxy_server.py:
- `POST /llm/complete` — resolves API key via credential proxy, calls litellm.completion()
- Non-streaming: returns JSON with content + usage + cost
- Streaming: returns `StreamingResponse` with `text/event-stream`, forwards SSE chunks, appends usage event at end
- Logs to `llm_usage` table after completion

- [ ] **Step 5: Implement credential bootstrap endpoint**

Add to proxy_server.py:
- `POST /credential/bootstrap` — returns credential value, only within first 10s of startup, each credential only once

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_security_proxy.py -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/security/proxy_server.py tests/test_security_proxy.py
git commit -m "feat: SecurityProxy MCP + LLM + credential bootstrap endpoints"
```

---

## Task 5: Wire Gateway to Use SecurityProxy

**Files:**
- Modify: `src/mycelos/app.py`
- Modify: `src/mycelos/gateway/server.py`
- Modify: `src/mycelos/connectors/http_tools.py`
- Modify: `src/mycelos/channels/telegram.py`

- [ ] **Step 1: Modify App container**

In `src/mycelos/app.py`:
- Add `_proxy_client` field to `__init__` (None)
- Add `proxy_client` lazy property (set externally by gateway)
- Add `set_proxy_client(client)` method
- Remove `load_all_credentials_to_env()` and `load_credential_to_env()` methods — proxy handles all credential injection now
- Keep `credentials` property but only for CLI commands that don't start the proxy (e.g., `mycelos init`, `mycelos db`). Add comment: "# Only for CLI mode — Gateway uses proxy_client"

- [ ] **Step 2: Modify gateway/server.py startup**

In `create_app()`:
- Before starting FastAPI: create `ProxyLauncher`, start proxy
- Create `SecurityProxyClient` with socket path and token
- Set `mycelos.set_proxy_client(proxy_client)`
- `MYCELOS_MASTER_KEY` is already cleared by ProxyLauncher.start()
- MCP connectors: replace `_start_mcp_connectors()` to use `proxy_client.mcp_start()` instead of `mcp_manager.connect_recipe()`. Remove `MCPConnectorManager` usage from Gateway entirely — MCP sessions now live inside the proxy.
- Telegram: use `proxy_client` to bootstrap bot token via `POST /credential/bootstrap`
- Store `proxy_launcher` on `api.state` for shutdown
- Register shutdown hook to stop proxy
- Start a daemon health monitor thread that calls `launcher.ensure_alive()` every 10s. On proxy restart, re-issue `proxy_client.mcp_start()` for each active connector to restore MCP sessions.

- [ ] **Step 3: Modify http_tools.py to delegate to proxy**

In `src/mycelos/connectors/http_tools.py`:
- `http_get()` and `http_post()` check if `_proxy_client` is set
- If set: delegate to `proxy_client.http_get/post()`
- If not set (CLI mode): use direct httpx with SSRF filter (existing code)
- Module-level `_proxy_client: SecurityProxyClient | None = None`
- `set_proxy_client(client)` function to inject it

- [ ] **Step 4: Modify telegram.py to use bootstrapped token**

In `src/mycelos/channels/telegram.py`:
- `setup_telegram()` already accepts `bot_token` as parameter
- No change needed in telegram.py itself — the change is in gateway/server.py where the token is obtained via `proxy_client` bootstrap instead of `credentials.get_credential()`

- [ ] **Step 5: Run all tests**

Run: `pytest tests/ --tb=short -q`
Expected: All pass (existing tests may need fixture updates for proxy_client)

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/app.py src/mycelos/gateway/server.py src/mycelos/connectors/http_tools.py
git commit -m "feat: Gateway uses SecurityProxy for all external access"
```

---

## Task 6: Wire LLM Broker Through Proxy

**Files:**
- Modify: `src/mycelos/llm/broker.py`
- Modify: `tests/test_security_proxy.py`

- [ ] **Step 1: Write test for proxied LLM call**

```python
class TestLlmBrokerProxy:
    def test_broker_delegates_to_proxy(self):
        """LiteLLMBroker uses proxy_client when available."""
        from mycelos.llm.broker import LiteLLMBroker
        mock_proxy = MagicMock()
        mock_proxy.llm_complete.return_value = {
            "content": "Hello",
            "tool_calls": None,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "anthropic/claude-haiku-4-5",
        }
        broker = LiteLLMBroker(default_model="anthropic/claude-haiku-4-5",
                                proxy_client=mock_proxy)
        result = broker.complete([{"role": "user", "content": "hi"}])
        assert result.content == "Hello"
        mock_proxy.llm_complete.assert_called_once()
```

- [ ] **Step 2: Modify LiteLLMBroker**

In `src/mycelos/llm/broker.py`:
- Add `proxy_client` parameter to `__init__` (optional, default None)
- In `complete()`: if `self._proxy_client` is set, delegate to `proxy_client.llm_complete(model, messages, tools, stream=False)`
- In `complete_stream()`: if `self._proxy_client` is set, delegate to `proxy_client.llm_complete(model, messages, tools, stream=True)` and yield chunks
- If proxy_client is None: use existing litellm direct call (for CLI/testing)

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_security_proxy.py tests/test_llm_broker.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add src/mycelos/llm/broker.py tests/test_security_proxy.py
git commit -m "feat: LiteLLMBroker delegates to SecurityProxy when available"
```

---

## Task 7: Security Invariant Tests

**Files:**
- Create: `tests/security/test_proxy_isolation.py`

- [ ] **Step 1: Write security tests**

```python
"""Security tests for SecurityProxy isolation.

Verifies that the Gateway cannot access credentials directly
and that the proxy enforces authentication.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from mycelos.security.proxy_server import create_proxy_app
from starlette.testclient import TestClient


SESSION_TOKEN = "sec-test-token-" + "x" * 48


@pytest.fixture
def proxy_client():
    os.environ["MYCELOS_PROXY_TOKEN"] = SESSION_TOKEN
    os.environ["MYCELOS_MASTER_KEY"] = "test-key"
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_DB_PATH"] = str(Path(tmp) / "test.db")
        from mycelos.storage.database import SQLiteStorage
        SQLiteStorage(Path(tmp) / "test.db").initialize()
        app = create_proxy_app()
        yield TestClient(app)


class TestProxyAuthEnforcement:
    def test_rejects_no_auth(self, proxy_client):
        assert proxy_client.get("/health").status_code == 401

    def test_rejects_empty_bearer(self, proxy_client):
        resp = proxy_client.get("/health", headers={"Authorization": "Bearer "})
        assert resp.status_code == 401

    def test_rejects_wrong_scheme(self, proxy_client):
        resp = proxy_client.get("/health", headers={"Authorization": f"Basic {SESSION_TOKEN}"})
        assert resp.status_code == 401

    def test_accepts_correct_token(self, proxy_client):
        resp = proxy_client.get("/health", headers={"Authorization": f"Bearer {SESSION_TOKEN}"})
        assert resp.status_code == 200


class TestSsrfProtection:
    """SSRF tests that run through the proxy (not just _validate_url)."""

    def test_blocks_localhost(self, proxy_client):
        resp = proxy_client.post("/http", json={
            "method": "GET", "url": "http://127.0.0.1/admin",
        }, headers={"Authorization": f"Bearer {SESSION_TOKEN}", "X-User-Id": "test"})
        assert resp.json()["status"] == 0

    def test_blocks_metadata(self, proxy_client):
        resp = proxy_client.post("/http", json={
            "method": "GET", "url": "http://169.254.169.254/latest/",
        }, headers={"Authorization": f"Bearer {SESSION_TOKEN}", "X-User-Id": "test"})
        assert resp.json()["status"] == 0


class TestCredentialIsolation:
    def test_no_credential_in_http_response(self, proxy_client):
        """Credential values never appear in proxy HTTP responses."""
        resp = proxy_client.post("/http", json={
            "method": "GET",
            "url": "https://example.com",
            "inject_credential": "nonexistent",
        }, headers={"Authorization": f"Bearer {SESSION_TOKEN}", "X-User-Id": "test"})
        data = resp.json()
        # Should fail gracefully, not expose any internal state
        assert "MYCELOS_MASTER_KEY" not in str(data)
        assert "test-key" not in str(data)


class TestBootstrapWindow:
    def test_bootstrap_within_window(self, proxy_client):
        """Bootstrap endpoint works within the startup window."""
        resp = proxy_client.post("/credential/bootstrap", json={
            "service": "telegram",
        }, headers={"Authorization": f"Bearer {SESSION_TOKEN}"})
        # May return 404 (credential not found) or 200, but not 403 within window
        assert resp.status_code != 403

    def test_bootstrap_same_credential_twice_rejected(self, proxy_client):
        """Same credential cannot be bootstrapped twice."""
        proxy_client.post("/credential/bootstrap", json={
            "service": "telegram",
        }, headers={"Authorization": f"Bearer {SESSION_TOKEN}"})
        resp = proxy_client.post("/credential/bootstrap", json={
            "service": "telegram",
        }, headers={"Authorization": f"Bearer {SESSION_TOKEN}"})
        # Second call should be rejected (403 or error)
        assert resp.status_code in (403, 409)
```

- [ ] **Step 2: Run security tests**

Run: `pytest tests/security/test_proxy_isolation.py -v`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add tests/security/test_proxy_isolation.py
git commit -m "test: security invariants for SecurityProxy isolation"
```

---

## Task 8: E2E Integration Test + Cleanup

**Files:**
- Create: `tests/e2e/test_proxy_integration.py`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Write E2E test**

```python
"""E2E test: Gateway starts SecurityProxy, makes calls through it."""

import os
import tempfile
import time
from pathlib import Path

import pytest

from mycelos.security.proxy_launcher import ProxyLauncher
from mycelos.security.proxy_client import SecurityProxyClient


@pytest.fixture
def proxy_system():
    """Start a real proxy process for integration testing."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "e2e-test-key"

        # Initialize DB
        from mycelos.storage.database import SQLiteStorage
        storage = SQLiteStorage(data_dir / "mycelos.db")
        storage.initialize()

        launcher = ProxyLauncher(data_dir, "e2e-test-key")
        launcher.start()

        client = SecurityProxyClient(launcher.socket_path, launcher.session_token)
        yield launcher, client

        launcher.stop()


class TestProxyE2E:
    def test_health_through_socket(self, proxy_system):
        launcher, client = proxy_system
        health = client.health()
        assert health["status"] == "ok"

    def test_http_blocked_ssrf(self, proxy_system):
        _, client = proxy_system
        result = client.http_get("http://127.0.0.1:8080/secret")
        assert result["status"] == 0
        assert "blocked" in result.get("error", "").lower()

    def test_master_key_not_in_gateway_env(self, proxy_system):
        """After proxy starts, master key should be clearable from gateway."""
        launcher, _ = proxy_system
        # Simulate clearing (gateway would do this)
        if "MYCELOS_MASTER_KEY" in os.environ:
            del os.environ["MYCELOS_MASTER_KEY"]
        # Proxy should still work
        _, client = proxy_system
        health = client.health()
        assert health["status"] == "ok"

    def test_proxy_restart_recovery(self, proxy_system):
        """Proxy crash + restart recovers cleanly."""
        launcher, client = proxy_system
        # Kill proxy
        launcher._process.terminate()
        launcher._process.join(timeout=2)
        assert not launcher.is_running
        # Restart
        launcher.restart()
        health = client.health()
        assert health["status"] == "ok"

    def test_degraded_mode_after_max_restarts(self, proxy_system):
        """After 3 failed restarts, ensure_alive stops trying."""
        launcher, _ = proxy_system
        launcher._restart_count = 3
        launcher._process.terminate()
        launcher._process.join(timeout=2)
        # Should not restart — already at max
        launcher.ensure_alive()
        assert not launcher.is_running
```

- [ ] **Step 2: Run all tests**

Run: `pytest tests/ --tb=short -q`
Expected: All pass

- [ ] **Step 3: Update CHANGELOG.md**

Add SecurityProxy entry under today's date.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_proxy_integration.py CHANGELOG.md
git commit -m "feat: SecurityProxy — process isolation for credentials and network access"
```

---

## Notes

### Backward Compatibility

- CLI commands (`mycelos chat`, `mycelos init`, `mycelos db`) still work without the proxy — they use `EncryptedCredentialProxy` directly
- Only `mycelos gateway` starts the proxy
- `LiteLLMBroker` and `http_tools` have dual paths: proxy if available, direct if not

### What Moves to the Proxy Process

- `EncryptedCredentialProxy` (master key + decrypt)
- `MCPConnectorManager` (subprocess management)
- `litellm` (LLM API calls)
- SSRF filter (`_validate_url`)
- `httpx` outbound calls

### What Stays in the Gateway

- `CapabilityTokenManager` + `PolicyEngine` (authorization)
- `ChatService` + `Orchestrator` (business logic)
- `BackgroundTaskRunner` + Huey (scheduling)
- SQLite storage (local)
- Filesystem access (Mounts)
