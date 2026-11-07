import json
import time
from fastapi import FastAPI
from fastapi.testclient import TestClient
from huey import MemoryHuey
from mycelos.app import App
from mycelos.knowledge.local_sources import LocalSourceService
from mycelos.scheduler.local_source_task import register_local_source_task


def test_manual_import_runs_when_routines_are_paused(tmp_path):
    from mycelos.scheduler.local_source_task import attach_source_import_runtime
    app=App(tmp_path/'data');app.storage.initialize()
    app.memory.set('default','system','knowledge_config',json.dumps({'embedding_provider':'none'}))
    root=tmp_path/'source';root.mkdir();(root/'idea.md').write_text('Docustore imports documents.')
    svc=LocalSourceService(app);source=svc.add('Docs','folder',str(root),mode='direct')
    api=FastAPI();api.state.no_scheduler=True;api.state.scheduler_running=False
    attach_source_import_runtime(api,app)
    with TestClient(api):
        svc.enqueue(source['id'])
        deadline=time.monotonic()+5
        while svc.get(source['id'])['status'] in ('queued','running') and time.monotonic()<deadline:
            time.sleep(.02)
        assert svc.get(source['id'])['last_run']
        assert not api.state.scheduler_running
        assert api.state.no_scheduler
        assert len(api.state.source_import_huey._registry._registry)==1
    assert app.local_source_task is None


def test_legacy_and_duplicate_jobs_do_not_import(tmp_path):
    app=App(tmp_path/'data');app.storage.initialize()
    app.memory.set('default','system','knowledge_config',json.dumps({'embedding_provider':'none'}))
    root=tmp_path/'source';root.mkdir();(root/'idea.md').write_text('Project knowledge')
    svc=LocalSourceService(app);source=svc.add('Docs','folder',str(root),mode='direct')
    queue=MemoryHuey();app.local_source_task=register_local_source_task(queue,app)
    app.local_source_task(source['id'],'default')
    queue.execute(queue.dequeue())
    assert svc.get(source['id'])['last_run'] is None
    svc.enqueue(source['id']);task=queue.dequeue()
    queue.execute(task);queue.execute(task)
    assert app.storage.fetchone("SELECT count(*) n FROM workflow_runs WHERE kind='source_sync'")['n']==1


def test_recovery_keeps_durable_request_but_releases_lost_request(tmp_path):
    from mycelos.scheduler.local_source_task import recover_imports
    app=App(tmp_path/'data');app.storage.initialize()
    root=tmp_path/'source';root.mkdir()
    svc=LocalSourceService(app);source=svc.add('Docs','folder',str(root),mode='direct')
    queue=MemoryHuey();app.local_source_task=register_local_source_task(queue,app)
    svc.enqueue(source['id'])
    recover_imports(app,queue)
    assert svc.get(source['id'])['status']=='queued'
    queue.dequeue()
    recover_imports(app,queue)
    assert svc.get(source['id'])['status']=='error'
    assert svc.get(source['id'])['import_request_id'] is None


def test_busy_source_check_retries_same_request(tmp_path):
    from datetime import datetime,timedelta,timezone
    from mycelos.knowledge.source_lock import source_lock
    from mycelos.scheduler.local_source_task import recover_imports
    app=App(tmp_path/'data');app.storage.initialize()
    app.memory.set('default','system','knowledge_config',json.dumps({'embedding_provider':'none'}))
    root=tmp_path/'source';root.mkdir();(root/'idea.md').write_text('Project knowledge')
    svc=LocalSourceService(app);source=svc.add('Docs','folder',str(root),mode='direct')
    queue=MemoryHuey();app.local_source_task=register_local_source_task(queue,app)
    svc.enqueue(source['id']);task=queue.dequeue()
    with source_lock(app.data_dir,source['id']):
        queue.execute(task)
    future=datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(seconds=5)
    # Huey places retry tasks in the pending queue before scheduling them.
    pending=queue.dequeue()
    if pending:
        queue.execute(pending)
    assert queue.scheduled_count()==1
    recover_imports(app,queue)
    assert svc.get(source['id'])['status']=='queued'
    for retry in queue.read_schedule(future):
        queue.execute(retry,timestamp=future)
    assert svc.get(source['id'])['last_run']
