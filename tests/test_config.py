"""Tests for ConfigGenerationManager (NixOS-style generations)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mycelos.config import ConfigGenerationManager
from mycelos.config.generations import (
    ConfigTamperError,
    GenerationNotFoundError,
    NoActiveGenerationError,
)
from mycelos.storage.database import SQLiteStorage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_manager(db_path: Path) -> ConfigGenerationManager:
    storage = SQLiteStorage(db_path)
    storage.initialize()
    return ConfigGenerationManager(storage)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_apply_first_config(db_path: Path) -> None:
    """First apply() creates generation with ID 1."""
    manager = make_manager(db_path)
    gen_id = manager.apply({"key": "value"}, description="initial")
    assert gen_id == 1
    assert manager.get_active_generation_id() == 1


def test_apply_creates_new_generation(db_path: Path) -> None:
    """Each apply() with a different config produces a new, incrementing ID."""
    manager = make_manager(db_path)
    id1 = manager.apply({"a": 1})
    id2 = manager.apply({"a": 2})
    id3 = manager.apply({"a": 3})

    assert id1 == 1
    assert id2 == 2
    assert id3 == 3
    assert manager.get_active_generation_id() == 3


def test_rollback_to_previous(db_path: Path) -> None:
    """rollback() with no argument moves to the parent of the current generation."""
    manager = make_manager(db_path)
    manager.apply({"v": 1})          # gen 1  (parent=None)
    manager.apply({"v": 2})          # gen 2  (parent=1)
    manager.apply({"v": 3})          # gen 3  (parent=2)

    rolled = manager.rollback()      # should return 2
    assert rolled == 2
    assert manager.get_active_generation_id() == 2


def test_rollback_to_specific(db_path: Path) -> None:
    """rollback(to_generation=N) makes generation N active."""
    manager = make_manager(db_path)
    manager.apply({"v": 1})   # gen 1
    manager.apply({"v": 2})   # gen 2
    manager.apply({"v": 3})   # gen 3
    manager.apply({"v": 4})   # gen 4

    rolled = manager.rollback(to_generation=1)
    assert rolled == 1
    assert manager.get_active_generation_id() == 1


def test_rollback_to_nonexistent_raises(db_path: Path) -> None:
    """rollback() to a missing generation raises GenerationNotFoundError."""
    manager = make_manager(db_path)
    manager.apply({"v": 1})

    with pytest.raises(GenerationNotFoundError):
        manager.rollback(to_generation=999)


def test_get_config_snapshot(db_path: Path) -> None:
    """get_active_config() returns the exact dict that was applied."""
    manager = make_manager(db_path)
    cfg = {"model": "gpt-4o", "temperature": 0.7, "max_tokens": 1024}
    manager.apply(cfg)

    snapshot = manager.get_active_config()
    assert snapshot == cfg


def test_list_generations(db_path: Path) -> None:
    """list_generations() returns all generations newest-first with active marker."""
    manager = make_manager(db_path)
    manager.apply({"v": 1}, description="first")
    manager.apply({"v": 2}, description="second")
    manager.apply({"v": 3}, description="third")
    manager.rollback(to_generation=1)

    generations = manager.list_generations()

    assert len(generations) == 3

    # Newest first
    ids = [g.id for g in generations]
    assert ids == [3, 2, 1]

    # Active marker is on generation 1 after the rollback
    active_flags = {g.id: g.is_active for g in generations}
    assert active_flags[1] is True
    assert active_flags[2] is False
    assert active_flags[3] is False

    # Descriptions are preserved
    desc_map = {g.id: g.description for g in generations}
    assert desc_map[1] == "first"
    assert desc_map[2] == "second"
    assert desc_map[3] == "third"


def test_duplicate_config_deduplication(db_path: Path) -> None:
    """Applying an identical config reuses the existing generation (same ID)."""
    manager = make_manager(db_path)
    cfg = {"stable": True}

    id1 = manager.apply(cfg, description="original")
    manager.apply({"stable": False})  # different config → new generation

    # Re-apply the first config — must return id1, not a new row
    id_reused = manager.apply(cfg, description="duplicate attempt")

    assert id_reused == id1
    assert manager.get_active_generation_id() == id1

    # Only two distinct generations should exist in the database
    generations = manager.list_generations()
    assert len(generations) == 2


def test_diff_generations(db_path: Path) -> None:
    """diff() reports added, removed, and changed keys correctly."""
    manager = make_manager(db_path)
    id_a = manager.apply(
        {"shared": "same", "removed_key": "old_value", "changed_key": 1}
    )
    id_b = manager.apply(
        {"shared": "same", "added_key": "new_value", "changed_key": 2}
    )

    result = manager.diff(id_a, id_b)

    assert result.generation_a == id_a
    assert result.generation_b == id_b

    # Key present in a but not b
    assert "removed_key" in result.removed
    assert result.removed["removed_key"] == "old_value"

    # Key present in b but not a
    assert "added_key" in result.added
    assert result.added["added_key"] == "new_value"

    # Key present in both with different value
    assert "changed_key" in result.changed
    assert result.changed["changed_key"] == (1, 2)

    # Key identical in both should not appear anywhere
    assert "shared" not in result.added
    assert "shared" not in result.removed
    assert "shared" not in result.changed

    assert not result.is_empty()


def test_diff_identical_generations(db_path: Path) -> None:
    """diff() reports empty result for identical configs (deduplication path)."""
    manager = make_manager(db_path)
    cfg = {"x": 1}
    id_a = manager.apply(cfg)
    id_b = manager.apply(cfg)   # deduplicated → same id

    result = manager.diff(id_a, id_b)
    assert result.is_empty()


def test_no_active_generation_raises(db_path: Path) -> None:
    """get_active_config() raises when no generation has been applied."""
    manager = make_manager(db_path)

    with pytest.raises(NoActiveGenerationError):
        manager.get_active_config()


# ---------------------------------------------------------------------------
# SEC09 — Config snapshot tamper detection
# ---------------------------------------------------------------------------


def test_sec09_tamper_detection_on_get_active_config(db_path: Path) -> None:
    """get_active_config() must detect tampered config_snapshot via hash mismatch."""
    manager = make_manager(db_path)
    gen_id = manager.apply({"key": "original"})

    # Tamper: swap the snapshot without updating config_hash
    storage = SQLiteStorage(db_path)
    storage.initialize()
    storage.execute(
        "UPDATE config_generations SET config_snapshot = ? WHERE id = ?",
        ('{"key":"malicious"}', gen_id),
    )

    with pytest.raises(ConfigTamperError) as exc_info:
        manager.get_active_config()
    assert exc_info.value.generation_id == gen_id


def test_sec09_tamper_detection_on_load_config(db_path: Path) -> None:
    """diff() (which uses _load_config) must also detect tampering."""
    manager = make_manager(db_path)
    id_a = manager.apply({"a": 1})
    id_b = manager.apply({"a": 2})

    storage = SQLiteStorage(db_path)
    storage.initialize()
    storage.execute(
        "UPDATE config_generations SET config_snapshot = ? WHERE id = ?",
        ('{"a":999}', id_a),
    )

    with pytest.raises(ConfigTamperError):
        manager.diff(id_a, id_b)


def test_sec09_untampered_config_passes(db_path: Path) -> None:
    """An untouched generation must load cleanly."""
    manager = make_manager(db_path)
    manager.apply({"key": "value", "nested": {"x": 1}})
    config = manager.get_active_config()
    assert config == {"key": "value", "nested": {"x": 1}}


def test_sec09_tamper_emits_audit_event(db_path: Path) -> None:
    """When an audit logger is wired in, a tamper detection must be logged."""
    from mycelos.audit import SQLiteAuditLogger

    storage = SQLiteStorage(db_path)
    storage.initialize()
    audit = SQLiteAuditLogger(storage)
    manager = ConfigGenerationManager(storage, audit=audit)

    gen_id = manager.apply({"key": "original"})
    storage.execute(
        "UPDATE config_generations SET config_snapshot = ? WHERE id = ?",
        ('{"key":"malicious"}', gen_id),
    )

    with pytest.raises(ConfigTamperError):
        manager.get_active_config()

    events = audit.query(event_type="config.tamper_detected")
    assert len(events) == 1
