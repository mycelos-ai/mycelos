"""Explicit source imports, separate from periodic model routines."""
from contextlib import asynccontextmanager
import logging
import threading

from huey import SqliteHuey
from huey.consumer import Consumer
from huey.exceptions import RetryTask

from mycelos.knowledge.local_sources import LocalSourceService
from mycelos.knowledge.source_lock import source_lock
from mycelos.i18n import t as _t

logger = logging.getLogger(__name__)


def register_local_source_task(huey, app):
    @huey.task()
    def import_local_source(source_id: str, user_id: str, request_id: str | None = None):
        if not request_id:
            return {'status': 'deferred'}
        service = LocalSourceService(app, user_id)
        try:
            return service.ingest(source_id, request_id=request_id)
        except ValueError:
            # A check may own the file lock. Retry only before import claimed it;
            # analysis failures set status=error and must not repeat model work.
            source = service.get(source_id)
            if source['status'] == 'queued' and source.get('import_request_id') == request_id:
                raise RetryTask(delay=2)
            raise
    return import_local_source


def recover_imports(app, huey):
    """Release abandoned claims. Durable queued requests retain their consent."""
    pending = {task.args[2] for task in [*huey.pending(), *huey.scheduled()] if len(task.args) == 3}
    for row in app.storage.fetchall("SELECT * FROM local_sources WHERE status IN ('queued','running')"):
        try:
            with source_lock(app.data_dir, row['id']):
                service=LocalSourceService(app,row['user_id'])
                current=service.get(row['id'])
                if current['status']=='running':
                    service._recover_locked(row['id'])
                elif current['status']=='queued' and current.get('import_request_id') not in pending:
                    app.storage.execute("UPDATE local_sources SET status='error',import_request_id=NULL,error=? WHERE id=?",
                                        (_t("sources.import_job_lost"),row['id']))
        except ValueError:
            continue  # Another process still owns the import.


def attach_source_import_runtime(api, app):
    def start():
        existing=getattr(api.state,'source_import_thread',None)
        if existing and existing.is_alive():
            return
        huey=SqliteHuey('mycelos-source-imports',filename=str(app.data_dir/'source-imports.db'))
        app.local_source_task=register_local_source_task(huey,app)
        recover_imports(app,huey)
        consumer=Consumer(huey,workers=1,periodic=False,initial_delay=.1,max_delay=2)
        consumer._set_signal_handlers=lambda:None
        thread=threading.Thread(target=consumer.run,name='source-imports',daemon=True)
        api.state.source_import_huey=huey
        api.state.source_import_consumer=consumer
        api.state.source_import_thread=thread
        thread.start()
        logger.info('Explicit source imports ready; periodic model routines are separate')

    def stop():
        consumer=getattr(api.state,'source_import_consumer',None)
        if consumer:
            consumer.stop()
        thread=getattr(api.state,'source_import_thread',None)
        if thread:
            thread.join(timeout=5)
        app.local_source_task=None

    previous=api.router.lifespan_context
    @asynccontextmanager
    async def lifetime(application):
        async with previous(application) as state:
            start()
            try:
                yield state
            finally:
                stop()
    api.router.lifespan_context=lifetime
