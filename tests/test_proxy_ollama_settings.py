"""The proxy uses the saved Ollama address for both response modes."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('provider, expected_base', [
    ('ollama', 'http://ollama.local:11434'),
    ('openai', None),
    ('custom', 'http://ollama.local:11434'),
])
def test_proxy_uses_saved_api_base_only_for_ollama(tmp_path, monkeypatch, stream, provider, expected_base):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    from mycelos.app import App
    from mycelos.security import proxy_server
    monkeypatch.setenv('MYCELOS_MASTER_KEY', 'ollama-proxy-test-key')
    monkeypatch.setenv('MYCELOS_PROXY_TOKEN', 'ollama-proxy-test-token')
    monkeypatch.setenv('MYCELOS_DB_PATH', str(tmp_path / 'mycelos.db'))
    app = App(tmp_path)
    app.initialize()
    app.credentials.store_credential(provider, {'api_base': 'http://ollama.local:11434', 'api_key': 'test-secret'})
    completion = MagicMock()
    if stream:
        completion.return_value = iter([SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(content='test', tool_calls=None))])])
    else:
        completion.return_value = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='test', tool_calls=None))], usage=None)
    monkeypatch.setattr(proxy_server, 'litellm', SimpleNamespace(
        completion=completion, completion_cost=lambda **kwargs: 0))
    client = TestClient(proxy_server.create_proxy_app())
    response = client.post('/llm/complete', headers={
        'Authorization': 'Bearer ollama-proxy-test-token',
    }, json={'model': provider + '/test', 'messages': [{'role':'user','content':'test'}], 'stream':stream,'generation_options':{'reasoning_effort':'none','drop_params':False,'max_completion_tokens':8000}})
    assert response.status_code == 200
    assert completion.call_args.kwargs['reasoning_effort'] == 'none'
    assert completion.call_args.kwargs['drop_params'] is False
    assert completion.call_args.kwargs['max_completion_tokens'] == 8000
    assert 'test' in response.text
    assert completion.call_args.kwargs.get('api_base') == expected_base

    if provider == 'custom':
        assert completion.call_args.kwargs['model'] == 'openai/test'
        assert completion.call_args.kwargs['allowed_openai_params'] == ['reasoning_effort', 'max_completion_tokens']
        assert completion.call_args.kwargs['api_key'] == 'test-secret'
