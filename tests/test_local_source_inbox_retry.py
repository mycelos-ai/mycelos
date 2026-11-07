from fastapi import FastAPI
from fastapi.testclient import TestClient
from mycelos.app import App
from mycelos.gateway.routers.inbox import router
from mycelos.knowledge.local_sources import LocalSourceService
from mycelos.knowledge.inbox_model import InboxModel
from mycelos.scheduler.run_recorder import RunRecorder, CAUSES


def test_local_source_retry_uses_import_queue_and_names_source(tmp_path, monkeypatch):
    app = App(tmp_path / 'data')
    app.storage.initialize()
    path = tmp_path / 'note.md'
    path.write_text('Small document')
    svc = LocalSourceService(app)
    source = svc.add('Livingagent README', 'file', str(path), mode='direct')
    recorder = RunRecorder(app.storage)
    recorder.fail(recorder.start('source_sync', source['id']), CAUSES['source_failed'])
    entry = next(e for e in InboxModel(app.storage).list_entries('default') if e['kind']=='failed_run')
    assert 'Livingagent README' in entry['title']
    assert source['id'] not in entry['title']
    assert 'did not return the data' not in entry['why']
    calls = []
    def enqueue(self, source_id):
        calls.append(source_id)
        return {'source_id':source_id,'status':'queued'}
    monkeypatch.setattr(LocalSourceService,'enqueue',enqueue)
    api = FastAPI()
    api.state.mycelos = app
    api.include_router(router)
    client = TestClient(api)
    response = client.post('/api/inbox/runs/'+source['id']+'/retry')
    assert response.status_code == 200
    assert response.json()['status'] == 'queued'
    assert calls == [source['id']]
    assert client.post('/api/inbox/runs/not-a-source/retry').status_code == 404
    assert calls == [source['id']]
