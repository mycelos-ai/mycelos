"""Integration test: Config generation and rollback.

Tests that config changes create new generations and can be rolled back
to restore prior state for connectors and policies.

Cost estimate: ~$0.00 (no LLM calls)
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_config_rollback_restores_state(integration_app):
    """Create config changes, rollback, verify state restored."""
    app = integration_app

    # Record initial generation
    gen1 = app.config.get_active_generation_id()
    assert gen1 is not None, "Should have an initial generation after initialize_with_config"

    # Add a connector — this should create a new generation
    app.connector_registry.register(
        "test-mcp-rollback",
        "Test MCP Rollback",
        "mcp",
        ["test.read"],
    )
    gen2 = app.config.get_active_generation_id()
    assert gen2 is not None
    assert gen2 > gen1, f"Should have a new generation after connector register: {gen1} -> {gen2}"

    # Verify connector exists
    connector = app.connector_registry.get("test-mcp-rollback")
    assert connector is not None, "Connector should exist after registration"

    # Rollback to gen1
    app.config.rollback(gen1, state_manager=app.state_manager)

    # Generation pointer should be back at gen1
    current_gen = app.config.get_active_generation_id()
    assert current_gen == gen1, \
        f"Active generation should be gen1={gen1} after rollback, got {current_gen}"

    # Connector should be gone after rollback
    connector_after = app.connector_registry.get("test-mcp-rollback")
    assert connector_after is None, \
        "Connector should be gone after rollback to gen1"


@pytest.mark.integration
def test_config_multiple_generations(integration_app):
    """Verify that each state change creates a distinct generation."""
    app = integration_app

    gen_ids = set()

    gen_start = app.config.get_active_generation_id()
    gen_ids.add(gen_start)

    # Register two connectors
    app.connector_registry.register("conn-a", "Connector A", "mcp", ["a.read"])
    gen_after_a = app.config.get_active_generation_id()
    gen_ids.add(gen_after_a)

    app.connector_registry.register("conn-b", "Connector B", "mcp", ["b.read"])
    gen_after_b = app.config.get_active_generation_id()
    gen_ids.add(gen_after_b)

    # Each generation should be unique
    assert len(gen_ids) == 3, \
        f"Each change should produce a distinct generation ID: {gen_ids}"

    # All connectors should exist in current state
    assert app.connector_registry.get("conn-a") is not None
    assert app.connector_registry.get("conn-b") is not None


@pytest.mark.integration
def test_policy_rollback(integration_app):
    """Policy changes should be rollbackable."""
    app = integration_app

    gen1 = app.config.get_active_generation_id()

    # Set a policy
    app.policy_engine.set_policy(
        "default", "test-agent", "dangerous.action", "always"
    )
    gen2 = app.config.get_active_generation_id()
    assert gen2 is not None
    assert gen2 > gen1, "Policy set should create a new generation"

    # Verify policy is set
    decision = app.policy_engine.evaluate("default", "test-agent", "dangerous.action")
    assert decision == "always", f"Policy should be 'always', got '{decision}'"

    # Rollback to gen1
    app.config.rollback(gen1, state_manager=app.state_manager)

    # Policy should be gone — default is 'confirm'
    decision_after = app.policy_engine.evaluate("default", "test-agent", "dangerous.action")
    assert decision_after != "always", \
        f"After rollback, policy should not be 'always', got '{decision_after}'"


@pytest.mark.integration
def test_config_list_generations(integration_app):
    """Config generations should be listable."""
    app = integration_app

    # Make a change to ensure we have at least one generation
    app.connector_registry.register("gen-test-conn", "Gen Test", "mcp", ["gen.test"])

    generations = app.config.list_generations()
    assert len(generations) >= 1, "Should have at least one generation"

    # Each generation should have an ID (GenerationInfo dataclass has .id)
    for gen in generations:
        # GenerationInfo is a dataclass — access via attribute or dict key
        gen_id = gen.id if hasattr(gen, "id") else gen.get("generation_id") or gen.get("id")
        assert gen_id is not None, f"Generation should have an ID field: {gen}"
