"""LiteLLM proxy support for the custom provider.

Covers the three gaps against the other apps that share Stefan's proxy:
LITELLM_HOST / LITELLM_SECRET fallback, context windows from /model/info,
and speech input through the proxy, selectable in the web settings.
"""
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mycelos.app import App
from mycelos.gateway.routers.models import router
from mycelos.llm.custom import (
    custom_completion_kwargs,
    env_api_base,
    resolve_custom_credential,
)
from mycelos.speech.transcription import SttError, SttRequest, SttService


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('MYCELOS_MASTER_KEY', 'litellm-proxy-tests')
    monkeypatch.delenv('LITELLM_HOST', raising=False)
    monkeypatch.delenv('LITELLM_SECRET', raising=False)
    app = App(tmp_path)
    app.storage.initialize()
    api = FastAPI()
    api.state.mycelos = app
    api.include_router(router)
    return app, TestClient(api)


# -- Env fallback ------------------------------------------------------------

def test_env_host_gets_v1_suffix_once():
    assert env_api_base({'LITELLM_HOST': 'https://llm.example.com/'}) == 'https://llm.example.com/v1'
    assert env_api_base({'LITELLM_HOST': 'https://llm.example.com/v1'}) == 'https://llm.example.com/v1'
    assert env_api_base({}) == ''


def test_env_fills_only_blank_fields():
    environ = {'LITELLM_HOST': 'http://proxy:4000', 'LITELLM_SECRET': 'env-secret'}
    assert resolve_custom_credential(None, environ) == {
        'api_base': 'http://proxy:4000/v1', 'api_key': 'env-secret'}
    stored = {'api_base': 'http://stored/v1', 'api_key': 'stored-secret'}
    assert resolve_custom_credential(stored, environ) == stored
    assert resolve_custom_credential({'api_base': 'http://stored/v1'}, environ)['api_key'] == 'env-secret'


def test_completion_works_from_env_alone(monkeypatch):
    monkeypatch.setenv('LITELLM_HOST', 'http://proxy:4000')
    monkeypatch.setenv('LITELLM_SECRET', 'env-secret')
    assert custom_completion_kwargs('custom/pro', None) == {
        'model': 'openai/pro', 'api_base': 'http://proxy:4000/v1', 'api_key': 'env-secret'}


def test_completion_without_any_config_still_fails_closed(monkeypatch):
    monkeypatch.delenv('LITELLM_HOST', raising=False)
    monkeypatch.delenv('LITELLM_SECRET', raising=False)
    with pytest.raises(ValueError):
        custom_completion_kwargs('custom/pro', None)


def test_settings_show_env_configured_custom_without_secret(env, monkeypatch):
    app, client = env
    monkeypatch.setenv('LITELLM_HOST', 'http://proxy:4000')
    monkeypatch.setenv('LITELLM_SECRET', 'env-secret-value')
    response = client.get('/api/models/settings')
    assert response.status_code == 200
    custom = next(p for p in response.json()['providers'] if p['id'] == 'custom')
    assert custom['configured'] is True
    assert custom['url'] == 'http://proxy:4000/v1'
    assert custom['env_secret'] is True
    assert 'env-secret-value' not in response.text


def test_save_without_token_uses_env_secret_and_does_not_store_it(env, monkeypatch):
    app, client = env
    monkeypatch.setenv('LITELLM_SECRET', 'env-secret-value')
    response = client.put('/api/models/providers/custom', json={'url': 'http://proxy:4000/v1', 'api_key': ''})
    assert response.status_code == 200, response.text
    stored = app.credentials.get_credential('custom')
    assert stored == {'api_base': 'http://proxy:4000/v1'}
    assert custom_completion_kwargs('custom/pro', stored)['api_key'] == 'env-secret-value'


def test_save_without_token_and_without_env_is_rejected(env):
    app, client = env
    response = client.put('/api/models/providers/custom', json={'url': 'http://proxy:4000/v1', 'api_key': ''})
    assert response.status_code == 400


def test_provider_check_uses_env_fallback(env, monkeypatch):
    from mycelos.llm import provider_test
    app, client = env
    monkeypatch.setenv('LITELLM_HOST', 'http://proxy:4000')
    monkeypatch.setenv('LITELLM_SECRET', 'env-secret')
    get = MagicMock(return_value=httpx.Response(200, json={'data': [{'id': 'pro'}]}))
    monkeypatch.setattr(provider_test, '_get', get)
    result = provider_test.check_provider(app, 'custom')
    assert result['ok'] is True
    assert get.call_args.args == ('http://proxy:4000/v1/models', {'Authorization': 'Bearer env-secret'})


# -- Context windows ---------------------------------------------------------

def _proxy(models, info_response):
    def fake_get(url, headers):
        if url.endswith('/model/info'):
            return info_response
        return httpx.Response(200, json={'data': [{'id': m} for m in models]})
    return fake_get


def test_discovery_stores_context_windows_from_model_info(env, monkeypatch):
    from mycelos.llm import provider_test
    app, client = env
    app.credentials.store_credential('custom', {'api_base': 'http://proxy/v1', 'api_key': 'k'})
    app.model_registry.add_model('custom/pro', 'custom', 'opus', max_context=8000)
    info = httpx.Response(200, json={'data': [
        {'model_name': 'pro', 'model_info': {'max_input_tokens': 1_000_000}},
        {'model_name': 'pro', 'model_info': {'max_input_tokens': 200_000}},
        {'model_name': 'fast', 'model_info': {'max_input_tokens': None, 'max_tokens': 32_000}},
        {'model_name': 'stt', 'model_info': {}},
    ]})
    monkeypatch.setattr(provider_test, '_get', _proxy(['pro', 'fast', 'stt'], info))
    response = client.post('/api/models/providers/custom/discover')
    assert response.status_code == 200, response.text
    pro = app.model_registry.get_model('custom/pro')
    # The smallest window of an alias' deployments, and the user's tier stays.
    assert pro['max_context'] == 200_000
    assert pro['tier'] == 'opus'
    assert app.model_registry.get_model('custom/fast')['max_context'] == 32_000
    assert app.model_registry.get_model('custom/stt')['max_context'] is None


def test_discovery_without_model_info_keeps_working(env, monkeypatch):
    from mycelos.llm import provider_test
    app, client = env
    app.credentials.store_credential('custom', {'api_base': 'http://plain/v1', 'api_key': 'k'})
    app.model_registry.add_model('custom/local', 'custom', 'haiku', max_context=4096)
    monkeypatch.setattr(provider_test, '_get', _proxy(['local'], httpx.Response(404, json={'detail': 'k'})))
    response = client.post('/api/models/providers/custom/discover')
    assert response.status_code == 200
    assert app.model_registry.get_model('custom/local')['max_context'] == 4096


def test_registered_context_window_drives_compaction():
    from mycelos.chat.compaction import needs_compaction
    messages = [{'role': 'user', 'content': 'x' * 40_000}]  # ~10k tokens
    assert needs_compaction(messages, model='custom/pro') is False
    assert needs_compaction(messages, model='custom/pro', context_window=16_000) is True


def test_chat_service_reads_context_window_from_registry(env):
    from mycelos.chat.service import ChatService
    app, client = env
    app.model_registry.add_model('custom/pro', 'custom', 'opus', max_context=32_000)
    service = ChatService.__new__(ChatService)
    service._app = app
    assert service._registered_context_window('custom/pro') == 32_000
    assert service._registered_context_window('custom/unknown') is None
    assert service._registered_context_window(None) is None


# -- Speech input --------------------------------------------------------------

def _ok_response():
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {'text': 'Hallo', 'language': 'de'}
    return response


def _request(model='whisper-1'):
    return SttRequest(audio=b'a', filename='a.ogg', mime_type='audio/ogg', model=model, language='de')


def test_custom_stt_uses_custom_credential_and_saved_alias(monkeypatch):
    monkeypatch.delenv('MYCELOS_STT_PROVIDER', raising=False)
    service = SttService(
        credential_lookup=lambda name, user_id='default': {'api_base': 'http://proxy/v1', 'api_key': 'k'} if name == 'custom' else None,
        settings_lookup=lambda user_id: {'provider': 'custom', 'model': 'whisper-large'},
    )
    with patch('mycelos.speech.transcription.httpx.post', return_value=_ok_response()) as post:
        assert service.transcribe(_request()).text == 'Hallo'
    assert post.call_args.args[0] == 'http://proxy/v1/audio/transcriptions'
    assert post.call_args.kwargs['headers'] == {'Authorization': 'Bearer k'}
    assert post.call_args.kwargs['data']['model'] == 'whisper-large'


def test_custom_stt_defaults_to_stt_alias_and_env_fallback(monkeypatch):
    monkeypatch.setenv('LITELLM_HOST', 'http://proxy:4000')
    monkeypatch.setenv('LITELLM_SECRET', 'env-secret')
    service = SttService(credential_lookup=lambda *a, **k: None,
                         settings_lookup=lambda user_id: {'provider': 'custom'})
    with patch('mycelos.speech.transcription.httpx.post', return_value=_ok_response()) as post:
        service.transcribe(_request())
    assert post.call_args.args[0] == 'http://proxy:4000/v1/audio/transcriptions'
    assert post.call_args.kwargs['data']['model'] == 'stt'


def test_explicit_provider_beats_saved_setting(monkeypatch):
    service = SttService(credential_lookup=lambda *a, **k: None,
                         settings_lookup=lambda user_id: {'provider': 'custom'})
    assert service.resolve_provider('local') == 'local'
    assert service.resolve_provider(None) == 'custom'


def test_custom_stt_without_server_fails(monkeypatch):
    monkeypatch.delenv('LITELLM_HOST', raising=False)
    service = SttService(credential_lookup=lambda *a, **k: None,
                         settings_lookup=lambda user_id: {'provider': 'custom'})
    with pytest.raises(SttError):
        service.transcribe(_request())


def test_custom_stt_is_blocked_in_eu_mode():
    service = SttService(credential_lookup=lambda *a, **k: {'api_base': 'http://proxy/v1'},
                         eu_mode_check=lambda: True,
                         settings_lookup=lambda user_id: {'provider': 'custom'})
    with pytest.raises(SttError):
        service.transcribe(_request())


def test_stt_settings_endpoints(env):
    app, client = env
    assert client.get('/api/models/stt').json()['provider'] == ''
    response = client.put('/api/models/stt', json={'provider': 'custom', 'model': 'whisper-large'})
    assert response.status_code == 200, response.text
    assert client.get('/api/models/stt').json() | {'env_default': None} == {
        'provider': 'custom', 'model': 'whisper-large', 'env_default': None}
    assert client.put('/api/models/stt', json={'provider': 'openai'}).status_code == 400
    assert client.put('/api/models/stt', json={'provider': 'custom', 'model': 'a b'}).status_code == 400
    events = app.storage.fetchall("SELECT event_type FROM audit_events WHERE event_type = 'models.stt_saved'")
    assert len(events) == 1


def test_proxy_reads_saved_stt_choice():
    token = 'stt-proxy-token-' + 'x' * 48
    os.environ['MYCELOS_PROXY_TOKEN'] = token
    os.environ['MYCELOS_MASTER_KEY'] = 'test-key-stt'
    os.environ['LITELLM_HOST'] = 'http://proxy:4000'
    try:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'test.db'
            os.environ['MYCELOS_DB_PATH'] = str(db)
            from mycelos.storage.database import SQLiteStorage
            from mycelos.memory.service import SQLiteMemoryService
            storage = SQLiteStorage(db)
            storage.initialize()
            memory = SQLiteMemoryService(storage)
            memory.set('default', 'system', 'stt_provider', 'custom')
            memory.set('default', 'system', 'stt_model', 'whisper-large')
            from mycelos.security.proxy_server import create_proxy_app
            from starlette.testclient import TestClient
            client = TestClient(create_proxy_app())
            with patch('mycelos.speech.transcription.httpx.post', return_value=_ok_response()) as post:
                response = client.post('/stt/transcribe', files={'audio': ('a.ogg', b'a')},
                                       headers={'Authorization': f'Bearer {token}', 'X-User-Id': 'default'})
            assert response.status_code == 200, response.text
            assert post.call_args.args[0] == 'http://proxy:4000/v1/audio/transcriptions'
            assert post.call_args.kwargs['data']['model'] == 'whisper-large'
    finally:
        os.environ.pop('LITELLM_HOST', None)
