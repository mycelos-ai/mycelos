from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mycelos.app import App
from mycelos.gateway.routers.models import router


def setup(tmp_path):
    app = App(tmp_path)
    app.storage.initialize()
    api = FastAPI()
    api.state.mycelos = app
    api.include_router(router)
    return app, TestClient(api)


def test_settings_roundtrip_and_invalid_values(tmp_path):
    app, client = setup(tmp_path)
    body = {'reasoning': 'none', 'fallbacks': [], 'max_output_tokens': 8000}
    response = client.put('/api/models/task-settings/source_analysis', json=body)
    assert response.status_code == 200
    assert client.get('/api/models/task-settings').json()['source_analysis'] == body
    assert client.put('/api/models/task-settings/source_analysis', json={**body, 'reasoning': 'magic'}).status_code == 400
    assert client.put('/api/models/task-settings/source_analysis', json={**body, 'fallbacks': ['missing']} ).status_code == 400


def test_custom_alias_options_reach_proxy_and_fallback_is_logged(tmp_path):
    from mycelos.llm.broker import LLMBroker
    app, client = setup(tmp_path)
    app.model_registry.add_model('custom/spare', 'custom', 'sonnet')
    assert client.put('/api/models/task-settings/source_relations', json={
        'reasoning':'none', 'fallbacks':['custom/spare'], 'max_output_tokens':8000}).status_code == 200
    calls = []
    class Proxy:
        def llm_complete(self, **kw):
            calls.append(kw)
            if kw['model'] == 'custom/cheap':
                raise RuntimeError('UnsupportedParamsError: reasoning_effort')
            return {'content':'ok', 'usage':{}}
    broker = LLMBroker(storage=app.storage, proxy_client=Proxy())
    assert broker.complete([], model='custom/cheap', purpose='source_relations').content == 'ok'
    assert [c['model'] for c in calls] == ['custom/cheap', 'custom/spare']
    assert all(c['generation_options'] == {'reasoning_effort':'none', 'max_completion_tokens':8000, 'drop_params':False} for c in calls)
    assert app.audit.query(event_type='llm.request_failed')


def test_default_sends_no_extra_options(tmp_path):
    from mycelos.llm.broker import LLMBroker
    calls=[]
    broker=LLMBroker(proxy_client=SimpleNamespace(llm_complete=lambda **kw:(calls.append(kw) or {'content':'ok'})))
    broker.complete([],model='custom/cheap')
    assert 'generation_options' not in calls[0]


def test_doctor_reports_model_setting_failure(tmp_path):
    from mycelos.doctor.checks import check_model_requests
    app, _ = setup(tmp_path)
    assert check_model_requests(app)['status'] == 'ok'
    app.audit.log('llm.request_failed', details={'model':'custom/cheap', 'purpose':'source_relations', 'unsupported_settings':True, 'fallback_model':None})
    result = check_model_requests(app)
    assert result['status'] == 'warning'
    assert 'custom/cheap' in result['details']


def test_proxy_error_does_not_become_answer(tmp_path):
    from mycelos.llm.broker import LLMBroker
    broker = LLMBroker(proxy_client=SimpleNamespace(llm_complete=lambda **kw:{'error':'UnsupportedParamsError: reasoning_effort'}))
    with pytest.raises(RuntimeError, match='UnsupportedParamsError'):
        broker.complete([],model='custom/cheap')


def test_stream_uses_task_settings(tmp_path, monkeypatch):
    from mycelos.llm.broker import LLMBroker
    app, client = setup(tmp_path)
    client.put('/api/models/task-settings/execution', json={'reasoning':'low'})
    seen=[]
    def completion(**kw):
        seen.append(kw)
        return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='ok',tool_calls=None))],usage=None)])
    import litellm
    monkeypatch.setattr(litellm,'completion',completion)
    broker=LLMBroker(storage=app.storage)
    assert list(broker.complete_stream([],model='openai/test')) == ['ok']
    assert seen[0]['reasoning_effort'] == 'low'


def test_custom_alias_forwards_options_for_remote_validation(tmp_path, monkeypatch):
    from mycelos.llm.broker import LLMBroker
    app, client = setup(tmp_path)
    client.put('/api/models/task-settings/source_relations', json={'reasoning':'none'})
    seen=[]
    import litellm
    def completion(**kw):
        seen.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='ok',tool_calls=None))],usage=SimpleNamespace(prompt_tokens=1,completion_tokens=1,total_tokens=2))
    monkeypatch.setattr(litellm,'completion',completion)
    credentials=SimpleNamespace(get_credential=lambda *a,**kw:{'api_base':'http://localhost:4000/v1','api_key':'demo'})
    broker=LLMBroker(storage=app.storage,credential_proxy=credentials)
    broker.complete([],model='custom/cheap',purpose='source_relations')
    assert seen[0]['allowed_openai_params']==['reasoning_effort']
    assert seen[0]['drop_params'] is False


def test_proxy_finish_reason_survives_broker():
    from mycelos.llm.broker import LLMBroker
    broker = LLMBroker(proxy_client=SimpleNamespace(llm_complete=lambda **kw: {
        'content':'partial', 'finish_reason':'length', 'usage':{}}))
    assert broker.complete([], model='custom/cheap').finish_reason == 'length'
