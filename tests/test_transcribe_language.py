"""The user's configured language must be passed to STT as a hint.

Whisper's auto-detection misfires badly on short clips (German speech
came back as English word salad). The user already told us their
language in settings — every transcription path passes it through.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client():
    from mycelos.gateway.server import create_app
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-sttlang"
        from mycelos.app import App
        from mycelos.setup import web_init
        a = App(data_dir)
        a.initialize()
        web_init(a, api_key="sk-ant-api03-FAKETESTKEYSTTLANG")
        fastapi_app = create_app(data_dir, no_scheduler=True,
                                 host="0.0.0.0", allow_insecure_bind=True)
        yield TestClient(fastapi_app)


def test_transcribe_passes_user_language(client, monkeypatch):
    from mycelos import i18n
    monkeypatch.setattr(i18n, "get_language", lambda: "de")

    mycelos = client.app.state.mycelos
    fake_proxy = MagicMock()
    fake_proxy.stt_transcribe.return_value = {
        "text": "Hallo Welt", "language": "de", "duration_seconds": 1.2,
    }
    mycelos._proxy_client = fake_proxy

    resp = client.post(
        "/api/transcribe",
        files={"audio": ("voice.ogg", b"fake-ogg", "audio/ogg")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["text"] == "Hallo Welt"

    kwargs = fake_proxy.stt_transcribe.call_args.kwargs
    assert kwargs.get("language") == "de", (
        "the user's configured language must reach the STT as a hint"
    )
