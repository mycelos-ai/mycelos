"""E2E test: SecurityProxy process lifecycle and integration.

Tests that a real proxy process can start, serve health checks,
handle HTTP requests through SSRF filter, and restart after crash.

These tests fork actual processes — slower than unit tests.
"""

import os
import tempfile
import time
from pathlib import Path

import pytest

# E2E tests require forking a real uvicorn process on a Unix socket.
# This may fail in sandboxed environments (macOS sandbox, CI containers).
pytestmark = pytest.mark.skipif(
    os.environ.get("MYCELOS_SKIP_PROXY_E2E", "1") == "1",
    reason="Set MYCELOS_SKIP_PROXY_E2E=0 to run proxy E2E tests (requires Unix socket permissions)",
)


@pytest.fixture
def proxy_system():
    """Start a real SecurityProxy child process for integration testing."""
    from mycelos.security.proxy_launcher import ProxyLauncher
    from mycelos.security.proxy_client import SecurityProxyClient
    from mycelos.storage.database import SQLiteStorage

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        master_key = "e2e-test-key-proxy-integration"

        # Set master key for the launcher
        os.environ["MYCELOS_MASTER_KEY"] = master_key

        # Initialize DB (proxy needs it for audit writes)
        storage = SQLiteStorage(data_dir / "mycelos.db")
        storage.initialize()
        storage.close()

        launcher = ProxyLauncher(data_dir, master_key)
        try:
            launcher.start()
        except RuntimeError as e:
            pytest.skip(f"Could not start proxy: {e}")

        client = SecurityProxyClient(
            socket_path=launcher.socket_path, token=launcher.session_token
        )

        yield launcher, client, data_dir

        launcher.stop()
        # Restore env for other tests
        os.environ.pop("MYCELOS_MASTER_KEY", None)


class TestProxyE2E:
    """Full integration tests with a real proxy process."""

    def test_health_through_socket(self, proxy_system):
        """Proxy responds to health checks over Unix socket."""
        _, client, _ = proxy_system
        health = client.health()
        assert health["status"] == "ok"
        assert "uptime_seconds" in health

    def test_http_blocked_ssrf(self, proxy_system):
        """SSRF filter works through the full process chain."""
        _, client, _ = proxy_system
        result = client.http_get("http://127.0.0.1:8080/secret")
        assert result["status"] == 0
        assert "blocked" in result.get("error", "").lower()

    def test_http_blocks_metadata(self, proxy_system):
        """AWS metadata endpoint blocked through proxy."""
        _, client, _ = proxy_system
        result = client.http_get("http://169.254.169.254/latest/meta-data/")
        assert result["status"] == 0
        assert "blocked" in result.get("error", "").lower()

    def test_proxy_is_separate_process(self, proxy_system):
        """Proxy runs in a separate process, not the test process."""
        launcher, _, _ = proxy_system
        assert launcher.is_running
        assert launcher._process.pid != os.getpid()

    def test_master_key_clearable_after_start(self, proxy_system):
        """After proxy starts, master key can be cleared from parent env."""
        launcher, client, _ = proxy_system
        # The launcher already cleared MYCELOS_MASTER_KEY from parent env
        # Proxy should still work
        health = client.health()
        assert health["status"] == "ok"

    def test_audit_events_written(self, proxy_system):
        """Proxy writes audit events to the shared DB."""
        _, client, data_dir = proxy_system
        from mycelos.storage.database import SQLiteStorage

        # Make a request that triggers an audit event
        client.http_get("http://10.0.0.1/internal")

        # Check audit_events table
        storage = SQLiteStorage(data_dir / "mycelos.db")
        events = storage.fetchall(
            "SELECT * FROM audit_events WHERE event_type LIKE 'proxy.%'"
        )
        storage.close()
        # Should have at least proxy.started + proxy.ssrf_blocked
        assert len(events) >= 1


class TestProxyRestart:
    """Tests for proxy crash detection and restart."""

    def test_proxy_crash_detected(self, proxy_system):
        """Launcher detects when proxy process dies."""
        launcher, _, _ = proxy_system
        assert launcher.is_running

        # Kill the proxy
        launcher._process.terminate()
        launcher._process.join(timeout=3)

        assert not launcher.is_running

    def test_ensure_alive_restarts(self, proxy_system):
        """ensure_alive() restarts a dead proxy."""
        launcher, _, _ = proxy_system

        # Kill the proxy
        launcher._process.terminate()
        launcher._process.join(timeout=3)
        assert not launcher.is_running

        # Re-set master key for restart
        os.environ["MYCELOS_MASTER_KEY"] = launcher._master_key

        # ensure_alive should restart it
        result = launcher.ensure_alive()
        assert result is True
        assert launcher.is_running

        # New client works
        from mycelos.security.proxy_client import SecurityProxyClient
        new_client = SecurityProxyClient(
            socket_path=launcher.socket_path, token=launcher.session_token
        )
        health = new_client.health()
        assert health["status"] == "ok"

    def test_max_restarts_enters_degraded(self, proxy_system):
        """After MAX_RESTARTS, proxy stays down (degraded mode)."""
        launcher, _, _ = proxy_system

        # Simulate having already hit max restarts
        launcher._restart_count = launcher.MAX_RESTARTS

        # Kill the proxy
        launcher._process.terminate()
        launcher._process.join(timeout=3)

        # ensure_alive should NOT restart
        result = launcher.ensure_alive()
        assert result is False
        assert not launcher.is_running
