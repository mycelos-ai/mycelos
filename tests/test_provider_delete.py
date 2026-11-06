import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mycelos.app import App
from mycelos.gateway.routers.models import router

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('MYCELOS_MASTER_KEY', 'delete-test')
    app = App(tmp_path)
    app.storage.initialize()
    for provider in ('anthropic', 'custom'):
        app.credentials.store_credential(provider, {'api_key': 'secret'})
    for mid, provider, tier in [('anthropic/old','anthropic','haiku'), ('custom/fast','custom','haiku'), ('custom/pro','custom','opus')]:
        app.model_registry.add_model(mid, provider, tier)
    app.model_registry.set_system_defaults({'execution':['anthropic/old']})
    api = FastAPI(); api.state.mycelos = app; api.include_router(router)
    return app, TestClient(api)

def preview(client):
    r = client.get('/api/models/providers/anthropic/delete-preview')
    assert r.status_code == 200, r.text
    return r.json()

def test_confirmed_delete_reassigns_and_removes_access(env):
    app, client = env
    p = preview(client)
    assert p['changes'][0]['after'] == ['custom/fast']
    assert app.credentials.get_credential('anthropic')
    r = client.request('DELETE','/api/models/providers/anthropic',json={'token':p['token']})
    assert r.status_code == 200, r.text
    assert app.model_registry.resolve_models(None,'execution') == ['custom/fast']
    assert app.credentials.get_credential('anthropic') is None
    assert app.credentials.get_credential('custom')
    assert app.model_registry.get_model('anthropic/old') is None

def test_preserves_existing_fallback(env):
    app, client = env
    app.model_registry.set_system_defaults({'execution':['anthropic/old','custom/pro','custom/fast']})
    assert preview(client)['changes'][0]['after'] == ['custom/pro','custom/fast']


def test_delete_updates_task_fallbacks(env):
    app, client = env
    client.put('/api/models/task-settings/source_relations', json={'reasoning':'none','fallbacks':['anthropic/old','custom/pro']})
    p = preview(client)
    assert any(c['kind']=='task_settings' for c in p['changes'])
    assert client.request('DELETE','/api/models/providers/anthropic',json={'token':p['token']}).status_code == 200
    assert client.get('/api/models/task-settings').json()['source_relations']['fallbacks']==['custom/pro']

def test_stale_preview_cannot_delete(env):
    app, client = env
    p = preview(client)
    app.model_registry.set_system_defaults({'execution':['anthropic/old','custom/pro']})
    assert client.request('DELETE','/api/models/providers/anthropic',json={'token':p['token']}).status_code == 409
    assert app.credentials.get_credential('anthropic')

def test_no_replacement_blocks(env):
    app, client = env
    app.credentials.delete_credential('custom')
    p = preview(client)
    assert p['blockers']
    assert client.request('DELETE','/api/models/providers/anthropic',json={'token':p['token']}).status_code == 409
    assert app.credentials.get_credential('anthropic')

def test_source_and_workflow_replacement(env):
    app, client = env
    app.storage.execute("INSERT INTO local_sources(id,user_id,name,kind,root,mode,model) VALUES('s','default','Projekt','folder','/tmp','analyze','anthropic/old')")
    app.storage.execute("INSERT INTO workflows(id,name,steps,model) VALUES('w','Summary','[]','anthropic/old')")
    p = preview(client)
    assert len(p['changes']) == 3
    r = client.request('DELETE','/api/models/providers/anthropic',json={'token':p['token']})
    assert r.status_code == 200, r.text
    for table in ('local_sources','workflows'):
        assert app.storage.fetchone(f'SELECT model FROM {table}')['model'] == 'custom/fast'

def test_busy_source_blocks(env):
    app, client = env
    app.storage.execute("INSERT INTO local_sources(id,user_id,name,kind,root,mode,model,status) VALUES('s','default','Projekt','folder','/tmp','analyze','anthropic/old','queued')")
    p = preview(client)
    assert p['blockers']
    assert client.request('DELETE','/api/models/providers/anthropic',json={'token':p['token']}).status_code == 409

def test_failure_rolls_back_changes(env, monkeypatch):
    app, client = env
    p = preview(client)
    def fail(*a, **kw):
        raise RuntimeError('test failure')
    monkeypatch.setattr(app.audit,'log',fail)
    with pytest.raises(RuntimeError):
        client.request('DELETE','/api/models/providers/anthropic',json={'token':p['token']})
    assert app.credentials.get_credential('anthropic')
    assert app.model_registry.resolve_models(None,'execution') == ['anthropic/old']

def test_disabled_and_non_eu_models_are_not_replacements(env):
    app, client = env
    app.storage.execute("UPDATE llm_models SET status='disabled' WHERE provider='custom'")
    assert preview(client)['blockers']
    app.storage.execute("UPDATE llm_models SET status='available' WHERE provider='custom'")
    app.memory.set('default','system','eu_mode',True)
    assert preview(client)['blockers']
