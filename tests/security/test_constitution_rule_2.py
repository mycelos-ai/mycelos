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
