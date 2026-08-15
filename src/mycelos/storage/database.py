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
            # Provenance: who created the note (agent id / 'user' / 'organizer'
            # / 'import') and from what (JSON: kind, conversation_id,
            # connector, external_id, filename, url).
            ("knowledge_notes", "created_by", "TEXT"),
            ("knowledge_notes", "source", "TEXT"),
            # Classification retry bookkeeping — see KnowledgeOrganizerHandler.
            ("knowledge_notes", "organizer_attempts", "INTEGER NOT NULL DEFAULT 0"),
            # Organizer confidence at filing time; NULL when never classified
            # or filed with certainty. Drives the "review placements" view.
            ("knowledge_notes", "placement_confidence", "REAL"),
            # Typed graph edges: wikilink | parent | related | merged_from.
            ("knowledge_links", "kind", "TEXT"),
        ]
        # This loop is unconditional and runs on every connect. That matters:
        # re-applying schema.sql only happens when one of the check-tables
        # above is missing, so a column added to schema.sql alone never
        # reaches an existing database. SQLite has no ADD COLUMN IF NOT
        # EXISTS; the SELECT ... LIMIT 0 probe is what makes each entry
        # idempotent. Add new columns here, not to schema.sql only.
        for table, column, col_type in _MIGRATIONS:
            try:
                self._conn.execute(f"SELECT {column} FROM {table} LIMIT 0")
            except sqlite3.OperationalError:
                try:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                    self._conn.commit()
                except sqlite3.OperationalError:
                    pass  # Column might already exist in some edge case

        self._migrate_workflow_runs_to_routine_runs()

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

        # One-shot migration: earlier code wrote MCP-connector credentials
        # under 'connector:<id>' while the MCP manager / SecurityProxy
        # looked them up under '<id>'. Drop the prefix so every credential
        # is keyed by the connector id a user actually sees. Existing bare
        # rows (channels, builtins) are untouched; collisions (same id on
        # both sides) skip the rename so we don't clobber a newer entry.
        try:
            self._conn.execute(
                """UPDATE credentials
                      SET service = SUBSTR(service, LENGTH('connector:') + 1)
                    WHERE service LIKE 'connector:%'
                      AND NOT EXISTS (
                            SELECT 1 FROM credentials c2
                             WHERE c2.user_id = credentials.user_id
                               AND c2.label   = credentials.label
                               AND c2.service = SUBSTR(credentials.service, LENGTH('connector:') + 1)
                      )"""
            )
            # Anything that WOULD collide: drop the prefixed one, the bare
            # row wins (it's newer in every case we've seen).
            self._conn.execute(
                "DELETE FROM credentials WHERE service LIKE 'connector:%'"
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

        # source_attachments / source_rules tables for SourceAttachmentService
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS source_attachments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id   TEXT NOT NULL,
                user_id     TEXT NOT NULL DEFAULT 'default' REFERENCES users(id),
                topic_path  TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                UNIQUE (source_id, user_id, topic_path)
            );
            CREATE INDEX IF NOT EXISTS idx_source_attachments_source
                ON source_attachments(source_id, user_id);
            CREATE TABLE IF NOT EXISTS source_rules (
                source_id   TEXT NOT NULL,
                user_id     TEXT NOT NULL DEFAULT 'default' REFERENCES users(id),
                rule_text   TEXT NOT NULL DEFAULT '',
                updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                PRIMARY KEY (source_id, user_id)
            );
            """
        )
        self._conn.commit()

    def _migrate_workflow_runs_to_routine_runs(self) -> None:
        """Give workflow_runs a routine `kind` and a nullable workflow_id.

        This does not fit the _MIGRATIONS list above. That list can only ADD
        columns, and this migration must also DROP a NOT NULL constraint —
        SQLite has no ALTER TABLE for that. The only way is a table rebuild:
        create the new shape, copy the rows, drop the old table, rename, and
        recreate the three indexes that went down with the old table.

        Runs on every connect, like the loop above, because re-applying
        schema.sql only happens when a check-table is missing. Idempotency
        comes from the `kind` probe: once the rebuilt table is in place the
        whole block is skipped, so opening the same database repeatedly
        neither errors nor touches the data.

        The rebuild is wrapped in one transaction. A half-migrated runs
        table — rows copied but the rename not done, or indexes missing —
        is the worst outcome here, so it is all-or-nothing.

        Foreign keys must be OFF for the swap. With them ON, dropping the
        old table would be refused or would leave dangling references from
        tables that point at it. PRAGMA foreign_keys is a no-op inside a
        transaction, so it is toggled around the transaction, not within it.
        """
        try:
            self._conn.execute("SELECT kind, routine_key FROM workflow_runs LIMIT 0")
            already_migrated = True
        except sqlite3.OperationalError:
            already_migrated = False

        if not already_migrated:
            # Column order matches the new schema.sql definition. The copy
            # lists every column explicitly: SELECT * would depend on the
            # old table's order and silently misalign if it ever differed.
            self._conn.execute("PRAGMA foreign_keys=OFF")
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.executescript(
                    """
                    CREATE TABLE workflow_runs_new (
                        id              TEXT PRIMARY KEY,
                        kind            TEXT NOT NULL DEFAULT 'workflow',
                        routine_key     TEXT,
                        workflow_id     TEXT REFERENCES workflows(id),
                        task_id         TEXT REFERENCES tasks(id),
                        user_id         TEXT NOT NULL DEFAULT 'default' REFERENCES users(id),
                        status          TEXT NOT NULL DEFAULT 'running',
                        current_step    TEXT,
                        completed_steps TEXT,
                        artifacts       TEXT,
                        error           TEXT,
                        cost            REAL NOT NULL DEFAULT 0.0,
                        budget_limit    REAL,
                        retry_count     INTEGER NOT NULL DEFAULT 0,
                        conversation    TEXT,
                        clarification   TEXT,
                        notified_at     TEXT,
                        session_id      TEXT,
                        created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                        updated_at      TEXT
                    );

                    INSERT INTO workflow_runs_new
                        (id, kind, routine_key, workflow_id, task_id, user_id,
                         status, current_step, completed_steps, artifacts, error,
                         cost, budget_limit, retry_count, conversation,
                         clarification, notified_at, session_id, created_at,
                         updated_at)
                    SELECT
                         id, 'workflow', NULL, workflow_id, task_id, user_id,
                         status, current_step, completed_steps, artifacts, error,
                         cost, budget_limit, retry_count, conversation,
                         clarification, notified_at, session_id, created_at,
                         updated_at
                      FROM workflow_runs;

                    DROP TABLE workflow_runs;
                    ALTER TABLE workflow_runs_new RENAME TO workflow_runs;

                    CREATE INDEX IF NOT EXISTS idx_workflow_runs_status
                        ON workflow_runs(status);
                    CREATE INDEX IF NOT EXISTS idx_workflow_runs_user
                        ON workflow_runs(user_id, status);
                    CREATE INDEX IF NOT EXISTS idx_workflow_runs_session_id
                        ON workflow_runs(session_id);
                    """
                )
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
            finally:
                self._conn.execute("PRAGMA foreign_keys=ON")

        # workflow_events: zero writers, zero readers. Dropped in W33.
        # Separate from the rebuild above so a database that already has the
        # new runs table still loses the dead table.
        try:
            self._conn.execute("DROP TABLE IF EXISTS workflow_events")
            self._conn.commit()
        except sqlite3.OperationalError:
            pass

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
