"""scripts/cleanup_orphan_connectors.py — dry-run + apply behaviors."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "cleanup_orphan_connectors.py"


def _seed_db(data_dir: Path) -> None:
    """Initialize an App so all tables exist, then inject one orphan row
    plus one real recipe-backed row that must survive cleanup."""
    from mycelos.app import App
    os.environ["MYCELOS_MASTER_KEY"] = "cleanup-test-key"
    app = App(data_dir)
    app.initialize()
    # Orphan: id NOT in RECIPES
    app.connector_registry.register(
        connector_id="web-search-duckduckgo",
        name="DuckDuckGo (legacy)",
        connector_type="search",
        capabilities=["search.web", "search.news"],
        description="legacy entry from before the registry unification",
        setup_type="none",
    )
    # Real recipe-backed row that must SURVIVE the cleanup
    app.connector_registry.register(
        connector_id="fetch",
        name="HTTP Fetch",
        connector_type="mcp",
        capabilities=["fetch"],
        description="real recipe — keep me",
        setup_type="none",
    )


def _count_connectors(db_path: Path) -> set[str]:
    """Direct read — bypass App so we test the script's effect, not the API."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT id FROM connectors").fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def test_dry_run_lists_orphans_and_changes_nothing(tmp_data_dir: Path) -> None:
    """Default --dry-run should print the orphan and not mutate the DB."""
    _seed_db(tmp_data_dir)
    db_path = tmp_data_dir / "mycelos.db"
    before = _count_connectors(db_path)
    assert "web-search-duckduckgo" in before
    assert "fetch" in before

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--data-dir", str(tmp_data_dir)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )
    assert result.returncode == 0, result.stderr
    assert "web-search-duckduckgo" in result.stdout
    assert "DRY RUN" in result.stdout.upper() or "would" in result.stdout.lower()

    after = _count_connectors(db_path)
    assert after == before, "dry-run must not mutate the DB"


def test_apply_removes_orphans_and_keeps_real_recipes(tmp_data_dir: Path) -> None:
    """--apply deletes the orphan, keeps the real recipe, writes a backup."""
    _seed_db(tmp_data_dir)
    db_path = tmp_data_dir / "mycelos.db"

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--data-dir", str(tmp_data_dir), "--apply"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )
    assert result.returncode == 0, result.stderr

    after = _count_connectors(db_path)
    assert "web-search-duckduckgo" not in after
    assert "fetch" in after, "real recipe-backed row must survive cleanup"

    backups = list(tmp_data_dir.glob("mycelos.db.bak-*"))
    assert backups, f"no backup file created in {tmp_data_dir}"

    from mycelos.app import App
    app = App(tmp_data_dir)
    events = app.audit.query(event_type="connector.orphan_removed", limit=10)
    assert events, "orphan removal must emit a connector.orphan_removed audit event"
    # audit.query() returns `details` as a JSON string per the SQLite layer
    # (no automatic deserialization). Parse here for the assertion.
    parsed = [json.loads(e["details"]) if isinstance(e.get("details"), str) else (e.get("details") or {}) for e in events]
    assert any(d.get("id") == "web-search-duckduckgo" for d in parsed)


def test_apply_is_idempotent(tmp_data_dir: Path) -> None:
    """Running --apply twice does nothing extra on the second run."""
    _seed_db(tmp_data_dir)
    subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--data-dir", str(tmp_data_dir), "--apply"],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        check=True,
    )
    second = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--data-dir", str(tmp_data_dir), "--apply"],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )
    assert second.returncode == 0, second.stderr
    assert "0 orphans" in second.stdout.lower() or "no orphans" in second.stdout.lower()


def test_missing_db_exits_nonzero(tmp_path: Path) -> None:
    """Pointing the script at a directory without mycelos.db must fail clean."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--data-dir", str(tmp_path)],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )
    assert result.returncode != 0
    assert "mycelos.db" in (result.stderr + result.stdout).lower()
