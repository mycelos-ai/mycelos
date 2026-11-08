"""A dedicated Huey queue which can only inspect local sources.

This queue intentionally does not register import, model or embedding tasks.
It remains available while the general model scheduler is paused.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import threading

from huey import SqliteHuey, crontab
from huey.consumer import Consumer

from mycelos.knowledge.source_checks import SourceCheckService
from mycelos.i18n import t as _t

logger = logging.getLogger(__name__)


def enqueue_source_check(app, source_id, user_id='default', *, due_only=False):
    service = SourceCheckService(app, user_id)
    service.status(source_id)  # Resolve ownership even when no worker is available.
    task = getattr(app, 'source_check_task', None)
    if task is None:
        raise RuntimeError(_t("sources.check_unavailable"))
    if not service.claim(source_id, due_only=due_only):
        return service.status(source_id)
    try:
        task(source_id, user_id, service.status(source_id)["request_id"])
    except Exception as error:
        service.fail_enqueue(source_id)
        raise RuntimeError(_t("sources.check_start_failed")) from error
    return service.status(source_id)


def enqueue_due_checks(app):
    for row in app.storage.fetchall('SELECT DISTINCT user_id FROM local_sources'):
        service = SourceCheckService(app, row['user_id'])
        try:
            service.recover()
            sources = service.due()
        except Exception:
            logger.exception('Source schedule could not be read')
            continue
        for source in sources:
            try:
                enqueue_source_check(app, source['source_id'], row['user_id'], due_only=True)
            except Exception:
                logger.exception('Source check could not be queued')


def register_source_check_tasks(huey, app):
    @huey.task()
    def check_local_source(source_id: str, user_id: str, request_id: str | None = None):
        if not request_id:
            return {"status": "deferred"}
        try:
            return SourceCheckService(app, user_id).check(source_id, request_id=request_id)
        except ValueError:
            # The source may have been paused or claimed by an explicit import.
            # The persisted lease allows a later retry, without model work.
            logger.info('Source check deferred: source unavailable or busy')
            return {'status': 'deferred'}

    @huey.periodic_task(crontab(minute='*'))
    def schedule_source_checks():
        enqueue_due_checks(app)

    return check_local_source, schedule_source_checks


def attach_source_check_runtime(api, app):
    """Start and stop one check-only consumer with the gateway lifetime."""
    def start_source_checks():
        existing = getattr(api.state, 'source_check_thread', None)
        if existing and existing.is_alive():
            return
        huey = SqliteHuey(name='mycelos-source-checks', filename=str(app.data_dir / 'source-checks.db'))
        app.source_check_task, _ = register_source_check_tasks(huey, app)
        consumer = Consumer(huey, workers=1, periodic=True, initial_delay=0.1, max_delay=5.0)
        consumer._set_signal_handlers = lambda: None
        thread = threading.Thread(target=consumer.run, name='source-checks', daemon=True)
        api.state.source_check_huey = huey
        api.state.source_check_consumer = consumer
        api.state.source_check_thread = thread
        thread.start()
        enqueue_due_checks(app)
        logger.info('Source checks started; model tasks are excluded')

    def stop_source_checks():
        consumer = getattr(api.state, 'source_check_consumer', None)
        if consumer:
            consumer.stop()
        thread = getattr(api.state, 'source_check_thread', None)
        if thread:
            thread.join(timeout=5)
        app.source_check_task = None

    previous_lifetime = api.router.lifespan_context

    @asynccontextmanager
    async def source_check_lifetime(application):
        async with previous_lifetime(application) as state:
            start_source_checks()
            try:
                yield state
            finally:
                stop_source_checks()

    api.router.lifespan_context = source_check_lifetime
