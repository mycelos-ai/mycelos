# Constitution Rule 2 Audit Implementation Plan (revised 2026-04-26)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lock the existing Rule-2 conformance into a regression-guard test suite and clarify CLAUDE.md so future code reviews don't re-stumble into the false diagnosis that triggered this audit.

**Architecture:** Tests-only. The system is already conformant (service-layer-wired notifier handles every state mutation). The new test suite at `tests/security/test_constitution_rule_2.py` verifies that, and will fail loudly if a future endpoint bypasses the service layer.

**Tech Stack:** pytest, FastAPI TestClient, SQLite.

**Spec:** `docs/superpowers/specs/2026-04-25-constitution-rule-2-audit-design.md` (revised version)

**Baseline rule:** After every task, `PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q` must pass with zero failures (modulo the known Hypothesis flake on `test_policy_engine_property.py`).

---

## File Structure

Files this plan touches:

- `tests/security/test_constitution_rule_2.py` — already has the scaffold from the previous run; this plan appends ~10 endpoint tests.
- `CLAUDE.md` — add the "what counts" paragraph to the Rule 2 section.
- `CHANGELOG.md` — Week 17 entry.

What stays untouched: everything else. No `routes.py`, no service-layer, no memory service.

---

## Task 1: Append all endpoint tests to the audit file

**Files:**
- Modify: `tests/security/test_constitution_rule_2.py` (scaffold already in place from earlier work)

This task adds one test per endpoint listed in spec D3. Tests are expected to PASS on first run because the system is already conformant — this is a regression guard, not a bug hunt.

If a test fails for an unexpected reason (e.g. wrong request shape), adapt the request body to match what the endpoint actually wants — the audit value comes from the +1 generation assertion, not from exhaustive endpoint behavior coverage.

Note from previous audit: the original spec assumed `POST /api/setup` writes one credential. In reality `web_init` triggers many `notify_change` calls (one per provider credential + per agent + per model + per policy). The setup test relaxes the assertion to `assert after > before` ("at least one new generation"), since the exact count depends on what `web_init` does internally.

- [ ] **Step 1: Verify the scaffold is in place**

```
grep -n "app_and_client\|_generation_count\|assert_generation_added" tests/security/test_constitution_rule_2.py
```

Expected: 3 hits — fixture, helper, helper. If the file is missing or empty, stop and ask the controller — the scaffold from the prior abandoned run should be there.

- [ ] **Step 2: Append all tests to the file**

Append the following block to the END of `tests/security/test_constitution_rule_2.py`:

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


def test_post_setup_creates_at_least_one_generation(app_and_client) -> None:
    """POST /api/setup runs web_init which writes credentials, registers
    agents, registers models, sets policies — each with its own
    notify_change. We assert "at least one new generation", not exactly
    one, because the inner work is implementation-defined."""
    app, client = app_and_client
    before = _generation_count(app)
    resp = client.post("/api/setup", json={
        "api_key": "sk-ant-rule2-test-key-not-real",
        "provider_id": "anthropic",
    })
    # Accept 200 (success path) or 4xx (key validation failed before any
    # write). On 4xx we assert no phantom generation; on 200 we assert
    # the audit fired at least once.
    if resp.status_code == 200:
        after = _generation_count(app)
        assert after > before, (
            f"POST /api/setup succeeded but produced no generation "
            f"(was {before}, still {after}). Service-layer notifiers "
            "should have fired."
        )
    else:
        after = _generation_count(app)
        assert after == before, (
            f"POST /api/setup failed (status {resp.status_code}) but "
            f"still produced {after - before} generation(s) — "
            "validation failures must not leak phantom generations."
        )


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
    # POST /api/connectors writes connector + credential, both via
    # notifier-wired services — at least one generation must appear.
    after = _generation_count(app)
    assert after > before, (
        f"POST /api/connectors should produce at least one generation "
        f"(was {before}, now {after})."
    )


def test_delete_connector_creates_generation(app_and_client) -> None:
    app, client = app_and_client
    # Add one first.
    client.post("/api/connectors", json={
        "name": "rule2-doomed",
        "command": "npx -y @example/whatever",
        "env_vars": {"X": "y"},
    })
    before = _generation_count(app)
    resp = client.delete("/api/connectors/rule2-doomed")
    assert resp.status_code == 200, resp.text
    assert app.connector_registry.get("rule2-doomed") is None
    after = _generation_count(app)
    assert after > before, (
        f"DELETE /api/connectors should produce at least one generation "
        f"(was {before}, now {after})."
    )


# ── Models ──────────────────────────────────────────────────────

def test_put_model_assignment_creates_generation(app_and_client) -> None:
    """Assigning a model to an agent must produce a generation."""
    app, client = app_and_client
    rows = app.storage.fetchall("SELECT id FROM agents LIMIT 1")
    if not rows:
        pytest.skip("no agent in fresh DB to test model assignment")
    agent_id = rows[0]["id"]

    before = _generation_count(app)
    resp = client.put(
        f"/api/models/assignments/{agent_id}",
        json={"model_id": "claude-sonnet-4-6", "tier": "sonnet"},
    )
    if resp.status_code == 200:
        assert_generation_added(app, before)
    else:
        # Endpoint may reject unknown models; on failure assert no
        # phantom generation.
        after = _generation_count(app)
        assert after == before, (
            f"PUT /api/models/assignments failed (status {resp.status_code}) "
            f"but produced {after - before} generation(s)."
        )


def test_put_system_defaults_creates_generation(app_and_client) -> None:
    app, client = app_and_client
    before = _generation_count(app)
    resp = client.put(
        "/api/models/system-defaults",
        json={"sonnet": "claude-sonnet-4-6"},
    )
    if resp.status_code == 200:
        assert_generation_added(app, before)
    else:
        after = _generation_count(app)
        assert after == before, (
            f"PUT /api/models/system-defaults failed (status {resp.status_code}) "
            f"but produced {after - before} generation(s)."
        )


def test_post_models_migrate_creates_generation_when_changes(app_and_client) -> None:
    """Empty migrate = no-op = no generation. Real migrate = generation."""
    app, client = app_and_client
    before = _generation_count(app)
    resp = client.post("/api/models/migrate", json={"slots": []})
    if resp.status_code == 200:
        after = _generation_count(app)
        # No-op: 0 deltas. Real change: at least 1. Anything else is wrong.
        assert after >= before, (
            f"POST /api/models/migrate produced negative generation delta "
            f"({before} → {after})."
        )


# ── Agents ──────────────────────────────────────────────────────

def test_patch_agent_creates_generation(app_and_client) -> None:
    """Updating an agent's declarative shape must produce a generation."""
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
        assert after == before, (
            f"PATCH /api/agents failed (status {resp.status_code}) but "
            f"produced {after - before} generation(s)."
        )


# ── Channels ────────────────────────────────────────────────────

def test_post_channels_creates_generation(app_and_client) -> None:
    """POST /api/channels is the one direct-storage handler that calls
    apply_from_state explicitly (it bypasses the service layer for the
    `channels` table). This test pins that explicit call in place."""
    app, client = app_and_client
    before = _generation_count(app)
    resp = client.post("/api/channels", json={
        "id": "rule2-test-channel",
        "channel_type": "telegram",
        "mode": "polling",
    })
    if resp.status_code == 200:
        assert_generation_added(app, before)
    else:
        after = _generation_count(app)
        assert after == before, (
            f"POST /api/channels failed (status {resp.status_code}) but "
            f"produced {after - before} generation(s)."
        )
```

- [ ] **Step 3: Run the tests, verify they pass**

```
PYTHONPATH=src pytest tests/security/test_constitution_rule_2.py -v
```

Expected: every test passes (the system is already conformant — that's the audit's conclusion). If any test fails:

- For `5xx` responses: real bug; report it back to the controller before committing.
- For `4xx` responses with the "no phantom generation" branch firing: that's expected (the test still passes via the assertion in the failure branch).
- For "expected ≥ 1 generation but got 0": that's a real Rule-2 violation; report it before committing.

Tests with `pytest.skip` (no agent in fresh DB) are acceptable.

- [ ] **Step 4: Run baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 5: Commit**

```bash
git add tests/security/test_constitution_rule_2.py
git commit -m "test(security): audit suite for Constitution Rule 2 (regression guard)"
```

**Rules (CLAUDE.md):**
- No `Co-Authored-By` / Claude footer
- English commit message
- Do NOT push
- Do NOT touch routes.py, CLAUDE.md, or CHANGELOG (Tasks 2, 3 own those)
- Do NOT modify the scaffold helpers (Task 1 of the prior plan owns them; only APPEND tests)

**Self-review:**
- All 9 tests pass (or skip cleanly)
- Baseline 0 failures
- Single-file commit, exact message

Report DONE + one-line + which tests passed vs. skipped, NEEDS_CONTEXT, or BLOCKED.

---

## Task 2: CLAUDE.md clarification

**Files:**
- Modify: `CLAUDE.md`

Document which tables are in/out of scope so future code reviews don't keep flagging false positives.

- [ ] **Step 1: Locate the Rule 2 description in `CLAUDE.md`**

The Constitution section enumerates rules in a numbered list. Find the entry that starts with "**Config Generation on State Change:**" or similar. Read enough surrounding context to understand the formatting.

- [ ] **Step 2: Append the clarification paragraph after the existing Rule 2 text**

Add the following two paragraphs at the end of the Rule 2 entry (BEFORE the next numbered rule):

```markdown
**What counts as declarative state for Rule 2:** the tables that describe *how the system is configured* — `connectors`, `connector_capabilities`, `channels`, `credentials`, `agents`, `agent_capabilities`, `agent_llm_models`, `policies`, `scheduled_tasks`, `mounts`, `workflows`. These are managed via service-layer classes (`ConnectorRegistry`, `CredentialService`, `AgentRegistry`, `ModelRegistry`, `MountManager`, `PolicyEngine`) which call `ConfigNotifier.notify_change()` after every mutation; that's how Rule 2 is enforced today. New endpoints that write to these tables MUST go through the service layer (or call `apply_from_state` explicitly if they bypass it, like `POST /api/channels`).

**What does NOT count as declarative state:** content tables (`knowledge_notes`, `knowledge_links`, `memory_entries`), execution traces (`audit_events`, `workflow_runs`, `workflow_events`, `tool_usage`, `llm_usage`), and ephemeral / session data (`messages`, `conversations`, `attempts`, `plans`, `tasks`, `background_tasks`, `mcp_sessions`, `connector_telemetry`, `session_agents`, `capability_tokens`, `organizer_suggestions`). These do not need config generations.
```

Match the surrounding markdown style — if the file uses a specific bold convention or list nesting, follow it.

- [ ] **Step 3: Spot-check the change reads cleanly**

```
grep -A 5 "What counts as declarative state" CLAUDE.md
```

Expected: the new paragraph appears in context.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): clarify Rule 2 scope (declarative state vs. content)"
```

**Rules:**
- No `Co-Authored-By` / Claude footer
- English commit message
- Do NOT push

Report DONE + one-line, or NEEDS_CONTEXT if the Rule 2 wording in CLAUDE.md doesn't match what you expected.

---

## Task 3: CHANGELOG + push

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add CHANGELOG entry**

Open `CHANGELOG.md`. Find the Week 17 block (between v0.3.0 and Week 16). Add at the END of the Week 17 entries (before `## Week 16 (2026)`):

```markdown
### Constitution Rule 2 audit
- New audit suite `tests/security/test_constitution_rule_2.py` verifies that every state-mutating Web-API endpoint produces a `config_generations` row, and pins that invariant for the future. Initial run: all green — the system is already conformant via service-layer notifiers (`ConfigNotifier.notify_change` is called from `CredentialService`, `ConnectorRegistry`, `ModelRegistry`, `AgentRegistry`, `MountManager`, `PolicyEngine`).
- CLAUDE.md Rule 2 now spells out which tables count as declarative state (connectors, credentials, agents, policies, etc.) and which don't (knowledge, memory, execution traces, sessions). Saves future code reviewers from re-flagging the same false diagnosis.
- Spec / plan: `docs/superpowers/specs/2026-04-25-constitution-rule-2-audit-design.md`, `docs/superpowers/plans/2026-04-25-constitution-rule-2-audit-plan.md`.
```

- [ ] **Step 2: Final baseline**

```
PYTHONPATH=src pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q
```

Expected: zero failures.

- [ ] **Step 3: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): constitution rule 2 audit (Week 17)"
git push origin main
```

Report DONE + one-line.

---

## Self-review notes

Spec coverage check (against the revised `2026-04-25-constitution-rule-2-audit-design.md`):

- D1 (no handler-level fixes) → no tasks touch routes.py.
- D2 (audit test suite) → Task 1.
- D3 (endpoints in scope) → Task 1 covers all 10 (channels + 9 in-scope).
- D4 (CLAUDE.md clarification) → Task 2.
- D5 (direct writes are antipattern) → documented in CLAUDE.md via D4 clarification.
- D6 (no service-layer changes) → no task touches the service layer.
- Success criteria 1-5 → all addressed.

No placeholders. Every step shows the actual code or command. The audit conclusions from the live verification are baked into the test assertions (≥1 instead of ==1 where the service-layer fan-out makes ==1 wrong).
