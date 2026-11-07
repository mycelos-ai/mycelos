from unittest.mock import MagicMock
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mycelos.app import App
from mycelos.gateway.routers.models import router


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('MYCELOS_MASTER_KEY', 'discovery-only')
    app=App(tmp_path)
    app.storage.initialize()
    app.credentials.store_credential('custom', {'api_base':'http://demo.local/v1','api_key':'test-secret'})
    api=FastAPI();api.state.mycelos=app;api.include_router(router)
    return app,TestClient(api)


def test_discovery_registers_aliases_without_changing_assignments(env, monkeypatch):
    from mycelos.llm import provider_test
    app,client=env
    app.model_registry.add_model('custom/fast','custom','haiku')
    app.model_registry.set_system_defaults({'execution':['custom/fast']})
    get=MagicMock(return_value=httpx.Response(200,json={'data':[{'id':'fast'},{'id':'Qwen/local'},{'id':'fast'}]}))
    monkeypatch.setattr(provider_test,'_get',get)
    response=client.post('/api/models/providers/custom/discover')
    assert response.status_code==200,response.text
    assert response.json()['count']==2
    assert app.model_registry.get_model('custom/fast')['tier']=='haiku'
    assert app.model_registry.get_model('custom/Qwen/local')['tier']=='sonnet'
    assert app.model_registry.resolve_models(None,'execution')==['custom/fast']
    assert get.call_args.args[0]=='http://demo.local/v1/models'
    assert get.call_args.args[1]=={'Authorization':'Bearer test-secret'}
    assert client.get('/api/models/catalog/custom').json()['models'][1]['id']=='custom/Qwen/local'


def test_failed_discovery_does_not_remove_existing_models(env,monkeypatch):
    from mycelos.llm import provider_test
    app,client=env
    app.model_registry.add_model('custom/keep','custom','opus')
    monkeypatch.setattr(provider_test,'_get',lambda *a:httpx.Response(405,json={'error':'test-secret'}))
    response=client.post('/api/models/providers/custom/discover')
    assert response.status_code==422
    assert 'test-secret' not in response.text
    assert app.model_registry.get_model('custom/keep')
