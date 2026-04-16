"""Security tests for Policy Engine (SEC08)."""

from pathlib import Path

import pytest

from mycelos.storage.database import SQLiteStorage
from mycelos.security.policies import PolicyEngine


def make_storage(db_path: Path) -> SQLiteStorage:
    storage = SQLiteStorage(db_path)
    storage.initialize()
    for uid in ("alice", "bob"):
        storage.execute(
            "INSERT OR IGNORE INTO users (id, name, status) VALUES (?, ?, ?)",
            (uid, uid, "active"),
        )
    return storage


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
    engine.set_policy("default", None, "email.send", "always")
    engine.set_policy("default", "untrusted-agent", "email.send", "never")
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


# -- SEC08: Policy Bypass Scenarios --


def test_sec08_self_modification_blocked(engine: PolicyEngine) -> None:
    """SEC08: An agent cannot modify its own policy."""
    engine.set_policy("default", "agent-a", "email.send", "never")
    with pytest.raises(PermissionError, match="cannot modify its own policy"):
        engine.set_policy("default", "agent-a", "email.send", "always", requested_by="agent-a")


def test_sec08_creator_registration_never_auto(engine: PolicyEngine) -> None:
    """SEC08: agent.register always requires confirmation, never auto-learnable."""
    engine.set_policy("default", "creator-agent", "agent.register", "always")
    decision = engine.evaluate("default", "creator-agent", "agent.register")
    assert decision == "confirm"


# -- Hypothesis property-based tests --

import uuid

from hypothesis import given, strategies as st, settings, HealthCheck

# Strategy for valid decisions
decisions = st.sampled_from(["always", "confirm", "prepare", "never"])
agent_ids = st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"))
resources = st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="._"))


@given(agent_id=agent_ids, resource=resources, decision=decisions)
@settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_policy_roundtrip_property(tmp_path: Path, agent_id: str, resource: str, decision: str) -> None:
    """Property: setting a policy and evaluating it returns the set decision."""
    db = tmp_path / f"test_{uuid.uuid4().hex[:8]}.db"
    storage = make_storage(db)
    engine = PolicyEngine(storage)
    engine.set_policy("default", agent_id, resource, decision)
    if resource in ("agent.register",):
        assert engine.evaluate("default", agent_id, resource) == "confirm"
    else:
        assert engine.evaluate("default", agent_id, resource) == decision


@given(decision=st.text(min_size=1).filter(lambda d: d not in ("always", "confirm", "prepare", "never")))
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_invalid_decision_rejected_property(tmp_path: Path, decision: str) -> None:
    """Property: any decision not in {always, confirm, prepare, never} is rejected."""
    db = tmp_path / f"test_{uuid.uuid4().hex[:8]}.db"
    storage = make_storage(db)
    engine = PolicyEngine(storage)
    with pytest.raises(ValueError):
        engine.set_policy("default", "agent", "resource", decision)
