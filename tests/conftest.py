from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Temporary data directory for tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def db_path(tmp_data_dir: Path) -> Path:
    """Path to temporary SQLite database."""
    return tmp_data_dir / "mycelos.db"


# ---------------------------------------------------------------------------
# Auto-create test users for FK constraints
# ---------------------------------------------------------------------------

_TEST_USERS = ("default", "stefan", "alice", "bob", "u1", "u2",
               "user1", "user2", "user-alice", "user_a", "user_b",
               "telegram:42", "telegram:99")

_original_initialize = None


@pytest.fixture(autouse=True)
def _auto_seed_test_users():
    """After any SQLiteStorage.initialize(), seed common test users.

    This prevents FK constraint violations in tests that use non-default
    user_ids (e.g., user_id="stefan", user_id="alice").
    """
    from mycelos.storage.database import SQLiteStorage

    original = SQLiteStorage.initialize

    def patched_initialize(self):
        original(self)
        for uid in _TEST_USERS:
            try:
                self.execute(
                    "INSERT OR IGNORE INTO users (id, name, status) VALUES (?, ?, ?)",
                    (uid, uid, "active"),
                )
            except Exception:
                pass

    with patch.object(SQLiteStorage, "initialize", patched_initialize):
        yield


@pytest.fixture(autouse=True)
def _reset_proxy_client():
    """Make sure tests never leak http_tools._proxy_client to the next
    test. A handful of integration tests set this to a MagicMock to
    validate the two-container code path; if that state bled into a
    test expecting single-container (direct httpx) behaviour, the
    httpx.get mock wouldn't fire and the test would either hit the
    real network or fail with a Mock-related error.
    """
    from mycelos.connectors import http_tools

    original = getattr(http_tools, "_proxy_client", None)
    try:
        yield
    finally:
        http_tools._proxy_client = original
