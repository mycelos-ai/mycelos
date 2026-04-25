# Constitution Rule 2 Audit — Design

**Date:** 2026-04-25
**Status:** Draft
**Scope:** Close a systematic NixOS-rollback-promise hole: every Web-API endpoint that mutates declarative state must call `app.config.apply_from_state(...)` so the change is captured as a config generation. Today only one (`POST /api/channels`) does so.

## Problem

CLAUDE.md Rule 2 ("Config Generation on State Change") says every change to declarative state MUST create a new config generation. The CLI commands honour this — `connector_cmd.py`, `init_cmd.py`, `model_cmd.py` all call `apply_from_state` after mutations. The Web-API does not, with one exception:

```
$ grep -c apply_from_state src/mycelos/gateway/routes.py
1
```

Result: any change made via the Web UI cannot be rolled back to a "state of just before this change" — `mycelos config rollback` only rewinds to whatever the last CLI-driven generation was. This breaks the NixOS promise.

## Goal

After this spec ships, every state-mutating Web endpoint that touches a declarative table writes a fresh generation, and a test suite locks the invariant in place so future endpoints don't regress.

## Decisions

### D1: Test-driven audit (one test per mutating endpoint)

A new test file `tests/security/test_constitution_rule_2.py` contains one test per relevant endpoint. Each test:

1. Reads the current `MAX(id)` from `config_generations`.
2. Calls the endpoint with a realistic request body.
3. Asserts the mutation happened (the relevant DB row exists / is updated / is deleted).
4. Asserts `MAX(id)` from `config_generations` increased by exactly 1.

A test that's red is a bug; we fix the corresponding handler by adding the missing `apply_from_state` call. A test that's green proves the endpoint is conformant.

The file lives under `tests/security/` because Rule 2 is a security-relevant invariant (rollback ability is a recovery primitive).

### D2: Declarative tables in scope

These tables hold declarative state and changes to them must produce a generation:

- `connectors` + `connector_capabilities`
- `channels`
- `credentials` (the rotation/grant lifecycle is part of declarative config)
- `agent_llm_models` + `agent_capabilities`
- `agents` (when shape changes — name, model, system prompt)
- `policies`
- `scheduled_tasks`
- `memory_entries` (system / agent / shared scopes; **session scope excluded**)
- `mounts`
- `workflows`

Out of scope (content / ephemeral / observability):

- `audit_events` (write-only log)
- `workflow_runs`, `workflow_events` (execution traces)
- `knowledge_notes`, `knowledge_links`, `knowledge_config` (content — filesystem-versioned, plus organizer suggestions)
- `messages`, `conversations`, `attempts`, `plans`, `tasks`, `background_tasks`, `background_task_steps`
- `mcp_sessions`, `connector_telemetry`, `tool_usage`, `llm_usage`
- `session_agents`, `capability_tokens`
- `organizer_suggestions`
- `users` (single-user; addressed by separate auth spec)

### D3: Endpoints that need a fix

Audited list as of 2026-04-25:

| Endpoint | Writes to | Status |
|---|---|---|
| `POST /api/channels` | channels | ✅ already conformant |
| `POST /api/connectors` | connectors, connector_capabilities, credentials, channels | ❌ fix |
| `DELETE /api/connectors/{id}` | connectors + caps + credentials | ❌ fix |
| `PATCH /api/agents/{id}` | agents | ❌ fix |
| `POST /api/models/migrate` | agent_llm_models | ❌ fix |
| `PUT /api/models/system-defaults` | agent_llm_models (system rows) | ❌ fix |
| `PUT /api/models/assignments/{agent_id}` | agent_llm_models | ❌ fix |
| `POST /api/setup` | credentials | ❌ fix |
| `POST /api/credentials` | credentials | ❌ fix |
| `DELETE /api/credentials/{service}` | credentials | ❌ fix |
| `POST /api/memory` | memory_entries (non-session scopes) | ❌ fix (session-scope writes are exempt) |
| `PUT /api/system/update-check-enabled` | system config | ❌ fix |
| `POST /api/config/rollback` | restores a generation | n/a (rollback uses its own path) |

Endpoints that touch tables but only in observability / ephemeral ways are out of scope: `POST /api/chat`, `POST /api/sessions`, `POST /api/transcribe`, `POST /api/upload`, `POST /api/connectors/oauth/start` (writes a transient `oauth_state` row only), all knowledge/organizer endpoints, telegram check/verify, model refresh.

### D4: How handlers call `apply_from_state`

The existing pattern (`gateway/routes.py:2532`):

```python
mycelos.config.apply_from_state(
    state_manager=mycelos.state_manager,
    description=f"Channel '{name}' configured",
    trigger="channel_setup",
)
```

Each fixed handler gets a similar call AFTER the mutation succeeds. The `description` is human-readable, the `trigger` is a short token used by `mycelos config list` to filter. We use these triggers consistently:

- `connector_setup`, `connector_remove`
- `agent_update`, `agent_model_assign`, `agent_model_default`
- `credential_setup`, `credential_remove`
- `memory_set`
- `model_migrate`
- `system_setting`

If a handler is wrapped in a `try/except` that swallows mutation errors (Rule 3 — fail-closed), the `apply_from_state` call goes INSIDE the success path so failed mutations don't produce phantom generations.

### D5: Initial baseline

Per Stefan's call (Frage 2): no automatic baseline-snapshot at boot. The next mutating call after this fix ships writes a generation that captures the current state. Older changes are simply not roll-back-able to a "between-then-and-now" point — but the user is single-user and aware.

### D6: No rollback regression

The `POST /api/config/rollback` endpoint stays as-is — it uses its own `rollback(to_generation=...)` path which is the inverse of `apply_from_state`. We do NOT add a generation for "I rolled back" (that would defeat rollback's purpose). The rollback itself is audit-logged, which is enough.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│ tests/security/test_constitution_rule_2.py (NEW)     │
│   one test per mutating endpoint                     │
│   pre-state count → call → post-state +1 assertion   │
└──────────────────────────────────────────────────────┘
                       ↓ red tests reveal:
┌──────────────────────────────────────────────────────┐
│ src/mycelos/gateway/routes.py                        │
│   add `apply_from_state` after each successful       │
│   mutation in: ~12 handlers (see D3 table)           │
└──────────────────────────────────────────────────────┘
```

## Components

### `tests/security/test_constitution_rule_2.py` (new)

One test per endpoint in D3. Common structure:

```python
def test_post_credentials_creates_generation(client, app) -> None:
    before = app.storage.fetchone(
        "SELECT COALESCE(MAX(id), 0) AS max_id FROM config_generations"
    )["max_id"]

    resp = client.post("/api/credentials", json={
        "service": "test-service",
        "secret": "test-value",
    })
    assert resp.status_code == 200, resp.text

    after = app.storage.fetchone(
        "SELECT COALESCE(MAX(id), 0) AS max_id FROM config_generations"
    )["max_id"]
    assert after == before + 1, (
        "Constitution Rule 2: state-mutating endpoint must create a "
        "config generation"
    )
```

A shared fixture provides `client` (FastAPI `TestClient`) and `app` (the underlying `App` instance for direct DB queries).

For endpoints that modify state without inserting (PUT/PATCH/DELETE), the test uses the same generation-count-delta assertion. The "mutation actually happened" assertion adapts: e.g. `DELETE /api/credentials/x` asserts the row is gone; `PATCH /api/agents/x` asserts the field is updated.

For endpoints with multiple sub-paths (e.g. `POST /api/connectors` with recipe vs. custom), only one representative test per endpoint — the goal is rule enforcement, not exhaustive endpoint coverage.

### `src/mycelos/gateway/routes.py`

For each red test, find the corresponding handler and add `apply_from_state` after the mutation block. Trigger / description per D4.

Example for `POST /api/credentials` (around line 3165):

```python
@api.post("/api/credentials")
async def add_credential(body: CredentialAddRequest, request: Request) -> dict:
    mycelos = api.state.mycelos
    mycelos.credentials.store_credential(
        body.service, {"api_key": body.secret}, description=body.description
    )
    mycelos.audit.log("credential.stored", details={"service": body.service},
                      user_id=_resolve_user_id(request))
    mycelos.config.apply_from_state(
        state_manager=mycelos.state_manager,
        description=f"Credential '{body.service}' stored",
        trigger="credential_setup",
    )
    return {"status": "ok"}
```

The `state_manager` attribute on `App` already exists (CLI uses it).

## Data Flow

```
User writes via Web UI
  ↓
POST /api/credentials {service, secret}
  ↓
Handler:
  credentials.store_credential(...)   ← table mutation
  audit.log("credential.stored", ...) ← Rule 1
  config.apply_from_state(...)         ← Rule 2 (NEW)
  ↓
config_generations gains a new row
  ↓
Later: `mycelos config list` shows the change
       `mycelos config rollback N` restores prior state
```

## Error Handling

- **Mutation succeeds, `apply_from_state` raises:** log a warning with `"connector.gen_failed"` audit event but return 200 to the user — the data IS in the DB even if the generation snapshot failed. Better to have an audit-trail-only record than silently undo the mutation. (Same fail mode the existing `POST /api/channels` handler accepts.)
- **Mutation fails:** existing error path returns 500 / 4xx; `apply_from_state` is never reached; no phantom generation. ✓
- **Test detects N+1 ≠ before+1:** test fails with the spec violation message; engineer fixes the handler.

## Testing

The test file IS the spec. Running the suite is the audit:

```
PYTHONPATH=src pytest tests/security/test_constitution_rule_2.py -v
```

Initial run reveals all violations. Each fix turns one test green. Final run: all green.

The existing baseline (`pytest tests/`) must stay at zero failures throughout — fixing handlers should not break any test that previously passed (the new `apply_from_state` call adds a row to `config_generations`; nothing else observable changes).

Manual verification (Stefan, after the fix lands):

1. Open the Web UI, set up a new connector or change a model assignment.
2. Run `mycelos config list` — the new entry appears with the correct trigger label.
3. Note the generation number, change something else, run `mycelos config rollback <N>` to confirm the rollback restores the prior state.

## Success Criteria

1. `tests/security/test_constitution_rule_2.py` exists with one test per endpoint in D3.
2. All tests in that file pass.
3. The existing baseline still passes (zero failures).
4. Every endpoint listed as "❌ fix" in D3 has a `apply_from_state(...)` call after its mutation.
5. CHANGELOG entry under Week 17.
6. New endpoints added to `routes.py` in the future SHOULD include their test in this file (note in the file's docstring).

## Non-Goals

- Initial baseline snapshot at boot (per D5).
- Per-row generation deltas (a generation captures everything; that's a NixOS feature, not a database schema change).
- Migrating existing CLI handlers to a different pattern (they're already conformant).
- Auth / user model (separate spec, Task #76).
- The `add_connector` channels-table insert getting its own generation distinct from the connector's — one generation per endpoint call is enough.
