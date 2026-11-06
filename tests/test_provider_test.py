from types import SimpleNamespace
from unittest.mock import MagicMock
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mycelos.app import App
from mycelos.gateway.routers.models import router


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('MYCELOS_MASTER_KEY', 'provider-test-only')
    app = App(tmp_path)
    app.storage.initialize()
    app.credentials.store_credential('custom', {'api_base':'http://demo.local/v1', 'api_key':'private-token'})
    api = FastAPI()
    api.state.mycelos = app
    api.include_router(router)
    return app, TestClient(api)


def test_custom_test_checks_saved_endpoint_without_completion(env, monkeypatch):
    from mycelos.llm import provider_test
    app, client = env
    get = MagicMock(return_value=httpx.Response(200, json={'data':[{'id':'local'}]}))
    monkeypatch.setattr(provider_test, '_get', get)
    result = client.post('/api/models/providers/custom/test')
    assert result.status_code == 200
    assert result.json()['ok']
    assert result.json()['model_count'] == 1
    assert get.call_args.args == ('http://demo.local/v1/models', {'Authorization':'Bearer private-token'})
    assert 'private-token' not in result.text


@pytest.mark.parametrize('status,body', [(401, {'error':'private-token'}),(404,{}),(200,{'wrong':[]})])
def test_failure_is_clear_and_never_returns_remote_body(env, monkeypatch, status, body):
    from mycelos.llm import provider_test
    _, client = env
    monkeypatch.setattr(provider_test, '_get', lambda *a: httpx.Response(status,json=body))
    response = client.post('/api/models/providers/custom/test')
    assert not response.json()['ok']
    assert 'private-token' not in response.text


def test_timeout_is_bounded_and_safe(env, monkeypatch):
    from mycelos.llm import provider_test
    _, client = env
    def fail(*args):
        raise httpx.ReadTimeout('secret private-token')
    monkeypatch.setattr(provider_test, '_get', fail)
    response = client.post('/api/models/providers/custom/test')
    assert not response.json()['ok']
    assert 'Zeit' in response.json()['message']
    assert 'private-token' not in response.text


def test_unknown_provider_is_rejected(env):
    _, client = env
    assert client.post('/api/models/providers/missing/test').status_code == 400


@pytest.mark.parametrize('provider,url,header,body', [
    ('openai','https://api.openai.com/v1/models','Authorization',{'data':[]}),
    ('anthropic','https://api.anthropic.com/v1/models','x-api-key',{'data':[]}),
    ('gemini','https://generativelanguage.googleapis.com/v1beta/models','x-goog-api-key',{'models':[]}),
    ('mistral','https://api.mistral.ai/v1/models','Authorization',{'data':[]}),
    ('openrouter','https://openrouter.ai/api/v1/key','Authorization',{'data':{}}),
    ('ollama','http://demo.local/api/tags',None,{'models':[]}),
])
def test_each_provider_uses_its_authenticated_read_endpoint(env, monkeypatch, provider, url, header, body):
    from mycelos.llm import provider_test
    app, client = env
    app.credentials.store_credential(provider, {'api_key':'test-secret', 'api_base':'http://demo.local'})
    get = MagicMock(return_value=httpx.Response(200,json=body))
    monkeypatch.setattr(provider_test, '_get', get)
    response = client.post(f'/api/models/providers/{provider}/test')
    assert response.json()['ok']
    assert response.json()['generation_tested'] is False
    assert get.call_args.args[0] == url
    if header:
        assert 'test-secret' in get.call_args.args[1][header]


def test_vertex_test_reports_limited_scope(env, monkeypatch):
    from mycelos.llm import provider_test
    _, client = env
    login = MagicMock()
    monkeypatch.setattr(provider_test, '_vertex_login', login)
    response = client.post('/api/models/providers/vertex_ai/test')
    assert response.json()['ok']
    assert 'Modellzugriff' in response.json()['message']
    login.assert_called_once()


def test_http_client_does_not_follow_redirects(monkeypatch):
    from mycelos.llm import provider_test
    client = MagicMock()
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(provider_test.httpx, 'Client', factory)
    provider_test._get('http://demo.local/models', {'Authorization':'Bearer test-secret'})
    factory.assert_called_once_with(timeout=10, follow_redirects=False)
