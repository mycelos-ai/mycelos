# Custom MCP Connector Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a usable Custom-MCP setup path on the Connectors page with multi-variable env support and MCP-Registry-driven prefill.

**Architecture:** Frontend Multi-Var form replaces the single-secret input. Backend `POST /api/connectors` accepts a new `env_vars: dict[str, str]` field stored as a JSON blob with the `__multi__` sentinel marker on `env_var`. MCP spawn path detects the sentinel and merges all keys into the subprocess env. New `GET /api/connectors/lookup-env-vars` endpoint exposes the existing `mcp_search.lookup_env_vars(package)` to the browser. Recipe setup paths are unchanged.

**Tech Stack:** FastAPI, Pydantic, SQLite, Alpine.js (vanilla, no build step), pytest.

**Spec:** `docs/superpowers/specs/2026-04-25-custom-mcp-setup-design.md`

**Baseline rule:** After every task, `PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q` must pass with zero failures.

---

## File Structure

Files this plan touches:

- `src/mycelos/gateway/routes.py` — `ConnectorAddRequest` gets `env_vars` field; `POST /api/connectors` handler branches on `env_vars` vs. `secret`; new `GET /api/connectors/lookup-env-vars` endpoint; auto-start for custom MCPs (recipe-less with command).
- `src/mycelos/connectors/mcp_client.py:254-292` — credential resolution sees `__multi__` sentinel and merges JSON blob.
- `src/mycelos/frontend/pages/connectors.html` — Add Connector form gets multi-row env-vars list; `loadRecipes` / `addConnector` Alpine state changes.
- `tests/test_custom_mcp_setup.py` (NEW) — backend POST behaviors for env_vars vs. secret.
- `tests/test_lookup_env_vars_endpoint.py` (NEW) — endpoint shape.
- `tests/test_mcp_spawn_multi_env.py` (NEW) — spawn-time injection.
- `CHANGELOG.md` — Week 17 entry.

What stays untouched:

- `src/mycelos/connectors/mcp_search.py:97` — `lookup_env_vars` already exists.
- Recipe-setup code paths (Channels, MCP recipes with secret/oauth_http).
- Credentials store schema.
- `_BLOCKED_ENV_VARS` filter in `mcp_client.py` — multi-var injection still respects it.

---

## Task 1: Backend — extend `POST /api/connectors` to accept `env_vars`

**Files:**
- Modify: `src/mycelos/gateway/routes.py:167-172` (request model)
- Modify: `src/mycelos/gateway/routes.py:2027-2071` (handler credential branch)
- Test: `tests/test_custom_mcp_setup.py` (new)

The existing handler currently takes `secret: str | None`. Add `env_vars: dict[str, str] | None` and route both paths through credential storage. Recipe code (which uses `body.secret`) is unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_custom_mcp_setup.py`:

```python
"""POST /api/connectors with env_vars stores a multi-var credential blob."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "custom-mcp-test-key"
        from mycelos.app import App
        from mycelos.gateway.server import create_app
        App(Path(tmp)).initialize()
        fastapi_app = create_app(Path(tmp), no_scheduler=True, host="0.0.0.0")
        yield TestClient(fastapi_app)


def _stored_credential(tmp_data_dir, service):
    """Read the stored credential row directly so we test the on-disk shape."""
    from mycelos.app import App
    app = App(tmp_data_dir)
    return app.credentials.get_credential(service)


def test_post_with_env_vars_stores_multi_blob(client, tmp_path) -> None:
    # Use the same tmp dir the client fixture uses by re-deriving via env.
    # Simpler: hit the API and then read via app pointing at the same dir.
    resp = client.post("/api/connectors", json={
        "name": "context7",
        "command": "npx -y @upstash/context7-mcp",
        "env_vars": {"API_KEY": "ctx_abc", "WORKSPACE": "ws_42"},
    })
    assert resp.status_code == 200, resp.text
    # Read the credential through the app
    from mycelos.app import App
    # The client fixture's tmp dir is gone after teardown — instead
    # assert via the API list endpoint that the connector exists.
    listed = client.get("/api/connectors").json()
    assert any(c.get("id") == "context7" for c in listed), listed


def test_post_with_env_vars_writes_multi_sentinel(tmp_data_dir: Path) -> None:
    """Direct App-level test — verify on-disk credential shape."""
    from mycelos.app import App
    from mycelos.gateway.server import create_app

    os.environ["MYCELOS_MASTER_KEY"] = "custom-mcp-direct-test"
    App(tmp_data_dir).initialize()
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0")
    c = TestClient(fastapi_app)

    resp = c.post("/api/connectors", json={
        "name": "context7",
        "command": "npx -y @upstash/context7-mcp",
        "env_vars": {"API_KEY": "ctx_abc", "WORKSPACE": "ws_42"},
    })
    assert resp.status_code == 200, resp.text

    app = App(tmp_data_dir)
    cred = app.credentials.get_credential("context7")
    assert cred is not None
    assert cred["env_var"] == "__multi__"
    blob = json.loads(cred["api_key"])
    assert blob == {"API_KEY": "ctx_abc", "WORKSPACE": "ws_42"}


def test_post_with_legacy_secret_still_works(tmp_data_dir: Path) -> None:
    """Existing recipe-style POST {secret: '...'} path is preserved."""
    from mycelos.app import App
    from mycelos.gateway.server import create_app

    os.environ["MYCELOS_MASTER_KEY"] = "custom-mcp-legacy-test"
    App(tmp_data_dir).initialize()
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0")
    c = TestClient(fastapi_app)

    resp = c.post("/api/connectors", json={
        "name": "myconn",
        "command": "npx -y some-pkg",
        "secret": "abc123",
    })
    assert resp.status_code == 200, resp.text

    app = App(tmp_data_dir)
    cred = app.credentials.get_credential("myconn")
    assert cred is not None
    # Legacy path: env_var is the heuristic name, NOT the sentinel
    assert cred["env_var"] != "__multi__"
    assert cred["env_var"] == "MYCONN_API_KEY"
    assert cred["api_key"] == "abc123"


def test_post_env_vars_wins_over_secret(tmp_data_dir: Path) -> None:
    """When both env_vars and secret are sent, env_vars wins (the explicit, multi-var path)."""
    from mycelos.app import App
    from mycelos.gateway.server import create_app

    os.environ["MYCELOS_MASTER_KEY"] = "custom-mcp-precedence-test"
    App(tmp_data_dir).initialize()
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0")
    c = TestClient(fastapi_app)

    resp = c.post("/api/connectors", json={
        "name": "both",
        "command": "npx -y some-pkg",
        "secret": "ignored",
        "env_vars": {"REAL_KEY": "kept"},
    })
    assert resp.status_code == 200, resp.text

    app = App(tmp_data_dir)
    cred = app.credentials.get_credential("both")
    assert cred is not None
    assert cred["env_var"] == "__multi__"
    assert json.loads(cred["api_key"]) == {"REAL_KEY": "kept"}


def test_post_env_vars_filters_empty_keys(tmp_data_dir: Path) -> None:
    """Rows with empty key are dropped; values may be empty (intentional feature flag pattern)."""
    from mycelos.app import App
    from mycelos.gateway.server import create_app

    os.environ["MYCELOS_MASTER_KEY"] = "custom-mcp-filter-test"
    App(tmp_data_dir).initialize()
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0")
    c = TestClient(fastapi_app)

    resp = c.post("/api/connectors", json={
        "name": "filt",
        "command": "npx -y some-pkg",
        "env_vars": {"": "dropped", "  ": "also dropped", "REAL": "kept", "FLAG": ""},
    })
    assert resp.status_code == 200, resp.text

    app = App(tmp_data_dir)
    cred = app.credentials.get_credential("filt")
    assert cred is not None
    blob = json.loads(cred["api_key"])
    assert blob == {"REAL": "kept", "FLAG": ""}


def test_post_no_creds_at_all_still_registers(tmp_data_dir: Path) -> None:
    """Some MCPs need no credentials — connector should register, no credential row."""
    from mycelos.app import App
    from mycelos.gateway.server import create_app

    os.environ["MYCELOS_MASTER_KEY"] = "custom-mcp-nocred-test"
    App(tmp_data_dir).initialize()
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0")
    c = TestClient(fastapi_app)

    resp = c.post("/api/connectors", json={
        "name": "envless",
        "command": "npx -y some-envless-pkg",
    })
    assert resp.status_code == 200, resp.text

    app = App(tmp_data_dir)
    assert app.connector_registry.get("envless") is not None
    assert app.credentials.get_credential("envless") is None
```

- [ ] **Step 2: Run tests, confirm they fail**

```
PYTHONPATH=src pytest tests/test_custom_mcp_setup.py -v
```

Expected: failures because `env_vars` field doesn't exist on the request model.

- [ ] **Step 3: Add `env_vars` field to `ConnectorAddRequest`**

In `src/mycelos/gateway/routes.py` find the class at line 167:

```python
class ConnectorAddRequest(BaseModel):
    """Request body for POST /api/connectors."""
    name: str
    command: str = ""
    secret: str | None = None
```

Replace with:

```python
class ConnectorAddRequest(BaseModel):
    """Request body for POST /api/connectors."""
    name: str
    command: str = ""
    secret: str | None = None
    env_vars: dict[str, str] | None = None  # multi-var path; wins over `secret`
```

- [ ] **Step 4: Branch the credential-storage block on `env_vars`**

In the same file, find the credential-storage block at lines 2038-2071. It currently looks like:

```python
        if body.secret:
            try:
                # Recipe-declared env_var name (e.g. BRAVE_API_KEY) if the
                # connector is a known MCP recipe; otherwise derive from
                # the name.
                if recipe and recipe.credentials:
                    env_var_name = recipe.credentials[0].get("env_var", "")
                else:
                    env_var_name = f"{body.name.upper().replace('-', '_')}_API_KEY"

                logger.info(
                    "add_connector: storing credential service=%s env_var=%s",
                    body.name, env_var_name,
                )
                mycelos.credentials.store_credential(
                    body.name,
                    {"api_key": body.secret, "env_var": env_var_name},
                    description=f"Credentials for {body.name}",
                )
                logger.info("add_connector: store_credential returned OK for %s", body.name)
                mycelos.audit.log(
                    "credential.stored",
                    details={"connector": body.name, "env_var": env_var_name},
                    user_id=_resolve_user_id(request),
                )
            except Exception as e:
                logger.exception("Credential storage failed for connector %s: %s", body.name, e)
                mycelos.audit.log(
                    "credential.store_failed",
                    details={"connector": body.name, "error": str(e)},
                    user_id=_resolve_user_id(request),
                )
        else:
            logger.info("add_connector: no secret provided for %s — skipping store", body.name)
```

Replace with:

```python
        # env_vars (multi-var) wins over legacy single `secret`. We support
        # both shapes so recipe-setup code (which sends `secret`) keeps working.
        cleaned_env_vars: dict[str, str] | None = None
        if body.env_vars:
            cleaned_env_vars = {
                k: v for k, v in body.env_vars.items() if k.strip()
            }
            if not cleaned_env_vars:
                cleaned_env_vars = None  # all keys were blank — fall through

        if cleaned_env_vars:
            try:
                import json as _json
                logger.info(
                    "add_connector: storing multi-var credential service=%s vars=%s",
                    body.name, list(cleaned_env_vars.keys()),
                )
                mycelos.credentials.store_credential(
                    body.name,
                    {
                        "api_key": _json.dumps(cleaned_env_vars),
                        "env_var": "__multi__",
                        "connector": body.name,
                    },
                    description=f"Credentials for {body.name}",
                )
                mycelos.audit.log(
                    "credential.stored",
                    details={"connector": body.name, "env_var": "__multi__",
                             "var_names": list(cleaned_env_vars.keys())},
                    user_id=_resolve_user_id(request),
                )
            except Exception as e:
                logger.exception("Credential storage failed for connector %s: %s", body.name, e)
                mycelos.audit.log(
                    "credential.store_failed",
                    details={"connector": body.name, "error": str(e)},
                    user_id=_resolve_user_id(request),
                )
        elif body.secret:
            try:
                # Recipe-declared env_var name (e.g. BRAVE_API_KEY) if the
                # connector is a known MCP recipe; otherwise derive from
                # the name.
                if recipe and recipe.credentials:
                    env_var_name = recipe.credentials[0].get("env_var", "")
                else:
                    env_var_name = f"{body.name.upper().replace('-', '_')}_API_KEY"

                logger.info(
                    "add_connector: storing credential service=%s env_var=%s",
                    body.name, env_var_name,
                )
                mycelos.credentials.store_credential(
                    body.name,
                    {"api_key": body.secret, "env_var": env_var_name},
                    description=f"Credentials for {body.name}",
                )
                mycelos.audit.log(
                    "credential.stored",
                    details={"connector": body.name, "env_var": env_var_name},
                    user_id=_resolve_user_id(request),
                )
            except Exception as e:
                logger.exception("Credential storage failed for connector %s: %s", body.name, e)
                mycelos.audit.log(
                    "credential.store_failed",
                    details={"connector": body.name, "error": str(e)},
                    user_id=_resolve_user_id(request),
                )
        else:
            logger.info("add_connector: no creds provided for %s — skipping store", body.name)
```

- [ ] **Step 5: Run the new tests**

```
PYTHONPATH=src pytest tests/test_custom_mcp_setup.py -v
```

Expected: 6 pass.

- [ ] **Step 6: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures. The Hypothesis flake on `test_policy_engine_property.py` is a known unrelated flake — re-run that one file alone if it appears.

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/gateway/routes.py tests/test_custom_mcp_setup.py
git commit -m "feat(gateway): POST /api/connectors accepts env_vars (multi-var credential blob)"
```

---

## Task 2: Backend — `GET /api/connectors/lookup-env-vars` endpoint

**Files:**
- Modify: `src/mycelos/gateway/routes.py` (add new endpoint near `/api/connectors/recipes`)
- Test: `tests/test_lookup_env_vars_endpoint.py` (new)

Wraps the existing `mcp_search.lookup_env_vars(package)` so the frontend can prefill the form. Never raises; returns `[]` on miss or registry error.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lookup_env_vars_endpoint.py`:

```python
"""GET /api/connectors/lookup-env-vars — registry-driven env-var prefill."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "lookup-env-test-key"
        from mycelos.app import App
        from mycelos.gateway.server import create_app
        App(Path(tmp)).initialize()
        fastapi_app = create_app(Path(tmp), no_scheduler=True, host="0.0.0.0")
        yield TestClient(fastapi_app)


def test_lookup_returns_envelope_on_hit(client: TestClient) -> None:
    """When the registry returns vars, endpoint returns {env_vars: [...]}."""
    fake_hit = [{"name": "API_KEY", "secret": True}, {"name": "WORKSPACE", "secret": False}]
    with patch("mycelos.connectors.mcp_search.lookup_env_vars", return_value=fake_hit):
        resp = client.get("/api/connectors/lookup-env-vars?package=@upstash/context7-mcp")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"env_vars": fake_hit}


def test_lookup_returns_empty_on_miss(client: TestClient) -> None:
    """When the registry has no entry, endpoint returns {env_vars: []} (still 200)."""
    with patch("mycelos.connectors.mcp_search.lookup_env_vars", return_value=[]):
        resp = client.get("/api/connectors/lookup-env-vars?package=nonexistent-pkg")
    assert resp.status_code == 200
    assert resp.json() == {"env_vars": []}


def test_lookup_swallows_registry_error(client: TestClient) -> None:
    """Network/registry failure does NOT bubble up — returns empty list, 200."""
    with patch("mycelos.connectors.mcp_search.lookup_env_vars",
               side_effect=Exception("network down")):
        resp = client.get("/api/connectors/lookup-env-vars?package=anything")
    assert resp.status_code == 200
    assert resp.json() == {"env_vars": []}


def test_lookup_requires_package_query_param(client: TestClient) -> None:
    """Missing query param → 422 (FastAPI default)."""
    resp = client.get("/api/connectors/lookup-env-vars")
    assert resp.status_code == 422
```

- [ ] **Step 2: Run, confirm they fail**

```
PYTHONPATH=src pytest tests/test_lookup_env_vars_endpoint.py -v
```

Expected: 404 on the GET (endpoint not yet defined).

- [ ] **Step 3: Add the endpoint**

In `src/mycelos/gateway/routes.py` find the existing `@api.get("/api/connectors/recipes")` declaration (around line 1677). Add this new endpoint directly above it:

```python
    @api.get("/api/connectors/lookup-env-vars")
    async def lookup_connector_env_vars(package: str) -> dict:
        """Return env-var hints for a known MCP package.

        Wraps mcp_search.lookup_env_vars so the Custom-MCP setup form
        can prefill its fields when the user types a known package.
        Failures are silenced — registry availability is not the user's
        problem; an empty list lets the user enter vars manually.
        """
        from mycelos.connectors.mcp_search import lookup_env_vars
        try:
            env_vars = lookup_env_vars(package) or []
        except Exception:
            env_vars = []
        return {"env_vars": env_vars}
```

- [ ] **Step 4: Run the new tests**

```
PYTHONPATH=src pytest tests/test_lookup_env_vars_endpoint.py -v
```

Expected: 4 pass.

- [ ] **Step 5: Baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/gateway/routes.py tests/test_lookup_env_vars_endpoint.py
git commit -m "feat(gateway): GET /api/connectors/lookup-env-vars (MCP registry hint)"
```

---

## Task 3: Spawn — handle `__multi__` sentinel in MCP credential injection

**Files:**
- Modify: `src/mycelos/connectors/mcp_client.py:254-292`
- Test: `tests/test_mcp_spawn_multi_env.py` (new)

Today the credential-injection loop in `_build_env` reads each env_var declared in `_env_vars`, and for `credential:<service>` sources, looks up the credential and injects ONE key. For Custom MCPs with multi-var blobs, the env_vars dict won't list each variable — it'll list ONE entry that points at the multi blob. We need to detect `cred["env_var"] == "__multi__"` and merge ALL JSON keys into the env.

There are two patterns:

- **Recipe path:** env_vars dict is built by the recipe-aware caller, which lists each `recipe.credentials[i].env_var` mapped to `credential:<id>`. The credential's `env_var` field matches the dict key. Spawn loop calls `cred["api_key"]` and writes it under `env_var`.
- **Custom-MCP path (this task):** the new auto-start (Task 4) puts ONE entry — let's call it the "multi-marker" — into the env_vars dict, e.g. `{"__multi__": "credential:context7"}`. The spawn loop sees this key, looks up the credential, sees `cred["env_var"] == "__multi__"`, parses the JSON blob, and merges ALL keys into the real env (skipping `__multi__` itself).

- [ ] **Step 1: Write the failing test**

Create `tests/test_mcp_spawn_multi_env.py`:

```python
"""MCP spawn injects all keys from a __multi__ credential blob into the subprocess env."""

from __future__ import annotations

from unittest.mock import MagicMock


def test_multi_var_credential_expands_into_env() -> None:
    """A credential with env_var='__multi__' and JSON blob in api_key
    expands into one env entry per blob key."""
    from mycelos.connectors.mcp_client import MCPClient
    import json

    cred_proxy = MagicMock()
    cred_proxy.get_credential.return_value = {
        "api_key": json.dumps({"API_KEY": "ctx_abc", "WORKSPACE": "ws_42"}),
        "env_var": "__multi__",
        "connector": "context7",
    }

    client = MCPClient(
        connector_id="context7",
        command="npx -y @upstash/context7-mcp",
        env_vars={"__multi__": "credential:context7"},
        credential_proxy=cred_proxy,
    )
    env = client._build_env()

    assert env.get("API_KEY") == "ctx_abc"
    assert env.get("WORKSPACE") == "ws_42"
    assert "__multi__" not in env, "sentinel must not leak into the spawn env"


def test_legacy_single_var_credential_still_works() -> None:
    """Recipe-style single-var credential keeps its existing behavior."""
    from mycelos.connectors.mcp_client import MCPClient

    cred_proxy = MagicMock()
    cred_proxy.get_credential.return_value = {
        "api_key": "secret123",
        "env_var": "BRAVE_API_KEY",
        "connector": "brave-search",
    }

    client = MCPClient(
        connector_id="brave-search",
        command="npx -y @brave/brave-search-mcp-server",
        env_vars={"BRAVE_API_KEY": "credential:brave-search"},
        credential_proxy=cred_proxy,
    )
    env = client._build_env()
    assert env.get("BRAVE_API_KEY") == "secret123"


def test_multi_var_blocked_keys_are_dropped() -> None:
    """Even via __multi__, blocked env vars must not be injected."""
    from mycelos.connectors.mcp_client import MCPClient, _BLOCKED_ENV_VARS
    import json

    blocked = next(iter(_BLOCKED_ENV_VARS))  # any blocked name

    cred_proxy = MagicMock()
    cred_proxy.get_credential.return_value = {
        "api_key": json.dumps({"SAFE": "ok", blocked: "BAD"}),
        "env_var": "__multi__",
        "connector": "evil",
    }

    client = MCPClient(
        connector_id="evil",
        command="npx -y something",
        env_vars={"__multi__": "credential:evil"},
        credential_proxy=cred_proxy,
    )
    env = client._build_env()
    assert env.get("SAFE") == "ok"
    assert blocked not in env, f"blocked var {blocked!r} must be dropped even from multi-blob"


def test_multi_var_malformed_json_skipped() -> None:
    """Bad JSON in api_key — log and skip injection, do not crash."""
    from mycelos.connectors.mcp_client import MCPClient

    cred_proxy = MagicMock()
    cred_proxy.get_credential.return_value = {
        "api_key": "{ this is not json",
        "env_var": "__multi__",
        "connector": "broken",
    }

    client = MCPClient(
        connector_id="broken",
        command="npx -y something",
        env_vars={"__multi__": "credential:broken"},
        credential_proxy=cred_proxy,
    )
    env = client._build_env()
    # No exception; nothing extra injected from the broken blob
    assert "__multi__" not in env
```

- [ ] **Step 2: Confirm tests fail**

```
PYTHONPATH=src pytest tests/test_mcp_spawn_multi_env.py -v
```

Expected: at minimum the multi-var expansion test fails (sentinel handling not yet present).

- [ ] **Step 3: Patch `_build_env` in `mcp_client.py`**

In `src/mycelos/connectors/mcp_client.py` find the loop at lines 254-292 (the `for env_var, source in self._env_vars.items():` block). Replace the entire loop with:

```python
        # Inject credentials from CredentialProxy (skip blocked vars).
        # Two shapes are supported:
        #  - Single-var: env_vars maps {ENV_NAME: "credential:<service>"}; the
        #    credential's `api_key` is injected under ENV_NAME.
        #  - Multi-var (custom MCPs with __multi__ sentinel): env_vars maps
        #    {"__multi__": "credential:<service>"}; the credential's `api_key`
        #    is a JSON dict whose keys/values are merged into env directly.
        import json as _json
        for env_var, source in self._env_vars.items():
            if env_var in _BLOCKED_ENV_VARS:
                logger.warning(
                    "Blocked dangerous env var '%s' for MCP server '%s'",
                    env_var, self.connector_id,
                )
                continue
            if not source.startswith("credential:"):
                env[env_var] = source
                continue

            service = source[len("credential:"):]
            if not self._credential_proxy:
                logger.warning(
                    "No credential_proxy available for MCP server '%s' — "
                    "env_var '%s' will not be injected",
                    self.connector_id, env_var,
                )
                continue
            try:
                cred = self._credential_proxy.get_credential(service)
            except Exception as e:
                logger.warning(
                    "Failed to load credential '%s' for MCP server '%s': %s",
                    service, self.connector_id, e,
                )
                continue
            if not cred or "api_key" not in cred:
                logger.warning(
                    "Credential '%s' not found for MCP server '%s' "
                    "(proxy returned %s)",
                    service, self.connector_id,
                    "None" if cred is None else "dict without api_key",
                )
                continue

            cred_kind = cred.get("env_var")
            if cred_kind == "__multi__":
                try:
                    blob = _json.loads(cred["api_key"])
                except Exception as e:
                    logger.warning(
                        "Multi-var credential '%s' has malformed JSON for MCP server '%s': %s",
                        service, self.connector_id, e,
                    )
                    continue
                if not isinstance(blob, dict):
                    logger.warning(
                        "Multi-var credential '%s' is not a JSON object for MCP server '%s' "
                        "(got %s)",
                        service, self.connector_id, type(blob).__name__,
                    )
                    continue
                injected: list[str] = []
                for k, v in blob.items():
                    if k in _BLOCKED_ENV_VARS:
                        logger.warning(
                            "Blocked dangerous env var '%s' from multi-var credential "
                            "for MCP server '%s'", k, self.connector_id,
                        )
                        continue
                    env[k] = str(v)
                    injected.append(k)
                logger.info(
                    "Multi-var credential '%s' loaded for MCP server '%s' (vars=%s)",
                    service, self.connector_id, injected,
                )
            else:
                env[env_var] = cred["api_key"]
                logger.info(
                    "Credential '%s' loaded for MCP server '%s' (env_var=%s, key_len=%d)",
                    service, self.connector_id, env_var, len(cred["api_key"]),
                )
```

- [ ] **Step 4: Run the new tests**

```
PYTHONPATH=src pytest tests/test_mcp_spawn_multi_env.py -v
```

Expected: 4 pass.

- [ ] **Step 5: Baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures (legacy single-var path must still work for recipes).

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/connectors/mcp_client.py tests/test_mcp_spawn_multi_env.py
git commit -m "feat(mcp): spawn-time __multi__ sentinel expands JSON blob into env"
```

---

## Task 4: Backend — auto-start Custom MCPs after registration

**Files:**
- Modify: `src/mycelos/gateway/routes.py:2090-2133` (the `if not is_builtin and recipe and ...` block)

Today only recipe-backed connectors auto-start. Custom MCPs (recipe is None, body.command set) just get registered and the first tool-call has to lazy-spawn. Worse, when env_vars came in as `__multi__`, the spawn-side `env_vars` dict from the API handler would be empty (no recipe.credentials to map). We need to construct the right env_vars dict for the multi case here too: `{"__multi__": "credential:<name>"}`.

- [ ] **Step 1: Locate the existing recipe auto-start block**

Read `src/mycelos/gateway/routes.py` lines 2088-2133. Confirm the structure:

```python
        if not is_builtin and recipe and recipe.command and recipe.transport == "stdio":
            try:
                env_vars: dict[str, str] = dict(recipe.static_env)
                for cred_spec in recipe.credentials:
                    env_var = cred_spec["env_var"]
                    env_vars[env_var] = f"credential:{body.name}"
                ...
```

- [ ] **Step 2: Add a parallel branch for Custom MCPs**

Right AFTER the `if not is_builtin and recipe ...` block (after the `except Exception as e: logger.warning(...)` of the recipe branch), insert:

```python
        # Custom-MCP auto-start: no recipe, but we have a command. Mirror
        # the recipe auto-start path but with synthesized env_vars based on
        # the stored credential's shape (multi-var vs. single-var).
        if not is_builtin and not recipe and body.command:
            try:
                stored = mycelos.credentials.get_credential(body.name)
                env_vars: dict[str, str] = {}
                if stored:
                    if stored.get("env_var") == "__multi__":
                        env_vars["__multi__"] = f"credential:{body.name}"
                    elif stored.get("env_var"):
                        env_vars[stored["env_var"]] = f"credential:{body.name}"

                from mycelos.connectors import http_tools as _http_tools
                proxy_client = getattr(_http_tools, "_proxy_client", None)
                import shlex
                argv = shlex.split(body.command)
                if proxy_client is not None:
                    resp = proxy_client.mcp_start(
                        connector_id=body.name,
                        command=argv,
                        env_vars=env_vars,
                        transport="stdio",
                    )
                    if resp.get("error"):
                        raise RuntimeError(resp["error"])
                    tools = resp.get("tools", [])
                    mycelos.mcp_manager.register_remote_session(
                        connector_id=body.name,
                        session_id=resp.get("session_id", ""),
                        tools=tools,
                    )
                    tool_count = len(tools)
                else:
                    tools = mycelos.mcp_manager.connect(
                        connector_id=body.name,
                        command=body.command,
                        env_vars=env_vars,
                        transport="stdio",
                    )
                    tool_count = len(tools)
                logger.info(
                    "Custom MCP server '%s' auto-started: %d tools",
                    body.name, tool_count,
                )
            except Exception as e:
                logger.warning("Custom MCP auto-start failed for '%s': %s", body.name, e)
```

This keeps the recipe path untouched and adds a sibling block for `not recipe and body.command`.

- [ ] **Step 3: Quick smoke — no test required for this path**

Auto-start is best-effort (the `except` swallows failures and the user can manually trigger via the test endpoint). But verify the existing baseline still passes — auto-start failures should not break any test.

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures. Existing `test_custom_mcp_setup.py` tests don't hit a real MCP server (they only check the registration + credential), so the auto-start `except` catches the inevitable npm-not-installed failure cleanly.

- [ ] **Step 4: Commit**

```bash
git add src/mycelos/gateway/routes.py
git commit -m "feat(gateway): auto-start custom MCPs after register (mirrors recipe path)"
```

---

## Task 5: Frontend — multi-var Add Connector form

**Files:**
- Modify: `src/mycelos/frontend/pages/connectors.html`

Replace the single-secret input with a repeatable env-vars list. Wire command-blur to the new lookup endpoint. Submit sends `env_vars` instead of `secret`. Recipe setup card buttons (which use `setupRecipe(recipe)`) are NOT touched.

- [ ] **Step 1: Locate the form, the Alpine state, and the submit handler**

Run:

```
grep -n "newConnector\|addConnector\|showAddForm\|onCommandBlur\|envVars\|<!-- Add Connector Form -->" src/mycelos/frontend/pages/connectors.html
```

Expected hits:
- `newConnector: { name: '', command: '', secret: '' }` (Alpine state, around line 936)
- `<!-- Add Connector Form -->` (around line 142 after Mini-Refactor)
- `<input type="password" x-model="newConnector.secret">` (around line 167)
- `addConnector()` method (in the Alpine factory)
- `resetForm()` method

Read enough surrounding lines to see the full form markup, the Alpine state object, and the methods.

- [ ] **Step 2: Update Alpine state**

Find:

```javascript
        newConnector: { name: '', command: '', secret: '' },
```

Replace with:

```javascript
        newConnector: {
          name: '',
          command: '',
          envVars: [{ key: '', value: '', isSecret: true }],
          lookupHit: 0,  // 0 = no lookup yet, N = registry suggested N vars
        },
```

- [ ] **Step 3: Add the helper methods**

Inside the same Alpine factory object (near `addConnector()` / `resetForm()`), add three new methods:

```javascript
        addEnvVar() {
          this.newConnector.envVars.push({ key: '', value: '', isSecret: true });
        },

        removeEnvVar(idx) {
          this.newConnector.envVars.splice(idx, 1);
          if (this.newConnector.envVars.length === 0) this.addEnvVar();
        },

        async onCommandBlur() {
          const cmd = (this.newConnector.command || '').trim();
          if (!cmd) return;
          const parts = cmd.split(/\s+/);
          // First arg that looks like a package: starts with @scope/ or contains /
          const pkg = parts.find(p =>
            p && !p.startsWith('-') && (p.startsWith('@') || p.includes('/'))
          );
          if (!pkg) return;
          try {
            const resp = await fetch('/api/connectors/lookup-env-vars?package=' + encodeURIComponent(pkg));
            if (!resp.ok) return;
            const data = await resp.json();
            const known = Array.isArray(data?.env_vars) ? data.env_vars : [];
            if (!known.length) {
              this.newConnector.lookupHit = 0;
              return;
            }
            this.newConnector.envVars = known.map(v => ({
              key: v.name,
              value: '',
              isSecret: v.secret !== false,
            }));
            this.newConnector.lookupHit = known.length;
          } catch (_) {
            /* network error → leave the form alone */
          }
        },
```

- [ ] **Step 4: Update `addConnector()` to send env_vars**

Find the existing `addConnector()` implementation (look for `MycelosAPI.post('/api/connectors'` or similar). The current submit builds a payload from `newConnector.name`, `newConnector.command`, `newConnector.secret`.

Replace the payload-building section with:

```javascript
          const env_vars = {};
          for (const row of this.newConnector.envVars) {
            const k = (row.key || '').trim();
            if (!k) continue;
            env_vars[k] = (row.value || '').toString();
          }
          const payload = {
            name: this.newConnector.name.trim(),
            command: this.newConnector.command.trim(),
          };
          if (Object.keys(env_vars).length > 0) {
            payload.env_vars = env_vars;
          }
```

Then keep whatever `await MycelosAPI.post('/api/connectors', payload);` line is there — just make sure it sends `payload` (the new shape). Keep the success-path code (refresh list, close form, etc.) unchanged.

- [ ] **Step 5: Update `resetForm()`**

Find:

```javascript
          this.newConnector = { name: '', command: '', secret: '' };
```

Replace with:

```javascript
          this.newConnector = {
            name: '',
            command: '',
            envVars: [{ key: '', value: '', isSecret: true }],
            lookupHit: 0,
          };
```

- [ ] **Step 6: Replace the form HTML markup**

Find the form block in `<!-- Add Connector Form -->`. The current Name + Secret + Command + Actions structure (around lines 157-200) gets the Secret-field section replaced with the multi-var block.

Replace the whole `<div class="grid grid-cols-1 md:grid-cols-2 gap-5 mb-5">` block (Name + Secret) PLUS the Command block PLUS the original Actions row with this:

```html
          <!-- Name -->
          <div class="mb-5">
            <label class="block text-[10px] font-label uppercase tracking-widest text-on-surface-variant mb-2">Name</label>
            <input type="text" x-model="newConnector.name"
                   class="input-field" placeholder="e.g. context7">
          </div>

          <!-- Command -->
          <div class="mb-5">
            <label class="block text-[10px] font-label uppercase tracking-widest text-on-surface-variant mb-2">Command</label>
            <textarea x-model="newConnector.command"
                      @blur="onCommandBlur()"
                      class="input-field resize-none"
                      rows="2"
                      placeholder="npx -y @upstash/context7-mcp"></textarea>
          </div>

          <!-- Environment Variables -->
          <div class="mb-5">
            <div class="flex items-center justify-between mb-2">
              <label class="block text-[10px] font-label uppercase tracking-widest text-on-surface-variant">Environment Variables</label>
              <span x-show="newConnector.lookupHit > 0"
                    class="text-[10px] text-tertiary font-label">
                MCP Registry suggested <span x-text="newConnector.lookupHit"></span> variable(s)
              </span>
            </div>
            <template x-for="(row, idx) in newConnector.envVars" :key="idx">
              <div class="flex items-center gap-2 mb-2">
                <input type="text" x-model="row.key"
                       class="input-field flex-1" placeholder="Key">
                <input :type="row.isSecret ? 'password' : 'text'" x-model="row.value"
                       class="input-field flex-1" placeholder="Value">
                <button @click="removeEnvVar(idx)" type="button"
                        class="p-2 rounded-lg text-on-surface-variant/40 hover:text-error hover:bg-error/10 transition-colors"
                        title="Remove">
                  <span class="material-symbols-outlined text-base">delete</span>
                </button>
              </div>
            </template>
            <button @click="addEnvVar()" type="button"
                    class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-label
                           text-on-surface-variant/60 hover:text-primary hover:bg-primary/5 transition-colors">
              <span class="material-symbols-outlined text-sm">add</span>
              Variable
            </button>
          </div>

          <!-- Actions row -->
          <div class="flex items-center justify-between">
            <button @click="showSearch = !showSearch"
                    class="flex items-center gap-2 text-xs text-tertiary hover:text-tertiary-container transition-colors font-label">
              <span class="material-symbols-outlined text-sm">search</span>
              Search MCP Registry
            </button>
            <div class="flex items-center gap-3">
              <button @click="showAddForm = false; resetForm()"
                      class="text-xs text-on-surface-variant hover:text-on-surface transition-colors font-label uppercase tracking-widest">
                Cancel
              </button>
              <button @click="addConnector()"
                      :disabled="!newConnector.name.trim() || !newConnector.command.trim() || submitting"
                      class="btn-primary text-xs">
                <span x-show="!submitting" class="material-symbols-outlined text-sm">add</span>
                <span x-show="submitting" class="material-symbols-outlined text-sm animate-spin">progress_activity</span>
                <span>Add</span>
              </button>
            </div>
          </div>
```

This replaces the original Name+Secret 2-col grid, the Command block, and the Actions row in one swap. The button text/icon classes keep the existing styling.

- [ ] **Step 7: HTML well-formedness check**

Same script as the previous frontend tasks:

```
PYTHONPATH=src python3 -c "
from html.parser import HTMLParser
class V(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.errors = []
    def handle_starttag(self, tag, attrs):
        if tag not in ('br','img','input','meta','link','hr'):
            self.stack.append(tag)
    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.errors.append(f'mismatch close </{tag}>, top={self.stack[-3:]}')
v = V()
v.feed(open('src/mycelos/frontend/pages/connectors.html').read())
if v.stack: print('UNCLOSED:', v.stack[-5:])
if v.errors: print('\n'.join(v.errors[:10]))
print('OK' if not v.stack and not v.errors else 'FAIL')
"
```

Expected: `OK` (modulo the known pre-existing parser false-positive on a literal `<strong>` inside an `x-html` attribute string — if you see exactly that one and nothing new, it's parity with HEAD).

- [ ] **Step 8: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 9: Commit**

```bash
git add src/mycelos/frontend/pages/connectors.html
git commit -m "feat(web): multi-var Add Connector form with MCP Registry prefill"
```

---

## Task 6: CHANGELOG + push

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add entry under Week 17**

Open `CHANGELOG.md`. Find the Week 17 block (between v0.3.0 and Week 16). Add at the END of the Week 17 entries:

```markdown
### Custom MCP connector setup
- The "Add Connector" form on the Connectors page is a real Custom-MCP setup wizard now. Name, Command, and a repeatable Environment Variables list (Key + Value + delete row + "+ Variable" button) replace the previous single-secret field.
- When the user pastes a Command, the form looks up the npm package against the MCP Registry (`GET /api/connectors/lookup-env-vars?package=<pkg>`) and pre-fills the env-vars rows on a hit. On miss or registry error, the form silently leaves an empty row for manual entry.
- `POST /api/connectors` now accepts `env_vars: dict[str, str]` alongside the legacy `secret: string`. When `env_vars` is set, the credential is stored as a JSON blob with the `__multi__` sentinel on the `env_var` field; recipe-setup code paths keep using the old single-var shape.
- MCP spawn (`mcp_client._build_env`) detects the `__multi__` sentinel, parses the JSON blob, and merges every key/value into the subprocess env. Blocked env vars are still filtered.
- Custom MCPs now auto-start after registration (mirrors the recipe auto-start path) so the first tool call doesn't pay the lazy-spawn cost.
- Spec / plan: `docs/superpowers/specs/2026-04-25-custom-mcp-setup-design.md`, `docs/superpowers/plans/2026-04-25-custom-mcp-setup-plan.md`.
```

- [ ] **Step 2: Run final baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 3: Manual smoke (controller assists Stefan)**

After commit + push, Stefan reloads the Connectors page. Verifies:

1. "Add Connector" → form appears below header with new env-vars block.
2. Paste `npx -y @upstash/context7-mcp`, tab away → env-vars rows pre-fill if registry knows the package; otherwise empty row stays.
3. Manually add `+ Variable` and `🗑` rows; verify add/remove works.
4. Submit with at least Name + Command set; new connector appears in Installed.
5. Test the new connector via its card "Test" button.

If the registry is offline / returns nothing for the test package, only steps 3-5 are testable; step 2 is best-effort.

- [ ] **Step 4: Commit changelog**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): custom MCP connector setup (Week 17)"
```

- [ ] **Step 5: Push**

```bash
git push origin main
```

---

## Self-review notes

Spec coverage check (against `2026-04-25-custom-mcp-setup-design.md`):

- D1 (recipes unchanged) → Tasks 1, 4 (new code branches alongside recipe code; recipe branches untouched).
- D2 (multi-var form) → Task 5.
- D3 (registry lookup) → Tasks 2 (endpoint) + 5 (frontend wiring).
- D4 (JSON blob + `__multi__`) → Task 1 (storage), Task 3 (spawn).
- D5 (proxy spawn-time logic) → Task 3.
- D6 (empty-rows policy) → Task 1 (backend filters empty keys), Task 5 (frontend allows empty rows).
- D7 (legacy `secret` preserved) → Task 1 (handler still accepts secret).
- Custom-MCP auto-start (gap surfaced by spec D5 implication) → Task 4.
- CHANGELOG → Task 6.

No spec requirement without a task. No placeholder-style steps in the plan. Method names consistent across tasks (`onCommandBlur`, `addEnvVar`, `removeEnvVar`, `addConnector`, `resetForm`).

Open follow-ups (out of scope, deferred to next spec):
- Capability auto-discovery for Custom MCPs (Spec 2 — Capability Hybrid).
- Per-variable rotation UI.
- Removing the legacy `secret: string` field.
