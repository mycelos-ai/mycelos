"""The gateway's queue consumers stop completely when it shuts down.

A plain ``Consumer.stop()`` only ends huey's main loop; the worker and
scheduler threads kept polling their SQLite queue and could recreate
``source-checks.db`` after the data directory was removed. CI failed on that
with "Directory not empty" while tearing down a test's temporary directory.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient


def test_source_queue_threads_stop_with_the_gateway(monkeypatch):
    from huey.consumer import Worker

    original_loop = Worker.loop

    def slow_loop(self, now=None):
        # A worker in the middle of a queue poll when the gateway stops.
        result = original_loop(self, now)
        time.sleep(0.5)
        return result

    monkeypatch.setattr(Worker, "loop", slow_loop)
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "background-shutdown-test-key"
        from mycelos.app import App
        from mycelos.gateway.server import create_app
        App(Path(tmp)).initialize()
        api = create_app(Path(tmp), no_scheduler=True, host="0.0.0.0", allow_insecure_bind=True)
        with TestClient(api):
            consumers = [api.state.source_check_consumer, api.state.source_import_consumer]
            threads = [t for c in consumers for _, t in c.worker_threads]
            threads += [c.scheduler for c in consumers]
            assert any(t.is_alive() for t in threads)
        assert not any(t.is_alive() for t in threads)
        assert not api.state.source_check_thread.is_alive()
        assert not api.state.source_import_thread.is_alive()
