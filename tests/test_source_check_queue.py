from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from huey import SqliteHuey


def test_queue_registers_only_checks_and_no_model_work(tmp_path, monkeypatch):
    from mycelos.scheduler import source_check_task as module
    service = MagicMock()
    service.status.return_value = {'status': 'queued', 'request_id': 'request1'}
    service.claim.return_value = True
    service.check.return_value = {'status': 'idle', 'pending_count': 1}
    monkeypatch.setattr(module, 'SourceCheckService', lambda *args: service)
    huey = SqliteHuey('source-checks-test', filename=str(tmp_path / 'queue.db'))
    app = SimpleNamespace(storage=MagicMock())
    app.source_check_task, periodic = module.register_source_check_tasks(huey, app)
    result = module.enqueue_source_check(app, 'source', 'alice')
    assert result['status'] == 'queued'
    service.check.assert_not_called()
    task = huey.dequeue()
    huey.execute(task)
    service.check.assert_called_once_with('source', request_id='request1')
    assert len(huey._registry._registry) == 2


def test_enqueue_failure_releases_claim(monkeypatch):
    from mycelos.scheduler import source_check_task as module
    service = MagicMock()
    service.claim.return_value = True
    monkeypatch.setattr(module, 'SourceCheckService', lambda *args: service)
    app = SimpleNamespace(source_check_task=MagicMock(side_effect=OSError('queue unavailable')))
    with pytest.raises(RuntimeError):
        module.enqueue_source_check(app, 'source', 'alice')
    service.fail_enqueue.assert_called_once_with('source')


def test_busy_check_is_not_queued_again(monkeypatch):
    from mycelos.scheduler import source_check_task as module
    service = MagicMock()
    service.claim.return_value = False
    service.status.return_value = {'status': 'running'}
    monkeypatch.setattr(module, 'SourceCheckService', lambda *args: service)
    app = SimpleNamespace(source_check_task=MagicMock())
    assert module.enqueue_source_check(app, 'source', 'alice')['status'] == 'running'
    app.source_check_task.assert_not_called()


def test_due_sources_are_claimed_once_across_owners(tmp_path):
    from mycelos.app import App
    from mycelos.knowledge.local_sources import LocalSourceService
    from mycelos.knowledge.source_checks import SourceCheckService
    from mycelos.scheduler.source_check_task import enqueue_due_checks
    app = App(tmp_path / 'data')
    app.storage.initialize()
    root = tmp_path / 'docs'
    root.mkdir()
    app.source_check_task = MagicMock()
    for owner in ('alice', 'bob'):
        source = LocalSourceService(app, owner).add(owner, 'folder', str(root), mode='direct')
        SourceCheckService(app, owner).configure(source['id'], 15)
    app.storage.execute("UPDATE local_source_checks SET next_check='2000-01-01T00:00:00Z'")
    enqueue_due_checks(app)
    assert app.source_check_task.call_count == 2
    enqueue_due_checks(app)
    assert app.source_check_task.call_count == 2
    assert {call.args[1] for call in app.source_check_task.call_args_list} == {'alice', 'bob'}


def test_lifetime_starts_separate_consumer_while_model_tasks_stay_paused(tmp_path, monkeypatch):
    import threading
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mycelos.app import App
    from mycelos.scheduler import source_check_task as module
    stopped = threading.Event()
    started = threading.Event()
    class FakeConsumer:
        def __init__(self, huey, **kwargs):
            assert huey.name == 'mycelos-source-checks'
        def run(self):
            started.set()
            stopped.wait(5)
        def stop(self):
            stopped.set()
    monkeypatch.setattr(module, 'Consumer', FakeConsumer)
    app = App(tmp_path)
    app.storage.initialize()
    app.local_source_task = None
    api = FastAPI()
    api.state.no_scheduler = True
    module.attach_source_check_runtime(api, app)
    with TestClient(api):
        assert started.wait(1)
        assert callable(app.source_check_task)
        assert app.local_source_task is None
        assert api.state.no_scheduler is True
    assert stopped.is_set()
    assert app.source_check_task is None


def test_legacy_queued_job_without_request_id_cannot_scan(tmp_path, monkeypatch):
    from mycelos.scheduler import source_check_task as module
    service = MagicMock()
    monkeypatch.setattr(module, 'SourceCheckService', lambda *args: service)
    huey = SqliteHuey('legacy-check', filename=str(tmp_path / 'queue.db'))
    app = SimpleNamespace(storage=MagicMock())
    check, _ = module.register_source_check_tasks(huey, app)
    check('source', 'alice')
    huey.execute(huey.dequeue())
    service.check.assert_not_called()
