"""The UI language setting must actually take effect — for translations
AND for the STT transcription hint — without a server restart.

Three bugs conspired here: the settings button wrote one memory key while
startup read another; get_language() was a process-global set once at
boot; and the setter went through a chat slash command. This pins the
fixed behavior: a dedicated endpoint persists the language, and
get_language() reflects it live.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

# The single canonical memory key for the user's UI language.
LANG_KEY = "user.language"


@pytest.fixture(autouse=True)
def _reset_i18n():
    """Keep the i18n process-global from leaking into other test files."""
    from mycelos.i18n import bind_app, set_language
    yield
    bind_app(None)
    set_language("en")


@pytest.fixture
def app():
    from mycelos.app import App
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-lang"
        os.environ.pop("MYCELOS_LANG", None)
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def client(app):
    from mycelos.gateway.server import create_app
    from mycelos.setup import web_init
    web_init(app, api_key="sk-ant-api03-FAKETESTKEYLANG")
    # Reuse the same app instance so DB writes are visible to assertions.
    fastapi_app = create_app(app.data_dir, no_scheduler=True,
                             host="0.0.0.0", allow_insecure_bind=True)
    fastapi_app.state.mycelos = app
    return TestClient(fastapi_app)


class TestLanguageEndpoint:
    def test_get_language_default_en(self, client):
        resp = client.get("/api/language")
        assert resp.status_code == 200
        assert resp.json()["language"] == "en"

    def test_set_language_persists(self, client, app):
        resp = client.post("/api/language", json={"language": "de"})
        assert resp.status_code == 200
        # Stored under the canonical key startup also reads.
        assert app.memory.get("default", "system", LANG_KEY) == "de"

    def test_set_language_rejects_unknown(self, client):
        resp = client.post("/api/language", json={"language": "klingon"})
        assert resp.status_code == 422

    def test_set_language_updates_get_language_live(self, client):
        """No restart needed: get_language() reflects the new value."""
        from mycelos.i18n import get_language
        client.post("/api/language", json={"language": "de"})
        assert get_language() == "de"

    def test_i18n_endpoint_reflects_new_language(self, client):
        client.post("/api/language", json={"language": "de"})
        resp = client.get("/api/i18n")
        assert resp.json()["lang"] == "de"

    def test_audit_event(self, client, app):
        client.post("/api/language", json={"language": "de"})
        row = app.storage.fetchone(
            "SELECT details FROM audit_events WHERE event_type='language.changed' "
            "ORDER BY id DESC LIMIT 1"
        )
        assert row is not None


class TestSttUsesLiveLanguage:
    def test_transcribe_hint_follows_setting(self, client, app):
        """The STT language hint must reflect the CURRENT setting, the whole
        reason the user reported wrong transcriptions."""
        from unittest.mock import MagicMock
        client.post("/api/language", json={"language": "de"})

        fake_proxy = MagicMock()
        fake_proxy.stt_transcribe.return_value = {"text": "Hallo", "language": "de"}
        app._proxy_client = fake_proxy

        resp = client.post(
            "/api/transcribe",
            files={"audio": ("voice.ogg", b"fake", "audio/ogg")},
        )
        assert resp.status_code == 200
        assert fake_proxy.stt_transcribe.call_args.kwargs.get("language") == "de"
