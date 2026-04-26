# Constitution Rule 2 Audit — Design (revised 2026-04-26)

**Date:** 2026-04-25 (revised 2026-04-26 after code audit)
**Status:** Draft
**Scope:** Verify the NixOS-rollback promise for Web-API mutations and lock the invariant in tests for the future. Plus clarify CLAUDE.md so future code reviews don't re-stumble into the false diagnosis that triggered this spec.

## Background — what the audit found

Initial trigger: a code review of `add_connector` claimed the handler skips `apply_from_state`, breaking Rule 2. A direct grep of `routes.py` shows only one `apply_from_state` call (in `POST /api/channels`), which seemed to confirm a systemic hole across ~12 web endpoints.

The audit (subagent ran live tests + I traced the service layer) revealed the real shape:

- **`ConfigNotifier.notify_change()` (`src/mycelos/config/notifier.py`)** wraps `apply_from_state` and is called from every state-mutating service-layer method:
  - `CredentialService.store_credential / delete_credential / rotate_credential`
  - `ConnectorRegistry.register / set_status / update / remove`
  - `ModelRegistry.add_model / remove_model / set_system_defaults / set_agent_models`
  - `AgentRegistry.register / update / set_capabilities / set_models / …`
  - `MountManager.add / revoke / delete`
  - `PolicyEngine` policy writes
- The handlers in `routes.py` don't need to call `apply_from_state` themselves because the service-layer methods they call already do. The single explicit call in `POST /api/channels` exists because that handler bypasses the service layer (direct `storage.execute` on the `channels` table).

Memory and Knowledge are not declarative state at all — they're content (user preferences, notes, the LLM's growing model of the user) and are intentionally outside Rule 2's scope.

**Conclusion: there are zero open Rule 2 violations.** The spec's premise was wrong. What still has value is (a) a test suite that locks the invariant down for the future and (b) a CLAUDE.md clarification so the same false diagnosis doesn't keep recurring.

## Decisions

### D1: No handler-level fixes

`routes.py` stays untouched. Every existing mutator that goes through a service-layer method is already conformant. The `POST /api/channels` direct insert keeps its explicit `apply_from_state` call (D5).

### D2: Audit test suite as regression guard

A new file `tests/security/test_constitution_rule_2.py` contains one test per state-mutating Web endpoint. Each test:

1. Reads `MAX(id) FROM config_generations`.
2. Calls the endpoint with a realistic request body.
3. Asserts the mutation actually happened (DB row exists / removed / updated).
4. Asserts `MAX(id)` advanced by exactly 1.

A future endpoint that writes directly to a state table without going through a notifier-wired service will fail this suite — that's the regression-guard property we want.

The tests should mostly pass on first run because the system is already conformant. Any test that fails reveals a real bug (e.g. someone added a new endpoint with a direct write).

### D3: Endpoints in scope (declarative state only)

Per the spec's earlier audit, in scope:

- `POST /api/connectors`, `DELETE /api/connectors/{id}`
- `POST /api/credentials`, `DELETE /api/credentials/{service}`
- `POST /api/setup`
- `PATCH /api/agents/{id}`
- `POST /api/models/migrate`, `PUT /api/models/system-defaults`, `PUT /api/models/assignments/{agent_id}`
- `POST /api/channels` (already explicit)

Out of scope (clarified in D4):
- `POST /api/memory`, `PUT /api/system/update-check-enabled` — both write to `memory_entries`, which is content, not config.
- All knowledge / organizer / chat / session / audio / upload endpoints.

### D4: CLAUDE.md clarification — what counts as declarative state

Add a short paragraph to `CLAUDE.md` Rule 2 to nail down which tables are in scope. Without this, the next code reviewer will trip on the same shape.

Proposed text (to be inserted in the Rule 2 description in CLAUDE.md):

> **What counts as declarative state for Rule 2:** the tables that describe *how the system is configured* — `connectors`, `connector_capabilities`, `channels`, `credentials`, `agents`, `agent_capabilities`, `agent_llm_models`, `policies`, `scheduled_tasks`, `mounts`, `workflows`. These are managed via service-layer classes (`ConnectorRegistry`, `CredentialService`, `AgentRegistry`, `ModelRegistry`, `MountManager`, `PolicyEngine`) which call `ConfigNotifier.notify_change()` after every mutation; that's how Rule 2 is enforced today. New endpoints that write to these tables MUST go through the service layer (or call `apply_from_state` explicitly if they bypass it, like `POST /api/channels`).
>
> **What does NOT count as declarative state:** content tables (`knowledge_notes`, `knowledge_links`, `memory_entries`), execution traces (`audit_events`, `workflow_runs`, `workflow_events`, `tool_usage`, `llm_usage`), and ephemeral/session data (`messages`, `conversations`, `attempts`, `plans`, `tasks`, `background_tasks`, `mcp_sessions`, `connector_telemetry`, `session_agents`, `capability_tokens`, `organizer_suggestions`). These do not need config generations.

### D5: Direct `storage.execute` writes are an antipattern

`POST /api/channels` and the channel-row insert inside `add_connector` (line 2133) bypass the service layer and write directly to the `channels` table. The former calls `apply_from_state` explicitly; the latter relies on the prior `connector_registry.register()` having captured the state via its own notifier (since `apply_from_state` snapshots the whole DB).

We won't fix the channel-row direct insert in this spec — it's cosmetic (the snapshot includes it) — but the audit test for `POST /api/connectors` will catch it if someone ever moves the connector_registry call elsewhere and breaks the implicit coverage.

### D6: No handler refactors, no service-layer changes

The original spec had 11 handler edits and a Memory-service notifier addition. After audit: zero edits needed. This is a tests-only spec.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│ tests/security/test_constitution_rule_2.py (NEW)     │
│   1 test per state-mutating endpoint (~10 tests)     │
│   each: pre-count → call → mutation+1 generation     │
└──────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│ CLAUDE.md (revised)                                  │
│   Rule 2 section gains "what counts" paragraph (D4)  │
└──────────────────────────────────────────────────────┘
```

## Components

### `tests/security/test_constitution_rule_2.py` (new)

Common fixtures + helpers:

```python
@pytest.fixture
def app_and_client(tmp_data_dir):
    """Initialised App + bound TestClient."""
    os.environ["MYCELOS_MASTER_KEY"] = "constitution-rule-2-test-key"
    from mycelos.app import App
    from mycelos.gateway.server import create_app
    app = App(tmp_data_dir)
    app.initialize()
    fastapi_app = create_app(tmp_data_dir, no_scheduler=True, host="0.0.0.0")
    with TestClient(fastapi_app) as client:
        yield app, client


def _generation_count(app):
    row = app.storage.fetchone(
        "SELECT COALESCE(MAX(id), 0) AS max_id FROM config_generations"
    )
    return int(row["max_id"])


def assert_generation_added(app, before, *, expected_delta=1):
    after = _generation_count(app)
    assert after == before + expected_delta, (
        f"Constitution Rule 2 violation: expected {expected_delta} new "
        f"config generation(s) (was {before}, now {after}). "
        "The endpoint mutated declarative state without going through "
        "a notifier-wired service-layer method."
    )
    return after
```

One test per endpoint in D3. For an endpoint with multiple sub-paths (e.g. `POST /api/connectors` recipe vs. custom), one representative test is enough.

### `CLAUDE.md`

Add the D4 paragraph to the Rule 2 description in the Constitution section.

## Data Flow

Existing flow, unchanged. Documented for clarity:

```
Handler (e.g. POST /api/credentials)
  ↓
mycelos.credentials.store_credential(...)        [service layer]
  ↓
self._storage.execute("INSERT INTO credentials ...")
self._notifier.notify_change("Credential stored", "credential_store")
  ↓
ConfigNotifier.notify_change()
  ↓
self._config.apply_from_state(state_manager, description, trigger)
  ↓
config_generations table gains a new row
```

The handler doesn't see any of this — Rule 2 is enforced at the service-layer boundary.

## Error Handling

If a service-layer method raises mid-mutation, neither the row nor the generation is written (transactional). If the generation insert fails after the row is written, `ConfigNotifier` logs a warning and continues — the data is still in the DB but rollback may be off by one. Pre-existing behavior; not in scope to change here.

## Testing

The test file IS the audit. Running it is the verification:

```
PYTHONPATH=src pytest tests/security/test_constitution_rule_2.py -v
```

Expected on the first run: all tests pass (the system is already conformant — that's the audit conclusion). Future regressions break red.

The existing baseline (`pytest tests/`) must stay at zero failures throughout.

Manual verification (after merge, optional): open the Web UI, change something, run `mycelos config list` — the change should appear with a service-layer trigger label (`credential_store`, `connector_register`, etc.).

## Success Criteria

1. `tests/security/test_constitution_rule_2.py` exists with one test per endpoint in D3.
2. All tests pass on first run (the audit confirms the system is conformant).
3. Existing baseline still green (zero failures).
4. CLAUDE.md gains the D4 paragraph clarifying which tables count.
5. CHANGELOG entry under Week 17.

## Non-Goals

- Memory service notifier addition (memory is content, not config).
- Channel-row-insert direct-write fix in `add_connector` (cosmetic — covered by the prior `connector_registry.register` generation).
- Removing the legacy `apply_from_state` call in `POST /api/channels` (it's correct because the handler bypasses the service layer).
- Refactoring `routes.py` for any reason.
- Auth / user model.

## Notes for future code reviewers

If you're tempted to flag a `routes.py` handler as missing `apply_from_state`:

1. Check whether the handler calls a service-layer method (`mycelos.credentials.*`, `mycelos.connector_registry.*`, `mycelos.model_registry.*`, `mycelos.agent_registry.*`, `mycelos.mounts.*`, `mycelos.policy_engine.*`).
2. If yes, that method calls `ConfigNotifier.notify_change()` which calls `apply_from_state`. The handler is conformant.
3. If no — the handler does direct `storage.execute` on a state table — flag it. Add an explicit `apply_from_state` call (like `POST /api/channels`) or refactor through a service.
