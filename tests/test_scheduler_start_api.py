"""The user can start one scheduler without a server restart."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mycelos.gateway.routers.setup import router


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('LITELLM_LOCAL_MODEL_COST_MAP', 'True')
    monkeypatch.setenv('MYCELOS_MASTER_KEY', 'scheduler-start-test-key')
    from mycelos.app import App
    from mycelos.chat.service import ChatService
    mycelos = App(tmp_path)
    mycelos.initialize()
    mycelos.local_source_task = MagicMock(name="independent_source_import")
    api = FastAPI()
    api.state.mycelos = mycelos
    api.state.chat_service = ChatService(mycelos)
    api.state.scheduler_running = False
    api.state.no_scheduler = True
    api.include_router(router)
    return TestClient(api)


def test_repeated_start_creates_one_consumer(client, monkeypatch):
    from mycelos.scheduler import huey_app
    consumer = MagicMock()
    consumer.is_alive.return_value = True
    start = MagicMock(return_value=consumer)
    monkeypatch.setattr(huey_app, 'start_consumer_thread', start)
    manual_import = client.app.state.mycelos.local_source_task
    for _ in range(2):
        response = client.post('/api/system/scheduler/start')
        assert response.status_code == 200
        assert response.json() == {'running': True}
    assert start.call_count == 1
    assert client.app.state.scheduler_running is True
    assert client.app.state.no_scheduler is False
    assert client.app.state.mycelos.local_source_task is manual_import
    assert client.app.state.chat_service._pipeline_task is client.app.state.pipeline_task


def test_failed_start_reports_503_and_can_retry(client, monkeypatch):
    from mycelos.scheduler import huey_app
    consumer = MagicMock()
    consumer.is_alive.return_value = True
    monkeypatch.setattr(huey_app, 'start_consumer_thread', MagicMock(side_effect=[
        RuntimeError('private backend details'), consumer,
    ]))
    response = client.post('/api/system/scheduler/start')
    assert response.status_code == 503
    assert 'private backend details' not in response.text
    assert client.app.state.scheduler_running is False
    assert client.post('/api/system/scheduler/start').json() == {'running': True}


def test_concurrent_start_creates_one_consumer(client, monkeypatch):
    from mycelos.gateway import server
    from mycelos.scheduler import huey_app
    consumer = MagicMock()
    consumer.is_alive.return_value = True
    start = MagicMock(return_value=consumer)
    monkeypatch.setattr(huey_app, 'start_consumer_thread', start)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: server.start_background_scheduler(
            client.app.state.mycelos, client.app), range(2)))
    assert results == [True, True]
    assert start.call_count == 1
