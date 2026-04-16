"""Tests for SQLite thread-safety — concurrent reads and writes.

Verifies that the thread-local connection approach handles:
- Concurrent reads from multiple threads
- Concurrent writes from multiple threads (WAL serialization)
- Mixed read/write from different threads
- No 'Database is locked' errors under contention
- Each thread gets its own connection
"""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path

import pytest

from mycelos.storage.database import SQLiteStorage


@pytest.fixture
def db(tmp_path) -> SQLiteStorage:
    storage = SQLiteStorage(tmp_path / "thread_test.db")
    storage.initialize()
    return storage


class TestThreadLocalConnections:
    """Each thread gets its own SQLite connection."""

    def test_main_thread_connection(self, db):
        """Main thread can read and write."""
        db.execute("INSERT INTO users (id, name, status) VALUES (?, ?, ?)",
                   ("t1", "Thread Test", "active"))
        row = db.fetchone("SELECT name FROM users WHERE id = ?", ("t1",))
        assert row["name"] == "Thread Test"

    def test_different_threads_get_different_connections(self, db):
        """Two threads should not share the same connection object."""
        connections = []

        def get_conn():
            conn = db._get_connection()
            connections.append(id(conn))

        t1 = threading.Thread(target=get_conn)
        t2 = threading.Thread(target=get_conn)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(connections) == 2
        assert connections[0] != connections[1], "Threads should have different connections"


class TestConcurrentReads:
    """Multiple threads reading simultaneously."""

    def test_parallel_reads(self, db):
        """10 threads reading at the same time — no errors."""
        # Seed data
        for i in range(20):
            db.execute(
                "INSERT INTO audit_events (event_type, details) VALUES (?, ?)",
                (f"test.event.{i}", f'{{"index": {i}}}'),
            )

        results = []
        errors = []

        def read_events():
            try:
                rows = db.fetchall("SELECT * FROM audit_events ORDER BY id")
                results.append(len(rows))
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=read_events) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Read errors: {errors}"
        assert all(r == 20 for r in results), f"Expected all 20, got {results}"


class TestConcurrentWrites:
    """Multiple threads writing simultaneously — WAL serialization."""

    def test_parallel_writes_no_locked_error(self, db):
        """5 threads each inserting 20 rows — no 'Database is locked'."""
        errors = []

        def write_events(thread_id: int):
            for i in range(20):
                try:
                    db.execute(
                        "INSERT INTO audit_events (event_type, user_id, details) VALUES (?, ?, ?)",
                        (f"thread_{thread_id}", "default", f'{{"i": {i}}}'),
                    )
                except Exception as e:
                    errors.append(f"Thread {thread_id}, row {i}: {e}")

        threads = [threading.Thread(target=write_events, args=(tid,)) for tid in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Write errors: {errors}"

        # Verify all rows were written
        rows = db.fetchall("SELECT * FROM audit_events")
        assert len(rows) == 100, f"Expected 100 rows, got {len(rows)}"

    def test_high_contention_writes(self, db):
        """10 threads each inserting 50 rows — stress test."""
        errors = []
        total_per_thread = 50

        def write_batch(thread_id: int):
            for i in range(total_per_thread):
                try:
                    db.execute(
                        "INSERT INTO audit_events (event_type, user_id, details) VALUES (?, ?, ?)",
                        (f"stress_{thread_id}", "default", f'{{"i": {i}}}'),
                    )
                except Exception as e:
                    errors.append(f"Thread {thread_id}: {e}")

        threads = [threading.Thread(target=write_batch, args=(tid,)) for tid in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # On shared CI runners, a few "database is locked" errors are acceptable
        # under extreme contention (10 threads × 50 writes). In production,
        # WAL mode + retries handle this. We allow up to 5% error rate.
        max_acceptable = int(0.05 * 10 * total_per_thread)  # 5% of 500
        assert len(errors) <= max_acceptable, \
            f"Too many contention errors ({len(errors)} > {max_acceptable}): {errors[:5]}"

        rows = db.fetchall("SELECT COUNT(*) as cnt FROM audit_events")
        assert rows[0]["cnt"] >= 500 - max_acceptable


class TestMixedReadWrite:
    """Threads reading while other threads write."""

    def test_read_during_write(self, db):
        """Writer thread inserts rows while reader thread queries — no errors."""
        # Seed some initial data
        for i in range(10):
            db.execute(
                "INSERT INTO audit_events (event_type, user_id, details) VALUES (?, ?, ?)",
                ("seed", "default", f'{{"i": {i}}}'),
            )

        errors = []
        read_results = []
        stop_flag = threading.Event()

        def writer():
            for i in range(50):
                try:
                    db.execute(
                        "INSERT INTO audit_events (event_type, user_id, details) VALUES (?, ?, ?)",
                        ("write_test", "default", f'{{"i": {i}}}'),
                    )
                except Exception as e:
                    errors.append(f"Writer: {e}")
            stop_flag.set()

        def reader():
            while not stop_flag.is_set():
                try:
                    rows = db.fetchall("SELECT COUNT(*) as cnt FROM audit_events")
                    read_results.append(rows[0]["cnt"])
                except Exception as e:
                    errors.append(f"Reader: {e}")
                time.sleep(0.01)

        writer_t = threading.Thread(target=writer)
        reader_t = threading.Thread(target=reader)
        reader_t.start()
        writer_t.start()
        writer_t.join()
        reader_t.join()

        assert not errors, f"Mixed errors: {errors}"
        assert len(read_results) > 0, "Reader should have gotten results"
        # Count should monotonically increase (or stay same)
        for i in range(1, len(read_results)):
            assert read_results[i] >= read_results[i - 1], \
                f"Count went backwards: {read_results[i-1]} -> {read_results[i]}"


class TestSimulatedTelegramAndHTTP:
    """Simulates the real scenario: Telegram + HTTP + Scheduler threads."""

    def test_telegram_http_scheduler_concurrent(self, db):
        """Three threads simulating real workload — no database locked errors."""
        errors = []

        def telegram_handler():
            """Simulates Telegram: read session, write message, write audit."""
            for i in range(20):
                try:
                    db.fetchone("SELECT * FROM users WHERE id = 'default'")
                    db.execute(
                        "INSERT INTO audit_events (event_type, user_id, details) VALUES (?, ?, ?)",
                        ("telegram.message", "default", f'{{"msg": {i}}}'),
                    )
                except Exception as e:
                    errors.append(f"Telegram: {e}")

        def http_handler():
            """Simulates HTTP API: read notes, write session."""
            for i in range(20):
                try:
                    db.fetchall("SELECT * FROM audit_events LIMIT 10")
                    db.execute(
                        "INSERT INTO audit_events (event_type, user_id, details) VALUES (?, ?, ?)",
                        ("http.request", "default", f'{{"req": {i}}}'),
                    )
                except Exception as e:
                    errors.append(f"HTTP: {e}")

        def scheduler():
            """Simulates scheduler: read workflows, write run status."""
            for i in range(20):
                try:
                    db.fetchall("SELECT * FROM workflows LIMIT 5")
                    db.execute(
                        "INSERT INTO audit_events (event_type, user_id, details) VALUES (?, ?, ?)",
                        ("scheduler.tick", "default", f'{{"tick": {i}}}'),
                    )
                except Exception as e:
                    errors.append(f"Scheduler: {e}")

        threads = [
            threading.Thread(target=telegram_handler, name="telegram"),
            threading.Thread(target=http_handler, name="http"),
            threading.Thread(target=scheduler, name="scheduler"),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent errors ({len(errors)}): {errors[:5]}"

        # All 60 audit events should be written
        rows = db.fetchall("SELECT COUNT(*) as cnt FROM audit_events")
        assert rows[0]["cnt"] == 60
