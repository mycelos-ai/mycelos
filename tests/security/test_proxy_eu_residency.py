"""EU-residency enforcement at the SecurityProxy — the LAST egress point.

The gateway broker already enforces EU mode, but the proxy is where the
bytes actually leave the machine. Defense in depth: the proxy reads the
persisted EU-mode state from the (read-only) DB and refuses non-EU STT
backends and LLM providers itself, so even a future code path that
bypasses the broker cannot exfiltrate.

Also covers credential injection for the EU providers (mistral, vertex_ai),
which the proxy previously did not map — they would have authenticated via
env vars inside the proxy, bypassing the audited credential path.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SESSION_TOKEN = "eu-proxy-test-token-" + "x" * 44
AUTH = {"Authorization": f"Bearer {SESSION_TOKEN}", "X-User-Id": "default"}


@pytest.fixture
def proxy_env():
    os.environ["MYCELOS_PROXY_TOKEN"] = SESSION_TOKEN
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-eu-proxy"
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        os.environ["MYCELOS_DB_PATH"] = str(db_path)
        from mycelos.storage.database import SQLiteStorage
        storage = SQLiteStorage(db_path)
        storage.initialize()
        yield storage


@pytest.fixture
def client(proxy_env):
    from starlette.testclient import TestClient
    from mycelos.security.proxy_server import create_proxy_app
    return TestClient(create_proxy_app())


def _set_eu_mode(storage, enabled: bool) -> None:
    """Persist the EU-mode flag the way mycelos.llm.eu_mode does (system
    memory scope, JSON-encoded bool)."""
    storage.execute(
        "INSERT INTO memory_entries (user_id, scope, agent_id, key, value, created_by) "
        "VALUES ('default', 'system', NULL, 'eu_mode', ?, 'system')",
        (json.dumps(enabled),),
    )


def _store_credential(service: str, cred: dict) -> None:
    from mycelos.storage.database import SQLiteStorage
    from mycelos.security.credentials import EncryptedCredentialProxy
    db = SQLiteStorage(Path(os.environ["MYCELOS_DB_PATH"]))
    EncryptedCredentialProxy(db, "test-key-eu-proxy").store_credential(service, cred)


# ---- STT gating ----------------------------------------------------------

class TestProxySttEuGate:
    def test_eu_mode_denies_cloud_stt(self, proxy_env, client):
        _set_eu_mode(proxy_env, True)
        _store_credential("openai", {"api_key": "sk-test"})
        resp = client.post(
            "/stt/transcribe",
            files={"audio": ("voice.ogg", b"fake-audio")},
            data={"provider": "openai"},
            headers=AUTH,
        )
        assert resp.status_code == 400
        assert "eu" in resp.json().get("error", "").lower()

    def test_eu_mode_allows_local_stt(self, proxy_env, client):
        _set_eu_mode(proxy_env, True)
        with patch("mycelos.speech.transcription.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"text": "local ok", "language": "de",
                                           "duration_seconds": 1.0}
            mock_httpx.post.return_value = mock_resp
            mock_httpx.TimeoutException = Exception
            mock_httpx.RequestError = Exception
            resp = client.post(
                "/stt/transcribe",
                files={"audio": ("voice.wav", b"fake-wav")},
                data={"provider": "local"},
                headers=AUTH,
            )
        assert resp.status_code == 200
        assert resp.json()["text"] == "local ok"

    def test_eu_mode_off_allows_cloud_stt(self, proxy_env, client):
        _store_credential("openai", {"api_key": "sk-test"})
        with patch("mycelos.speech.transcription.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"text": "cloud ok", "language": "en",
                                           "duration": 1.5}
            mock_httpx.post.return_value = mock_resp
            mock_httpx.TimeoutException = Exception
            mock_httpx.RequestError = Exception
            resp = client.post(
                "/stt/transcribe",
                files={"audio": ("voice.ogg", b"fake-audio")},
                headers=AUTH,
            )
        assert resp.status_code == 200


# ---- LLM gating (defense in depth) ---------------------------------------

class TestProxyLlmEuGate:
    def test_eu_mode_denies_us_provider(self, proxy_env, client):
        _set_eu_mode(proxy_env, True)
        with patch("mycelos.security.proxy_server.litellm") as mock_litellm:
            resp = client.post(
                "/llm/complete",
                json={"model": "anthropic/claude-sonnet-4-6",
                      "messages": [{"role": "user", "content": "hi"}]},
                headers=AUTH,
            )
            mock_litellm.completion.assert_not_called()
        assert resp.status_code == 403
        assert "eu" in resp.json().get("error", "").lower()

    def test_eu_mode_allows_mistral(self, proxy_env, client):
        _set_eu_mode(proxy_env, True)
        _store_credential("mistral", {"api_key": "mistral-test-key-123456"})
        with patch("mycelos.security.proxy_server.litellm") as mock_litellm:
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = "bonjour"
            mock_resp.choices[0].message.tool_calls = None
            mock_resp.usage.prompt_tokens = 5
            mock_resp.usage.completion_tokens = 5
            mock_resp.usage.total_tokens = 10
            mock_litellm.completion.return_value = mock_resp
            mock_litellm.completion_cost.return_value = 0.0
            resp = client.post(
                "/llm/complete",
                json={"model": "mistral/mistral-large-latest",
                      "messages": [{"role": "user", "content": "hi"}]},
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            # The stored Mistral key was injected — no env-var bypass.
            kwargs = mock_litellm.completion.call_args.kwargs
            assert kwargs.get("api_key") == "mistral-test-key-123456"

    def test_vertex_credentials_injected(self, proxy_env, client):
        """Vertex AI is multi-field — the proxy must inject all three fields
        from the credential store instead of falling back to env vars."""
        _store_credential("vertex_ai", {
            "vertex_credentials": '{"type": "service_account"}',
            "vertex_project": "proj-1",
            "vertex_location": "europe-west4",
        })
        with patch("mycelos.security.proxy_server.litellm") as mock_litellm:
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = "hallo"
            mock_resp.choices[0].message.tool_calls = None
            mock_resp.usage.prompt_tokens = 5
            mock_resp.usage.completion_tokens = 5
            mock_resp.usage.total_tokens = 10
            mock_litellm.completion.return_value = mock_resp
            mock_litellm.completion_cost.return_value = 0.0
            resp = client.post(
                "/llm/complete",
                json={"model": "vertex_ai/gemini-2.5-pro",
                      "messages": [{"role": "user", "content": "hi"}]},
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            kwargs = mock_litellm.completion.call_args.kwargs
            assert kwargs.get("vertex_credentials") == '{"type": "service_account"}'
            assert kwargs.get("vertex_project") == "proj-1"
            assert kwargs.get("vertex_location") == "europe-west4"

    def test_eu_mode_denies_vertex_with_us_region(self, proxy_env, client):
        """EU mode + a Vertex credential pinned to a US region must be
        refused — the region is part of the residency guarantee."""
        _set_eu_mode(proxy_env, True)
        _store_credential("vertex_ai", {
            "vertex_credentials": '{"type": "service_account"}',
            "vertex_project": "proj-1",
            "vertex_location": "us-central1",
        })
        with patch("mycelos.security.proxy_server.litellm") as mock_litellm:
            resp = client.post(
                "/llm/complete",
                json={"model": "vertex_ai/gemini-2.5-pro",
                      "messages": [{"role": "user", "content": "hi"}]},
                headers=AUTH,
            )
            mock_litellm.completion.assert_not_called()
        assert resp.status_code == 403
