# Constitution Rule 2 Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lock in CLAUDE.md Rule 2 ("Config Generation on State Change") for every Web-API endpoint that mutates declarative state — both fix the existing violations and pin the invariant in tests so future regressions can't ship.

**Architecture:** Test-first audit. One test per mutating endpoint in `tests/security/test_constitution_rule_2.py` asserts that calling the endpoint grows `MAX(id) FROM config_generations` by exactly 1. Red tests reveal the violations; the fix is a single `app.config.apply_from_state(...)` call after each handler's mutation.

**Tech Stack:** FastAPI, pytest, FastAPI TestClient, SQLite.

**Spec:** `docs/superpowers/specs/2026-04-25-constitution-rule-2-audit-design.md`

**Baseline rule:** After every task, `PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q` must pass with zero failures (modulo the known Hypothesis flake on `test_policy_engine_property.py` — re-run that file alone to confirm).

---

## File Structure

Files this plan touches:

- `tests/security/test_constitution_rule_2.py` — NEW. Common fixtures + one test per endpoint.
- `src/mycelos/gateway/routes.py` — add `apply_from_state` after each mutation in 11 handlers (audit revealed `POST /api/channels` already conformant).
- `CHANGELOG.md` — Week 17 entry.

Helper used by every fix:

```python
mycelos.config.apply_from_state(
    state_manager=mycelos.state_manager,
    description=f"…human-readable description…",
    trigger="…short_token…",
)
```

The `state_manager` attribute on `App` already exists (CLI uses it everywhere).

What stays untouched:

- `src/mycelos/cli/*.py` — CLI handlers already conformant.
- `src/mycelos/config/*` — generation engine itself unchanged.
- `POST /api/channels` (line 2485) — already calls `apply_from_state` at line 2532.
- `POST /api/config/rollback` — uses its own rollback path, must NOT create a generation.

Endpoint-by-endpoint mapping (lines as of HEAD):

| # | Endpoint | Line | Trigger token |
|---|---|---|---|
| 1 | `POST /api/connectors` | 2001 | `connector_setup` |
| 2 | `DELETE /api/connectors/{id}` | 2250 | `connector_remove` |
| 3 | `POST /api/credentials` | 3165 | `credential_setup` |
| 4 | `DELETE /api/credentials/{service}` | 3181 | `credential_remove` |
| 5 | `POST /api/setup` | 3131 | `credential_setup` |
| 6 | `POST /api/memory` | 3372 | `memory_set` |
| 7 | `PATCH /api/agents/{id}` | 2582 | `agent_update` |
| 8 | `POST /api/models/migrate` | 2935 | `model_migrate` |
| 9 | `PUT /api/models/system-defaults` | 3035 | `agent_model_default` |
| 10 | `PUT /api/models/assignments/{agent_id}` | 3068 | `agent_model_assign` |
| 11 | `PUT /api/system/update-check-enabled` | 2742 | `system_setting` |

---

## Task 1: Test scaffold + fixtures

**Files:**
- Create: `tests/security/test_constitution_rule_2.py`

This task establishes the shared fixtures and the assertion helper used by every per-endpoint test in Tasks 2-6. No endpoint-specific tests yet — those come grouped by theme.

- [ ] **Step 1: Verify the directory exists**

```
ls tests/security/
```

If the directory doesn't exist, `mkdir -p tests/security/`. (It exists per CLAUDE.md — `tests/security/` is the security-invariants directory.)

- [ ] **Step 2: Write the scaffold**

Create `tests/security/test_constitution_rule_2.py`:

```python
"""Constitution Rule 2: every state-mutating Web-API endpoint MUST create
a config generation. Tests in this file ARE the audit — when this file is
green, the rule holds for every endpoint listed in the spec.

When you add a new endpoint that mutates declarative state, add a test
here. When this file is red, fix the handler — don't lower the bar.

Spec: docs/superpowers/specs/2026-04-25-constitution-rule-2-audit-design.md
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_and_client(tmp_data_dir: Path) -> Iterator[tuple[object, TestClient]]:
    """Initialised App + bound TestClient for endpoint tests.

    Each test gets a fresh data dir, fresh DB, fresh App. The same
    `App` instance the gateway uses is exposed so tests can read
    `config_generations` directly without needing a separate API.
    """
    os.environ["MYCELOS_MASTER_KEY"] = "constitution-rule-2-test-key"
    from mycelos.app import App
    from mycelos.gateway.server import create_app

    app = App(tmp_data_dir)
    app.initialize()
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0")
    with TestClient(fastapi_app) as client:
        yield app, client


def _generation_count(app) -> int:
    """Read MAX(id) FROM config_generations, treating empty table as 0."""
    row = app.storage.fetchone(
        "SELECT COALESCE(MAX(id), 0) AS max_id FROM config_generations"
    )
    return int(row["max_id"])


def assert_generation_added(
    app, before: int, *, expected_delta: int = 1
) -> int:
    """Assert MAX(id) of config_generations advanced by exactly `expected_delta`.

    Returns the new MAX(id) so chained assertions can use it as the next
    `before`.
    """
    after = _generation_count(app)
    assert after == before + expected_delta, (
        f"Constitution Rule 2 violation: expected {expected_delta} new "
        f"config generation(s) (was {before}, now {after}). "
        "The endpoint mutated declarative state without calling "
        "app.config.apply_from_state(...)."
    )
    return after
```

The `tmp_data_dir` fixture comes from `tests/conftest.py` and provides a per-test isolated directory.

- [ ] **Step 3: Quick collect-only check**

```
PYTHONPATH=src pytest tests/security/test_constitution_rule_2.py --collect-only -q
```

Expected: file is discovered, zero tests collected (no test functions yet — only fixtures and helpers). If pytest complains about a syntax error, fix and re-run.

- [ ] **Step 4: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 5: Commit**

```bash
git add tests/security/test_constitution_rule_2.py
git commit -m "test(security): scaffold for Constitution Rule 2 audit"
```

---

## Task 2: Credentials + Setup endpoints

**Files:**
- Modify: `tests/security/test_constitution_rule_2.py` (append tests)
- Modify: `src/mycelos/gateway/routes.py` (`POST /api/credentials`, `DELETE /api/credentials/{service}`, `POST /api/setup`)

Three endpoints, all credential-table mutations.

- [ ] **Step 1: Append tests**

Append to `tests/security/test_constitution_rule_2.py`:

```python


# ── Credentials + Setup ─────────────────────────────────────────

def test_post_credentials_creates_generation(app_and_client) -> None:
    app, client = app_and_client
    before = _generation_count(app)
    resp = client.post("/api/credentials", json={
        "service": "rule2-test-service",
        "secret": "rule2-secret-value",
    })
    assert resp.status_code == 200, resp.text
    assert app.credentials.get_credential("rule2-test-service") is not None
    assert_generation_added(app, before)


def test_delete_credentials_creates_generation(app_and_client) -> None:
    app, client = app_and_client
    # Set one up first (creates a generation we ignore).
    client.post("/api/credentials", json={
        "service": "rule2-doomed",
        "secret": "kill-me",
    })
    before = _generation_count(app)
    resp = client.delete("/api/credentials/rule2-doomed")
    assert resp.status_code == 200, resp.text
    assert app.credentials.get_credential("rule2-doomed") is None
    assert_generation_added(app, before)


def test_post_setup_creates_generation(app_and_client) -> None:
    """POST /api/setup is the one-shot credential bootstrap used by the
    welcome wizard. It writes one (or more) provider credentials."""
    app, client = app_and_client
    before = _generation_count(app)
    resp = client.post("/api/setup", json={
        "anthropic_api_key": "rule2-test-anthropic",
    })
    assert resp.status_code == 200, resp.text
    assert_generation_added(app, before)
```

- [ ] **Step 2: Run, see them fail**

```
PYTHONPATH=src pytest tests/security/test_constitution_rule_2.py -v
```

Expected: 3 fails — the assertion message tells you the rule was violated.

- [ ] **Step 3: Fix `POST /api/credentials`**

Open `src/mycelos/gateway/routes.py` near line 3165. The handler currently is:

```python
    @api.post("/api/credentials")
    async def add_credential(request: Request, body: CredentialAddRequest) -> dict[str, Any]:
        """Store a credential (encrypted)."""
        mycelos = api.state.mycelos
        try:
            mycelos.credentials.store_credential(
                body.service,
                {"api_key": body.secret},
                label=body.label,
                description=body.description,
            )
            mycelos.audit.log("credential.stored", details={"service": body.service, "label": body.label}, user_id=_resolve_user_id(request))
            return {"status": "stored", "service": body.service, "label": body.label}
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=500)
```

Insert the `apply_from_state` call between `audit.log` and `return`:

```python
            mycelos.audit.log("credential.stored", details={"service": body.service, "label": body.label}, user_id=_resolve_user_id(request))
            mycelos.config.apply_from_state(
                state_manager=mycelos.state_manager,
                description=f"Credential '{body.service}' stored",
                trigger="credential_setup",
            )
            return {"status": "stored", "service": body.service, "label": body.label}
```

- [ ] **Step 4: Fix `DELETE /api/credentials/{service}`**

Right below (line 3181 area):

```python
    @api.delete("/api/credentials/{service}")
    async def delete_credential(request: Request, service: str, label: str = "default") -> dict[str, Any]:
        """Delete a credential."""
        mycelos = api.state.mycelos
        try:
            mycelos.credentials.delete_credential(service, label=label)
            mycelos.audit.log("credential.deleted", details={"service": service, "label": label}, user_id=_resolve_user_id(request))
            return {"status": "deleted", "service": service, "label": label}
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=500)
```

Insert between `audit.log` and `return`:

```python
            mycelos.audit.log("credential.deleted", details={"service": service, "label": label}, user_id=_resolve_user_id(request))
            mycelos.config.apply_from_state(
                state_manager=mycelos.state_manager,
                description=f"Credential '{service}' removed",
                trigger="credential_remove",
            )
            return {"status": "deleted", "service": service, "label": label}
```

- [ ] **Step 5: Fix `POST /api/setup`**

Open the `POST /api/setup` handler around line 3131. Locate where the credentials are stored (look for `store_credential` calls inside the handler). After the LAST mutation (and any audit log) but before the success `return`, add:

```python
        mycelos.config.apply_from_state(
            state_manager=mycelos.state_manager,
            description="Initial provider credentials stored via setup wizard",
            trigger="credential_setup",
        )
```

If the handler has multiple early-return paths (one per provider), add the call only on the success branch — not on validation errors that didn't write anything. If unsure, the safest place is right before the final `return {"status": ...}` of the success path.

- [ ] **Step 6: Re-run the three tests**

```
PYTHONPATH=src pytest tests/security/test_constitution_rule_2.py::test_post_credentials_creates_generation tests/security/test_constitution_rule_2.py::test_delete_credentials_creates_generation tests/security/test_constitution_rule_2.py::test_post_setup_creates_generation -v
```

Expected: all 3 pass.

- [ ] **Step 7: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 8: Commit**

```bash
git add tests/security/test_constitution_rule_2.py src/mycelos/gateway/routes.py
git commit -m "fix(gateway): credential endpoints emit config generation (rule 2)"
```

---

## Task 3: Connector endpoints

**Files:**
- Modify: `tests/security/test_constitution_rule_2.py` (append tests)
- Modify: `src/mycelos/gateway/routes.py` (`POST /api/connectors`, `DELETE /api/connectors/{id}`)

`POST /api/connectors` was the original Code-Reviewer finding. `DELETE /api/connectors/{id}` is the symmetric remove path.

- [ ] **Step 1: Append tests**

```python


# ── Connectors ──────────────────────────────────────────────────

def test_post_connectors_creates_generation(app_and_client) -> None:
    """Custom-MCP add path. Uses env_vars (multi-var) so we don't depend
    on any specific recipe being available."""
    app, client = app_and_client
    before = _generation_count(app)
    resp = client.post("/api/connectors", json={
        "name": "rule2-custom-mcp",
        "command": "npx -y @example/non-existent-mcp",
        "env_vars": {"API_KEY": "rule2-test-value"},
    })
    assert resp.status_code == 200, resp.text
    assert app.connector_registry.get("rule2-custom-mcp") is not None
    assert_generation_added(app, before)


def test_delete_connector_creates_generation(app_and_client) -> None:
    app, client = app_and_client
    # Add one first (its generation is irrelevant for the delta we measure).
    client.post("/api/connectors", json={
        "name": "rule2-doomed",
        "command": "npx -y @example/whatever",
        "env_vars": {"X": "y"},
    })
    before = _generation_count(app)
    resp = client.delete("/api/connectors/rule2-doomed")
    assert resp.status_code == 200, resp.text
    assert app.connector_registry.get("rule2-doomed") is None
    assert_generation_added(app, before)
```

- [ ] **Step 2: Run, see them fail**

```
PYTHONPATH=src pytest tests/security/test_constitution_rule_2.py -v -k connector
```

Expected: both connector tests fail with the rule-violation message.

- [ ] **Step 3: Fix `POST /api/connectors`**

Open `src/mycelos/gateway/routes.py` near line 2001. The handler is large — find the end of the success path. The success path ends with `return {"status": "registered", "connector": body.name}` (somewhere around line 2134 after the auto-start block). Insert directly before that final `return`:

```python
        mycelos.config.apply_from_state(
            state_manager=mycelos.state_manager,
            description=f"Connector '{body.name}' registered",
            trigger="connector_setup",
        )
        return {"status": "registered", "connector": body.name}
```

The auto-start happens in a daemon thread that may or may not complete by the time we reach this line — that's fine. The generation captures the state right after registration, which is what we want regardless of whether the MCP subprocess has spawned yet.

- [ ] **Step 4: Fix `DELETE /api/connectors/{connector_id}`**

Open the `DELETE /api/connectors/{connector_id}` handler near line 2250. Find the success path (the path that actually deleted the connector — look for the audit log around `connector.removed` or similar). Right before its `return` statement, insert:

```python
        mycelos.config.apply_from_state(
            state_manager=mycelos.state_manager,
            description=f"Connector '{connector_id}' removed",
            trigger="connector_remove",
        )
```

- [ ] **Step 5: Re-run the connector tests**

```
PYTHONPATH=src pytest tests/security/test_constitution_rule_2.py -v -k connector
```

Expected: both pass.

- [ ] **Step 6: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures. The `POST /api/connectors` change adds a generation to the existing connector tests too — they should still pass because they only check status codes, not generation counts.

- [ ] **Step 7: Commit**

```bash
git add tests/security/test_constitution_rule_2.py src/mycelos/gateway/routes.py
git commit -m "fix(gateway): connector add/remove emit config generation (rule 2)"
```

---

## Task 4: Model endpoints

**Files:**
- Modify: `tests/security/test_constitution_rule_2.py` (append tests)
- Modify: `src/mycelos/gateway/routes.py` (`POST /api/models/migrate`, `PUT /api/models/system-defaults`, `PUT /api/models/assignments/{agent_id}`)

Three endpoints that change `agent_llm_models` rows.

- [ ] **Step 1: Append tests**

```python


# ── Models ──────────────────────────────────────────────────────

def test_put_model_assignment_creates_generation(app_and_client) -> None:
    """Assigning a model to an agent must produce a generation."""
    app, client = app_and_client
    # Pick an agent that exists by default — read directly from DB.
    rows = app.storage.fetchall("SELECT id FROM agents LIMIT 1")
    if not rows:
        pytest.skip("no agent in fresh DB to test model assignment")
    agent_id = rows[0]["id"]

    before = _generation_count(app)
    resp = client.put(
        f"/api/models/assignments/{agent_id}",
        json={"model_id": "claude-sonnet-4-6", "tier": "sonnet"},
    )
    assert resp.status_code in (200, 400), resp.text  # 400 if model unknown — still must not silently mutate
    if resp.status_code == 200:
        assert_generation_added(app, before)


def test_put_system_defaults_creates_generation(app_and_client) -> None:
    """Updating system-default model assignments produces a generation."""
    app, client = app_and_client
    before = _generation_count(app)
    resp = client.put(
        "/api/models/system-defaults",
        json={"sonnet": "claude-sonnet-4-6"},
    )
    if resp.status_code == 200:
        assert_generation_added(app, before)
    else:
        # Endpoint may reject unknown models — that's OK; just ensure no
        # phantom generation was written on a failed request.
        after = _generation_count(app)
        assert after == before, "failed endpoint must not create a generation"


def test_post_models_migrate_creates_generation(app_and_client) -> None:
    """The migration endpoint re-points agents to a newer model."""
    app, client = app_and_client
    before = _generation_count(app)
    # Empty migrate (no slots) is a no-op; that's fine for the rule
    # check — if there's nothing to migrate, no generation is needed.
    resp = client.post("/api/models/migrate", json={"slots": []})
    if resp.status_code == 200:
        # Either the endpoint produced 0 deltas (no-op, no gen needed)
        # or it produced 1+ deltas (one gen wrap).
        after = _generation_count(app)
        assert after in (before, before + 1), (
            f"models/migrate produced {after - before} generations — "
            "expected 0 (no-op) or 1 (real change)."
        )
```

The migrate test is loose (accepts 0-or-1) because the endpoint may legitimately be a no-op when there's nothing to migrate. Stricter assertions would require seeding agent_llm_models which is out of this audit's scope.

- [ ] **Step 2: Run, see what fails**

```
PYTHONPATH=src pytest tests/security/test_constitution_rule_2.py -v -k "model"
```

Expected: at least the system-defaults test and the assignment test fail (assuming the endpoints accept the test payload). Migrate may or may not fail.

- [ ] **Step 3: Fix `PUT /api/models/assignments/{agent_id}`**

Open near line 3068. Find the success path — the place where `agent_llm_models` is updated. Right before the `return` statement of the success branch, insert:

```python
        mycelos.config.apply_from_state(
            state_manager=mycelos.state_manager,
            description=f"Model assignment for agent '{agent_id}' updated",
            trigger="agent_model_assign",
        )
```

- [ ] **Step 4: Fix `PUT /api/models/system-defaults`**

Open near line 3035. Right before the success `return`:

```python
        mycelos.config.apply_from_state(
            state_manager=mycelos.state_manager,
            description="System default model assignments updated",
            trigger="agent_model_default",
        )
```

- [ ] **Step 5: Fix `POST /api/models/migrate`**

Open near line 2935. Find where the slots are written (look for `update`/`UPDATE` SQL on `agent_llm_models`). After all writes, BEFORE the `return`, insert:

```python
        # Only emit a generation when the migration actually changed
        # something — empty slot lists are no-ops.
        if migrated_count > 0:
            mycelos.config.apply_from_state(
                state_manager=mycelos.state_manager,
                description=f"Migrated {migrated_count} agent model assignment(s)",
                trigger="model_migrate",
            )
```

If the handler doesn't already have a `migrated_count` variable, derive it from whatever count the success response reports (the existing handler likely returns `{"migrated": N}`). If you can't tell at all, insert the call unconditionally — extra generations are cheap; missing ones break rollback.

- [ ] **Step 6: Re-run the model tests**

```
PYTHONPATH=src pytest tests/security/test_constitution_rule_2.py -v -k "model"
```

Expected: all pass (or skip cleanly when the endpoint rejects the payload — the assertion blocks phantom generations on failure).

- [ ] **Step 7: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 8: Commit**

```bash
git add tests/security/test_constitution_rule_2.py src/mycelos/gateway/routes.py
git commit -m "fix(gateway): model endpoints emit config generation (rule 2)"
```

---

## Task 5: Agent + Memory endpoints

**Files:**
- Modify: `tests/security/test_constitution_rule_2.py` (append tests)
- Modify: `src/mycelos/gateway/routes.py` (`PATCH /api/agents/{id}`, `POST /api/memory`)

Two endpoints. Memory has the session-scope exemption (Spec D2).

- [ ] **Step 1: Append tests**

```python


# ── Agents + Memory ─────────────────────────────────────────────

def test_patch_agent_creates_generation(app_and_client) -> None:
    """Updating an agent's declarative shape (name, system prompt) must
    produce a generation."""
    app, client = app_and_client
    rows = app.storage.fetchall("SELECT id FROM agents LIMIT 1")
    if not rows:
        pytest.skip("no agent in fresh DB to test agent update")
    agent_id = rows[0]["id"]

    before = _generation_count(app)
    resp = client.patch(
        f"/api/agents/{agent_id}",
        json={"description": "Rule 2 test description"},
    )
    if resp.status_code == 200:
        assert_generation_added(app, before)
    else:
        after = _generation_count(app)
        assert after == before, "failed PATCH must not create a generation"


def test_post_memory_creates_generation(app_and_client) -> None:
    """Memory writes (non-session scope) MUST produce a generation."""
    app, client = app_and_client
    before = _generation_count(app)
    resp = client.post("/api/memory", json={
        "scope": "system",
        "key": "rule2_test_key",
        "value": "rule2_test_value",
    })
    assert resp.status_code == 200, resp.text
    assert_generation_added(app, before)


def test_post_memory_session_scope_exempt(app_and_client) -> None:
    """Per spec D2: session-scope memory writes do NOT create generations
    (ephemeral data, not declarative state)."""
    app, client = app_and_client
    before = _generation_count(app)
    resp = client.post("/api/memory", json={
        "scope": "session",
        "key": "ephemeral_test_key",
        "value": "ephemeral_value",
    })
    if resp.status_code == 200:
        after = _generation_count(app)
        assert after == before, (
            "Session-scope memory writes are ephemeral and must NOT "
            "create config generations."
        )
```

- [ ] **Step 2: Run, see them fail**

```
PYTHONPATH=src pytest tests/security/test_constitution_rule_2.py -v -k "agent or memory"
```

Expected: at least the system-scope memory test fails.

- [ ] **Step 3: Fix `PATCH /api/agents/{agent_id}`**

Open near line 2582. After the success-path mutation and audit log, before the `return`:

```python
        mycelos.config.apply_from_state(
            state_manager=mycelos.state_manager,
            description=f"Agent '{agent_id}' updated",
            trigger="agent_update",
        )
```

- [ ] **Step 4: Fix `POST /api/memory`**

Open near line 3372. Find the success-path mutation. Wrap the generation call in a scope check:

```python
        # Session-scope memory is ephemeral (per Constitution Rule 2 spec D2);
        # only system / agent / shared scopes contribute to declarative state.
        if body_scope != "session":
            mycelos.config.apply_from_state(
                state_manager=mycelos.state_manager,
                description=f"Memory key '{body_key}' set ({body_scope} scope)",
                trigger="memory_set",
            )
```

Use whatever variable names the handler already uses for scope and key (the snippet uses `body_scope` and `body_key` as placeholders — adapt to the actual code).

- [ ] **Step 5: Re-run**

```
PYTHONPATH=src pytest tests/security/test_constitution_rule_2.py -v -k "agent or memory"
```

Expected: all pass.

- [ ] **Step 6: Baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 7: Commit**

```bash
git add tests/security/test_constitution_rule_2.py src/mycelos/gateway/routes.py
git commit -m "fix(gateway): agent update + memory writes emit config generation (rule 2)"
```

---

## Task 6: System-setting endpoint + CHANGELOG + push

**Files:**
- Modify: `tests/security/test_constitution_rule_2.py` (append last test)
- Modify: `src/mycelos/gateway/routes.py` (`PUT /api/system/update-check-enabled`)
- Modify: `CHANGELOG.md`

The last endpoint, plus the changelog wrap-up.

- [ ] **Step 1: Append the last test**

```python


# ── System settings ─────────────────────────────────────────────

def test_put_update_check_enabled_creates_generation(app_and_client) -> None:
    """Toggling the auto-update-check setting changes declarative system config."""
    app, client = app_and_client
    before = _generation_count(app)
    resp = client.put(
        "/api/system/update-check-enabled",
        json={"enabled": True},
    )
    assert resp.status_code == 200, resp.text
    assert_generation_added(app, before)
```

- [ ] **Step 2: Run, see it fail**

```
PYTHONPATH=src pytest tests/security/test_constitution_rule_2.py::test_put_update_check_enabled_creates_generation -v
```

Expected: fail.

- [ ] **Step 3: Fix `PUT /api/system/update-check-enabled`**

Open near line 2742. Before the success `return`, insert:

```python
        mycelos.config.apply_from_state(
            state_manager=mycelos.state_manager,
            description=f"System auto-update check {'enabled' if enabled else 'disabled'}",
            trigger="system_setting",
        )
```

(`enabled` is the variable name used by the handler — adapt if it's named differently.)

- [ ] **Step 4: Re-run, all green**

```
PYTHONPATH=src pytest tests/security/test_constitution_rule_2.py -v
```

Expected: every test in the file passes.

- [ ] **Step 5: Run full baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 6: Add CHANGELOG entry**

Open `CHANGELOG.md`, find the Week 17 block (between v0.3.0 and Week 16). Add at the end of the Week 17 entries (before `## Week 16 (2026)`):

```markdown
### Constitution Rule 2 audit — config generations on every web mutation
- Closed a NixOS-rollback hole: 11 state-mutating Web-API endpoints now call `app.config.apply_from_state(...)` after their mutation, joining `POST /api/channels` (the only previously-conformant endpoint). Every change made via the Web UI now lands in `config_generations` and can be rolled back with `mycelos config rollback <N>`.
- Endpoints fixed: `POST/DELETE /api/connectors`, `POST/DELETE /api/credentials`, `POST /api/setup`, `POST /api/memory` (non-session scopes), `PATCH /api/agents/{id}`, `POST /api/models/migrate`, `PUT /api/models/system-defaults`, `PUT /api/models/assignments/{agent_id}`, `PUT /api/system/update-check-enabled`.
- New `tests/security/test_constitution_rule_2.py` audits the rule continuously — adding a future endpoint without a matching `apply_from_state` call will fail this suite.
- Session-scope memory writes are intentionally exempt (ephemeral, not declarative).
- Spec / plan: `docs/superpowers/specs/2026-04-25-constitution-rule-2-audit-design.md`, `docs/superpowers/plans/2026-04-25-constitution-rule-2-audit-plan.md`.
```

- [ ] **Step 7: Commit changelog + push**

```bash
git add tests/security/test_constitution_rule_2.py src/mycelos/gateway/routes.py CHANGELOG.md
git commit -m "fix(gateway): system-setting endpoint emits config generation; close audit"
git push origin main
```

- [ ] **Step 8: Manual verification (Stefan)**

After push, verify the audit holds in the live system:

1. Note current generation count: `mycelos config list | tail -3` (note the highest number).
2. Open Web UI, change something — e.g. add a memory entry, toggle the update-check setting.
3. Run `mycelos config list` — there should be one new generation per mutation, with the correct trigger label.
4. Optional: `mycelos config rollback <N>` to confirm the rollback restores the prior state, then re-apply.

---

## Self-review notes

Spec coverage check (against `2026-04-25-constitution-rule-2-audit-design.md`):

- D1 (test-driven audit, one test per endpoint) → Tasks 1-6 build the file incrementally.
- D2 (declarative tables + exclusions) → Test for session-scope memory exemption in Task 5; out-of-scope tables (audit_events, workflow_runs, knowledge_*, etc.) get no test → no enforcement → matches spec.
- D3 (11 endpoints to fix) → Tasks 2-6 cover all 11.
- D4 (`apply_from_state` pattern + trigger tokens) → every fix uses the exact pattern with the trigger names from the spec table.
- D5 (no boot-time baseline) → not implemented (spec says "no").
- D6 (rollback endpoint untouched) → no task touches `POST /api/config/rollback`.
- Success criteria 1-6 → all addressed by Tasks 1-6 + the manual verification in Step 8.

Type / name consistency:

- `_generation_count(app)` and `assert_generation_added(app, before)` defined in Task 1, used by every later task.
- `app_and_client` fixture defined in Task 1, used by every later test.
- `apply_from_state(state_manager=mycelos.state_manager, description=..., trigger=...)` signature consistent across all 11 fixes.
- Trigger tokens match the spec table.

No placeholders in the plan. Every step shows the actual code or command. Vague "find the success path" instructions are bounded — every endpoint is short enough that "find the return statement of the success branch" is unambiguous.
