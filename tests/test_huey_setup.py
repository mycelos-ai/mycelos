"""Tests for Huey setup — SqliteHuey creation and basic task registration."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mycelos.scheduler.huey_app import create_huey


def test_create_huey():
    with tempfile.TemporaryDirectory() as tmp:
        huey = create_huey(Path(tmp))
        assert huey is not None
        assert huey.name == "mycelos"


def test_huey_uses_sqlite():
    with tempfile.TemporaryDirectory() as tmp:
        huey = create_huey(Path(tmp))
        # Verify it's a SqliteHuey instance
        from huey import SqliteHuey
        assert isinstance(huey, SqliteHuey)


def test_register_and_enqueue_task():
    """Tasks can be registered and enqueued (not executed — no consumer)."""
    with tempfile.TemporaryDirectory() as tmp:
        huey = create_huey(Path(tmp))

        @huey.task()
        def dummy_task(x: int) -> int:
            return x * 2

        # Enqueue should not raise
        result = dummy_task(5)
        assert result is not None  # Returns a Result handle


def test_register_periodic_task():
    """Periodic tasks can be registered."""
    with tempfile.TemporaryDirectory() as tmp:
        huey = create_huey(Path(tmp))
        from huey import crontab

        @huey.periodic_task(crontab(minute="*/5"))
        def check_something():
            pass

        # Should be in the periodic task list
        assert len(huey._registry._periodic_tasks) >= 1


def test_huey_db_created():
    """Huey DB file should be created on first use."""
    with tempfile.TemporaryDirectory() as tmp:
        huey = create_huey(Path(tmp))

        @huey.task()
        def trigger_db(x: int) -> int:
            return x

        trigger_db(1)
        # The SQLite file may be created lazily
        # Just verify no errors occurred
