import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock
from mycelos.app import App
from mycelos.gateway.routers.local_sources import router
from mycelos.knowledge.local_sources import LocalSourceService


@pytest.fixture
def env(tmp_path):
    app = App(tmp_path / 'data')
    app.storage.initialize()
    root = tmp_path / 'docs'
    root.mkdir()
    (root / 'README.md').write_text('Docustore stores documents locally')
    source = LocalSourceService(app).add('Docs', 'folder', str(root), mode='analyze', model='never-call')
    api = FastAPI()
    api.state.mycelos = app
    api.include_router(router)
    return app, source, TestClient(api)


def test_schedule_and_queue_do_not_start_import(env):
    app, source, client = env
    app.local_source_task = MagicMock(side_effect=AssertionError('No import'))
    app.source_check_task = MagicMock()
    url = '/api/local-sources/' + source['id']
    response = client.put(url + '/check-schedule', json={'interval_minutes': 60})
    assert response.status_code == 200, response.text
    assert response.json()['interval_minutes'] == 60
    assert client.post(url + '/check').status_code == 200
    assert app.source_check_task.call_count == 1
    assert app.source_check_task.call_args.args[:2] == (source['id'], 'default')
    assert app.source_check_task.call_args.args[2]
    assert client.post(url + '/check').status_code == 200
    assert app.source_check_task.call_count == 1
    app.local_source_task.assert_not_called()
    assert client.get(url + '/pending').json() == []


def test_invalid_schedule_and_missing_worker(env):
    app, source, client = env
    url = '/api/local-sources/' + source['id']
    for value in (True, -1, 1, '60'):
        assert client.put(url + '/check-schedule', json={'interval_minutes': value}).status_code == 422
    assert client.post(url + '/check').status_code == 503
    assert client.get(url + '/check-status').json()['interval_minutes'] == 0
    assert client.get('/api/local-sources/missing/check-status').status_code == 422
