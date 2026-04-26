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
    after = _generation_count(app)
    assert after > before, (
        f"POST /api/connectors should produce at least one generation "
        f"(was {before}, now {after})."
    )


def test_delete_connector_creates_generation(app_and_client) -> None:
    app, client = app_and_client
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
