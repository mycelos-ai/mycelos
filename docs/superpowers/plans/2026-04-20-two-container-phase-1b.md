# Two-Container Deployment — Phase 1b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the security separation started in Phase 1a: move credential writes out of the gateway into the proxy, lock the gateway container off the `default` Docker network, and route every remaining outbound call through the proxy. After this, the gateway container has no master key in memory and no route to the public internet — prompt injection and RCE in the gateway are contained to its own filesystem and its own Docker network.

**Architecture:** Build on Phase 1a's existing split. The proxy gets four new endpoints for credential CRUD; `SecurityProxyClient` gets matching methods; the gateway's `app.credentials` becomes a thin wrapper that delegates to the proxy in two-container mode. Separately, the handful of remaining direct `httpx` calls in gateway tools (`search_tools`, `github_tools`, `mcp_search`, release check, LiteLLM cost-map fetch, Telegram polling) route through `proxy_client.http_get/post`. Finally, `docker-compose.yml` drops the gateway's `default` network membership and a new E2E test asserts `curl google.com` from the gateway container fails.

**Tech Stack:** Same as Phase 1a — FastAPI, httpx, cryptography, pytest, Docker Compose.

---

## Non-goals (explicitly out of scope)

- Schema-level credential split into a separate `proxy.db`. Phase 1b keeps both containers on one `mycelos.db`; the proxy gets write access to the `credentials` table (file-level SQLite lock + WAL + busy_timeout handles concurrency). A dedicated proxy DB lands in Phase 1.5 if it becomes necessary.
- Passkey auth, public exposure, Caddy sidecar. All of that is Phase 2.
- mTLS between gateway and proxy. Phase 3.
- Changes to the single-container (`--role all`) mode. Legacy fork path must stay unchanged throughout this phase.

---

## File Structure

### New files

- `tests/test_proxy_credential_endpoints.py` — pytest tests that boot a proxy FastAPI app and hit the four new endpoints with a TestClient.
- `tests/test_proxy_client_credentials.py` — tests the new `SecurityProxyClient` methods.
- `tests/test_credentials_delegating_wrapper.py` — tests the gateway-side `DelegatingCredentialProxy` that forwards writes to the proxy.

### Modified files

- `src/mycelos/security/proxy_server.py` — add `POST /credential/store`, `DELETE /credential/{service}/{label}`, `GET /credential/list`, `POST /credential/rotate`. Re-use existing auth helper `_check_auth`. Open storage read-write (today it's read-only). Keep `/credential/bootstrap` unchanged (it serves running LLM calls; separate purpose).
- `src/mycelos/security/proxy_client.py` — add `credential_store(service, payload, label="default", description=None)`, `credential_delete(service, label)`, `credential_list()`, `credential_rotate(service, label)`.
- `src/mycelos/security/credentials.py` — new `DelegatingCredentialProxy` class with the same public interface as `EncryptedCredentialProxy` but forwarding write ops to a `SecurityProxyClient`. `get_credential` / `list_credentials` (metadata only — no decrypt) still hit the DB directly so non-sensitive UI reads don't roundtrip through the proxy.
- `src/mycelos/app.py` — `app.credentials` property detects `MYCELOS_PROXY_URL` and returns `DelegatingCredentialProxy(storage, proxy_client)` instead of `EncryptedCredentialProxy(storage, master_key, …)`. Master-key env var no longer read by the gateway when running in two-container mode.
- `src/mycelos/gateway/server.py` — in the external-proxy branch (added in Task 4 of Phase 1a), stop eagerly reading the master key. The master key line at lines ~360-364 is gated behind "no proxy URL set".
- `src/mycelos/connectors/search_tools.py` — replace direct `httpx.get(...)` with `_proxy_client.http_get(...)` when proxy is wired.
- `src/mycelos/connectors/github_tools.py` — same pattern for the two `httpx` calls.
- `src/mycelos/connectors/mcp_search.py` — same pattern.
- `src/mycelos/llm/model_registry.py` — the LiteLLM cost-map fetch at line 300 routes via proxy.
- `src/mycelos/agents/handlers/model_updater_handler.py` — release-check at line 130 routes via proxy.
- `src/mycelos/chat/slash_commands.py` — Telegram `getMe`/`getUpdates` calls route via proxy (or explicit opt-in to direct for interactive tests).
- `docker-compose.yml` — gateway drops `default` network. Gateway service's `volumes` stop bind-mounting `mycelos.db` separately (it's in the data dir); proxy's DB mount switches from `read_only: true` to `read_only: false` so the proxy can write the credentials table.
- `tests/test_compose_structure.py` — new test: gateway service is NOT on `default` network, proxy's `mycelos.db` mount is NOT `read_only`.
- `tests/e2e/test_two_container_deployment.sh` — append a new assertion: `docker compose exec gateway curl -m 3 https://example.com` MUST fail with a connection error.

### Deleted files

None.

---

## Task 1: Proxy `/credential/*` endpoints (store, delete, list, rotate)

**Files:**
- Modify: `src/mycelos/security/proxy_server.py`
- Test: `tests/test_proxy_credential_endpoints.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_proxy_credential_endpoints.py`:

```python
"""Proxy /credential/{store,delete,list,rotate} endpoints.

Phase 1b: the proxy is the only process that writes credentials. Tests
boot an isolated proxy FastAPI app in-process with a temp DB and master
key, then exercise the four new endpoints through a TestClient.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def proxy_app(tmp_path: Path, monkeypatch):
    """Boot a proxy FastAPI app with a fresh storage + master key."""
    from mycelos.app import App
    from mycelos.security.proxy_server import create_proxy_app

    monkeypatch.setenv("MYCELOS_MASTER_KEY", "phase-1b-test-key-" + "x" * 16)
    monkeypatch.setenv("MYCELOS_PROXY_TOKEN", "test-token")
    monkeypatch.setenv("MYCELOS_DB_PATH", str(tmp_path / "mycelos.db"))

    # App.initialize creates the DB schema the proxy will write into.
    app = App(tmp_path)
    app.initialize()

    proxy = create_proxy_app()
    client = TestClient(proxy)
    client.headers.update({"Authorization": "Bearer test-token"})
    return client


def test_credential_store_persists(proxy_app):
    resp = proxy_app.post(
        "/credential/store",
        json={
            "service": "anthropic",
            "label": "default",
            "payload": {"api_key": "sk-ant-test", "provider": "anthropic"},
            "description": "unit test",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "stored", "service": "anthropic", "label": "default"}

    # Subsequent list returns metadata (no plaintext key)
    lst = proxy_app.get("/credential/list")
    assert lst.status_code == 200
    services = {(e["service"], e["label"]) for e in lst.json()["credentials"]}
    assert ("anthropic", "default") in services
    for entry in lst.json()["credentials"]:
        assert "api_key" not in entry
        assert "encrypted" not in entry


def test_credential_delete(proxy_app):
    proxy_app.post(
        "/credential/store",
        json={"service": "foo", "label": "default", "payload": {"api_key": "x"}},
    )
    resp = proxy_app.delete("/credential/foo/default")
    assert resp.status_code == 200
    # List no longer contains it
    lst = proxy_app.get("/credential/list")
    services = {(e["service"], e["label"]) for e in lst.json()["credentials"]}
    assert ("foo", "default") not in services


def test_credential_store_requires_auth(proxy_app):
    proxy_app.headers.pop("Authorization", None)
    resp = proxy_app.post(
        "/credential/store",
        json={"service": "x", "label": "default", "payload": {"api_key": "y"}},
    )
    assert resp.status_code == 401


def test_credential_rotate_marks_row(proxy_app):
    proxy_app.post(
        "/credential/store",
        json={"service": "bar", "label": "default", "payload": {"api_key": "z"}},
    )
    resp = proxy_app.post("/credential/rotate", json={"service": "bar", "label": "default"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "rotated"
```

- [ ] **Step 2: Run tests — expect 4 failures** (`/credential/store` etc. don't exist yet).

- [ ] **Step 3: Add endpoints**

In `src/mycelos/security/proxy_server.py`, locate `_get_storage`. Today it returns a **read-only** SQLite connection. Change it so the connection is opened in read-write mode when we also need to write credentials:

```python
def _get_storage(read_only: bool = False) -> Any | None:
    ...
    if not read_only:
        conn = _sql3.connect(db_path_str, timeout=5)
    else:
        conn = _sql3.connect(f"file:{db_path_str}?mode=ro", uri=True, timeout=5)
    ...
```

Update the existing read callers to pass `read_only=True`; leave new credential-write endpoints on the default RW.

Then add the four endpoints. Re-use the same `_check_auth` helper every other endpoint uses. Reuse the existing `EncryptedCredentialProxy` from `mycelos.security.credentials` — it already does the encrypt/decrypt dance; we just need to instantiate it with the RW storage:

```python
@app.post("/credential/store")
async def credential_store(request: Request) -> JSONResponse:
    authorized, user_id = _check_auth(request)
    if not authorized:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    body = await request.json()
    service = body.get("service", "").strip()
    label = body.get("label", "default").strip() or "default"
    payload = body.get("payload")
    description = body.get("description")
    if not service or not isinstance(payload, dict) or not payload:
        return JSONResponse({"error": "service + payload dict required"}, status_code=400)
    from mycelos.security.credentials import EncryptedCredentialProxy
    storage = _get_storage(read_only=False)
    if storage is None:
        return JSONResponse({"error": "storage unavailable"}, status_code=500)
    key = EncryptedCredentialProxy(storage, master_key)
    key.store_credential(service, payload, user_id=user_id, label=label, description=description)
    return JSONResponse({"status": "stored", "service": service, "label": label})


@app.delete("/credential/{service}/{label}")
async def credential_delete(service: str, label: str, request: Request) -> JSONResponse:
    authorized, user_id = _check_auth(request)
    if not authorized:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    storage = _get_storage(read_only=False)
    if storage is None:
        return JSONResponse({"error": "storage unavailable"}, status_code=500)
    from mycelos.security.credentials import EncryptedCredentialProxy
    key = EncryptedCredentialProxy(storage, master_key)
    key.delete_credential(service, user_id=user_id, label=label)
    return JSONResponse({"status": "deleted", "service": service, "label": label})


@app.get("/credential/list")
async def credential_list(request: Request) -> JSONResponse:
    authorized, user_id = _check_auth(request)
    if not authorized:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    storage = _get_storage(read_only=False)
    if storage is None:
        return JSONResponse({"credentials": []})
    from mycelos.security.credentials import EncryptedCredentialProxy
    key = EncryptedCredentialProxy(storage, master_key)
    # list_credentials returns only non-sensitive columns by contract.
    items = key.list_credentials(user_id=user_id)
    return JSONResponse({"credentials": items})


@app.post("/credential/rotate")
async def credential_rotate(request: Request) -> JSONResponse:
    authorized, user_id = _check_auth(request)
    if not authorized:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    body = await request.json()
    service = body.get("service", "").strip()
    label = body.get("label", "default").strip() or "default"
    if not service:
        return JSONResponse({"error": "service required"}, status_code=400)
    storage = _get_storage(read_only=False)
    if storage is None:
        return JSONResponse({"error": "storage unavailable"}, status_code=500)
    from mycelos.security.credentials import EncryptedCredentialProxy
    key = EncryptedCredentialProxy(storage, master_key)
    key.mark_security_rotated(service, user_id=user_id, label=label)
    return JSONResponse({"status": "rotated", "service": service, "label": label})
```

- [ ] **Step 4: Run the new test suite** — 4 passed.

- [ ] **Step 5: Full regression**

```bash
pytest tests/security/ tests/test_security_proxy.py tests/test_app_credentials.py -v --timeout=30
```

All must stay green — the existing `/credential/bootstrap` path is unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/security/proxy_server.py tests/test_proxy_credential_endpoints.py
git commit -m "Proxy: credential/{store,delete,list,rotate} endpoints"
```

---

## Task 2: `SecurityProxyClient` credential methods

**Files:**
- Modify: `src/mycelos/security/proxy_client.py`
- Test: `tests/test_proxy_client_credentials.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_proxy_client_credentials.py`:

```python
"""SecurityProxyClient credential_* methods — gateway side of the RPC."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from mycelos.security.proxy_client import SecurityProxyClient


def test_credential_store_posts_body(monkeypatch):
    c = SecurityProxyClient(url="http://proxy:9110", token="t")
    called = {}
    def fake_request(method, path, **kwargs):
        called["method"] = method
        called["path"] = path
        called["json"] = kwargs.get("json")
        resp = MagicMock(); resp.status_code = 200
        resp.json = lambda: {"status": "stored", "service": "x", "label": "default"}
        return resp
    monkeypatch.setattr(c, "_request", fake_request)
    c.credential_store("x", {"api_key": "abc"}, label="default", description="unit")
    assert called["method"] == "POST"
    assert called["path"] == "/credential/store"
    assert called["json"] == {
        "service": "x",
        "label": "default",
        "payload": {"api_key": "abc"},
        "description": "unit",
    }


def test_credential_delete_uses_url_params(monkeypatch):
    c = SecurityProxyClient(url="http://proxy:9110", token="t")
    called = {}
    def fake_request(method, path, **kwargs):
        called["method"] = method
        called["path"] = path
        resp = MagicMock(); resp.status_code = 200
        resp.json = lambda: {"status": "deleted"}
        return resp
    monkeypatch.setattr(c, "_request", fake_request)
    c.credential_delete("anthropic", "default")
    assert called["method"] == "DELETE"
    assert called["path"] == "/credential/anthropic/default"


def test_credential_list_returns_items(monkeypatch):
    c = SecurityProxyClient(url="http://proxy:9110", token="t")
    def fake_request(method, path, **kwargs):
        resp = MagicMock(); resp.status_code = 200
        resp.json = lambda: {"credentials": [
            {"service": "anthropic", "label": "default", "description": None,
             "created_at": "2026-04-20T10:00:00Z"},
        ]}
        return resp
    monkeypatch.setattr(c, "_request", fake_request)
    items = c.credential_list()
    assert len(items) == 1
    assert items[0]["service"] == "anthropic"


def test_credential_rotate_posts_json(monkeypatch):
    c = SecurityProxyClient(url="http://proxy:9110", token="t")
    called = {}
    def fake_request(method, path, **kwargs):
        called["json"] = kwargs.get("json")
        resp = MagicMock(); resp.status_code = 200
        resp.json = lambda: {"status": "rotated"}
        return resp
    monkeypatch.setattr(c, "_request", fake_request)
    c.credential_rotate("slack", "default")
    assert called["json"] == {"service": "slack", "label": "default"}
```

- [ ] **Step 2: Run — 4 failures** (methods don't exist yet).

- [ ] **Step 3: Implement methods in `proxy_client.py`**

Append to the class:

```python
def credential_store(self, service: str, payload: dict, *,
                     label: str = "default", description: str | None = None) -> dict:
    resp = self._request(
        "POST",
        "/credential/store",
        json={"service": service, "label": label, "payload": payload, "description": description},
    )
    return resp.json() if hasattr(resp, "json") else {}

def credential_delete(self, service: str, label: str = "default") -> dict:
    resp = self._request("DELETE", f"/credential/{service}/{label}")
    return resp.json() if hasattr(resp, "json") else {}

def credential_list(self) -> list[dict]:
    resp = self._request("GET", "/credential/list")
    return resp.json().get("credentials", []) if hasattr(resp, "json") else []

def credential_rotate(self, service: str, label: str = "default") -> dict:
    resp = self._request(
        "POST",
        "/credential/rotate",
        json={"service": service, "label": label},
    )
    return resp.json() if hasattr(resp, "json") else {}
```

- [ ] **Step 4: Run tests — 4 passed.**

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/security/proxy_client.py tests/test_proxy_client_credentials.py
git commit -m "SecurityProxyClient: credential_{store,delete,list,rotate}"
```

---

## Task 3: `DelegatingCredentialProxy` for gateway-side

**Files:**
- Modify: `src/mycelos/security/credentials.py`
- Test: `tests/test_credentials_delegating_wrapper.py`

- [ ] **Step 1: Failing tests**

Create `tests/test_credentials_delegating_wrapper.py`:

```python
"""DelegatingCredentialProxy: gateway's thin wrapper when the proxy owns writes."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mycelos.security.credentials import DelegatingCredentialProxy


def test_store_delegates_to_proxy():
    client = MagicMock()
    client.credential_store.return_value = {"status": "stored"}
    storage = MagicMock()
    wrapper = DelegatingCredentialProxy(storage=storage, proxy_client=client)
    wrapper.store_credential("x", {"api_key": "k"}, user_id="default", label="default")
    client.credential_store.assert_called_once_with(
        "x", {"api_key": "k"}, label="default", description=None,
    )


def test_delete_delegates_to_proxy():
    client = MagicMock()
    storage = MagicMock()
    wrapper = DelegatingCredentialProxy(storage=storage, proxy_client=client)
    wrapper.delete_credential("x", user_id="default", label="default")
    client.credential_delete.assert_called_once_with("x", "default")


def test_list_delegates_to_proxy():
    client = MagicMock()
    client.credential_list.return_value = [{"service": "x", "label": "default"}]
    storage = MagicMock()
    wrapper = DelegatingCredentialProxy(storage=storage, proxy_client=client)
    assert wrapper.list_credentials(user_id="default") == [{"service": "x", "label": "default"}]


def test_get_credential_is_not_delegated_in_wrapper(tmp_path):
    """get_credential is a READ path. For Phase 1b it still hits the DB
    (metadata + decrypt via local EncryptedCredentialProxy) — only writes go
    through the proxy. If a future phase moves reads too, this test guards
    the boundary."""
    # Placeholder: if get_credential is intentionally out of scope for the
    # wrapper, assert the wrapper raises NotImplementedError so callers see
    # the boundary clearly.
    client = MagicMock()
    storage = MagicMock()
    wrapper = DelegatingCredentialProxy(storage=storage, proxy_client=client)
    with pytest.raises(NotImplementedError):
        wrapper.get_credential("x", user_id="default")


def test_mark_security_rotated_delegates():
    client = MagicMock()
    storage = MagicMock()
    wrapper = DelegatingCredentialProxy(storage=storage, proxy_client=client)
    wrapper.mark_security_rotated("x", user_id="default", label="default")
    client.credential_rotate.assert_called_once_with("x", "default")
```

- [ ] **Step 2: Run — 5 failures** (class doesn't exist yet).

- [ ] **Step 3: Implement `DelegatingCredentialProxy`**

In `src/mycelos/security/credentials.py`, after `EncryptedCredentialProxy`:

```python
class DelegatingCredentialProxy:
    """Gateway-side credential proxy for two-container mode.

    Forwards every WRITE operation (store/delete/rotate) to the SecurityProxy
    over the shared bearer-token TCP channel. The master key does not exist
    in this process.

    READ operations (get_credential) intentionally raise NotImplementedError:
    the only place a gateway-side caller needs a plaintext credential today
    is inside the LLM broker or MCP manager, and both of those paths already
    go through the proxy (mcp_start, llm_complete) — not through get_credential.
    If a new caller needs the plaintext, route it through the proxy too.

    list_credentials returns non-sensitive metadata only (service, label,
    description, created_at) and is delegated so the gateway never reads
    the credentials table directly.
    """

    def __init__(self, storage: Any, proxy_client: Any) -> None:
        self._storage = storage
        self._proxy_client = proxy_client

    def store_credential(
        self, service: str, payload: dict, *,
        user_id: str = "default", label: str = "default",
        description: str | None = None,
    ) -> None:
        self._proxy_client.credential_store(
            service, payload, label=label, description=description,
        )

    def delete_credential(
        self, service: str, *, user_id: str = "default", label: str = "default",
    ) -> None:
        self._proxy_client.credential_delete(service, label)

    def list_credentials(self, user_id: str = "default") -> list[dict]:
        return self._proxy_client.credential_list()

    def mark_security_rotated(
        self, service: str, *, user_id: str = "default", label: str = "default",
    ) -> None:
        self._proxy_client.credential_rotate(service, label)

    def get_credential(self, service: str, *, user_id: str = "default",
                       label: str = "default") -> dict | None:
        raise NotImplementedError(
            "Gateway cannot read credential plaintext in two-container mode. "
            "Route this call through SecurityProxyClient.llm_complete / mcp_start / http_get."
        )

    def list_services(self, user_id: str = "default") -> list[str]:
        items = self._proxy_client.credential_list()
        return sorted({i["service"] for i in items if i.get("service")})
```

- [ ] **Step 4: Run tests — 5 passed.**

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/security/credentials.py tests/test_credentials_delegating_wrapper.py
git commit -m "DelegatingCredentialProxy: gateway-side wrapper forwards writes to proxy"
```

---

## Task 4: `app.credentials` picks the right proxy based on mode

**Files:**
- Modify: `src/mycelos/app.py`
- Test: extend `tests/test_app_credentials.py`

- [ ] **Step 1: Locate**

Run: `grep -n "def credentials\|EncryptedCredentialProxy\|MYCELOS_PROXY_URL" src/mycelos/app.py`

- [ ] **Step 2: Add failing test**

Append to `tests/test_app_credentials.py`:

```python
def test_app_credentials_uses_delegating_wrapper_with_external_proxy(tmp_path, monkeypatch):
    """When MYCELOS_PROXY_URL is set, app.credentials must be a
    DelegatingCredentialProxy — NOT EncryptedCredentialProxy with a master key."""
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "ignored-in-two-container-mode")
    monkeypatch.setenv("MYCELOS_PROXY_URL", "http://proxy.internal:9110")
    monkeypatch.setenv("MYCELOS_PROXY_TOKEN", "tok")

    from mycelos.app import App
    from mycelos.security.credentials import DelegatingCredentialProxy, EncryptedCredentialProxy

    app = App(tmp_path)
    app.initialize()
    # Force proxy_client to be wired (simulating what gateway/server.py does)
    from mycelos.security.proxy_client import SecurityProxyClient
    app.set_proxy_client(SecurityProxyClient(url="http://proxy.internal:9110", token="tok"))

    creds = app.credentials
    assert isinstance(creds, DelegatingCredentialProxy)
    assert not isinstance(creds, EncryptedCredentialProxy)


def test_app_credentials_uses_encrypted_in_single_container(tmp_path, monkeypatch):
    """No MYCELOS_PROXY_URL → legacy EncryptedCredentialProxy in-process."""
    monkeypatch.setenv("MYCELOS_MASTER_KEY", "single-container-key-" + "x" * 20)
    monkeypatch.delenv("MYCELOS_PROXY_URL", raising=False)

    from mycelos.app import App
    from mycelos.security.credentials import EncryptedCredentialProxy

    app = App(tmp_path)
    app.initialize()
    assert isinstance(app.credentials, EncryptedCredentialProxy)
```

- [ ] **Step 3: Run — 2 failures.**

- [ ] **Step 4: Modify `app.py` credentials property**

```python
@property
def credentials(self):
    if self._credentials is None:
        import os as _os
        proxy_url = _os.environ.get("MYCELOS_PROXY_URL", "").strip()
        if proxy_url and self._proxy_client is not None:
            from mycelos.security.credentials import DelegatingCredentialProxy
            self._credentials = DelegatingCredentialProxy(
                storage=self.storage, proxy_client=self._proxy_client,
            )
        else:
            from mycelos.security.credentials import EncryptedCredentialProxy
            master_key = _os.environ.get("MYCELOS_MASTER_KEY", "")
            if not master_key:
                key_file = self.data_dir / ".master_key"
                if key_file.exists():
                    master_key = key_file.read_text().strip()
            self._credentials = EncryptedCredentialProxy(
                self.storage, master_key, notifier=self.config_notifier,
            )
    return self._credentials
```

- [ ] **Step 5: Note on proxy-client availability**

`DelegatingCredentialProxy` needs `self._proxy_client` to already be set. In the gateway boot sequence, that happens in `gateway/server.py` **before** any route reads `app.credentials`. Verify: the first read of `app.credentials` in the gateway code path is inside `api.state.chat_service = ChatService(mycelos)` — which is AFTER the proxy-client wiring in the same function. Safe.

- [ ] **Step 6: Run regression**

```bash
pytest tests/test_app_credentials.py tests/test_gateway_external_proxy.py tests/test_gateway.py -v --timeout=60
```

All pass.

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/app.py tests/test_app_credentials.py
git commit -m "app.credentials selects DelegatingCredentialProxy when proxy URL set"
```

---

## Task 5: Route remaining direct-httpx tools through proxy

**Files:**
- Modify: `src/mycelos/connectors/search_tools.py`
- Modify: `src/mycelos/connectors/github_tools.py`
- Modify: `src/mycelos/connectors/mcp_search.py`
- Modify: `src/mycelos/llm/model_registry.py`
- Modify: `src/mycelos/agents/handlers/model_updater_handler.py`

- [ ] **Step 1: Search for all direct-httpx call sites in gateway code**

```bash
grep -rn "httpx\.\(get\|post\|put\|delete\|request\)" src/mycelos \
    --include="*.py" | grep -v "security/proxy" | grep -v "connectors/mcp_client" \
    | grep -v "cli/chat_cmd" | grep -v "cli/serve_cmd" \
    | grep -v "doctor/" | grep -v "llm/ollama.py"
```

Expected list: `search_tools.py`, `github_tools.py`, `mcp_search.py`, `http_tools.py` (already proxy-aware — double-check), `model_registry.py:300`, `model_updater_handler.py:130`, `chat/slash_commands.py` (Telegram calls). Ollama and doctor self-health stay direct — both are localhost-internal.

- [ ] **Step 2: Pattern for each file**

For each tool module, import `http_tools._proxy_client` and use it when available; fall back to direct `httpx` otherwise (keeps single-container and tests working). Example for `search_tools.py`:

```python
def _do_get(url, headers=None, timeout=15):
    from mycelos.connectors.http_tools import _proxy_client as _pc
    if _pc is not None:
        resp = _pc.http_get(url, headers=headers, timeout=timeout)
        # The proxy returns a dict; shape it to mimic httpx.Response-enough for callers
        return resp
    resp = httpx.get(url, headers=headers, timeout=timeout)
    return {
        "status": resp.status_code,
        "headers": dict(resp.headers),
        "body": resp.text,
        "url": str(resp.url),
    }
```

Callers in each tool then read `.get("status")` / `.get("body")` instead of `.status_code` / `.text`. This is a mechanical 10-line refactor per site.

- [ ] **Step 3: Per-file unit tests**

Each of the five files has an existing test file. Extend the existing tests so that **one new test per file** asserts the proxy path is taken when `_proxy_client` is set:

```python
def test_tool_uses_proxy_when_available(monkeypatch):
    from mycelos.connectors import http_tools, search_tools
    mock_pc = MagicMock()
    mock_pc.http_get.return_value = {"status": 200, "body": '{"results": []}', "headers": {}, "url": "x"}
    monkeypatch.setattr(http_tools, "_proxy_client", mock_pc)
    result = search_tools.some_public_function(...)
    mock_pc.http_get.assert_called_once()
```

- [ ] **Step 4: Commit after each file** — keep commits small and reviewable.

```bash
git commit -m "Route search_tools through proxy when available"
git commit -m "Route github_tools through proxy when available"
# ... etc
```

- [ ] **Step 5: Telegram is different**

Telegram `getUpdates` is a **long-poll** (30s timeout). Running it through the proxy means keeping a long-running HTTP connection to the proxy container, which is fine, but verify the proxy's `http_proxy` endpoint honors the `timeout` parameter and doesn't impose a shorter cap. If it does, add a `long_poll=True` flag to the proxy endpoint that disables the cap for this specific use case. Otherwise Telegram polling would break.

Test this by running `mycelos connector setup telegram` in a dev install and confirming `getUpdates` still works through the proxy path.

---

## Task 6: Gateway container off the `default` network

**Files:**
- Modify: `docker-compose.yml`
- Modify: `tests/test_compose_structure.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_compose_structure.py`:

```python
def test_gateway_not_on_default_network(compose):
    """Phase 1b: gateway reaches only the proxy; no direct internet route."""
    gw = compose["services"]["gateway"]
    networks = gw.get("networks", [])
    # Flatten dict-form networks to names
    if isinstance(networks, dict):
        networks = list(networks.keys())
    assert "default" not in networks, \
        f"gateway must not be on default network (got {networks})"
    assert "mycelos-internal" in networks


def test_proxy_db_mount_is_writable(compose):
    """Phase 1b: proxy must write to credentials table; DB mount becomes RW."""
    px = compose["services"]["proxy"]
    for v in px.get("volumes", []):
        if isinstance(v, dict) and "mycelos.db" in str(v.get("target", "")):
            assert v.get("read_only") is not True, \
                "proxy must be able to write credentials — mycelos.db must not be read_only"
```

- [ ] **Step 2: Run — both fail.**

- [ ] **Step 3: Update compose**

In `docker-compose.yml`:

```yaml
  gateway:
    ...
    networks:
      - mycelos-internal   # drop default
    ...
```

And on the proxy service:

```yaml
      - type: bind
        source: ${MYCELOS_DATA_DIR:-./data}/mycelos.db
        target: /data/mycelos.db
        # Phase 1b: proxy writes credentials; no longer read_only.
```

- [ ] **Step 4: Test pass + structural suite stays green**

```bash
pytest tests/test_compose_structure.py -v
```

All 9 (7 existing + 2 new) pass.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml tests/test_compose_structure.py
git commit -m "Gateway network lockdown + proxy DB write access"
```

---

## Task 7: E2E regression — gateway cannot reach the internet

**Files:**
- Modify: `tests/e2e/test_two_container_deployment.sh`

- [ ] **Step 1: Append assertion**

After the existing four checks in the E2E script, add:

```bash
# 5. Gateway cannot reach the public internet
if docker compose exec -T gateway curl -fsSL -m 3 https://example.com >/dev/null 2>&1; then
    echo "FAIL: gateway has an internet route (Phase 1b expects it to have none)"
    exit 1
fi
echo "OK: gateway has no direct internet route"
```

- [ ] **Step 2: Run locally (Docker required)**

```bash
bash tests/e2e/test_two_container_deployment.sh
```

Expected: `PASS: two-container deployment e2e`.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_two_container_deployment.sh
git commit -m "E2E: assert gateway has no direct internet route"
```

---

## Task 8: Update threat model and CHANGELOG

**Files:**
- Modify: `docs/security/two-container-deployment.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Rewrite threat model**

Promote every "Phase 1b" row in the "does not mitigate" table to the "mitigates" table. The final doc should read like Phase 1 is done, and Phase 2 (passkey) is what's next.

Specifically:
- Delete the "Phase 1a vs Phase 1b" section.
- Move "`EncryptedCredentialProxy` on first boot reads the master key" into `mitigates` as "Master key never reaches the gateway process in two-container mode."
- Move "gateway direct-httpx calls" into `mitigates` as "Gateway has no `default` network; every outbound request flows through the proxy or fails."
- Remove the "Phase 1b adds" section entirely — replace with a short "Phase 2 adds" section carrying forward passkey/tunnels/Caddy.

- [ ] **Step 2: CHANGELOG entry**

Add under the current week heading:

```markdown
### Two-Container Docker Deployment (Phase 1b — security lockdown)
- Credential writes move out of the gateway. `POST /credential/store`, `DELETE /credential/{service}/{label}`, `GET /credential/list`, `POST /credential/rotate` land on the SecurityProxy. Gateway uses `DelegatingCredentialProxy`, a thin wrapper that forwards writes and never reads plaintext.
- Master key no longer loaded in the gateway process when `MYCELOS_PROXY_URL` is set. Phase 1a kept a read path for first-boot init; that path is now unreachable in two-container mode.
- Gateway container drops off the `default` Docker network. Every outbound call from search tools, GitHub tools, MCP search, LiteLLM cost-map fetch, release check, and Telegram polling now routes through the proxy via `http_tools._proxy_client` — or fails.
- Proxy's `mycelos.db` mount flips from read-only to read-write (required for credential writes). Schema-level isolation into a dedicated proxy DB is deferred to a future phase.
- New E2E assertion: `docker compose exec gateway curl https://example.com` must fail after Phase 1b.
- Threat model doc updated. Phase 2 (passkey + public exposure) is the next milestone.
```

- [ ] **Step 3: Commit**

```bash
git add docs/security/two-container-deployment.md CHANGELOG.md
git commit -m "Phase 1b: docs — credential lockdown + gateway network lockdown"
```

---

## Final Verification

- [ ] **Full unit suite**

```bash
MYCELOS_MASTER_KEY=ci python -m pytest tests/ --ignore=tests/integration --ignore=tests/e2e -x --tb=short -q --timeout=60 -p no:cacheprovider
```

Expected: all pass.

- [ ] **Security suite explicit**

```bash
MYCELOS_MASTER_KEY=ci python -m pytest tests/security/ -q --timeout=30
```

- [ ] **Compose parse**

```bash
MYCELOS_PROXY_TOKEN=dummy MYCELOS_DATA_DIR=/tmp/fake docker compose -f docker-compose.yml config > /dev/null && echo OK
```

- [ ] **E2E (Docker required)**

```bash
bash tests/e2e/test_two_container_deployment.sh
```

Expected: `PASS: two-container deployment e2e`.

- [ ] **Push and check CI**

```bash
git push origin main   # or the feature branch, depending on workflow choice
```

Wait for the GitHub Actions run to go green.
