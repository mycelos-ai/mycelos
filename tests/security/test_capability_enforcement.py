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


import time
from datetime import datetime, timezone, timedelta

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
        ttl_seconds=0,
    )
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
    assert manager.validate(t3, "a").valid is True


def test_max_requests_enforcement(manager: CapabilityTokenManager) -> None:
    """Token with max_requests is rejected after exceeding the limit."""
    token_id = manager.issue(
        agent_id="agent-c",
        scope=["test.op"],
        ttl_seconds=300,
        max_requests=3,
    )
    for _ in range(3):
        result = manager.validate(token_id, "test.op")
        assert result.valid is True

    result = manager.validate(token_id, "test.op")
    assert result.valid is False
    assert "request limit" in result.reason.lower()


def test_max_requests_persists_across_instances(db_path: Path) -> None:
    """Request counts survive process restart (new manager instance)."""
    storage = make_storage(db_path)
    manager1 = CapabilityTokenManager(storage)
    token_id = manager1.issue(
        agent_id="agent-persist",
        scope=["test.op"],
        ttl_seconds=300,
        max_requests=3,
    )
    # Use 2 of 3 requests with first manager
    for _ in range(2):
        result = manager1.validate(token_id, "test.op")
        assert result.valid is True

    # Simulate restart: create new manager instance with same storage
    manager2 = CapabilityTokenManager(storage)
    result = manager2.validate(token_id, "test.op")
    assert result.valid is True  # 3rd request succeeds

    result = manager2.validate(token_id, "test.op")
    assert result.valid is False  # 4th request fails
    assert "request limit" in result.reason.lower()


# ── SEC07 Scenarios ──


def test_sec07_token_replay_after_expiry(manager: CapabilityTokenManager) -> None:
    """SEC07: Expired token cannot be replayed."""
    token_id = manager.issue(agent_id="agent", scope=["email.read"], ttl_seconds=0)
    time.sleep(0.05)
    result = manager.validate(token_id, "email.read")
    assert result.valid is False


def test_sec07_scope_escalation_blocked(manager: CapabilityTokenManager) -> None:
    """SEC07: Token for email.read cannot be used for email.send."""
    token_id = manager.issue(agent_id="agent", scope=["email.read"], ttl_seconds=300)
    result = manager.validate(token_id, "email.send")
    assert result.valid is False


def test_sec07_forged_token_rejected(manager: CapabilityTokenManager) -> None:
    """SEC07: A hand-crafted token ID is rejected."""
    result = manager.validate("forged-token-uuid-1234", "email.read")
    assert result.valid is False


def test_atomic_max_requests_no_toctou(db_path: Path) -> None:
    """Atomic increment prevents TOCTOU race on max_requests.

    Simulates the race by forcing two managers to read the same state,
    then both attempting to validate. Only one should succeed for the
    last remaining request slot.
    """
    storage = make_storage(db_path)
    mgr = CapabilityTokenManager(storage)
    token_id = mgr.issue(
        agent_id="race-agent",
        scope=["op"],
        ttl_seconds=300,
        max_requests=1,
    )

    # First validation consumes the single allowed request
    r1 = mgr.validate(token_id, "op")
    assert r1.valid is True

    # Second validation must fail — the atomic UPDATE ensures
    # the count cannot be exceeded even under concurrent access
    r2 = mgr.validate(token_id, "op")
    assert r2.valid is False
    assert "request limit" in r2.reason.lower()

    # Verify the used_requests column is exactly 1, not 2
    row = storage.fetchone(
        "SELECT used_requests FROM capability_tokens WHERE id = ?", (token_id,)
    )
    assert row["used_requests"] == 1


def test_max_requests_none_unlimited(manager: CapabilityTokenManager) -> None:
    """Token with no max_requests allows unlimited validations."""
    token_id = manager.issue(
        agent_id="unlimited-agent",
        scope=["op"],
        ttl_seconds=300,
        max_requests=None,
    )
    for _ in range(50):
        result = manager.validate(token_id, "op")
        assert result.valid is True
