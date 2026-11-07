import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mycelos.app import App
from mycelos.gateway.routers.models import router


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('MYCELOS_MASTER_KEY', 'test-model-settings-only')
    app = App(tmp_path)
    app.storage.initialize()
    api = FastAPI()
    api.state.mycelos = app
    api.include_router(router)
    return app, TestClient(api)


def test_add_provider_preserves_assignments_and_never_calls_model(env, monkeypatch):
    app, client = env
    app.model_registry.add_model('ollama/existing', 'ollama', 'sonnet')
    app.model_registry.set_system_defaults({'execution': ['ollama/existing']})
    import litellm
    monkeypatch.setattr(litellm, 'completion', lambda **kw: pytest.fail('No model call allowed'))
    response = client.put('/api/models/providers/openai', json={'api_key': 'demo-secret'})
    assert response.status_code == 200, response.text
    assert app.model_registry.resolve_models(None, 'execution') == ['ollama/existing']
    assert app.credentials.get_credential('openai')['api_key'] == 'demo-secret'
    assert 'demo-secret' not in client.get('/api/models/settings').text


def test_manual_model_and_local_url_are_used(env):
    app, client = env
    assert client.put('/api/models/providers/ollama', json={'url': 'http://localhost:11435'}).status_code == 200
    response = client.post('/api/models', json={'provider': 'ollama', 'model_id': 'qwen-local', 'tier': 'sonnet'})
    assert response.status_code == 200, response.text
    from mycelos.llm.broker import _resolve_litellm_kwargs
    assert _resolve_litellm_kwargs('ollama/qwen-local', app.credentials)['api_base'] == 'http://localhost:11435'
    from mycelos.setup import is_initialized
    assert is_initialized(app)


def test_invalid_provider_and_model_do_not_write(env):
    app, client = env
    assert client.put('/api/models/providers/missing', json={'api_key': 'x'}).status_code == 400
    assert client.put('/api/models/providers/ollama', json={'url': 'file:///tmp'}).status_code == 400
    assert client.post('/api/models', json={'provider': 'openai', 'model_id': 'ollama/foo', 'tier': 'sonnet'}).status_code == 400
    assert app.model_registry.list_models() == []


def test_model_update_keeps_its_existing_assignment(env):
    app, client = env
    app.model_registry.add_model('ollama/local', 'ollama', 'sonnet')
    app.model_registry.set_system_defaults({'execution': ['ollama/local']})
    response = client.post('/api/models', json={'provider': 'ollama', 'model_id': 'local', 'tier': 'opus'})
    assert response.status_code == 200, response.text
    assert app.model_registry.resolve_models(None, 'execution') == ['ollama/local']


def test_saved_key_replaces_old_environment_key(env, monkeypatch):
    app, client = env
    monkeypatch.setenv('OPENAI_API_KEY', 'obsolete-demo-key')
    client.put('/api/models/providers/openai', json={'api_key': 'new-demo-key'})
    from mycelos.llm.broker import _resolve_litellm_kwargs
    assert _resolve_litellm_kwargs('openai/demo', app.credentials)['api_key'] == 'new-demo-key'


def test_existing_ollama_configuration_stays_visible(env):
    app, client = env
    app.memory.set('default', 'system', 'ollama_url', 'http://localhost:11435')
    providers = client.get('/api/models/settings').json()['providers']
    assert next(p for p in providers if p['id'] == 'ollama')['configured'] is True


def test_custom_endpoint_and_token_preserved_and_not_exposed(env):
    app, client = env
    body = {'url': 'http://localhost:4000/v1', 'api_key': 'custom-secret'}
    response = client.put('/api/models/providers/custom', json=body)
    assert response.status_code == 200, response.text
    assert client.put('/api/models/providers/custom', json={'url':body['url'], 'api_key':''}).status_code == 200
    config = client.get('/api/models/settings')
    assert 'custom-secret' not in config.text
    custom = next(p for p in config.json()['providers'] if p['id'] == 'custom')
    assert custom['url'] == body['url']
    assert custom['configured']
    response = client.post('/api/models', json={'provider':'custom', 'model_id':'Qwen/my-model', 'tier':'sonnet'})
    assert response.json()['model_id'] == 'custom/Qwen/my-model'
    from mycelos.llm.broker import _resolve_litellm_kwargs
    assert _resolve_litellm_kwargs('custom/Qwen/my-model', app.credentials) == {
        'model':'openai/Qwen/my-model', 'api_base':body['url'], 'api_key':'custom-secret'}


def test_missing_custom_settings_cannot_use_public_openai(env):
    app, _ = env
    from mycelos.llm.broker import _resolve_litellm_kwargs
    with pytest.raises(ValueError):
        _resolve_litellm_kwargs('custom/model', app.credentials)
