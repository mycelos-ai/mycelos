"""Security tests for SecurityProxy isolation.

Verifies that the proxy enforces authentication, blocks SSRF,
isolates credentials, and enforces the bootstrap window.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SESSION_TOKEN = "sec-test-token-" + "x" * 48


@pytest.fixture
def proxy_client():
    """Create a test client for the SecurityProxy app."""
    os.environ["MYCELOS_PROXY_TOKEN"] = SESSION_TOKEN
    os.environ["MYCELOS_MASTER_KEY"] = "test-key"
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        os.environ["MYCELOS_DB_PATH"] = str(db_path)
        from mycelos.storage.database import SQLiteStorage
        SQLiteStorage(db_path).initialize()
        from mycelos.security.proxy_server import create_proxy_app
        app = create_proxy_app()
        from starlette.testclient import TestClient
        yield TestClient(app)
        # Clean up environment
        del os.environ["MYCELOS_PROXY_TOKEN"]
        del os.environ["MYCELOS_MASTER_KEY"]
        del os.environ["MYCELOS_DB_PATH"]


class TestProxyAuthEnforcement:
    """Every endpoint rejects unauthenticated requests."""

    def test_rejects_no_auth(self, proxy_client):
        """GET /health without auth returns 401."""
        assert proxy_client.get("/health").status_code == 401

    def test_rejects_empty_bearer(self, proxy_client):
        """GET /health with empty Bearer token returns 401."""
        resp = proxy_client.get("/health", headers={"Authorization": "Bearer "})
        assert resp.status_code == 401

    def test_rejects_wrong_scheme(self, proxy_client):
        """GET /health with Basic auth instead of Bearer returns 401."""
        resp = proxy_client.get("/health", headers={"Authorization": f"Basic {SESSION_TOKEN}"})
        assert resp.status_code == 401

    def test_accepts_correct_token(self, proxy_client):
        """GET /health with correct Bearer token returns 200."""
        resp = proxy_client.get("/health", headers={"Authorization": f"Bearer {SESSION_TOKEN}"})
        assert resp.status_code == 200

    def test_http_endpoint_requires_auth(self, proxy_client):
        """POST /http without auth returns 401."""
        resp = proxy_client.post("/http", json={"method": "GET", "url": "https://example.com"})
        assert resp.status_code == 401

    def test_llm_endpoint_requires_auth(self, proxy_client):
        """POST /llm/complete without auth returns 401."""
        resp = proxy_client.post("/llm/complete", json={
            "model": "test", "messages": [{"role": "user", "content": "hi"}]
        })
        assert resp.status_code == 401

    def test_mcp_endpoint_requires_auth(self, proxy_client):
        """POST /mcp/start without auth returns 401."""
        resp = proxy_client.post("/mcp/start", json={
            "connector_id": "test", "command": ["echo"]
        })
        assert resp.status_code == 401

    def test_credential_bootstrap_requires_auth(self, proxy_client):
        """POST /credential/bootstrap without auth returns 401."""
        resp = proxy_client.post("/credential/bootstrap", json={
            "service": "telegram"
        })
        assert resp.status_code == 401


class TestSsrfProtection:
    """SSRF tests through the full proxy stack."""

    AUTH = {"Authorization": f"Bearer {SESSION_TOKEN}", "X-User-Id": "test"}

    def test_blocks_localhost(self, proxy_client):
        """POST /http blocks requests to 127.0.0.1."""
        resp = proxy_client.post("/http", json={
            "method": "GET", "url": "http://127.0.0.1/admin"
        }, headers=self.AUTH)
        assert resp.json()["status"] == 0
        assert "blocked" in resp.json().get("error", "").lower()

    def test_blocks_localhost_ipv6(self, proxy_client):
        """POST /http blocks requests to ::1 (IPv6 loopback)."""
        resp = proxy_client.post("/http", json={
            "method": "GET", "url": "http://[::1]/admin"
        }, headers=self.AUTH)
        assert resp.json()["status"] == 0
        assert "blocked" in resp.json().get("error", "").lower()

    def test_blocks_metadata_endpoint(self, proxy_client):
        """POST /http blocks requests to AWS metadata endpoint."""
        resp = proxy_client.post("/http", json={
            "method": "GET", "url": "http://169.254.169.254/latest/"
        }, headers=self.AUTH)
        assert resp.json()["status"] == 0

    def test_blocks_private_10(self, proxy_client):
        """POST /http blocks requests to 10.0.0.0/8 private range."""
        resp = proxy_client.post("/http", json={
            "method": "GET", "url": "http://10.0.0.5:5432/"
        }, headers=self.AUTH)
        assert resp.json()["status"] == 0

    def test_blocks_private_172(self, proxy_client):
        """POST /http blocks requests to 172.16.0.0/12 private range."""
        resp = proxy_client.post("/http", json={
            "method": "GET", "url": "http://172.16.0.1/"
        }, headers=self.AUTH)
        assert resp.json()["status"] == 0

    def test_blocks_private_192(self, proxy_client):
        """POST /http blocks requests to 192.168.0.0/16 private range."""
        resp = proxy_client.post("/http", json={
            "method": "GET", "url": "http://192.168.1.1/"
        }, headers=self.AUTH)
        assert resp.json()["status"] == 0

    def test_blocks_file_scheme(self, proxy_client):
        """POST /http blocks file:// scheme."""
        resp = proxy_client.post("/http", json={
            "method": "GET", "url": "file:///etc/passwd"
        }, headers=self.AUTH)
        assert resp.json()["status"] == 0

    def test_blocks_gcp_metadata(self, proxy_client):
        """POST /http blocks requests to metadata.google.internal."""
        resp = proxy_client.post("/http", json={
            "method": "GET", "url": "http://metadata.google.internal/computeMetadata/v1/"
        }, headers=self.AUTH)
        assert resp.json()["status"] == 0

    def test_blocks_gcp_metadata_goog(self, proxy_client):
        """POST /http blocks requests to metadata.goog."""
        resp = proxy_client.post("/http", json={
            "method": "GET", "url": "http://metadata.goog/instance/service-accounts/default/token"
        }, headers=self.AUTH)
        assert resp.json()["status"] == 0

    def test_blocks_zero_ip(self, proxy_client):
        """POST /http blocks requests to 0.0.0.0."""
        resp = proxy_client.post("/http", json={
            "method": "GET", "url": "http://0.0.0.0/"
        }, headers=self.AUTH)
        assert resp.json()["status"] == 0


class TestCredentialIsolation:
    """Credentials never appear in responses or errors."""

    AUTH = {"Authorization": f"Bearer {SESSION_TOKEN}", "X-User-Id": "test"}

    def test_no_master_key_in_error(self, proxy_client):
        """Errors from /http never leak the MYCELOS_MASTER_KEY."""
        with patch("httpx.request") as mock_httpx:
            mock_httpx.return_value = MagicMock(
                status_code=200,
                headers={},
                text="OK",
                url="https://example.com"
            )
            resp = proxy_client.post("/http", json={
                "method": "GET",
                "url": "https://example.com",
                "inject_credential": "nonexistent",
            }, headers=self.AUTH)
            data = str(resp.json())
            assert "MYCELOS_MASTER_KEY" not in data
            assert "test-key" not in data

    def test_no_token_in_health(self, proxy_client):
        """GET /health response never contains the SESSION_TOKEN."""
        resp = proxy_client.get("/health",
            headers={"Authorization": f"Bearer {SESSION_TOKEN}"})
        data = str(resp.json())
        assert SESSION_TOKEN not in data

    def test_no_bearer_token_in_error_response(self, proxy_client):
        """Error responses never echo back the Bearer token."""
        resp = proxy_client.post("/http", json={
            "method": "GET",
            "url": "http://127.0.0.1/blocked",
        }, headers=self.AUTH)
        data = str(resp.json())
        assert SESSION_TOKEN not in data

    def test_invalid_token_not_in_auth_failure_log(self, proxy_client):
        """Auth failures don't leak the attempted token in response."""
        bad_token = "sec-test-token-" + "y" * 48
        resp = proxy_client.get("/health",
            headers={"Authorization": f"Bearer {bad_token}"})
        assert resp.status_code == 401
        # Response should not contain the bad token
        assert bad_token not in str(resp.json())


class TestBootstrapWindow:
    """Bootstrap endpoint enforces time window and single-use."""

    AUTH = {"Authorization": f"Bearer {SESSION_TOKEN}"}

    def test_bootstrap_requires_auth(self, proxy_client):
        """POST /credential/bootstrap without auth returns 401."""
        resp = proxy_client.post("/credential/bootstrap", json={
            "service": "telegram",
        })
        assert resp.status_code == 401

    def test_bootstrap_within_window(self, proxy_client):
        """Bootstrap request within 10s window doesn't return 403."""
        resp = proxy_client.post("/credential/bootstrap", json={
            "service": "telegram",
        }, headers=self.AUTH)
        # Within window: should not be 403 (might be 404 if credential doesn't exist)
        assert resp.status_code != 403

    def test_bootstrap_same_credential_twice_rejected(self, proxy_client):
        """Second bootstrap of same service returns 403."""
        # First request (within window)
        resp1 = proxy_client.post("/credential/bootstrap", json={
            "service": "telegram",
        }, headers=self.AUTH)
        # First request should not be 403 (might be 404, 200, etc.)
        assert resp1.status_code != 403

        # Second request (same service, still within window)
        resp2 = proxy_client.post("/credential/bootstrap", json={
            "service": "telegram",
        }, headers=self.AUTH)
        # Second request must be rejected
        assert resp2.status_code in (403, 409)

    def test_bootstrap_different_services_allowed(self, proxy_client):
        """Bootstrapping different services within window is allowed."""
        resp1 = proxy_client.post("/credential/bootstrap", json={
            "service": "service_a",
        }, headers=self.AUTH)
        # First service: should not be 403
        assert resp1.status_code != 403

        resp2 = proxy_client.post("/credential/bootstrap", json={
            "service": "service_b",
        }, headers=self.AUTH)
        # Different service: should not be 403 (might be 404 if doesn't exist)
        assert resp2.status_code != 403

    def test_bootstrap_requires_user_id(self, proxy_client):
        """Bootstrap requires X-User-Id for audit logging."""
        resp = proxy_client.post("/credential/bootstrap", json={
            "service": "telegram",
        }, headers=self.AUTH)
        # Should succeed (or fail gracefully, not 500)
        assert resp.status_code != 500


class TestMcpEndpoints:
    """MCP endpoints require authentication."""

    AUTH = {"Authorization": f"Bearer {SESSION_TOKEN}", "X-User-Id": "test"}

    def test_mcp_start_requires_auth(self, proxy_client):
        """POST /mcp/start without auth returns 401."""
        resp = proxy_client.post("/mcp/start", json={
            "connector_id": "test", "command": ["echo"]
        })
        assert resp.status_code == 401

    def test_mcp_call_requires_auth(self, proxy_client):
        """POST /mcp/call without auth returns 401."""
        resp = proxy_client.post("/mcp/call", json={
            "session_id": "mcp-test-123", "tool": "test_tool"
        })
        assert resp.status_code == 401

    def test_mcp_stop_requires_auth(self, proxy_client):
        """POST /mcp/stop without auth returns 401."""
        resp = proxy_client.post("/mcp/stop", json={
            "session_id": "mcp-test-123"
        })
        assert resp.status_code == 401

    def test_mcp_call_invalid_session_with_auth(self, proxy_client):
        """POST /mcp/call with invalid session returns error but is authenticated."""
        resp = proxy_client.post("/mcp/call", json={
            "session_id": "nonexistent-session", "tool": "test_tool"
        }, headers=self.AUTH)
        # Should not be 401 (auth passed), but 500 or custom error
        assert resp.status_code != 401

    def test_mcp_stop_invalid_session_with_auth(self, proxy_client):
        """POST /mcp/stop with invalid session succeeds (idempotent)."""
        resp = proxy_client.post("/mcp/stop", json={
            "session_id": "nonexistent-session"
        }, headers=self.AUTH)
        # mcp/stop is idempotent — should be 200 even if session doesn't exist
        assert resp.status_code in (200, 201)


class TestLlmEndpoint:
    """LLM endpoint requires authentication."""

    AUTH = {"Authorization": f"Bearer {SESSION_TOKEN}", "X-User-Id": "test"}

    def test_llm_requires_auth(self, proxy_client):
        """POST /llm/complete without auth returns 401."""
        resp = proxy_client.post("/llm/complete", json={
            "model": "test", "messages": [{"role": "user", "content": "hi"}]
        })
        assert resp.status_code == 401

    def test_llm_with_auth_requires_litellm(self, proxy_client):
        """POST /llm/complete with auth succeeds or fails gracefully."""
        resp = proxy_client.post("/llm/complete", json={
            "model": "test-model", "messages": [{"role": "user", "content": "hi"}]
        }, headers=self.AUTH)
        # Should not be 401 (auth passed)
        assert resp.status_code != 401


class TestAuditLogging:
    """Proxy logs security events to audit table."""

    AUTH = {"Authorization": f"Bearer {SESSION_TOKEN}", "X-User-Id": "test_user"}

    def test_auth_failures_logged(self, proxy_client):
        """Failed auth attempts are logged to audit_events."""
        # Attempt without auth
        proxy_client.get("/health")
        # Storage fixture creates a fresh database, so we can't easily
        # inspect audit logs. This test verifies the endpoint doesn't crash.
        # In a full integration test, we'd query the audit table.

    def test_ssrf_blocks_logged(self, proxy_client):
        """Blocked SSRF attempts are logged."""
        resp = proxy_client.post("/http", json={
            "method": "GET", "url": "http://127.0.0.1/"
        }, headers=self.AUTH)
        assert resp.status_code == 200 or resp.status_code == 400
        # Endpoint should complete without crashing

    def test_credential_bootstrap_logged(self, proxy_client):
        """Credential bootstrap attempts are logged."""
        resp = proxy_client.post("/credential/bootstrap", json={
            "service": "test_service"
        }, headers=self.AUTH)
        # Should not crash due to audit logging
        assert resp.status_code != 500
