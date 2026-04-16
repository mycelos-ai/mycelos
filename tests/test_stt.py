"""Tests for Speech-to-Text — proxy endpoint, client, handlers."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SESSION_TOKEN = "stt-test-token-" + "x" * 48


@pytest.fixture
def proxy_app():
    os.environ["MYCELOS_PROXY_TOKEN"] = SESSION_TOKEN
    os.environ["MYCELOS_MASTER_KEY"] = "test-key-stt"
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_DB_PATH"] = str(Path(tmp) / "test.db")
        from mycelos.storage.database import SQLiteStorage
        SQLiteStorage(Path(tmp) / "test.db").initialize()
        from mycelos.security.proxy_server import create_proxy_app
        yield create_proxy_app()


@pytest.fixture
def client(proxy_app):
    from starlette.testclient import TestClient
    return TestClient(proxy_app)


AUTH = {"Authorization": f"Bearer {SESSION_TOKEN}", "X-User-Id": "default"}


class TestSttEndpoint:
    def test_requires_auth(self, client):
        resp = client.post("/stt/transcribe",
            files={"audio": ("test.ogg", b"fake-audio")})
        assert resp.status_code == 401

    def test_rejects_oversized_audio(self, client):
        big_audio = b"x" * (26 * 1024 * 1024)
        resp = client.post("/stt/transcribe",
            files={"audio": ("test.ogg", big_audio)},
            headers=AUTH)
        assert resp.status_code == 413

    def test_fails_without_credential(self, client):
        resp = client.post("/stt/transcribe",
            files={"audio": ("test.ogg", b"fake-audio")},
            headers=AUTH)
        data = resp.json()
        assert resp.status_code == 400 or "not configured" in data.get("error", "").lower()

    def test_transcribes_with_mock_whisper(self, client):
        # Store a real credential so the proxy finds it
        from mycelos.storage.database import SQLiteStorage
        from mycelos.security.credentials import EncryptedCredentialProxy
        db = SQLiteStorage(Path(os.environ["MYCELOS_DB_PATH"]))
        proxy = EncryptedCredentialProxy(db, "test-key-stt")
        proxy.store_credential("openai", {"api_key": "sk-test-key"})

        # Patch httpx at the module level so the endpoint uses the mock
        with patch("mycelos.speech.transcription.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "text": "Hello world",
                "language": "en",
                "duration": 2.5,
            }
            mock_httpx.post.return_value = mock_resp
            mock_httpx.TimeoutException = Exception
            mock_httpx.RequestError = Exception

            resp = client.post("/stt/transcribe",
                files={"audio": ("voice.ogg", b"fake-ogg-data")},
                data={"language": "auto", "model": "whisper-1"},
                headers=AUTH)
            assert resp.status_code == 200
            data = resp.json()
            assert data["text"] == "Hello world"
            assert data["language"] == "en"
            assert data["duration_seconds"] == 2.5

    def test_empty_transcription(self, client):
        from mycelos.storage.database import SQLiteStorage
        from mycelos.security.credentials import EncryptedCredentialProxy
        db = SQLiteStorage(Path(os.environ["MYCELOS_DB_PATH"]))
        proxy = EncryptedCredentialProxy(db, "test-key-stt")
        proxy.store_credential("openai", {"api_key": "sk-test"})

        with patch("mycelos.speech.transcription.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"text": "", "duration": 0}
            mock_httpx.post.return_value = mock_resp
            mock_httpx.TimeoutException = Exception
            mock_httpx.RequestError = Exception

            resp = client.post("/stt/transcribe",
                files={"audio": ("voice.ogg", b"silence")},
                headers=AUTH)
            assert resp.status_code == 200
            assert resp.json()["text"] == ""

    def test_google_provider_uses_gemini_credential(self, client):
        from mycelos.storage.database import SQLiteStorage
        from mycelos.security.credentials import EncryptedCredentialProxy
        db = SQLiteStorage(Path(os.environ["MYCELOS_DB_PATH"]))
        proxy = EncryptedCredentialProxy(db, "test-key-stt")
        proxy.store_credential("gemini", {"api_key": "google-test-key"})

        with patch("mycelos.speech.transcription.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "candidates": [{"content": {"parts": [{"text": "Hallo Welt"}]}}],
            }
            mock_httpx.post.return_value = mock_resp
            mock_httpx.TimeoutException = Exception
            mock_httpx.RequestError = Exception

            resp = client.post("/stt/transcribe",
                files={"audio": ("voice.ogg", b"fake-ogg-data", "audio/ogg")},
                data={"provider": "google"},
                headers=AUTH)
            assert resp.status_code == 200
            assert resp.json()["text"] == "Hallo Welt"

    def test_local_provider_works_without_stored_credential(self, client):
        with patch("mycelos.speech.transcription.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "text": "local transcript",
                "language": "en",
                "duration_seconds": 1.2,
            }
            mock_httpx.post.return_value = mock_resp
            mock_httpx.TimeoutException = Exception
            mock_httpx.RequestError = Exception

            resp = client.post("/stt/transcribe",
                files={"audio": ("voice.wav", b"fake-wav", "audio/wav")},
                data={"provider": "local"},
                headers=AUTH)
            assert resp.status_code == 200
            assert resp.json()["text"] == "local transcript"


class TestSttClient:
    def test_client_sends_multipart(self):
        from mycelos.security.proxy_client import SecurityProxyClient
        with patch("mycelos.security.proxy_client.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"text": "hello", "language": "en", "duration_seconds": 1.0}),
            )
            mock_cls.return_value = mock_client

            proxy = SecurityProxyClient("/tmp/fake.sock", "token")
            result = proxy.stt_transcribe(b"audio-bytes", filename="voice.ogg", user_id="stefan")
            assert result["text"] == "hello"
            # Verify request was made
            mock_client.request.assert_called_once()

    def test_client_returns_dict(self):
        from mycelos.security.proxy_client import SecurityProxyClient
        with patch("mycelos.security.proxy_client.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.request.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"text": "test", "language": "de", "duration_seconds": 2.0}),
            )
            mock_cls.return_value = mock_client

            proxy = SecurityProxyClient("/tmp/fake.sock", "token")
            result = proxy.stt_transcribe(b"data")
            assert result["language"] == "de"
            assert result["duration_seconds"] == 2.0

    def test_client_connection_failure(self):
        from mycelos.security.proxy_client import SecurityProxyClient, ProxyUnavailableError
        import httpx
        with patch("mycelos.security.proxy_client.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.request.side_effect = httpx.ConnectError("refused")
            mock_cls.return_value = mock_client

            proxy = SecurityProxyClient("/tmp/fake.sock", "token")
            with pytest.raises(ProxyUnavailableError):
                proxy.stt_transcribe(b"audio")


class TestGatewayAudioRoute:
    def test_audio_route_processes_voice(self):
        import os, tempfile
        from pathlib import Path
        from unittest.mock import MagicMock
        from mycelos.app import App

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MYCELOS_MASTER_KEY"] = "test-key-route"
            app = App(Path(tmp))
            app.initialize()

            mock_proxy = MagicMock()
            mock_proxy.stt_transcribe.return_value = {
                "text": "Hello from voice",
                "language": "en",
                "duration_seconds": 1.0,
            }
            app.set_proxy_client(mock_proxy)

            from mycelos.gateway.routes import setup_routes
            from fastapi import FastAPI
            api = FastAPI()
            api.state.mycelos = app
            mock_service = MagicMock()
            mock_service.handle_message.return_value = []
            mock_service.create_session.return_value = "test-sess"
            api.state.chat_service = mock_service
            api.state.debug = False
            setup_routes(api)

            from starlette.testclient import TestClient
            client = TestClient(api)

            resp = client.post("/api/audio",
                files={"audio": ("voice.ogg", b"fake-audio")},
                params={"session_id": "test-sess", "user_id": "default"})
            assert resp.status_code == 200
            # Verify chat service was called with [Voice] prefix
            mock_service.handle_message.assert_called_once()
            call_args = mock_service.handle_message.call_args
            assert "[Voice]" in call_args[0][0] or "[Voice]" in str(call_args)


class TestTelegramVoiceHandler:
    def test_voice_handler_exists(self):
        """Verify the voice handler function is defined."""
        from mycelos.channels import telegram
        assert hasattr(telegram, 'handle_voice_message')
        assert callable(telegram.handle_voice_message)

    def test_voice_handler_calls_stt(self):
        """Verify voice handler downloads audio and calls stt_transcribe."""
        from unittest.mock import AsyncMock, MagicMock
        import asyncio
        from mycelos.channels import telegram

        # Save originals
        orig_bot = telegram._bot
        orig_app = telegram._app
        orig_service = telegram._chat_service
        orig_allowed = telegram._allowed_users.copy()

        try:
            mock_bot = MagicMock()
            mock_bot.send_chat_action = AsyncMock()
            mock_file = MagicMock()
            mock_file.file_path = "voice/file_123.ogg"
            mock_bot.get_file = AsyncMock(return_value=mock_file)
            mock_audio = MagicMock()
            mock_audio.read.return_value = b"fake-ogg-audio"
            mock_bot.download_file = AsyncMock(return_value=mock_audio)

            mock_proxy = MagicMock()
            mock_proxy.stt_transcribe.return_value = {
                "text": "Test transcription",
                "language": "en",
                "duration_seconds": 2.0,
            }

            mock_service = MagicMock()
            mock_service.handle_message.return_value = []

            telegram._bot = mock_bot
            telegram._app = MagicMock()
            telegram._app.proxy_client = mock_proxy
            telegram._chat_service = mock_service
            telegram._allowed_users = set()

            mock_message = MagicMock()
            mock_message.from_user.id = 123456
            mock_message.voice.file_id = "file_xyz"
            mock_message.voice.duration = 2
            mock_message.answer_chat_action = AsyncMock()
            mock_message.answer = AsyncMock()
            mock_message.reply = AsyncMock()

            # Drive the async handler in a dedicated thread so this test
            # doesn't fight over the main thread's event loop. Playwright's
            # uvloop (installed by other tests in the same run) would
            # otherwise refuse `asyncio.run` with "Cannot run the event
            # loop while another loop is running".
            import threading
            exc_holder: list[BaseException] = []

            def _drive():
                try:
                    asyncio.run(telegram.handle_voice_message(mock_message))
                except BaseException as e:  # noqa: BLE001 — re-raise in main
                    exc_holder.append(e)

            t = threading.Thread(target=_drive)
            t.start()
            t.join()
            if exc_holder:
                raise exc_holder[0]

            mock_proxy.stt_transcribe.assert_called_once()
            call_args = mock_proxy.stt_transcribe.call_args
            assert b"fake-ogg-audio" in call_args.kwargs.get("audio", b"") or \
                   b"fake-ogg-audio" == call_args[1].get("audio", b"")
        finally:
            telegram._bot = orig_bot
            telegram._app = orig_app
            telegram._chat_service = orig_service
            telegram._allowed_users = orig_allowed
