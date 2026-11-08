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


@pytest.fixture(autouse=True)
def _drop_gateway_log_handlers():
    """create_app() attaches a root FileHandler for <data_dir>/gateway.log.

    Tests build gateways in temporary directories that are deleted after the
    test. If that handler stays on the root logger and gets closed in between
    (uvicorn's dictConfig closes all handlers), the next warning logged by an
    unrelated test tries to reopen the file in the deleted directory and
    raises FileNotFoundError. Remove those handlers after each test.
    """
    import logging

    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.FileHandler) and handler.baseFilename.endswith("gateway.log"):
            root.removeHandler(handler)
            handler.close()


@pytest.fixture(autouse=True)
def _stop_security_proxies():
    """Stop every SecurityProxy child that a test started.

    create_app() forks a SecurityProxy child whenever MYCELOS_MASTER_KEY is
    set, which CI always does. Nothing in the tests stopped those children, so
    a full run kept a few hundred forked copies of the test process alive until
    the runner ran out of memory and the Tests workflow hung until GitHub
    cancelled it after six hours. Each child is now stopped when the test that
    started it ends.
    """
    from mycelos.security.proxy_launcher import ProxyLauncher

    started: list[ProxyLauncher] = []
    original_start = ProxyLauncher.start

    def tracking_start(self, *args, **kwargs):
        started.append(self)
        return original_start(self, *args, **kwargs)

    ProxyLauncher.start = tracking_start
    try:
        yield
    finally:
        ProxyLauncher.start = original_start
        for launcher in started:
            try:
                launcher.stop()
            except Exception:
                pass


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, config, items):
    """Run the Playwright browser tests last.

    pytest groups tests that use the parametrized, session-scoped
    ``browser_name`` fixture, which put the browser tests near the start of
    the run. The sync Playwright API keeps an event loop running in the main
    thread for the rest of the session, so every later async test failed
    with "Runner.run() cannot be called from a running event loop".
    """
    browser = [item for item in items if "page" in getattr(item, "fixturenames", ())]
    if not browser:
        return
    others = [item for item in items if "page" not in getattr(item, "fixturenames", ())]
    items[:] = others + browser
