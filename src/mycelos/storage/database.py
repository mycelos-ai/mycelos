"""SQLite storage backend with WAL mode."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class SQLiteStorage:
    """SQLite-based storage backend.

    Uses WAL mode for concurrent read access.
    Returns rows as dicts for easy field access.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()
        # Primary connection for schema init (main thread)
        self._conn: sqlite3.Connection | None = None

    def _get_connection(self) -> sqlite3.Connection:
        """Get a thread-local SQLite connection.

        Each thread gets its own connection. WAL mode allows concurrent
        reads across threads. Writes are serialized by SQLite internally
        with busy_timeout for retry.
        """
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10,  # wait up to 10s for locked DB
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")  # 5s retry on lock
        self._local.conn = conn
        # Lazy schema check on first connection per thread
        if self._conn is None:
            self._conn = conn
            self._ensure_schema()
        return conn

    def _ensure_schema(self) -> None:
        """Apply schema if tables or columns are missing. Safe to call on every connect."""
        schema_path = Path(__file__).parent / "schema.sql"
        if not schema_path.exists():
            return

        # Check if newest tables exist — if any missing, re-apply full schema
        # (CREATE TABLE IF NOT EXISTS is idempotent, safe to re-run)
        for check_table in ("llm_usage", "channels", "background_tasks", "users", "knowledge_notes", "session_agents", "tool_usage"):
            try:
                self._conn.execute(f"SELECT 1 FROM {check_table} LIMIT 0")
            except sqlite3.OperationalError:
                self._conn.executescript(schema_path.read_text())
                self._conn.commit()
                break

        # Migrate existing tables: add missing columns (V2 schema changes)
        _MIGRATIONS = [
            ("agents", "code_hash", "TEXT"),
            ("agents", "tests_hash", "TEXT"),
            ("agents", "prompt_hash", "TEXT"),
            ("agents", "user_facing", "INTEGER NOT NULL DEFAULT 0"),
            ("credentials", "user_id", "TEXT NOT NULL DEFAULT 'default'"),
            ("credentials", "label", "TEXT NOT NULL DEFAULT 'default'"),
            ("credentials", "description", "TEXT"),
            ("agents", "display_name", "TEXT"),
            ("workflows", "plan", "TEXT"),
            ("workflows", "model", "TEXT DEFAULT 'haiku'"),
            ("workflows", "allowed_tools", "TEXT DEFAULT '[]'"),
            ("workflow_runs", "conversation", "TEXT"),
            ("workflow_runs", "clarification", "TEXT"),
            ("workflow_runs", "session_id", "TEXT"),
            ("knowledge_notes", "parent_path", "TEXT"),
            ("knowledge_notes", "reminder", "BOOLEAN DEFAULT FALSE"),
            ("knowledge_notes", "remind_at", "TEXT"),
            ("knowledge_notes", "reminder_fired_at", "TEXT"),
            ("knowledge_notes", "remind_via", "TEXT"),
            ("knowledge_notes", "sort_order", "INTEGER DEFAULT 0"),
            ("agents", "system_prompt", "TEXT"),
            ("agents", "allowed_tools", "TEXT DEFAULT '[]'"),
            ("agents", "model", "TEXT"),
            ("workflows", "inputs", "TEXT DEFAULT '[]'"),
            ("workflows", "success_criteria", "TEXT"),
            ("workflows", "notification_mode", "TEXT DEFAULT 'result_only'"),
            ("knowledge_notes", "organizer_state", "TEXT NOT NULL DEFAULT 'pending'"),
            ("knowledge_notes", "organizer_seen_at", "TEXT"),
            ("knowledge_notes", "source_file", "TEXT"),
            # Connector operational telemetry — see ConnectorRegistry.record_*.
            ("connectors", "last_success_at", "TEXT"),
            ("connectors", "last_error", "TEXT"),
            ("connectors", "last_error_at", "TEXT"),
            # Reminder dispatch retry bookkeeping — see ReminderService.
            ("knowledge_notes", "dispatch_attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("knowledge_notes", "last_dispatch_error", "TEXT"),
        ]
        for table, column, col_type in _MIGRATIONS:
            try:
                self._conn.execute(f"SELECT {column} FROM {table} LIMIT 0")
            except sqlite3.OperationalError:
                try:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                    self._conn.commit()
                except sqlite3.OperationalError:
                    pass  # Column might already exist in some edge case

        # One-shot migration: earlier schema versions wrote '["chat"]' as
        # the default remind_via for every new note, regardless of which
        # channels the user had configured. That pins old reminders to
        # chat-only even after the user adds Telegram. Rewrite those to
        # NULL so the new "no instruction = all active channels" rule
        # applies. Explicit user choices (rows that contain 'telegram'
        # or 'email' in the JSON) are left alone.
        try:
            self._conn.execute(
                "UPDATE knowledge_notes SET remind_via = NULL "
                "WHERE remind_via = '[\"chat\"]'"
            )
            self._conn.commit()
        except sqlite3.OperationalError:
            pass

        # v3: organizer_suggestions table — idempotent.
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS organizer_suggestions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                note_path  TEXT NOT NULL,
                kind       TEXT NOT NULL,
                payload    TEXT NOT NULL,
                confidence REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                status     TEXT NOT NULL DEFAULT 'pending'
            );
            CREATE INDEX IF NOT EXISTS idx_organizer_pending
                ON organizer_suggestions(status) WHERE status = 'pending';
            CREATE INDEX IF NOT EXISTS idx_workflow_runs_session_id
                ON workflow_runs(session_id);
            """
        )
        self._conn.commit()

        # tool_usage table for Lazy Tool Discovery
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tool_usage (
                user_id    TEXT NOT NULL,
                agent_id   TEXT NOT NULL,
                tool_name  TEXT NOT NULL,
                call_count INTEGER NOT NULL DEFAULT 0,
                last_used  TEXT,
                PRIMARY KEY (user_id, agent_id, tool_name)
            );
            """
        )
        self._conn.commit()

    def initialize(self) -> None:
        """Create database and apply schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_connection()
        schema_path = Path(__file__).parent / "schema.sql"
        schema = schema_path.read_text()
        conn.executescript(schema)
        conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> Any:
        conn = self._get_connection()
        cursor = conn.execute(sql, params)
        # Skip auto-commit inside an active transaction — the transaction()
        # context manager commits (or rolls back) as a single atomic unit.
        if not getattr(self._local, "in_tx", False):
            conn.commit()
        return cursor

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Run a block of execute() calls atomically.

        Uses BEGIN IMMEDIATE so a write-lock is taken up front. On any
        exception, the entire block is rolled back — nothing is persisted
        partially. Required for config restore/rollback to give true
        NixOS-style all-or-nothing semantics.
        """
        conn = self._get_connection()
        if getattr(self._local, "in_tx", False):
            # Already inside a transaction — nested use is a no-op bracket
            yield
            return
        conn.execute("BEGIN IMMEDIATE")
        self._local.in_tx = True
        try:
            yield
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            self._local.in_tx = False

    def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        conn = self._get_connection()
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        conn = self._get_connection()
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def executescript(self, sql: str) -> None:
        conn = self._get_connection()
        conn.executescript(sql)
        conn.commit()

    def close(self) -> None:
        """Close the thread-local connection."""
        conn = getattr(self._local, "conn", None)
        if conn:
            conn.close()
            self._local.conn = None
        if self._conn:
            self._conn.close()
            self._conn = None
