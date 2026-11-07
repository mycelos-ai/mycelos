# Capability Tokens + Policy Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Capability Token Manager and Policy Engine — the two components that control what agents are allowed to do, and enforce those rules at runtime.

**Architecture:** The Policy Engine stores per-agent permission rules (always/never/confirm/prepare per resource). The Capability Token Manager issues short-lived tokens based on those policies. At runtime, every agent action is validated against its token — checking scope, expiry, revocation, and rate limits. Both use SQLite for persistence and follow the Protocol + Implementation pattern.

**Tech Stack:** Python 3.12+, SQLite, pytest, Hypothesis (property-based testing for Policy Engine)

**Scope Decisions:**
- **confirm/prepare at runtime:** These decisions require user interaction (confirm = ask now, prepare = park and ask later). For this phase we implement the *decision logic* and *storage*, but not the user-facing prompt/notification flow — that comes with the Notification Pipeline (Phase 5.5). The Policy Engine returns the decision; the caller handles it.
- **Rate limiting (per-minute sliding window):** SEC07 `@token-rate-limit` requires per-minute rate limiting. This phase implements cumulative `max_requests` only. Sliding-window rate limiting is TODO Phase 1.2b (needs proper time-window tracking, not just a counter).
- **Token signing:** Tokens are UUIDs stored in SQLite. Validation is a DB lookup + checks. No JWT/HMAC — simpler and the token never leaves the local system.
- **Audit logging:** SEC07/SEC08 require audit events for denied operations (e.g., `capability.denied`, `policy.violation`). This phase does NOT add audit calls — audit integration comes when the Security Layer is wired as a cross-cutting concern. TODO: add audit calls when integrating with Sandbox (Phase 2).
- **SEC08 `@creator-agent-manipulation`:** Requires AuditorAgent (Phase 3.5) — out of scope for this phase.
- **SEC08 `@race-condition`:** Relies on SQLite WAL mode consistency — no explicit concurrent test in this phase.
- **`requested_by` enforcement:** The `set_policy(requested_by=)` parameter depends on the caller (Security Layer) always passing the agent's identity. The PolicyEngine itself cannot enforce this — it trusts the caller. This is intentional; enforcement at the call site comes with the IPC protocol (Phase 2.2).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/mycelos/security/capabilities.py` | `CapabilityTokenManager` — issue, validate, revoke tokens |
| `src/mycelos/security/policies.py` | `PolicyEngine` — evaluate permission decisions per agent+resource |
| `src/mycelos/storage/schema.sql` | Add `capability_tokens` + `policies` tables |
| `src/mycelos/protocols.py` | Add `CapabilityTokenManager` + `PolicyEngine` protocols |
| `src/mycelos/app.py` | Add `capabilities` + `policy_engine` properties |
| `tests/security/test_capability_enforcement.py` | SEC07 scenario tests |
| `tests/security/test_policy_engine.py` | SEC08 scenario tests + Hypothesis |

---

### Task 1: Add capability_tokens + policies tables to schema

**Files:**
- Modify: `src/mycelos/storage/schema.sql`
- Test: `tests/security/test_capability_enforcement.py`

- [ ] **Step 1: Write failing test — tables exist**

Create `tests/security/test_capability_enforcement.py`:

```python
"""Security tests for capability token enforcement (SEC07)."""

from pathlib import Path

import pytest

from mycelos.storage.database import SQLiteStorage


def make_storage(db_path: Path) -> SQLiteStorage:
    storage = SQLiteStorage(db_path)
    storage.initialize()
    return storage


def test_capability_tokens_table_exists(db_path: Path) -> None:
    """The capability_tokens table must exist after init."""
    storage = make_storage(db_path)
    result = storage.fetchone(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='capability_tokens'"
    )
    assert result is not None


def test_policies_table_exists(db_path: Path) -> None:
    """The policies table must exist after init."""
    storage = make_storage(db_path)
    result = storage.fetchone(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='policies'"
    )
    assert result is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/security/test_capability_enforcement.py -v`
Expected: FAIL — tables don't exist

- [ ] **Step 3: Add tables to schema.sql**

Append to `src/mycelos/storage/schema.sql`:

```sql
-- Capability Tokens (Security Layer)
CREATE TABLE IF NOT EXISTS capability_tokens (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    agent_id    TEXT NOT NULL,
    task_id     TEXT,
    scope       TEXT NOT NULL,
    max_requests INTEGER,
    rate_limit  INTEGER,
    issued_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    expires_at  TEXT NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_cap_tokens_agent ON capability_tokens(agent_id, revoked);

-- Policies (Security Layer)
CREATE TABLE IF NOT EXISTS policies (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    agent_id    TEXT,
    resource    TEXT NOT NULL,
    decision    TEXT NOT NULL DEFAULT 'confirm',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(user_id, agent_id, resource)
);

CREATE INDEX IF NOT EXISTS idx_policies_agent ON policies(user_id, agent_id);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/security/test_capability_enforcement.py -v`
Expected: PASS

- [ ] **Step 5: Run all tests for regressions**

Run: `pytest tests/test_storage.py tests/test_config.py tests/test_memory.py tests/test_audit.py tests/security/ -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/storage/schema.sql tests/security/test_capability_enforcement.py
git commit -m "schema: add capability_tokens + policies tables"
```

---

### Task 2: Implement PolicyEngine

**Files:**
- Create: `src/mycelos/security/policies.py`
- Modify: `src/mycelos/protocols.py`
- Create: `tests/security/test_policy_engine.py`

- [ ] **Step 1: Write failing tests**

Create `tests/security/test_policy_engine.py`:

```python
"""Security tests for Policy Engine (SEC08)."""

from pathlib import Path
import uuid

import pytest

from mycelos.storage.database import SQLiteStorage


def make_storage(db_path: Path) -> SQLiteStorage:
    storage = SQLiteStorage(db_path)
    storage.initialize()
    return storage


# ── We import after defining helpers so tests fail cleanly if module missing ──
from mycelos.security.policies import PolicyEngine


@pytest.fixture
def engine(db_path: Path) -> PolicyEngine:
    storage = make_storage(db_path)
    return PolicyEngine(storage)


def test_set_and_get_policy(engine: PolicyEngine) -> None:
    """Setting a policy and getting it returns the correct decision."""
    engine.set_policy("default", "email-agent", "email.read", "always")
    decision = engine.evaluate("default", "email-agent", "email.read")
    assert decision == "always"


def test_default_decision_is_confirm(engine: PolicyEngine) -> None:
    """When no policy is set, the default decision is 'confirm'."""
    decision = engine.evaluate("default", "email-agent", "email.read")
    assert decision == "confirm"


def test_global_policy_applies_to_all_agents(engine: PolicyEngine) -> None:
    """A policy with agent_id=None applies to all agents."""
    engine.set_policy("default", None, "web.search", "always")
    assert engine.evaluate("default", "any-agent", "web.search") == "always"
    assert engine.evaluate("default", "other-agent", "web.search") == "always"


def test_agent_policy_overrides_global(engine: PolicyEngine) -> None:
    """Agent-specific policy takes priority over global policy."""
    engine.set_policy("default", None, "email.send", "always")  # global
    engine.set_policy("default", "untrusted-agent", "email.send", "never")  # specific
    assert engine.evaluate("default", "untrusted-agent", "email.send") == "never"
    assert engine.evaluate("default", "trusted-agent", "email.send") == "always"


def test_never_policy_blocks(engine: PolicyEngine) -> None:
    """A 'never' policy blocks the action."""
    engine.set_policy("default", "bad-agent", "shell.exec", "never")
    assert engine.evaluate("default", "bad-agent", "shell.exec") == "never"


def test_valid_decisions_only(engine: PolicyEngine) -> None:
    """Only always/confirm/prepare/never are valid decisions."""
    with pytest.raises(ValueError, match="Invalid decision"):
        engine.set_policy("default", "agent", "resource", "invalid")


def test_list_policies(engine: PolicyEngine) -> None:
    """list_policies returns all policies for a user."""
    engine.set_policy("default", "a", "email.read", "always")
    engine.set_policy("default", "b", "email.send", "never")
    policies = engine.list_policies("default")
    assert len(policies) == 2


def test_user_isolation(engine: PolicyEngine) -> None:
    """Policies for one user don't affect another."""
    engine.set_policy("alice", "agent-x", "email.read", "always")
    engine.set_policy("bob", "agent-x", "email.read", "never")
    assert engine.evaluate("alice", "agent-x", "email.read") == "always"
    assert engine.evaluate("bob", "agent-x", "email.read") == "never"


# ── SEC08: Policy Bypass Scenarios ──


def test_sec08_self_modification_blocked(engine: PolicyEngine) -> None:
    """SEC08: An agent cannot modify its own policy."""
    engine.set_policy("default", "agent-a", "email.send", "never")
    with pytest.raises(PermissionError, match="cannot modify its own policy"):
        engine.set_policy("default", "agent-a", "email.send", "always", requested_by="agent-a")


def test_sec08_creator_registration_never_auto(engine: PolicyEngine) -> None:
    """SEC08: The 'agent.register' resource always requires confirmation, never auto-learnable."""
    # Even if someone tries to set it to 'always'
    engine.set_policy("default", "creator-agent", "agent.register", "always")
    # The engine OVERRIDES this for agent.register — it's always 'confirm'
    decision = engine.evaluate("default", "creator-agent", "agent.register")
    assert decision == "confirm"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/security/test_policy_engine.py -v`
Expected: FAIL — PolicyEngine doesn't exist

- [ ] **Step 3: Add PolicyEngine protocol to protocols.py**

Append to `src/mycelos/protocols.py`:

```python
@runtime_checkable
class PolicyEngine(Protocol):
    """Interface for policy evaluation."""

    def evaluate(self, user_id: str, agent_id: str, resource: str) -> str: ...
    def set_policy(self, user_id: str, agent_id: str | None, resource: str, decision: str, requested_by: str | None = None) -> None: ...
    def list_policies(self, user_id: str, agent_id: str | None = None) -> list[dict]: ...
```

- [ ] **Step 4: Implement PolicyEngine**

Create `src/mycelos/security/policies.py`:

```python
"""Policy Engine — evaluates permission decisions per agent and resource.

Decisions: always, confirm, prepare, never
- always:  action is auto-allowed
- confirm: user must approve synchronously
- prepare: action runs, result parked for user review
- never:   action is blocked

Protected resources (never auto-learnable):
- agent.register: always requires human confirmation
"""

from __future__ import annotations

import uuid

from mycelos.protocols import StorageBackend

VALID_DECISIONS = {"always", "confirm", "prepare", "never"}

# Resources that always require confirmation regardless of policy
PROTECTED_RESOURCES = {"agent.register"}


class PolicyEngine:
    """SQLite-backed policy engine.

    Satisfies the PolicyEngine protocol from protocols.py.
    """

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    def evaluate(self, user_id: str, agent_id: str, resource: str) -> str:
        """Evaluate the policy decision for an agent accessing a resource.

        Lookup order: agent-specific policy > global policy > default (confirm).
        Protected resources always return 'confirm' regardless of stored policy.
        """
        if resource in PROTECTED_RESOURCES:
            return "confirm"

        # Check agent-specific policy first
        row = self._storage.fetchone(
            "SELECT decision FROM policies WHERE user_id = ? AND agent_id = ? AND resource = ?",
            (user_id, agent_id, resource),
        )
        if row:
            return row["decision"]

        # Fall back to global policy (agent_id IS NULL)
        row = self._storage.fetchone(
            "SELECT decision FROM policies WHERE user_id = ? AND agent_id IS NULL AND resource = ?",
            (user_id, resource),
        )
        if row:
            return row["decision"]

        return "confirm"

    def set_policy(
        self,
        user_id: str,
        agent_id: str | None,
        resource: str,
        decision: str,
        requested_by: str | None = None,
    ) -> None:
        """Set a policy for a user+agent+resource.

        Raises PermissionError if an agent tries to modify its own policy.
        Raises ValueError if the decision is not valid.
        """
        if decision not in VALID_DECISIONS:
            raise ValueError(f"Invalid decision '{decision}'. Must be one of: {VALID_DECISIONS}")

        if requested_by and requested_by == agent_id:
            raise PermissionError(
                f"Agent '{agent_id}' cannot modify its own policy. "
                "Only the user or Blueprint Lifecycle can change policies."
            )

        policy_id = str(uuid.uuid4())

        # Upsert: check if policy exists for this user+agent+resource
        if agent_id is not None:
            existing = self._storage.fetchone(
                "SELECT id FROM policies WHERE user_id = ? AND agent_id = ? AND resource = ?",
                (user_id, agent_id, resource),
            )
        else:
            existing = self._storage.fetchone(
                "SELECT id FROM policies WHERE user_id = ? AND agent_id IS NULL AND resource = ?",
                (user_id, resource),
            )

        if existing:
            self._storage.execute(
                "UPDATE policies SET decision = ? WHERE id = ?",
                (decision, existing["id"]),
            )
        else:
            self._storage.execute(
                "INSERT INTO policies (id, user_id, agent_id, resource, decision) VALUES (?, ?, ?, ?, ?)",
                (policy_id, user_id, agent_id, resource, decision),
            )

    def list_policies(self, user_id: str, agent_id: str | None = None) -> list[dict]:
        """List all policies for a user, optionally filtered by agent."""
        if agent_id:
            return self._storage.fetchall(
                "SELECT * FROM policies WHERE user_id = ? AND agent_id = ? ORDER BY resource",
                (user_id, agent_id),
            )
        return self._storage.fetchall(
            "SELECT * FROM policies WHERE user_id = ? ORDER BY agent_id, resource",
            (user_id,),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/security/test_policy_engine.py -v`
Expected: All 11 tests PASS

- [ ] **Step 6: Run all tests for regressions**

Run: `pytest tests/test_storage.py tests/test_config.py tests/test_memory.py tests/test_audit.py tests/security/ -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add src/mycelos/security/policies.py src/mycelos/protocols.py tests/security/test_policy_engine.py
git commit -m "feat: implement PolicyEngine with always/confirm/prepare/never decisions"
```

---

### Task 3: Implement CapabilityTokenManager

**Files:**
- Create: `src/mycelos/security/capabilities.py`
- Modify: `src/mycelos/protocols.py`
- Modify: `tests/security/test_capability_enforcement.py`

- [ ] **Step 1: Write failing tests for token operations**

Append to `tests/security/test_capability_enforcement.py`:

```python
from datetime import datetime, timezone, timedelta
import time

from mycelos.security.capabilities import CapabilityTokenManager


@pytest.fixture
def manager(db_path: Path) -> CapabilityTokenManager:
    storage = make_storage(db_path)
    return CapabilityTokenManager(storage)


def test_issue_and_validate_token(manager: CapabilityTokenManager) -> None:
    """Issuing a token and validating it succeeds."""
    token_id = manager.issue(
        agent_id="email-agent",
        scope=["email.read", "email.list"],
        ttl_seconds=300,
    )
    assert token_id is not None
    result = manager.validate(token_id, "email.read")
    assert result.valid is True
    assert result.agent_id == "email-agent"


def test_token_scope_check(manager: CapabilityTokenManager) -> None:
    """Token rejects operations outside its scope."""
    token_id = manager.issue(
        agent_id="email-agent",
        scope=["email.read"],
        ttl_seconds=300,
    )
    result = manager.validate(token_id, "email.send")
    assert result.valid is False
    assert "scope" in result.reason.lower()


def test_expired_token_rejected(manager: CapabilityTokenManager) -> None:
    """An expired token is rejected."""
    token_id = manager.issue(
        agent_id="agent-a",
        scope=["test.op"],
        ttl_seconds=0,  # expires immediately
    )
    # Small sleep to ensure expiry
    time.sleep(0.05)
    result = manager.validate(token_id, "test.op")
    assert result.valid is False
    assert "expired" in result.reason.lower()


def test_revoked_token_rejected(manager: CapabilityTokenManager) -> None:
    """A revoked token is rejected."""
    token_id = manager.issue(
        agent_id="agent-b",
        scope=["test.op"],
        ttl_seconds=300,
    )
    manager.revoke(token_id)
    result = manager.validate(token_id, "test.op")
    assert result.valid is False
    assert "revoked" in result.reason.lower()


def test_nonexistent_token_rejected(manager: CapabilityTokenManager) -> None:
    """A fake/forged token is rejected."""
    result = manager.validate("fake-token-id-12345", "test.op")
    assert result.valid is False
    assert "not found" in result.reason.lower()


def test_revoke_all_for_agent(manager: CapabilityTokenManager) -> None:
    """Revoking all tokens for an agent invalidates all of them."""
    t1 = manager.issue(agent_id="agent-x", scope=["a"], ttl_seconds=300)
    t2 = manager.issue(agent_id="agent-x", scope=["b"], ttl_seconds=300)
    t3 = manager.issue(agent_id="agent-y", scope=["a"], ttl_seconds=300)

    manager.revoke_all_for_agent("agent-x")

    assert manager.validate(t1, "a").valid is False
    assert manager.validate(t2, "b").valid is False
    assert manager.validate(t3, "a").valid is True  # different agent, not revoked


def test_max_requests_enforcement(manager: CapabilityTokenManager) -> None:
    """Token with max_requests is rejected after exceeding the limit."""
    token_id = manager.issue(
        agent_id="agent-c",
        scope=["test.op"],
        ttl_seconds=300,
        max_requests=3,
    )
    # First 3 should succeed
    for _ in range(3):
        result = manager.validate(token_id, "test.op")
        assert result.valid is True

    # 4th should fail
    result = manager.validate(token_id, "test.op")
    assert result.valid is False
    assert "request limit" in result.reason.lower()


# ── SEC07 Scenarios ──


def test_sec07_token_replay_after_expiry(manager: CapabilityTokenManager) -> None:
    """SEC07: Expired token cannot be replayed."""
    token_id = manager.issue(
        agent_id="agent",
        scope=["email.read"],
        ttl_seconds=0,
    )
    time.sleep(0.05)
    result = manager.validate(token_id, "email.read")
    assert result.valid is False


def test_sec07_scope_escalation_blocked(manager: CapabilityTokenManager) -> None:
    """SEC07: Token for email.read cannot be used for email.send."""
    token_id = manager.issue(
        agent_id="agent",
        scope=["email.read"],
        ttl_seconds=300,
    )
    result = manager.validate(token_id, "email.send")
    assert result.valid is False


def test_sec07_forged_token_rejected(manager: CapabilityTokenManager) -> None:
    """SEC07: A hand-crafted token ID is rejected."""
    result = manager.validate("forged-token-uuid-1234", "email.read")
    assert result.valid is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/security/test_capability_enforcement.py -v`
Expected: FAIL — CapabilityTokenManager doesn't exist

- [ ] **Step 3: Add CapabilityTokenManager protocol**

Append to `src/mycelos/protocols.py`:

```python
@dataclass(frozen=True)
class ValidationResult:
    """Result of a capability token validation."""
    valid: bool
    agent_id: str = ""
    reason: str = ""


@runtime_checkable
class CapabilityTokenManager(Protocol):
    """Interface for capability token management."""

    def issue(self, agent_id: str, scope: list[str], ttl_seconds: int, task_id: str | None = None, user_id: str = "default", max_requests: int | None = None) -> str: ...
    def validate(self, token_id: str, operation: str) -> ValidationResult: ...
    def revoke(self, token_id: str) -> None: ...
    def revoke_all_for_agent(self, agent_id: str) -> None: ...
```

- [ ] **Step 4: Implement CapabilityTokenManager**

Create `src/mycelos/security/capabilities.py`:

```python
"""Capability Token Manager — issues and validates short-lived, scope-limited tokens.

Tokens are UUIDs stored in SQLite. Each token has:
- scope: JSON array of allowed operations
- TTL: expiry time
- max_requests: optional request count limit
- revoked flag: for immediate invalidation

Validation checks: exists → not revoked → not expired → scope matches → request limit.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from threading import Lock

from mycelos.protocols import StorageBackend, ValidationResult


class CapabilityTokenManager:
    """SQLite-backed capability token manager.

    Satisfies the CapabilityTokenManager protocol from protocols.py.
    """

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage
        self._request_counts: dict[str, int] = {}
        self._lock = Lock()

    def issue(
        self,
        agent_id: str,
        scope: list[str],
        ttl_seconds: int,
        task_id: str | None = None,
        user_id: str = "default",
        max_requests: int | None = None,
    ) -> str:
        """Issue a new capability token. Returns the token ID."""
        token_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds)

        self._storage.execute(
            """INSERT INTO capability_tokens
               (id, user_id, agent_id, task_id, scope, max_requests, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                token_id,
                user_id,
                agent_id,
                task_id,
                json.dumps(scope),
                max_requests,
                expires_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            ),
        )
        return token_id

    def validate(self, token_id: str, operation: str) -> ValidationResult:
        """Validate a token for a specific operation.

        Checks in order: exists → not revoked → not expired → scope → request limit.
        """
        row = self._storage.fetchone(
            "SELECT * FROM capability_tokens WHERE id = ?", (token_id,)
        )
        if row is None:
            return ValidationResult(valid=False, reason="Token not found")

        if row["revoked"]:
            return ValidationResult(valid=False, agent_id=row["agent_id"], reason="Token revoked")

        # Check expiry
        expires_at = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires_at:
            return ValidationResult(valid=False, agent_id=row["agent_id"], reason="Token expired")

        # Check scope
        scope = json.loads(row["scope"])
        if operation not in scope:
            return ValidationResult(
                valid=False,
                agent_id=row["agent_id"],
                reason=f"Scope denied: '{operation}' not in {scope}",
            )

        # Check request limit
        if row["max_requests"] is not None:
            with self._lock:
                count = self._request_counts.get(token_id, 0)
                if count >= row["max_requests"]:
                    return ValidationResult(
                        valid=False,
                        agent_id=row["agent_id"],
                        reason=f"Request limit exceeded ({row['max_requests']})",
                    )
                self._request_counts[token_id] = count + 1

        return ValidationResult(valid=True, agent_id=row["agent_id"])

    def revoke(self, token_id: str) -> None:
        """Revoke a specific token."""
        self._storage.execute(
            "UPDATE capability_tokens SET revoked = 1 WHERE id = ?", (token_id,)
        )

    def revoke_all_for_agent(self, agent_id: str) -> None:
        """Revoke all tokens for an agent (e.g., after security incident)."""
        self._storage.execute(
            "UPDATE capability_tokens SET revoked = 1 WHERE agent_id = ? AND revoked = 0",
            (agent_id,),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/security/test_capability_enforcement.py -v`
Expected: All 13 tests PASS

- [ ] **Step 6: Verify protocol compliance**

Run: `python3 -c "from mycelos.protocols import CapabilityTokenManager; from mycelos.security.capabilities import CapabilityTokenManager as Impl; print(issubclass(Impl, CapabilityTokenManager))"`
Expected: `True`

- [ ] **Step 7: Run all tests for regressions**

Run: `pytest tests/test_storage.py tests/test_config.py tests/test_memory.py tests/test_audit.py tests/security/ -v`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add src/mycelos/security/capabilities.py src/mycelos/protocols.py tests/security/test_capability_enforcement.py
git commit -m "feat: implement CapabilityTokenManager with TTL, scope, revocation, request limits"
```

---

### Task 4: Property-based tests for Policy Engine (Hypothesis)

**Files:**
- Modify: `tests/security/test_policy_engine.py`

- [ ] **Step 1: Add Hypothesis property-based tests**

Append to `tests/security/test_policy_engine.py`:

```python
import uuid

from hypothesis import given, strategies as st, settings


# Strategy for valid decisions
decisions = st.sampled_from(["always", "confirm", "prepare", "never"])
agent_ids = st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"))
resources = st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="._"))


@given(agent_id=agent_ids, resource=resources, decision=decisions)
@settings(max_examples=50)
def test_policy_roundtrip_property(tmp_path: Path, agent_id: str, resource: str, decision: str) -> None:
    """Property: setting a policy and evaluating it returns the set decision."""
    # Use tmp_path directly (not db_path fixture) because Hypothesis
    # generates arguments and doesn't work with pytest fixtures as params.
    db = tmp_path / f"test_{uuid.uuid4().hex[:8]}.db"
    storage = make_storage(db)
    engine = PolicyEngine(storage)
    engine.set_policy("default", agent_id, resource, decision)
    if resource in ("agent.register",):
        assert engine.evaluate("default", agent_id, resource) == "confirm"
    else:
        assert engine.evaluate("default", agent_id, resource) == decision


@given(decision=st.text(min_size=1).filter(lambda d: d not in ("always", "confirm", "prepare", "never")))
@settings(max_examples=20)
def test_invalid_decision_rejected_property(tmp_path: Path, decision: str) -> None:
    """Property: any decision not in {always, confirm, prepare, never} is rejected."""
    db = tmp_path / f"test_{uuid.uuid4().hex[:8]}.db"
    storage = make_storage(db)
    engine = PolicyEngine(storage)
    with pytest.raises(ValueError):
        engine.set_policy("default", "agent", "resource", decision)
```

- [ ] **Step 2: Run Hypothesis tests**

Run: `pytest tests/security/test_policy_engine.py -v`
Expected: All tests PASS (including property-based)

- [ ] **Step 3: Commit**

```bash
git add tests/security/test_policy_engine.py
git commit -m "test: add Hypothesis property-based tests for PolicyEngine"
```

---

### Task 5: Wire into App container

**Files:**
- Modify: `src/mycelos/app.py`

- [ ] **Step 1: Add capabilities and policy_engine properties to App**

Add to `src/mycelos/app.py` — after the `credentials` property:

Import at top:
```python
from mycelos.security.capabilities import CapabilityTokenManager
from mycelos.security.policies import PolicyEngine
```

Add fields to `__init__`:
```python
self._capabilities: CapabilityTokenManager | None = None
self._policy_engine: PolicyEngine | None = None
```

Add properties:
```python
@property
def capabilities(self) -> CapabilityTokenManager:
    if self._capabilities is None:
        self._capabilities = CapabilityTokenManager(self.storage)
    return self._capabilities

@property
def policy_engine(self) -> PolicyEngine:
    if self._policy_engine is None:
        self._policy_engine = PolicyEngine(self.storage)
    return self._policy_engine
```

- [ ] **Step 2: Run all tests**

Run: `pytest tests/test_storage.py tests/test_config.py tests/test_memory.py tests/test_audit.py tests/security/ -v`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add src/mycelos/app.py
git commit -m "feat: wire CapabilityTokenManager + PolicyEngine into App container"
```

---

### Task 6: Final verification

- [ ] **Step 1: Verify protocol compliance for all new types**

Run:
```bash
python3 -c "
from mycelos.protocols import CapabilityTokenManager, PolicyEngine
from mycelos.security.capabilities import CapabilityTokenManager as CTM
from mycelos.security.policies import PolicyEngine as PE
print('CapabilityTokenManager:', issubclass(CTM, CapabilityTokenManager))
print('PolicyEngine:', issubclass(PE, PolicyEngine))
"
```
Expected: Both `True`

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/test_storage.py tests/test_config.py tests/test_memory.py tests/test_audit.py tests/security/ -v`
Expected: All pass (should be ~55+ tests total)

- [ ] **Step 3: Count security tests**

Run: `pytest tests/security/ -v --co`
Expected: ~25+ security tests across 3 files

- [ ] **Step 4: Commit if any remaining changes**

```bash
git status
# Commit if needed
```
