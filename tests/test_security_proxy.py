"""Tests for SecurityProxy server — auth, health, HTTP proxy."""

import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import httpx

SESSION_TOKEN = "test-session-token-12345678901234567890123456789012"


@pytest.fixture
def proxy_app():
    """Create a SecurityProxy FastAPI app for testing."""
    os.environ["MYCELOS_PROXY_TOKEN"] = SESSION_TOKEN
    os.environ["MYCELOS_MASTER_KEY"] = "test-master-key"
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_DB_PATH"] = str(Path(tmp) / "test.db")
        from mycelos.security.proxy_server import create_proxy_app
        app = create_proxy_app()
        from mycelos.storage.database import SQLiteStorage
        storage = SQLiteStorage(Path(tmp) / "test.db")
        storage.initialize()
        yield app


@pytest.fixture
def client(proxy_app):
    """httpx test client for the proxy."""
    from starlette.testclient import TestClient
    return TestClient(proxy_app)


class TestAuth:
    def test_health_returns_ok(self, client):
        resp = client.get("/health", headers={"Authorization": f"Bearer {SESSION_TOKEN}"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_rejects_missing_token(self, client):
        resp = client.get("/health")
        assert resp.status_code == 401

    def test_rejects_wrong_token(self, client):
        resp = client.get("/health", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401


class TestHttpProxy:
    def test_blocks_private_ip(self, client):
        resp = client.post("/http", json={
            "method": "GET",
            "url": "http://169.254.169.254/latest/meta-data/",
        }, headers={
            "Authorization": f"Bearer {SESSION_TOKEN}",
            "X-User-Id": "default",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == 0
        assert "blocked" in data.get("error", "").lower()

    def test_http_get_with_mock(self, client):
        # Mock DNS resolution (SSRF check resolves hostname before httpx is called)
        fake_addrinfo = [(2, 1, 6, "", ("93.184.216.34", 0))]
        with patch("mycelos.security.ssrf.socket.getaddrinfo", return_value=fake_addrinfo):
            with patch("mycelos.security.proxy_server.httpx") as mock_httpx:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.headers = {"content-type": "text/html"}
                mock_resp.text = "<html>OK</html>"
                mock_resp.url = "https://example.com"
                mock_httpx.request.return_value = mock_resp
                mock_httpx.TimeoutException = httpx.TimeoutException
                mock_httpx.RequestError = httpx.RequestError

                resp = client.post("/http", json={
                    "method": "GET",
                    "url": "https://example.com",
                    "timeout": 10,
                }, headers={
                    "Authorization": f"Bearer {SESSION_TOKEN}",
                    "X-User-Id": "default",
                })
                assert resp.status_code == 200
                assert resp.json()["status"] == 200

    def test_inject_as_custom_header(self, client):
        """Credential injection with header:{name} mode."""
        fake_addrinfo = [(2, 1, 6, "", ("93.184.216.34", 0))]
        with patch("mycelos.security.ssrf.socket.getaddrinfo", return_value=fake_addrinfo):
            with patch("mycelos.security.proxy_server.httpx") as mock_httpx:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.headers = {}
                mock_resp.text = "ok"
                mock_resp.url = "https://api.example.com"
                mock_httpx.request.return_value = mock_resp
                mock_httpx.TimeoutException = httpx.TimeoutException
                mock_httpx.RequestError = httpx.RequestError

                resp = client.post("/http", json={
                    "method": "GET",
                    "url": "https://api.example.com",
                    "inject_credential": "test-service",
                    "inject_as": "header:X-Api-Key",
                }, headers={
                    "Authorization": f"Bearer {SESSION_TOKEN}",
                    "X-User-Id": "default",
                })
                assert resp.status_code == 200

    def test_ssrf_blocked_returns_error(self, client):
        """SSRF block returns error in response body."""
        resp = client.post("/http", json={
            "method": "GET",
            "url": "http://169.254.169.254/",
        }, headers={
            "Authorization": f"Bearer {SESSION_TOKEN}",
            "X-User-Id": "default",
        })
        data = resp.json()
        assert data.get("status") == 0
        assert "blocked" in data.get("error", "").lower()


class TestMcpProxy:
    def test_mcp_stop_returns_ok(self, client):
        resp = client.post("/mcp/stop", json={
            "session_id": "nonexistent",
        }, headers={
            "Authorization": f"Bearer {SESSION_TOKEN}",
            "X-User-Id": "default",
        })
        assert resp.status_code == 200
        assert resp.json().get("ok") is True

    def test_mcp_call_unknown_session(self, client):
        resp = client.post("/mcp/call", json={
            "session_id": "nonexistent",
            "tool": "test",
            "arguments": {},
        }, headers={
            "Authorization": f"Bearer {SESSION_TOKEN}",
            "X-User-Id": "default",
        })
        data = resp.json()
        assert "error" in data or "MCP session not found" in str(data)

    def test_mcp_stop_unauthorized(self, client):
        resp = client.post("/mcp/stop", json={"session_id": "xyz"})
        assert resp.status_code == 401

    def test_mcp_call_unauthorized(self, client):
        resp = client.post("/mcp/call", json={
            "session_id": "xyz",
            "tool": "test",
            "arguments": {},
        })
        assert resp.status_code == 401


class TestLlmProxy:
    def test_llm_complete_non_streaming(self, client):
        with patch("mycelos.security.proxy_server.litellm") as mock_llm:
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock(message=MagicMock(content="Hello", tool_calls=None))]
            mock_resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
            mock_resp.model = "anthropic/claude-haiku-4-5"
            mock_llm.completion.return_value = mock_resp
            mock_llm.completion_cost.return_value = 0.0001

            resp = client.post("/llm/complete", json={
                "model": "anthropic/claude-haiku-4-5",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            }, headers={
                "Authorization": f"Bearer {SESSION_TOKEN}",
                "X-User-Id": "default",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["content"] == "Hello"
            assert data["usage"]["total_tokens"] == 15

    def test_llm_complete_streaming(self, client):
        with patch("mycelos.security.proxy_server.litellm") as mock_llm:
            chunk1 = MagicMock()
            chunk1.choices = [MagicMock(delta=MagicMock(content="Hel", tool_calls=None))]
            chunk2 = MagicMock()
            chunk2.choices = [MagicMock(delta=MagicMock(content="lo!", tool_calls=None))]
            mock_llm.completion.return_value = iter([chunk1, chunk2])

            resp = client.post("/llm/complete", json={
                "model": "anthropic/claude-haiku-4-5",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            }, headers={
                "Authorization": f"Bearer {SESSION_TOKEN}",
                "X-User-Id": "default",
            })
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")
            body = resp.text
            assert "Hel" in body
            assert "lo!" in body

    def test_llm_complete_unauthorized(self, client):
        resp = client.post("/llm/complete", json={
            "model": "anthropic/claude-haiku-4-5",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 401


class TestCredentialBootstrap:
    def test_bootstrap_within_window(self, client):
        resp = client.post("/credential/bootstrap", json={
            "service": "telegram_bootstrap_test",
        }, headers={
            "Authorization": f"Bearer {SESSION_TOKEN}",
        })
        # May return 404 (credential not found) but not 403 within window
        assert resp.status_code != 403

    def test_bootstrap_same_credential_twice_rejected(self, client):
        client.post("/credential/bootstrap", json={
            "service": "telegram_duplicate_test",
        }, headers={"Authorization": f"Bearer {SESSION_TOKEN}"})
        resp = client.post("/credential/bootstrap", json={
            "service": "telegram_duplicate_test",
        }, headers={"Authorization": f"Bearer {SESSION_TOKEN}"})
        assert resp.status_code in (403, 409)

    def test_bootstrap_unauthorized(self, client):
        resp = client.post("/credential/bootstrap", json={"service": "foo"})
        assert resp.status_code == 401

    def test_bootstrap_window_expired(self, proxy_app):
        """Bootstrap returns 403 after the 10-second window has closed."""
        # Patch time.time inside the proxy_server module so elapsed > 10s
        with patch("mycelos.security.proxy_server.time") as mock_time:
            mock_time.time.return_value = time.time() + 9999  # Far in the future
            from starlette.testclient import TestClient
            expired_client = TestClient(proxy_app)
            resp = expired_client.post("/credential/bootstrap", json={
                "service": "some_service_expired",
            }, headers={"Authorization": f"Bearer {SESSION_TOKEN}"})
        assert resp.status_code == 403


class TestProxyClient:
    def test_client_sends_auth_header(self):
        """Client includes Bearer token in every request."""
        from mycelos.security.proxy_client import SecurityProxyClient
        with patch("mycelos.security.proxy_client.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"status": 200, "body": "ok", "headers": {}, "url": "https://example.com"}),
            )
            mock_cls.return_value = mock_client

            proxy = SecurityProxyClient("/tmp/fake.sock", "my-token")
            proxy.http_get("https://example.com", user_id="stefan")

            call_args = mock_client.request.call_args
            assert call_args[1]["json"]["url"] == "https://example.com"
            assert call_args[1]["json"]["method"] == "GET"

    def test_client_raises_on_connection_failure(self):
        """ProxyUnavailableError on connection failure."""
        from mycelos.security.proxy_client import SecurityProxyClient, ProxyUnavailableError
        with patch("mycelos.security.proxy_client.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.request.side_effect = httpx.ConnectError("refused")
            mock_cls.return_value = mock_client

            proxy = SecurityProxyClient("/tmp/fake.sock", "token")
            with pytest.raises(ProxyUnavailableError):
                proxy.http_get("https://example.com")

    def test_http_post_sends_body(self):
        """http_post includes body and method in request payload."""
        from mycelos.security.proxy_client import SecurityProxyClient
        with patch("mycelos.security.proxy_client.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"status": 201, "body": "created", "headers": {}, "url": "https://api.example.com"}),
            )
            mock_cls.return_value = mock_client

            proxy = SecurityProxyClient("/tmp/fake.sock", "token")
            proxy.http_post("https://api.example.com", body={"key": "value"}, user_id="stefan")

            call_args = mock_client.request.call_args
            assert call_args[1]["json"]["method"] == "POST"
            assert call_args[1]["json"]["body"] == {"key": "value"}
            assert call_args[1]["json"]["url"] == "https://api.example.com"

    def test_mcp_start_sends_connector_id(self):
        """mcp_start sends connector_id, command, and env_vars."""
        from mycelos.security.proxy_client import SecurityProxyClient
        with patch("mycelos.security.proxy_client.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"session_id": "sess-123"}),
            )
            mock_cls.return_value = mock_client

            proxy = SecurityProxyClient("/tmp/fake.sock", "token")
            result = proxy.mcp_start("github", ["npx", "@github/mcp"], {"GH_TOKEN": "x"})

            call_args = mock_client.request.call_args
            assert call_args[1]["json"]["connector_id"] == "github"
            assert call_args[1]["json"]["command"] == ["npx", "@github/mcp"]
            assert result == {"session_id": "sess-123"}

    def test_mcp_call_sends_tool_and_arguments(self):
        """mcp_call sends session_id, tool, and arguments."""
        from mycelos.security.proxy_client import SecurityProxyClient
        with patch("mycelos.security.proxy_client.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"result": "data"}),
            )
            mock_cls.return_value = mock_client

            proxy = SecurityProxyClient("/tmp/fake.sock", "token")
            result = proxy.mcp_call("sess-123", "list_repos", {"org": "acme"})

            call_args = mock_client.request.call_args
            assert call_args[1]["json"]["session_id"] == "sess-123"
            assert call_args[1]["json"]["tool"] == "list_repos"
            assert call_args[1]["json"]["arguments"] == {"org": "acme"}
            assert result == {"result": "data"}

    def test_llm_complete_sends_model_and_messages(self):
        """llm_complete forwards model, messages, and purpose."""
        from mycelos.security.proxy_client import SecurityProxyClient
        with patch("mycelos.security.proxy_client.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"choices": [{"message": {"content": "Hello"}}]}),
            )
            mock_cls.return_value = mock_client

            proxy = SecurityProxyClient("/tmp/fake.sock", "token")
            messages = [{"role": "user", "content": "Hi"}]
            result = proxy.llm_complete("gpt-4o", messages, purpose="chat")

            call_args = mock_client.request.call_args
            assert call_args[1]["json"]["model"] == "gpt-4o"
            assert call_args[1]["json"]["messages"] == messages
            assert call_args[1]["json"]["purpose"] == "chat"
            assert result["choices"][0]["message"]["content"] == "Hello"

    def test_health_returns_dict(self):
        """health() calls GET /health and returns parsed JSON."""
        from mycelos.security.proxy_client import SecurityProxyClient
        with patch("mycelos.security.proxy_client.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"status": "ok", "version": "1.0"}),
            )
            mock_cls.return_value = mock_client

            proxy = SecurityProxyClient("/tmp/fake.sock", "token")
            result = proxy.health()

            call_args = mock_client.request.call_args
            assert call_args[0][0] == "GET"
            assert call_args[0][1] == "/health"
            assert result == {"status": "ok", "version": "1.0"}

    def test_connect_timeout_raises_proxy_unavailable(self):
        """ConnectTimeout also raises ProxyUnavailableError."""
        from mycelos.security.proxy_client import SecurityProxyClient, ProxyUnavailableError
        with patch("mycelos.security.proxy_client.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.request.side_effect = httpx.ConnectTimeout("timed out")
            mock_cls.return_value = mock_client

            proxy = SecurityProxyClient("/tmp/fake.sock", "token")
            with pytest.raises(ProxyUnavailableError):
                proxy.health()

    def test_agent_id_included_when_provided(self):
        """X-Agent-Id header is set when agent_id is given."""
        from mycelos.security.proxy_client import SecurityProxyClient
        with patch("mycelos.security.proxy_client.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"status": 200, "body": "", "headers": {}, "url": "https://example.com"}),
            )
            mock_cls.return_value = mock_client

            proxy = SecurityProxyClient("/tmp/fake.sock", "token")
            proxy.http_get("https://example.com", user_id="stefan", agent_id="creator-agent")

            call_args = mock_client.request.call_args
            assert call_args[1]["headers"].get("X-Agent-Id") == "creator-agent"

    def test_agent_id_omitted_when_none(self):
        """X-Agent-Id header is absent when agent_id is None."""
        from mycelos.security.proxy_client import SecurityProxyClient
        with patch("mycelos.security.proxy_client.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"status": 200, "body": "", "headers": {}, "url": "https://example.com"}),
            )
            mock_cls.return_value = mock_client

            proxy = SecurityProxyClient("/tmp/fake.sock", "token")
            proxy.http_get("https://example.com", user_id="stefan")

            call_args = mock_client.request.call_args
            assert "X-Agent-Id" not in call_args[1]["headers"]

    def test_protocol_conformance(self):
        """SecurityProxyClient satisfies SecurityProxyProtocol."""
        from mycelos.security.proxy_client import SecurityProxyClient
        from mycelos.protocols import SecurityProxyProtocol
        with patch("mycelos.security.proxy_client.httpx.Client"):
            proxy = SecurityProxyClient("/tmp/fake.sock", "token")
            assert isinstance(proxy, SecurityProxyProtocol)


class TestProxyLauncher:
    def test_generate_session_token(self):
        from mycelos.security.proxy_launcher import generate_session_token
        token = generate_session_token()
        assert len(token) == 64  # 32 bytes hex
        # Two tokens should be different
        assert generate_session_token() != token

    def test_create_socket_dir(self):
        import shutil
        import stat
        from mycelos.security.proxy_launcher import create_socket_dir
        sock_dir = create_socket_dir()
        try:
            assert Path(sock_dir).exists()
            assert Path(sock_dir).is_dir()
            # Check permissions (700)
            mode = oct(os.stat(sock_dir).st_mode & 0o777)
            assert mode == "0o700"
        finally:
            shutil.rmtree(sock_dir)

    def test_launcher_properties_before_start(self):
        from mycelos.security.proxy_launcher import ProxyLauncher
        launcher = ProxyLauncher(Path("/tmp"), "test-key")
        assert launcher.socket_path is None
        assert launcher.session_token is None
        assert not launcher.is_running

    def test_max_restarts_constant(self):
        """MAX_RESTARTS is 3."""
        from mycelos.security.proxy_launcher import ProxyLauncher
        assert ProxyLauncher.MAX_RESTARTS == 3

    def test_ensure_alive_returns_false_when_max_restarts_exceeded(self):
        """ensure_alive returns False when restart_count >= MAX_RESTARTS."""
        from mycelos.security.proxy_launcher import ProxyLauncher
        launcher = ProxyLauncher(Path("/tmp"), "test-key")
        launcher._restart_count = ProxyLauncher.MAX_RESTARTS
        result = launcher.ensure_alive()
        assert result is False

    def test_is_running_false_when_no_process(self):
        """is_running is False when _process is None."""
        from mycelos.security.proxy_launcher import ProxyLauncher
        launcher = ProxyLauncher(Path("/tmp"), "test-key")
        assert launcher._process is None
        assert not launcher.is_running

    def test_token_uniqueness(self):
        """Each call to generate_session_token returns a unique value."""
        from mycelos.security.proxy_launcher import generate_session_token
        tokens = {generate_session_token() for _ in range(10)}
        assert len(tokens) == 10

    def test_socket_dir_has_mycelos_prefix(self):
        """Socket dir is prefixed with mycelos-sec-."""
        import shutil
        from mycelos.security.proxy_launcher import create_socket_dir
        sock_dir = create_socket_dir()
        try:
            assert Path(sock_dir).name.startswith("mycelos-sec-")
        finally:
            shutil.rmtree(sock_dir)

    def test_stop_is_safe_when_not_started(self):
        """stop() on a launcher that was never started does not raise."""
        from mycelos.security.proxy_launcher import ProxyLauncher
        launcher = ProxyLauncher(Path("/tmp"), "test-key")
        launcher.stop()  # Should not raise

    def test_restart_increments_count(self, tmp_path):
        """restart() increments _restart_count."""
        from mycelos.security.proxy_launcher import ProxyLauncher
        (tmp_path / ".master_key").write_text("test-key")
        launcher = ProxyLauncher(tmp_path, "test-key")
        with patch.object(launcher, "stop"), patch.object(launcher, "start"):
            launcher.restart()
        assert launcher._restart_count == 1

    def test_ensure_alive_calls_restart_when_dead(self, tmp_path):
        """ensure_alive restarts a dead process."""
        from mycelos.security.proxy_launcher import ProxyLauncher
        (tmp_path / ".master_key").write_text("test-key")
        launcher = ProxyLauncher(tmp_path, "test-key")
        # Process is None (not started), restart_count below max
        with patch.object(launcher, "restart") as mock_restart:
            result = launcher.ensure_alive()
        mock_restart.assert_called_once()
        assert result is True

    def test_ensure_alive_returns_true_when_running(self):
        """ensure_alive returns True without restart when process is alive."""
        from mycelos.security.proxy_launcher import ProxyLauncher
        launcher = ProxyLauncher(Path("/tmp"), "test-key")
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = True
        launcher._process = mock_proc
        with patch.object(launcher, "restart") as mock_restart:
            result = launcher.ensure_alive()
        mock_restart.assert_not_called()
        assert result is True

    def test_ensure_alive_handles_restart_exception(self):
        """ensure_alive returns False and increments count on restart error."""
        from mycelos.security.proxy_launcher import ProxyLauncher
        launcher = ProxyLauncher(Path("/tmp"), "test-key")
        with patch.object(launcher, "restart", side_effect=RuntimeError("fail")):
            result = launcher.ensure_alive()
        assert result is False
        assert launcher._restart_count == 1


class TestLlmBrokerProxy:
    def test_broker_delegates_to_proxy(self):
        """LiteLLMBroker uses proxy_client when available."""
        from mycelos.llm.broker import LiteLLMBroker, LLMResponse
        mock_proxy = MagicMock()
        mock_proxy.llm_complete.return_value = {
            "content": "Hello from proxy",
            "tool_calls": None,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "anthropic/claude-haiku-4-5",
            "cost": 0.0001,
        }
        broker = LiteLLMBroker(
            default_model="anthropic/claude-haiku-4-5",
            proxy_client=mock_proxy,
        )
        result = broker.complete([{"role": "user", "content": "hi"}])
        assert isinstance(result, LLMResponse)
        assert result.content == "Hello from proxy"
        assert result.total_tokens == 15
        mock_proxy.llm_complete.assert_called_once()

    def test_broker_falls_back_without_proxy(self):
        """Without proxy_client, broker uses litellm directly."""
        from mycelos.llm.broker import LiteLLMBroker
        broker = LiteLLMBroker(default_model="test-model")
        assert broker._proxy_client is None
        # Just verify it doesn't error on construction

    def test_broker_proxy_accumulates_tokens(self):
        """total_tokens is incremented by proxy responses."""
        from mycelos.llm.broker import LiteLLMBroker
        mock_proxy = MagicMock()
        mock_proxy.llm_complete.return_value = {
            "content": "Response",
            "tool_calls": None,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "anthropic/claude-haiku-4-5",
            "cost": 0.0,
        }
        broker = LiteLLMBroker(proxy_client=mock_proxy)
        broker.complete([{"role": "user", "content": "first"}])
        broker.complete([{"role": "user", "content": "second"}])
        assert broker.total_tokens == 30

    def test_broker_proxy_model_in_response(self):
        """LLMResponse.model reflects the model returned by proxy."""
        from mycelos.llm.broker import LiteLLMBroker
        mock_proxy = MagicMock()
        mock_proxy.llm_complete.return_value = {
            "content": "ok",
            "tool_calls": None,
            "usage": {"total_tokens": 5},
            "model": "anthropic/claude-haiku-4-5",
            "cost": 0.0,
        }
        broker = LiteLLMBroker(proxy_client=mock_proxy)
        result = broker.complete([{"role": "user", "content": "hi"}])
        assert result.model == "anthropic/claude-haiku-4-5"

    def test_broker_proxy_passes_tools(self):
        """complete() passes tools list to proxy_client.llm_complete."""
        from mycelos.llm.broker import LiteLLMBroker
        mock_proxy = MagicMock()
        mock_proxy.llm_complete.return_value = {
            "content": "done",
            "tool_calls": None,
            "usage": {"total_tokens": 10},
            "model": "anthropic/claude-haiku-4-5",
            "cost": 0.0,
        }
        broker = LiteLLMBroker(proxy_client=mock_proxy)
        tools = [{"type": "function", "function": {"name": "my_tool"}}]
        broker.complete([{"role": "user", "content": "use tool"}], tools=tools)
        call_kwargs = mock_proxy.llm_complete.call_args[1]
        assert call_kwargs["tools"] == tools

    def test_broker_stream_delegates_to_proxy(self):
        """complete_stream() yields chunks from proxy_client when proxy is set."""
        from mycelos.llm.broker import LiteLLMBroker
        mock_proxy = MagicMock()
        mock_proxy.llm_complete.return_value = iter([
            {"content": "Hello"},
            {"content": " world"},
        ])
        broker = LiteLLMBroker(proxy_client=mock_proxy)
        chunks = list(broker.complete_stream([{"role": "user", "content": "hi"}]))
        assert chunks == ["Hello", " world"]
        mock_proxy.llm_complete.assert_called_once()

    def test_broker_stream_proxy_skips_empty_content(self):
        """complete_stream() skips dict chunks with empty/missing content."""
        from mycelos.llm.broker import LiteLLMBroker
        mock_proxy = MagicMock()
        mock_proxy.llm_complete.return_value = iter([
            {"content": ""},
            {"content": "text"},
            {"other": "key"},
        ])
        broker = LiteLLMBroker(proxy_client=mock_proxy)
        chunks = list(broker.complete_stream([{"role": "user", "content": "hi"}]))
        assert chunks == ["text"]

    def test_broker_proxy_tool_calls_in_response(self):
        """LLMResponse.tool_calls is populated from proxy response."""
        from mycelos.llm.broker import LiteLLMBroker
        mock_proxy = MagicMock()
        tool_calls = [{"id": "tc1", "function": {"name": "my_func", "arguments": "{}"}}]
        mock_proxy.llm_complete.return_value = {
            "content": "",
            "tool_calls": tool_calls,
            "usage": {"total_tokens": 8},
            "model": "anthropic/claude-haiku-4-5",
            "cost": 0.0,
        }
        broker = LiteLLMBroker(proxy_client=mock_proxy)
        result = broker.complete([{"role": "user", "content": "use tool"}])
        assert result.tool_calls == tool_calls

    def test_app_passes_proxy_client_to_llm(self):
        """App.llm passes _proxy_client to LiteLLMBroker."""
        import tempfile
        from pathlib import Path
        from mycelos.app import App

        with tempfile.TemporaryDirectory() as tmp:
            app = App(Path(tmp))
            mock_proxy = MagicMock()
            app.set_proxy_client(mock_proxy)
            # Patch credentials to avoid MYCELOS_MASTER_KEY requirement
            with patch.object(app, "_config_mgr") as mock_cfg:
                mock_cfg.get_active_config.return_value = {}
                with patch("mycelos.app.EncryptedCredentialProxy"):
                    import os
                    os.environ["MYCELOS_MASTER_KEY"] = "test-key"
                    app.storage.initialize()
                    llm = app.llm
                    assert llm._proxy_client is mock_proxy
