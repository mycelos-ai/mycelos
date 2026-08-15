"""workflow_runs carries a routine kind — one table, four writers.

Task 1 of the routine-run package. Three schema changes:

1. ``kind``        — 'workflow' | 'scheduled_task' | 'briefing' | 'source_sync'
2. ``routine_key`` — identity for the kinds that have no workflow_id
3. ``workflow_id`` — nullable, because a source sync has no workflow

Loosening NOT NULL must not loosen referential integrity: a non-null
workflow_id that names no workflow is still rejected.

The migration rebuilds the table (SQLite cannot drop NOT NULL with ALTER
TABLE), so the regression tests below follow the pattern established in
tests/test_uncertain_placement.py: build a database in the OLD shape, reopen
it with a fresh SQLiteStorage, and prove the rows survived.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mycelos.storage.database import SQLiteStorage


@pytest.fixture
def storage(tmp_path: Path) -> SQLiteStorage:
    s = SQLiteStorage(tmp_path / "routine_runs.db")
    s.initialize()
    return s


def _columns(storage: SQLiteStorage, table: str = "workflow_runs") -> dict[str, dict]:
    return {r["name"]: r for r in storage.fetchall(f"PRAGMA table_info({table})")}


def _index_names(storage: SQLiteStorage) -> set[str]:
    rows = storage.fetchall(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='workflow_runs'"
    )
    return {r["name"] for r in rows}


def _table_exists(storage: SQLiteStorage, table: str) -> bool:
    row = storage.fetchone(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return row is not None


# --- fresh database ------------------------------------------------------


def test_kind_and_routine_key_exist_on_a_fresh_database(storage) -> None:
    cols = _columns(storage)
    assert "kind" in cols
    assert "routine_key" in cols


def test_kind_defaults_to_workflow(storage) -> None:
    """The existing writer (WorkflowRunManager) does not set kind. Every row
    it writes must still classify as a workflow run."""
    storage.execute(
        "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
        ("wf1", "Test", "[]"),
    )
    storage.execute(
        "INSERT INTO workflow_runs (id, workflow_id) VALUES (?, ?)",
        ("run1", "wf1"),
    )
    row = storage.fetchone("SELECT kind FROM workflow_runs WHERE id=?", ("run1",))
    assert row["kind"] == "workflow"


def test_kind_is_not_nullable(storage) -> None:
    """A run with no kind cannot be classified — the column is NOT NULL."""
    cols = _columns(storage)
    assert cols["kind"]["notnull"] == 1


def test_routine_key_defaults_to_null(storage) -> None:
    """A workflow run is identified by workflow_id; routine_key stays empty."""
    storage.execute(
        "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
        ("wf1", "Test", "[]"),
    )
    storage.execute(
        "INSERT INTO workflow_runs (id, workflow_id) VALUES (?, ?)",
        ("run1", "wf1"),
    )
    row = storage.fetchone(
        "SELECT routine_key FROM workflow_runs WHERE id=?", ("run1",))
    assert row["routine_key"] is None


# --- nullable workflow_id ------------------------------------------------


def test_source_sync_run_inserts_without_a_workflow(storage) -> None:
    """A source sync has no workflow. Today the NOT NULL FK rejects this."""
    storage.execute(
        "INSERT INTO workflow_runs (id, workflow_id, kind, routine_key) "
        "VALUES (?, ?, ?, ?)",
        ("run-sync", None, "source_sync", "yt-summary"),
    )
    row = storage.fetchone(
        "SELECT workflow_id, kind, routine_key FROM workflow_runs WHERE id=?",
        ("run-sync",),
    )
    assert row["workflow_id"] is None
    assert row["kind"] == "source_sync"
    assert row["routine_key"] == "yt-summary"


def test_briefing_run_inserts_without_a_workflow(storage) -> None:
    storage.execute(
        "INSERT INTO workflow_runs (id, kind, routine_key) VALUES (?, ?, ?)",
        ("run-brief", "briefing", "briefing"),
    )
    row = storage.fetchone(
        "SELECT kind, routine_key FROM workflow_runs WHERE id=?", ("run-brief",))
    assert row["kind"] == "briefing"
    assert row["routine_key"] == "briefing"


def test_runs_group_per_routine_key(storage) -> None:
    """The inbox must be able to say WHICH routine failed, and group runs
    per routine — that is what routine_key is for."""
    for run_id, key in (("r1", "gmail"), ("r2", "gmail"), ("r3", "yt-summary")):
        storage.execute(
            "INSERT INTO workflow_runs (id, kind, routine_key) VALUES (?, ?, ?)",
            (run_id, "source_sync", key),
        )
    rows = storage.fetchall(
        "SELECT routine_key, COUNT(*) AS c FROM workflow_runs "
        "WHERE kind='source_sync' GROUP BY routine_key ORDER BY routine_key"
    )
    assert [(r["routine_key"], r["c"]) for r in rows] == [("gmail", 2), ("yt-summary", 1)]


# --- referential integrity survives --------------------------------------


def test_foreign_keys_are_enforced_by_the_connection(storage) -> None:
    """Guard for the test below: if PRAGMA foreign_keys were off, the FK
    assertion would pass for the wrong reason."""
    row = storage.fetchone("PRAGMA foreign_keys")
    assert row["foreign_keys"] == 1


def test_unknown_workflow_id_is_still_rejected(storage) -> None:
    """Allowing NULL must not allow a dangling reference."""
    with pytest.raises(sqlite3.IntegrityError):
        storage.execute(
            "INSERT INTO workflow_runs (id, workflow_id) VALUES (?, ?)",
            ("run-bad", "does-not-exist"),
        )


def test_unknown_workflow_id_is_rejected_after_the_rebuild(tmp_path: Path) -> None:
    """The rebuilt table must keep the REFERENCES clause. A rebuild that
    copies columns but drops the FK would silently pass every other test."""
    db_path = tmp_path / "fk-after-migration.db"
    _build_old_shape(db_path)
    reopened = SQLiteStorage(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        reopened.execute(
            "INSERT INTO workflow_runs (id, workflow_id) VALUES (?, ?)",
            ("run-bad", "does-not-exist"),
        )
    reopened.close()


def test_rebuilt_table_declares_the_foreign_key(storage) -> None:
    rows = storage.fetchall("PRAGMA foreign_key_list(workflow_runs)")
    targets = {(r["table"], r["from"], r["to"]) for r in rows}
    assert ("workflows", "workflow_id", "id") in targets


# --- migration on an existing database -----------------------------------


_OLD_WORKFLOW_RUNS = """
CREATE TABLE workflow_runs (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL REFERENCES workflows(id),
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
CREATE INDEX IF NOT EXISTS idx_workflow_runs_status ON workflow_runs(status);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_user ON workflow_runs(user_id, status);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_session_id ON workflow_runs(session_id);
CREATE TABLE IF NOT EXISTS workflow_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL,
    run_id      TEXT REFERENCES workflow_runs(id),
    step_id     TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_workflow ON workflow_events(workflow_id, created_at);
"""


def _build_old_shape(db_path: Path) -> None:
    """A database as it exists on the server today: workflow_runs with a
    NOT NULL workflow_id, no kind, no routine_key, plus workflow_events.

    Built by initializing normally and then rewriting the table back into
    its pre-migration shape with raw sqlite3, so the surrounding schema
    (users, workflows, tasks) is real.
    """
    seeded = SQLiteStorage(db_path)
    seeded.initialize()
    seeded.execute(
        "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
        ("wf-old", "Old workflow", "[]"),
    )
    seeded.close()

    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        "PRAGMA foreign_keys=OFF;\n"
        "DROP TABLE IF EXISTS workflow_runs;\n"
        "DROP TABLE IF EXISTS workflow_events;\n" + _OLD_WORKFLOW_RUNS
    )
    conn.commit()
    conn.close()


def _seed_old_rows(db_path: Path) -> None:
    """Two runs in the old shape: one plain, one carrying full state."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "INSERT INTO workflow_runs (id, workflow_id, status, created_at) "
        "VALUES (?, ?, ?, ?)",
        ("run-old-1", "wf-old", "completed", "2026-08-01T10:00:00Z"),
    )
    conn.execute(
        """INSERT INTO workflow_runs
              (id, workflow_id, status, current_step, completed_steps,
               artifacts, error, cost, budget_limit, retry_count,
               conversation, clarification, notified_at, session_id,
               created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("run-old-2", "wf-old", "failed", "step3", '["step1"]',
         '{"items": 2}', "budget exceeded", 0.25, 1.0, 3,
         '[{"role":"user"}]', "which one?", "2026-08-01T11:00:00Z",
         "sess-old", "2026-08-01T09:00:00Z", "2026-08-01T11:30:00Z"),
    )
    conn.commit()
    conn.close()


def test_migration_adds_columns_to_an_existing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "existing.db"
    _build_old_shape(db_path)
    _seed_old_rows(db_path)

    reopened = SQLiteStorage(db_path)
    cols = _columns(reopened)
    assert "kind" in cols
    assert "routine_key" in cols
    reopened.close()


def test_migration_keeps_existing_rows_intact(tmp_path: Path) -> None:
    """The rebuild copies rows. Every column must arrive unchanged, and the
    old rows must classify as workflow runs."""
    db_path = tmp_path / "existing-rows.db"
    _build_old_shape(db_path)
    _seed_old_rows(db_path)

    reopened = SQLiteStorage(db_path)
    assert reopened.fetchone(
        "SELECT COUNT(*) AS c FROM workflow_runs")["c"] == 2

    first = reopened.fetchone(
        "SELECT * FROM workflow_runs WHERE id=?", ("run-old-1",))
    assert first is not None, "the migration must not lose existing rows"
    assert first["kind"] == "workflow"
    assert first["routine_key"] is None
    assert first["workflow_id"] == "wf-old"
    assert first["status"] == "completed"
    assert first["created_at"] == "2026-08-01T10:00:00Z"

    second = reopened.fetchone(
        "SELECT * FROM workflow_runs WHERE id=?", ("run-old-2",))
    assert second["kind"] == "workflow"
    assert second["status"] == "failed"
    assert second["current_step"] == "step3"
    assert second["completed_steps"] == '["step1"]'
    assert second["artifacts"] == '{"items": 2}'
    assert second["error"] == "budget exceeded"
    assert second["cost"] == 0.25
    assert second["budget_limit"] == 1.0
    assert second["retry_count"] == 3
    assert second["conversation"] == '[{"role":"user"}]'
    assert second["clarification"] == "which one?"
    assert second["notified_at"] == "2026-08-01T11:00:00Z"
    assert second["session_id"] == "sess-old"
    assert second["updated_at"] == "2026-08-01T11:30:00Z"
    reopened.close()


def test_migration_recreates_the_three_indexes(tmp_path: Path) -> None:
    """DROP TABLE takes its indexes with it. All three must come back, or
    every status/user/session query on the runs table degrades to a scan."""
    db_path = tmp_path / "indexes.db"
    _build_old_shape(db_path)
    _seed_old_rows(db_path)

    reopened = SQLiteStorage(db_path)
    names = _index_names(reopened)
    assert "idx_workflow_runs_status" in names
    assert "idx_workflow_runs_user" in names
    assert "idx_workflow_runs_session_id" in names
    reopened.close()


def test_migrated_database_accepts_a_null_workflow_id(tmp_path: Path) -> None:
    """The point of the rebuild: after it, a source sync can record a run."""
    db_path = tmp_path / "null-after-migration.db"
    _build_old_shape(db_path)
    _seed_old_rows(db_path)

    reopened = SQLiteStorage(db_path)
    reopened.execute(
        "INSERT INTO workflow_runs (id, kind, routine_key) VALUES (?, ?, ?)",
        ("run-sync", "source_sync", "gmail"),
    )
    row = reopened.fetchone(
        "SELECT workflow_id, kind FROM workflow_runs WHERE id=?", ("run-sync",))
    assert row["workflow_id"] is None
    assert row["kind"] == "source_sync"
    reopened.close()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    """Opening the same database three times must not error and must not
    lose rows — the rebuild must recognise it has already run."""
    db_path = tmp_path / "idempotent.db"
    _build_old_shape(db_path)
    _seed_old_rows(db_path)

    for _ in range(3):
        s = SQLiteStorage(db_path)
        assert s.fetchone("SELECT COUNT(*) AS c FROM workflow_runs")["c"] == 2
        cols = _columns(s)
        assert "kind" in cols
        assert "routine_key" in cols
        s.close()


def test_reopening_a_fresh_database_is_idempotent(tmp_path: Path) -> None:
    """The already-migrated path: a database created from schema.sql must
    not be rebuilt again on every connect."""
    db_path = tmp_path / "fresh-reopen.db"
    first = SQLiteStorage(db_path)
    first.initialize()
    first.execute(
        "INSERT INTO workflows (id, name, steps) VALUES (?, ?, ?)",
        ("wf1", "Test", "[]"),
    )
    first.execute(
        "INSERT INTO workflow_runs (id, workflow_id) VALUES (?, ?)",
        ("run1", "wf1"),
    )
    first.close()

    for _ in range(3):
        s = SQLiteStorage(db_path)
        assert s.fetchone("SELECT COUNT(*) AS c FROM workflow_runs")["c"] == 1
        s.close()


# --- kind is constrained to the four legal values -------------------------
#
# A typo'd kind is the silent failure this package exists to prevent: the row
# lands in the table but every kind-filtered read misses it, so a failed sync
# looks exactly like a healthy one. The same four values must hold on a fresh
# database and on a migrated one — if the two definitions drift, a value the
# gateway accepts on one server is rejected on the next.

_VALID_KINDS = ("workflow", "scheduled_task", "briefing", "source_sync")
_INVALID_KINDS = ("source-sync", "sourcesync", "SOURCE_SYNC", "", "briefings")


def _insert_kind(storage: SQLiteStorage, run_id: str, kind: str) -> None:
    storage.execute(
        "INSERT INTO workflow_runs (id, kind, routine_key) VALUES (?, ?, ?)",
        (run_id, kind, "probe"),
    )


@pytest.mark.parametrize("kind", _VALID_KINDS)
def test_fresh_database_accepts_every_legal_kind(storage, kind: str) -> None:
    _insert_kind(storage, f"run-{kind}", kind)
    row = storage.fetchone(
        "SELECT kind FROM workflow_runs WHERE id=?", (f"run-{kind}",))
    assert row["kind"] == kind


@pytest.mark.parametrize("kind", _INVALID_KINDS)
def test_fresh_database_rejects_an_illegal_kind(storage, kind: str) -> None:
    """A typo, a wrong case and an empty string must all raise here, not
    return a row nobody can find."""
    with pytest.raises(sqlite3.IntegrityError):
        _insert_kind(storage, "run-bad", kind)


@pytest.mark.parametrize("kind", _VALID_KINDS)
def test_migrated_database_accepts_every_legal_kind(
    tmp_path: Path, kind: str
) -> None:
    db_path = tmp_path / f"kind-ok-{kind}.db"
    _build_old_shape(db_path)
    _seed_old_rows(db_path)

    reopened = SQLiteStorage(db_path)
    _insert_kind(reopened, f"run-{kind}", kind)
    row = reopened.fetchone(
        "SELECT kind FROM workflow_runs WHERE id=?", (f"run-{kind}",))
    assert row["kind"] == kind
    reopened.close()


@pytest.mark.parametrize("kind", _INVALID_KINDS)
def test_migrated_database_rejects_an_illegal_kind(
    tmp_path: Path, kind: str
) -> None:
    """The rebuilt table must carry the same CHECK as schema.sql. Without it
    a migrated database silently accepts what a fresh one refuses."""
    db_path = tmp_path / "kind-bad.db"
    _build_old_shape(db_path)
    _seed_old_rows(db_path)

    reopened = SQLiteStorage(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_kind(reopened, "run-bad", kind)
    reopened.close()


def test_migration_copies_old_rows_as_a_legal_kind(tmp_path: Path) -> None:
    """The constraint must be satisfiable by the data already on disk: every
    migrated row classifies as 'workflow'."""
    db_path = tmp_path / "kind-migrated-rows.db"
    _build_old_shape(db_path)
    _seed_old_rows(db_path)

    reopened = SQLiteStorage(db_path)
    kinds = {
        r["kind"]
        for r in reopened.fetchall("SELECT DISTINCT kind FROM workflow_runs")
    }
    assert kinds == {"workflow"}
    reopened.close()


# --- the rebuild is all-or-nothing ----------------------------------------


def test_a_crash_during_the_rebuild_leaves_the_database_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rebuild runs inside one transaction. Without it, a crash after the
    DROP commits the new table as an orphan, and every later open then fails
    with 'table workflow_runs_new already exists' — a permanently wedged
    database, which is the worst outcome in this package.

    Crash the rebuild, then assert the database is untouched and the next
    open recovers.
    """
    db_path = tmp_path / "crash-during-rebuild.db"
    _build_old_shape(db_path)
    _seed_old_rows(db_path)

    # sqlite3.Connection is immutable, so wrap the connection the storage
    # opens. The rebuild script runs statement by statement and fails at the
    # RENAME — after the new table was created and the old one dropped. That
    # is the point where only the transaction can still save the database.
    real_connect = sqlite3.connect

    class _CrashingConnection:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn

        def executescript(self, script: str):  # type: ignore[no-untyped-def]
            if "workflow_runs_new" not in script:
                return self._conn.executescript(script)
            for statement in script.split(";"):
                if not statement.strip():
                    continue
                if "RENAME TO" in statement:
                    raise sqlite3.OperationalError("injected failure")
                self._conn.execute(statement)
            return None

        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            return getattr(self._conn, name)

        def __setattr__(self, name: str, value) -> None:  # type: ignore[no-untyped-def]
            if name == "_conn":
                object.__setattr__(self, name, value)
            else:
                setattr(self._conn, name, value)

    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **kw: _CrashingConnection(real_connect(*a, **kw))
    )

    crashed = SQLiteStorage(db_path)
    with pytest.raises(sqlite3.OperationalError):
        crashed.fetchone("SELECT COUNT(*) AS c FROM workflow_runs")
    crashed.close()

    monkeypatch.undo()

    # Nothing half-done: the original table is intact, both rows are there,
    # and no orphan was left behind.
    raw = sqlite3.connect(str(db_path))
    raw.row_factory = sqlite3.Row
    tables = {
        r["name"]
        for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "workflow_runs" in tables
    assert "workflow_runs_new" not in tables, (
        "an orphan workflow_runs_new wedges every future open"
    )
    assert raw.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 2
    old_columns = {r["name"] for r in raw.execute("PRAGMA table_info(workflow_runs)")}
    assert "kind" not in old_columns, "the rebuild must not half-apply"
    raw.close()

    # The next open recovers rather than wedging.
    reopened = SQLiteStorage(db_path)
    assert reopened.fetchone("SELECT COUNT(*) AS c FROM workflow_runs")["c"] == 2
    cols = _columns(reopened)
    assert "kind" in cols
    assert "routine_key" in cols
    assert not _table_exists(reopened, "workflow_runs_new")
    reopened.close()


# --- the dead table ------------------------------------------------------


def test_workflow_events_is_gone_on_a_fresh_database(storage) -> None:
    """Zero writers, zero readers. Carrying it forward would keep implying
    a durable step log that does not exist."""
    assert not _table_exists(storage, "workflow_events")


def test_workflow_events_is_dropped_from_an_existing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "drop-events.db"
    _build_old_shape(db_path)
    _seed_old_rows(db_path)

    seeded = sqlite3.connect(str(db_path))
    seeded.execute(
        "INSERT INTO workflow_events (workflow_id, run_id, step_id, event_type) "
        "VALUES (?, ?, ?, ?)",
        ("wf-old", "run-old-1", "step1", "started"),
    )
    seeded.commit()
    seeded.close()

    reopened = SQLiteStorage(db_path)
    assert not _table_exists(reopened, "workflow_events")
    # The runs it referenced are untouched.
    assert reopened.fetchone("SELECT COUNT(*) AS c FROM workflow_runs")["c"] == 2
    reopened.close()


# =========================================================================
# Task 3 — the recorder: briefing and source syncs write runs
# =========================================================================
#
# Three of the four routine kinds wrote nothing durable. A dead sync looked
# exactly like a quiet one. `RunRecorder` gives the two non-workflow kinds
# the same start/finish/fail discipline WorkflowRunManager gives workflows,
# without any of its pause/resume/budget machinery.
#
# Two rules shape every test below:
#
# 1. **No connector text in `error`.** The recorder authors a fixed cause per
#    failure mode. An ingest exception is the most likely place in the whole
#    system to carry the content that failed to parse, so the exception's own
#    message never becomes the stored cause.
# 2. **Recording must not break the job it observes.** A run row that cannot
#    be written is logged, not raised. The user's data still arrives and the
#    next source still runs. This is deliberately the opposite of the
#    workflow run-start decision, where refusing costs one execution and
#    loses nothing.

import os
import tempfile
from datetime import datetime


@pytest.fixture
def app():
    """A real App on a temporary directory — same fixture the ingest and
    briefing suites use, so the recorder is exercised against real storage."""
    from mycelos.app import App

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-run-recorder"
        a = App(Path(tmp))
        a.initialize()
        yield a


# --- doubles -------------------------------------------------------------


class _FakeMcp:
    """Serves one fixed payload for every tool call."""

    def __init__(self, payload) -> None:
        self.calls: list = []
        self._payload = payload

    def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return self._payload


# A failure message built the way a real parse error is built: from the data
# that failed. Every one of these must be absent from the stored cause.
LEAKY_NOTE_TITLE = "Quartalsabschluss Steuerberatung"
LEAKY_ADDRESS = "anna.mueller@example.com"
LEAKY_STREET = "Hauptstrasse 14, 80331 Muenchen"
LEAKY_IBAN = "DE89 3704 0044 0532 0130 00"
LEAKY_MESSAGE = (
    f"could not parse note '{LEAKY_NOTE_TITLE}' from {LEAKY_ADDRESS} "
    f"living at {LEAKY_STREET} with account {LEAKY_IBAN}"
)
LEAKY_FRAGMENTS = (
    LEAKY_NOTE_TITLE,
    "Quartalsabschluss",
    "Steuerberatung",
    LEAKY_ADDRESS,
    "anna.mueller",
    "Hauptstrasse",
    "Muenchen",
    "80331",
    LEAKY_IBAN,
    "DE89",
    "0532",
)


class _LeakyMcp:
    """Raises an exception whose message carries personal data — the exact
    shape a real parse failure has."""

    def __init__(self) -> None:
        self.calls: list = []

    def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        raise ValueError(LEAKY_MESSAGE)


def _yt_page(item_id: str = "1:dQw4w9WgXcQ", title: str = "Retrieval 101") -> dict:
    return {
        "items": [
            {
                "id": item_id,
                "source": "yt-summary",
                "type": "note",
                "title": title,
                "description": "A talk about retrieval.",
                "resource": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "timestamp": "2026-08-13T09:12:00+00:00",
                "created": "2026-08-01T07:00:00+00:00",
                "tags": ["ai"],
                "kind": "youtube",
                "content": "## Summary\n\nThe talk explains RRF.",
            }
        ],
        "next_cursor": "",
        "has_more": False,
    }


def _register(app, name: str, status: str = "active") -> None:
    app.connector_registry.register(
        name, name, "mcp", [f"{name}.read"], description="test",
    )
    if status != "active":
        app.connector_registry.set_status(name, status)


def _runs(app, kind: str | None = None) -> list[dict]:
    sql = "SELECT * FROM workflow_runs"
    params: tuple = ()
    if kind:
        sql += " WHERE kind = ?"
        params = (kind,)
    sql += " ORDER BY routine_key, created_at"
    return [dict(r) for r in app.storage.fetchall(sql, params)]


def _artifacts(row: dict) -> dict:
    import json as _json

    return _json.loads(row["artifacts"] or "{}")


# --- RunRecorder in isolation --------------------------------------------


def test_recorder_start_writes_a_running_row(storage) -> None:
    from mycelos.scheduler.run_recorder import RunRecorder

    run_id = RunRecorder(storage).start("source_sync", "yt-summary", "default")
    row = storage.fetchone("SELECT * FROM workflow_runs WHERE id=?", (run_id,))
    assert row["kind"] == "source_sync"
    assert row["routine_key"] == "yt-summary"
    assert row["status"] == "running"
    assert row["workflow_id"] is None
    assert row["user_id"] == "default"


def test_recorder_finish_marks_completed_with_counts(storage) -> None:
    import json as _json

    from mycelos.scheduler.run_recorder import RunRecorder

    recorder = RunRecorder(storage)
    run_id = recorder.start("source_sync", "gmail", "default")
    recorder.finish(run_id, {"created": 3, "skipped_existing": 1})

    row = storage.fetchone("SELECT * FROM workflow_runs WHERE id=?", (run_id,))
    assert row["status"] == "completed"
    assert row["error"] is None
    assert _json.loads(row["artifacts"]) == {"created": 3, "skipped_existing": 1}


def test_recorder_fail_marks_failed_with_the_cause(storage) -> None:
    from mycelos.scheduler.run_recorder import CAUSES, RunRecorder

    recorder = RunRecorder(storage)
    run_id = recorder.start("source_sync", "gmail", "default")
    recorder.fail(run_id, CAUSES["source_rejected"])

    row = storage.fetchone("SELECT * FROM workflow_runs WHERE id=?", (run_id,))
    assert row["status"] == "failed"
    assert row["error"] == CAUSES["source_rejected"]


def test_recorder_rejects_an_illegal_kind(storage) -> None:
    """The CHECK is the point: a typo'd kind writes a row every kind-filtered
    read misses. Task 1 made that raise; the recorder must not swallow it."""
    from mycelos.scheduler.run_recorder import RunRecorder

    with pytest.raises(sqlite3.IntegrityError):
        RunRecorder(storage).start("source-sync", "gmail", "default")


def test_recorder_refuses_a_cause_it_did_not_author(storage) -> None:
    """The recorder is an allowlist, not a sanitizer.

    sanitize_cause_text documents its own limit: it cannot classify free
    prose. A street name and a company name carried as ordinary unquoted
    words survive it — this exact message proves that, since 'Hauptstrasse'
    and 'Steuerberatung' come through the sanitizer intact. So the recorder
    does not clean; it refuses. A cause is either one this package wrote, or
    it is not stored.
    """
    from mycelos.scheduler.run_recorder import CAUSES, RunRecorder

    recorder = RunRecorder(storage)
    run_id = recorder.start("source_sync", "gmail", "default")
    recorder.fail(run_id, LEAKY_MESSAGE)

    row = storage.fetchone("SELECT error FROM workflow_runs WHERE id=?", (run_id,))
    stored = row["error"] or ""
    assert stored == CAUSES["unrecognised"]
    for fragment in LEAKY_FRAGMENTS:
        assert fragment not in stored, f"{fragment!r} leaked into the cause"


def test_the_sanitizer_alone_would_not_have_caught_this(storage) -> None:
    """Pins the reason the allowlist exists. If a future change makes the
    sanitizer strong enough to strip this message, this test fails and the
    allowlist can be reconsidered — deliberately, not by accident."""
    from mycelos.workflows.run_cause import sanitize_cause_text

    sanitized = sanitize_cause_text(LEAKY_MESSAGE)
    assert "Hauptstrasse" in sanitized, (
        "the sanitizer still cannot classify free prose; the allowlist in "
        "RunRecorder._safe_cause is what makes the guarantee"
    )


def test_every_fixed_cause_is_storable(storage) -> None:
    """A cause this package wrote must survive its own allowlist. A fixed
    string that the sanitizer mangles would silently become the generic one
    and the reader would lose the failure mode."""
    from mycelos.scheduler.run_recorder import CAUSES, RunRecorder

    recorder = RunRecorder(storage)
    for index, (name, cause) in enumerate(CAUSES.items()):
        run_id = recorder.start("source_sync", f"probe-{index}", "default")
        recorder.fail(run_id, cause)
        row = storage.fetchone(
            "SELECT error FROM workflow_runs WHERE id=?", (run_id,))
        assert row["error"] == cause, f"the fixed cause '{name}' was not stored"


def test_recorder_stores_only_numbers_and_names_in_artifacts(storage) -> None:
    """Counts are numbers. A count dict that smuggles a title, a body or an
    address must not reach the column."""
    from mycelos.scheduler.run_recorder import RunRecorder

    recorder = RunRecorder(storage)
    run_id = recorder.start("source_sync", "yt-summary", "default")
    recorder.finish(
        run_id,
        {
            "created": 2,
            "source": "yt-summary",
            "title": LEAKY_NOTE_TITLE,
            "body": LEAKY_MESSAGE,
            "sender": LEAKY_ADDRESS,
        },
    )

    row = storage.fetchone("SELECT artifacts FROM workflow_runs WHERE id=?", (run_id,))
    stored = row["artifacts"] or "{}"
    for fragment in LEAKY_FRAGMENTS:
        assert fragment not in stored, f"{fragment!r} leaked into artifacts"
    import json as _json

    parsed = _json.loads(stored)
    assert parsed["created"] == 2
    assert parsed.get("source") == "yt-summary"


# --- source syncs write runs ---------------------------------------------


def test_successful_yt_summary_sync_writes_one_completed_run(app) -> None:
    from mycelos.scheduler.jobs import auto_ingest_check

    _register(app, "yt-summary")
    app.memory.set("default", "system", "auto_ingest_enabled", True)
    app._mcp_manager = _FakeMcp(_yt_page())

    auto_ingest_check(app)

    rows = _runs(app, "source_sync")
    assert len(rows) == 1
    row = rows[0]
    assert row["routine_key"] == "yt-summary"
    assert row["status"] == "completed"
    assert row["workflow_id"] is None
    assert row["error"] is None
    counts = _artifacts(row)
    assert counts["created"] == 1
    # The connector's real counts, not a two-key subset invented by the job.
    # yt-summary reports no `skipped_existing` at all, so the row must not
    # claim one: an absent count is absent, never a fabricated zero.
    assert counts["fetched"] == 1
    assert counts["skipped_unchanged"] == 0
    assert "skipped_existing" not in counts


def test_sync_that_returns_an_error_key_writes_a_failed_run(app) -> None:
    """One of the two failure shapes: ingest_fn returns a dict carrying an
    'error' key rather than raising."""
    from mycelos.scheduler.jobs import auto_ingest_check

    _register(app, "yt-summary")
    app.memory.set("default", "system", "auto_ingest_enabled", True)
    app._mcp_manager = _FakeMcp({"error": "upstream refused"})

    auto_ingest_check(app)

    rows = _runs(app, "source_sync")
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["error"]


def test_sync_that_raises_writes_a_failed_run(app) -> None:
    """The other failure shape: ingest_fn raises."""
    from mycelos.scheduler.jobs import auto_ingest_check

    _register(app, "yt-summary")
    app.memory.set("default", "system", "auto_ingest_enabled", True)
    app._mcp_manager = _LeakyMcp()

    auto_ingest_check(app)  # must not raise

    rows = _runs(app, "source_sync")
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["error"]


def test_a_failing_sync_stores_no_personal_data(app) -> None:
    """The single most important assertion in this task. The exception
    message carries a note title, an address, a German street address and an
    IBAN. None of it may reach the column."""
    from mycelos.scheduler.jobs import auto_ingest_check

    _register(app, "yt-summary")
    app.memory.set("default", "system", "auto_ingest_enabled", True)
    app._mcp_manager = _LeakyMcp()

    auto_ingest_check(app)

    rows = _runs(app, "source_sync")
    assert len(rows) == 1
    stored = (rows[0]["error"] or "") + (rows[0]["artifacts"] or "")
    for fragment in LEAKY_FRAGMENTS:
        assert fragment not in stored, f"{fragment!r} leaked into the run row"


def test_a_returned_error_string_does_not_reach_the_column(app) -> None:
    """A connector's own error string is untrusted for the same reason an
    exception message is: it is built from the data that failed."""
    from mycelos.scheduler.jobs import auto_ingest_check

    _register(app, "yt-summary")
    app.memory.set("default", "system", "auto_ingest_enabled", True)
    app._mcp_manager = _FakeMcp({"error": LEAKY_MESSAGE})

    auto_ingest_check(app)

    rows = _runs(app, "source_sync")
    stored = rows[0]["error"] or ""
    for fragment in LEAKY_FRAGMENTS:
        assert fragment not in stored, f"{fragment!r} leaked into the cause"


def test_two_sources_in_one_tick_write_two_runs(app) -> None:
    """auto_ingest_check loops INGEST_SOURCES. Each source is its own run."""
    from mycelos.scheduler.jobs import auto_ingest_check

    _register(app, "gmail")
    _register(app, "yt-summary")
    app.memory.set("default", "system", "auto_ingest_enabled", True)
    app._mcp_manager = _FakeMcp(_yt_page())

    auto_ingest_check(app)

    rows = _runs(app, "source_sync")
    assert {r["routine_key"] for r in rows} == {"gmail", "yt-summary"}
    assert len(rows) == 2


def test_a_skipped_source_writes_no_run(app) -> None:
    """A skip is not a run. An inactive connector never attempted anything,
    so a row would claim a run that never happened."""
    from mycelos.scheduler.jobs import auto_ingest_check

    _register(app, "yt-summary", status="inactive")
    app.memory.set("default", "system", "auto_ingest_enabled", True)
    app._mcp_manager = _FakeMcp(_yt_page())

    result = auto_ingest_check(app)

    assert "yt-summary" in result["skipped"]
    assert _runs(app, "source_sync") == []


def test_an_unregistered_source_writes_no_run(app) -> None:
    from mycelos.scheduler.jobs import auto_ingest_check

    app.memory.set("default", "system", "auto_ingest_enabled", True)
    app._mcp_manager = _FakeMcp(_yt_page())

    auto_ingest_check(app)

    assert _runs(app, "source_sync") == []


def test_auto_ingest_disabled_writes_no_run(app) -> None:
    from mycelos.scheduler.jobs import auto_ingest_check

    _register(app, "yt-summary")
    app._mcp_manager = _FakeMcp(_yt_page())

    auto_ingest_check(app)

    assert _runs(app) == []


def test_the_audit_event_still_happens(app) -> None:
    """The run row is additional, not a replacement."""
    import json as _json

    from mycelos.scheduler.jobs import auto_ingest_check

    _register(app, "yt-summary")
    app.memory.set("default", "system", "auto_ingest_enabled", True)
    app._mcp_manager = _FakeMcp(_yt_page())

    auto_ingest_check(app)

    row = app.storage.fetchone(
        "SELECT details FROM audit_events "
        "WHERE event_type='knowledge.auto_ingest.run'"
    )
    assert row is not None
    assert "yt-summary" in _json.loads(row["details"])["ran"]


def test_run_rows_carry_only_numbers_and_source_names(app) -> None:
    """The connector's whole result now reaches the recorder, so the
    allowlist is the only thing standing between a connector field and the
    column. The note title is the payload to watch: it travels in the same
    dict as the counts."""
    from mycelos.scheduler.jobs import auto_ingest_check
    from mycelos.scheduler.run_recorder import _ALLOWED_COUNT_KEYS

    _register(app, "yt-summary")
    app.memory.set("default", "system", "auto_ingest_enabled", True)
    app._mcp_manager = _FakeMcp(_yt_page(title="Quartalsabschluss Steuerberatung"))

    auto_ingest_check(app)

    rows = _runs(app, "source_sync")
    counts = _artifacts(rows[0])
    assert set(counts) <= _ALLOWED_COUNT_KEYS | {"source", "truncated"}
    for key, value in counts.items():
        if key == "source":
            assert value == "yt-summary"
        elif key == "truncated":
            assert isinstance(value, bool)
        else:
            assert isinstance(value, (int, float))
    stored = rows[0]["artifacts"] or ""
    for fragment in LEAKY_FRAGMENTS:
        assert fragment not in stored, f"{fragment!r} leaked into artifacts"


# --- SF-3: the audit payload carries what the column carries -------------
#
# `auto_ingest_check` returns a summary and hands the same dict to
# `app.audit.log`, which json.dumps it into `audit_events.details` with no
# filter of its own. Rule 1 names audit payloads beside logs and error
# columns, so the summary holds to the same discipline as the row: fixed
# causes in `errors`, allowlisted counts in `ran`.


def _audit_details(app) -> str:
    """The raw `knowledge.auto_ingest.run` payload, as stored."""
    row = app.storage.fetchone(
        "SELECT details FROM audit_events "
        "WHERE event_type='knowledge.auto_ingest.run'"
    )
    assert row is not None, "the tick must still audit"
    return row["details"] or ""


def test_the_audit_payload_carries_no_exception_text(app, monkeypatch) -> None:
    """A raised connector error used to reach `details` byte for byte.

    The exception message is built from the data that failed to parse, which
    is exactly what the run row refuses. The audit event sits four lines from
    the code that gets it right.
    """
    def _raises(app_, user_id="default"):
        raise ValueError(LEAKY_MESSAGE)

    _run_one_source(app, monkeypatch, "gmail", _raises)

    details = _audit_details(app)
    for fragment in LEAKY_FRAGMENTS:
        assert fragment not in details, f"{fragment!r} leaked into the audit"


def test_the_audit_payload_carries_no_connector_error_string(
    app, monkeypatch
) -> None:
    """The returned-error branch. Same rule, the other failure mode."""
    _run_one_source(
        app, monkeypatch, "gmail",
        lambda app_, user_id="default": {"error": LEAKY_MESSAGE},
    )

    details = _audit_details(app)
    for fragment in LEAKY_FRAGMENTS:
        assert fragment not in details, f"{fragment!r} leaked into the audit"


def test_the_audit_payload_names_the_failed_source_and_a_fixed_cause(
    app, monkeypatch
) -> None:
    """Dropping the text must not drop the signal.

    A payload that said only "something failed" would be safe and useless.
    The source name and the fixed cause are both kept.
    """
    import json as _json

    from mycelos.scheduler.run_recorder import CAUSES

    def _raises(app_, user_id="default"):
        raise ConnectionError(LEAKY_MESSAGE)

    _run_one_source(app, monkeypatch, "gmail", _raises)

    errors = _json.loads(_audit_details(app))["errors"]
    assert set(errors) == {"gmail"}
    assert errors["gmail"] == CAUSES["source_unreachable"]


def test_the_audit_payload_counts_go_through_the_allowlist(
    app, monkeypatch
) -> None:
    """`ran` used to carry the connector's whole return value.

    Task 3 widened it from a hardcoded two-key dict on the reasoning that the
    recorder's allowlist filters the row. That is right for the row and wrong
    for the audit payload, which has no allowlist of its own.
    """
    import json as _json

    from mycelos.scheduler.run_recorder import _ALLOWED_COUNT_KEYS

    _run_one_source(
        app, monkeypatch, "gmail",
        lambda app_, user_id="default": {
            "created": 2,
            "subject": LEAKY_NOTE_TITLE,
            "sender": LEAKY_ADDRESS,
            "body": LEAKY_STREET,
        },
    )

    details = _audit_details(app)
    for fragment in LEAKY_FRAGMENTS:
        assert fragment not in details, f"{fragment!r} leaked into the audit"

    counts = _json.loads(details)["ran"]["gmail"]
    assert set(counts) <= _ALLOWED_COUNT_KEYS | {"source", "truncated"}
    assert counts["created"] == 2, "the real counts must survive"


def test_the_audit_payload_matches_the_row_it_describes(app, monkeypatch) -> None:
    """One discipline, two surfaces. The row and the audit agree."""
    import json as _json

    _run_one_source(
        app, monkeypatch, "gmail",
        lambda app_, user_id="default": {
            "created": 1, "updated": 3, "subject": LEAKY_NOTE_TITLE,
        },
    )

    audited = _json.loads(_audit_details(app))["ran"]["gmail"]
    stored = _artifacts(_runs(app, "source_sync")[0])
    # The row additionally repeats its own routine_key as `source`.
    assert audited == {k: v for k, v in stored.items() if k != "source"}


# --- the real counts reach the row ---------------------------------------
#
# The job used to build its own two-key dict before the recorder's allowlist
# was consulted, so five of yt-summary's seven counts never reached the row
# and `skipped_existing` was stated as a zero the connector never reported.
# The allowlist is now the single filter.


class _CountingIngest:
    """Stands in for a connector, returning a fixed result dict."""

    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls = 0

    def __call__(self, app, user_id="default"):
        self.calls += 1
        return self.result


def _run_one_source(app, monkeypatch, name: str, ingest_fn) -> list[dict]:
    """Drive auto_ingest_check with exactly one registered source."""
    import mycelos.knowledge.connector_ingest as ci
    from mycelos.scheduler.jobs import auto_ingest_check

    _register(app, name)
    app.memory.set("default", "system", "auto_ingest_enabled", True)
    monkeypatch.setattr(ci, "INGEST_SOURCES", {name: ingest_fn})

    auto_ingest_check(app)
    return _runs(app, "source_sync")


def test_every_real_count_reaches_the_row(app, monkeypatch) -> None:
    """The full yt-summary count set, verbatim from connector_ingest.

    All seven survive. Before the fix this row read
    `{"created": 0, "skipped_existing": 0}` — it reported nothing happened
    for a sync that updated four notes and dropped one malformed item.
    """
    real = {
        "fetched": 17,
        "created": 0,
        "updated": 4,
        "skipped_unchanged": 12,
        "skipped_malformed": 1,
        "failed_updates": 0,
        "truncated": True,
    }
    rows = _run_one_source(app, monkeypatch, "yt-summary", _CountingIngest(real))

    assert len(rows) == 1
    counts = _artifacts(rows[0])
    assert rows[0]["status"] == "completed"
    for key, value in real.items():
        assert counts[key] == value, f"the count '{key}' was dropped"
    assert counts["source"] == "yt-summary"


def test_a_sync_that_only_updated_records_the_update_count(app, monkeypatch) -> None:
    """The regression in its smallest form: work that is not a create.

    A sync that created nothing but updated four notes must not read as a
    sync that did nothing.
    """
    rows = _run_one_source(
        app, monkeypatch, "yt-summary",
        _CountingIngest({"fetched": 4, "created": 0, "updated": 4}),
    )

    counts = _artifacts(rows[0])
    assert counts["updated"] == 4
    assert counts["created"] == 0
    assert counts["fetched"] == 4


def test_an_absent_count_is_absent_not_zero(app, monkeypatch) -> None:
    """yt-summary never reports `skipped_existing`. The row used to state it
    as 0 anyway — a number the connector never produced, presented as fact."""
    rows = _run_one_source(
        app, monkeypatch, "yt-summary",
        _CountingIngest({"fetched": 2, "created": 2}),
    )

    counts = _artifacts(rows[0])
    assert "skipped_existing" not in counts
    assert "updated" not in counts


def test_gmails_counts_all_reach_the_row(app, monkeypatch) -> None:
    """The other real connector's full return shape."""
    real = {"fetched": 9, "created": 3, "skipped_existing": 6}
    rows = _run_one_source(app, monkeypatch, "gmail", _CountingIngest(real))

    counts = _artifacts(rows[0])
    for key, value in real.items():
        assert counts[key] == value, f"the count '{key}' was dropped"


def test_truncated_marks_a_sync_that_left_a_backlog(app, monkeypatch) -> None:
    """`truncated` is Package 1's data-loss signal: the sync hit
    MAX_SYNC_PAGES with pages still pending. A row carrying only counts would
    state that as a complete sync."""
    rows = _run_one_source(
        app, monkeypatch, "yt-summary",
        _CountingIngest({"fetched": 5000, "created": 5000, "truncated": True}),
    )

    assert _artifacts(rows[0])["truncated"] is True


def test_a_connector_field_that_is_not_a_count_is_still_dropped(
    app, monkeypatch,
) -> None:
    """Passing the whole result through makes the allowlist load-bearing: the
    connector's dict now arrives intact, so anything it carries beyond a count
    must be dropped here."""
    rows = _run_one_source(
        app, monkeypatch, "yt-summary",
        _CountingIngest({
            "created": 1,
            "title": LEAKY_NOTE_TITLE,
            "last_item": LEAKY_MESSAGE,
            "sender": LEAKY_ADDRESS,
            "cursor": "eyJvZmZzZXQiOjQyfQ==",
            "next_cursor": LEAKY_STREET,
        }),
    )

    stored = rows[0]["artifacts"] or ""
    for fragment in LEAKY_FRAGMENTS:
        assert fragment not in stored, f"{fragment!r} leaked into artifacts"
    counts = _artifacts(rows[0])
    assert counts["created"] == 1
    assert set(counts) == {"created", "source"}


# --- a failure mode produces its own cause -------------------------------
#
# The gap the reviewer's M1 exposed: nothing asserted that a *specific*
# failure produces its *specific* cause end to end through auto_ingest_check.
# Passing `str(e)` straight into fail() survived all 67 tests, because the
# allowlist caught the raw text and quietly substituted the generic cause.
# The allowlist is a backstop; these tests pin the mapping itself.


class _RaisingIngest:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def __call__(self, app, user_id="default"):
        raise self.exc


def _cause_for(app, monkeypatch, ingest_fn) -> str:
    rows = _run_one_source(app, monkeypatch, "yt-summary", ingest_fn)
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    return rows[0]["error"] or ""


@pytest.mark.parametrize(
    "exc,expected",
    [
        (ValueError(LEAKY_MESSAGE), "response_unreadable"),
        (TypeError(LEAKY_MESSAGE), "response_unreadable"),
        (KeyError("id"), "response_unreadable"),
        (AttributeError(LEAKY_MESSAGE), "response_unreadable"),
        (ConnectionError(LEAKY_MESSAGE), "source_unreachable"),
        (TimeoutError(LEAKY_MESSAGE), "source_unreachable"),
        (OSError(LEAKY_MESSAGE), "source_unreachable"),
        (RuntimeError(LEAKY_MESSAGE), "source_failed"),
    ],
)
def test_a_raised_failure_stores_its_own_cause(
    app, monkeypatch, exc, expected,
) -> None:
    """Each exception type maps to one fixed cause, end to end through the
    job. Asserting equality with the exact string is the point: a mutation
    that degrades every cause to the generic one now fails here."""
    from mycelos.scheduler.run_recorder import CAUSES

    stored = _cause_for(app, monkeypatch, _RaisingIngest(exc))
    assert stored == CAUSES[expected]


def test_an_unclassified_exception_does_not_claim_an_auth_problem(
    app, monkeypatch,
) -> None:
    """The fallback used to be `source_rejected`, which tells the reader to
    re-authorise. An exception type we do not recognise is no evidence of an
    authorisation failure."""
    from mycelos.scheduler.run_recorder import CAUSES

    stored = _cause_for(app, monkeypatch, _RaisingIngest(RuntimeError("boom")))
    assert stored != CAUSES["source_rejected"]
    assert "authorised" not in stored


# The six failures `mcp_manager.call_tool` returns rather than raises. This is
# the branch that actually fires in production: the manager catches broadly
# and returns {"error": ...} for every transport failure. All six used to
# store "check that the connector is still authorised", and exactly one of
# them is an authorisation problem.
_RETURNED_ERRORS = [
    ("connector process died", "MCP tool call failed: [Errno 32] Broken pipe"),
    ("network outage", "MCP tool call failed: [Errno 61] Connection refused"),
    ("token expired", "MCP tool call failed: 401 Unauthorized"),
    ("stale session", "MCP tool 'yt-summary.export_since' not found"),
    ("proxy misconfigured",
     "Remote MCP tool requires proxy_client (not configured)"),
    ("malformed response",
     "MCP tool call failed: Expecting value: line 1 column 1 (char 0)"),
]


@pytest.mark.parametrize(
    "label,error_text", _RETURNED_ERRORS, ids=[e[0] for e in _RETURNED_ERRORS],
)
def test_a_returned_error_stores_the_neutral_cause(
    app, monkeypatch, label, error_text,
) -> None:
    """One cause for all six, and it names no remedy.

    Not because the six are the same failure, but because this branch has no
    evidence to tell them apart: the only signal is the connector's own error
    string, which is the text this column exists to keep out. So the cause
    states what is known and sends the reader to the log — instead of sending
    them to re-run an OAuth dance that fixes one case in six.
    """
    from mycelos.scheduler.run_recorder import CAUSES

    stored = _cause_for(
        app, monkeypatch, _CountingIngest({"error": error_text}))
    assert stored == CAUSES["source_failed"]
    assert "authorised" not in stored


def test_no_returned_error_text_reaches_the_column(app, monkeypatch) -> None:
    """The six real error strings carry paths, errnos and tool names. None of
    it is stored — the cause is fixed, not derived."""
    import mycelos.knowledge.connector_ingest as ci
    from mycelos.scheduler.jobs import auto_ingest_check
    from mycelos.scheduler.run_recorder import CAUSES

    _register(app, "yt-summary")
    app.memory.set("default", "system", "auto_ingest_enabled", True)

    for _label, error_text in _RETURNED_ERRORS:
        monkeypatch.setattr(ci, "INGEST_SOURCES", {
            "yt-summary": _CountingIngest({"error": error_text})})
        auto_ingest_check(app)

    rows = _runs(app, "source_sync")
    assert len(rows) == len(_RETURNED_ERRORS)
    for row in rows:
        stored = row["error"] or ""
        assert stored == CAUSES["source_failed"]
        for token in ("Errno", "401", "Broken pipe", "export_since",
                      "proxy_client", "Expecting value"):
            assert token not in stored


def test_the_briefing_failure_modes_store_their_own_causes(app) -> None:
    """The briefing's two failure modes are distinguishable and must not
    collapse into one another: built-but-undelivered is not the same as
    never-built."""
    from mycelos.scheduler.jobs import briefing_tick
    from mycelos.scheduler.run_recorder import CAUSES

    app._llm = _FakeBroker()
    app.memory.set("default", "system", "briefing_enabled", True)

    briefing_tick(app, now=datetime(2026, 6, 11, 9, 0),
                  reminder_service=_FakeDelivery(channels=["chat"]))
    rows = _runs(app, "briefing")
    assert rows[0]["error"] == CAUSES["briefing_undeliverable"]

    class _Exploding:
        def _default_channels(self):
            raise RuntimeError(LEAKY_MESSAGE)

    app.memory.set("default", "system", "briefing_last_sent", None)
    briefing_tick(app, now=datetime(2026, 6, 12, 9, 0),
                  reminder_service=_Exploding())
    rows = _runs(app, "briefing")
    assert len(rows) == 2
    assert any(r["error"] == CAUSES["briefing_failed"] for r in rows)


# --- an interrupt still closes the row -----------------------------------
#
# `except Exception` let KeyboardInterrupt, SystemExit and GeneratorExit
# leave a permanent 'running' row — the one state the package's invariant
# forbids. This is not hypothetical: a redeploy is the normal way the gateway
# process ends, and the hourly tick has a real chance of landing inside a
# sync. Both sites now catch BaseException, record, and re-raise.

_INTERRUPTS = [KeyboardInterrupt, SystemExit, GeneratorExit]


@pytest.mark.parametrize("exc_type", _INTERRUPTS, ids=lambda t: t.__name__)
def test_an_interrupt_during_a_sync_leaves_no_running_row(
    app, monkeypatch, exc_type,
) -> None:
    import mycelos.knowledge.connector_ingest as ci
    from mycelos.scheduler.jobs import auto_ingest_check

    _register(app, "yt-summary")
    app.memory.set("default", "system", "auto_ingest_enabled", True)
    monkeypatch.setattr(
        ci, "INGEST_SOURCES", {"yt-summary": _RaisingIngest(exc_type())})

    with pytest.raises(exc_type):
        auto_ingest_check(app)

    rows = _runs(app, "source_sync")
    assert len(rows) == 1
    assert rows[0]["status"] == "failed", (
        "an interrupt must not leave the row 'running' forever"
    )
    assert rows[0]["error"]


@pytest.mark.parametrize("exc_type", _INTERRUPTS, ids=lambda t: t.__name__)
def test_an_interrupt_during_a_sync_still_interrupts(
    app, monkeypatch, exc_type,
) -> None:
    """Recording must not swallow the interrupt. The row is closed, then the
    exception is re-raised, and the loop does not go on to the next source."""
    import mycelos.knowledge.connector_ingest as ci
    from mycelos.scheduler.jobs import auto_ingest_check

    _register(app, "gmail")
    _register(app, "yt-summary")
    app.memory.set("default", "system", "auto_ingest_enabled", True)
    second = _CountingIngest({"created": 1})
    monkeypatch.setattr(ci, "INGEST_SOURCES", {
        "gmail": _RaisingIngest(exc_type()),
        "yt-summary": second,
    })

    with pytest.raises(exc_type):
        auto_ingest_check(app)

    assert second.calls == 0, "the loop must stop at an interrupt"
    assert len(_runs(app, "source_sync")) == 1


@pytest.mark.parametrize("exc_type", _INTERRUPTS, ids=lambda t: t.__name__)
def test_an_interrupt_during_the_briefing_leaves_no_running_row(
    app, exc_type,
) -> None:
    from mycelos.scheduler.jobs import briefing_tick

    class _Interrupting:
        def _default_channels(self):
            raise exc_type()

    app._llm = _FakeBroker()
    app.memory.set("default", "system", "briefing_enabled", True)

    with pytest.raises(exc_type):
        briefing_tick(app, now=datetime(2026, 6, 11, 9, 0),
                      reminder_service=_Interrupting())

    rows = _runs(app, "briefing")
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["error"]


def test_an_ordinary_exception_is_still_swallowed_by_the_sync(
    app, monkeypatch,
) -> None:
    """The BaseException widening must not change the ordinary case: a
    RuntimeError in one connector is recorded and the next source still
    runs."""
    import mycelos.knowledge.connector_ingest as ci
    from mycelos.scheduler.jobs import auto_ingest_check

    _register(app, "gmail")
    _register(app, "yt-summary")
    app.memory.set("default", "system", "auto_ingest_enabled", True)
    second = _CountingIngest({"created": 1})
    monkeypatch.setattr(ci, "INGEST_SOURCES", {
        "gmail": _RaisingIngest(RuntimeError("boom")),
        "yt-summary": second,
    })

    result = auto_ingest_check(app)  # must not raise

    assert second.calls == 1, "one connector's failure must not stop the loop"
    assert set(result["errors"]) == {"gmail"}
    rows = _runs(app, "source_sync")
    assert {r["routine_key"]: r["status"] for r in rows} == {
        "gmail": "failed", "yt-summary": "completed",
    }


def test_an_ordinary_exception_in_the_briefing_is_still_swallowed(app) -> None:
    """briefing_tick must never raise on an ordinary error — the scheduler
    loop stays alive."""
    from mycelos.scheduler.jobs import briefing_tick

    class _Exploding:
        def _default_channels(self):
            raise RuntimeError("boom")

    app._llm = _FakeBroker()
    app.memory.set("default", "system", "briefing_enabled", True)

    result = briefing_tick(app, now=datetime(2026, 6, 11, 9, 0),
                           reminder_service=_Exploding())

    assert result["sent"] is False
    assert _runs(app, "briefing")[0]["status"] == "failed"


# --- recording must not break the job it observes ------------------------


class _BrokenRecorder:
    """Every recorder call fails. The sync must still complete."""

    def __init__(self, storage) -> None:
        self.starts = 0

    def start(self, kind, routine_key, user_id):
        self.starts += 1
        raise RuntimeError("run table unavailable")

    def finish(self, run_id, counts):
        raise RuntimeError("run table unavailable")

    def fail(self, run_id, cause):
        raise RuntimeError("run table unavailable")


def test_a_broken_recorder_does_not_stop_the_sync(app, monkeypatch) -> None:
    """Recording is observability. If the row cannot be written, the user's
    data still arrives — the opposite of the workflow run-start decision,
    where refusing an execution loses nothing."""
    import mycelos.scheduler.jobs as jobs
    from mycelos.scheduler.jobs import auto_ingest_check

    _register(app, "yt-summary")
    app.memory.set("default", "system", "auto_ingest_enabled", True)
    app._mcp_manager = _FakeMcp(_yt_page())
    monkeypatch.setattr(jobs, "RunRecorder", _BrokenRecorder)

    result = auto_ingest_check(app)  # must not raise

    assert "yt-summary" in result["ran"]
    notes = app.storage.fetchall(
        "SELECT path FROM knowledge_notes WHERE created_by='import'")
    assert len(notes) == 1


def test_a_broken_recorder_does_not_stop_the_next_source(app, monkeypatch) -> None:
    """Errors in one connector never crash the loop — the same must hold for
    the recorder wrapped around it."""
    import mycelos.scheduler.jobs as jobs
    from mycelos.scheduler.jobs import auto_ingest_check

    _register(app, "gmail")
    _register(app, "yt-summary")
    app.memory.set("default", "system", "auto_ingest_enabled", True)
    app._mcp_manager = _FakeMcp(_yt_page())
    broken = _BrokenRecorder(app.storage)
    monkeypatch.setattr(jobs, "RunRecorder", lambda storage: broken)

    result = auto_ingest_check(app)

    assert broken.starts == 2, "both sources were still attempted"
    assert set(result["ran"]) | set(result["errors"]) == {"gmail", "yt-summary"}


def test_a_broken_recorder_is_logged(app, monkeypatch, caplog) -> None:
    import logging

    import mycelos.scheduler.jobs as jobs
    from mycelos.scheduler.jobs import auto_ingest_check

    _register(app, "yt-summary")
    app.memory.set("default", "system", "auto_ingest_enabled", True)
    app._mcp_manager = _FakeMcp(_yt_page())
    monkeypatch.setattr(jobs, "RunRecorder", _BrokenRecorder)

    with caplog.at_level(logging.WARNING, logger="mycelos.scheduler"):
        auto_ingest_check(app)

    assert any("run" in r.message.lower() for r in caplog.records), (
        "a run row that cannot be written must not vanish silently"
    )


# --- the briefing --------------------------------------------------------


class _FakeDelivery:
    def __init__(self, channels=("chat", "telegram"), succeed=True) -> None:
        self.channels = list(channels)
        self.succeed = succeed
        self.dispatched: list = []

    def _default_channels(self) -> list[str]:
        return self.channels

    def dispatch(self, channel: str, message: str) -> bool:
        self.dispatched.append((channel, message))
        return self.succeed


class _FakeLLMResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeBroker:
    def __init__(self, content: str = "Good morning.") -> None:
        self.calls: list = []
        self._content = content

    def complete(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return _FakeLLMResponse(self._content)

    def chat(self, *args, **kwargs):
        return self.complete(*args, **kwargs)


def test_the_briefing_writes_a_run_on_delivery(app) -> None:
    from mycelos.scheduler.jobs import briefing_tick

    app._llm = _FakeBroker()
    app.memory.set("default", "system", "briefing_enabled", True)
    delivery = _FakeDelivery()

    result = briefing_tick(
        app, now=datetime(2026, 6, 11, 8, 0), reminder_service=delivery)

    assert result["sent"] is True
    rows = _runs(app, "briefing")
    assert len(rows) == 1
    assert rows[0]["routine_key"] == "briefing"
    assert rows[0]["status"] == "completed"
    assert rows[0]["workflow_id"] is None
    assert _artifacts(rows[0])["sent"] == 1


def test_a_briefing_that_is_not_due_writes_no_run(app) -> None:
    """A tick that decides not to deliver is not a run. The briefing job
    ticks every five minutes; a row per tick would be 288 rows a day."""
    from mycelos.scheduler.jobs import briefing_tick

    app.memory.set("default", "system", "briefing_enabled", True)
    app.memory.set("default", "system", "briefing_time", "07:30")

    briefing_tick(app, now=datetime(2026, 6, 11, 7, 0),
                  reminder_service=_FakeDelivery())

    assert _runs(app, "briefing") == []


def test_a_disabled_briefing_writes_no_run(app) -> None:
    from mycelos.scheduler.jobs import briefing_tick

    briefing_tick(app, now=datetime(2026, 6, 11, 9, 0),
                  reminder_service=_FakeDelivery())

    assert _runs(app, "briefing") == []


def test_a_briefing_that_could_not_be_delivered_records_a_failed_run(app) -> None:
    """Delivery was attempted and did not happen — that is a run that ended,
    and the invariant says it leaves a row saying so."""
    from mycelos.scheduler.jobs import briefing_tick

    app._llm = _FakeBroker()
    app.memory.set("default", "system", "briefing_enabled", True)

    briefing_tick(app, now=datetime(2026, 6, 11, 9, 0),
                  reminder_service=_FakeDelivery(channels=["chat"]))

    rows = _runs(app, "briefing")
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["error"]


def test_a_briefing_that_raises_records_a_failed_run(app) -> None:
    from mycelos.scheduler.jobs import briefing_tick

    class _Exploding:
        def _default_channels(self):
            raise RuntimeError(LEAKY_MESSAGE)

        def dispatch(self, channel, message):
            raise RuntimeError(LEAKY_MESSAGE)

    app._llm = _FakeBroker()
    app.memory.set("default", "system", "briefing_enabled", True)

    result = briefing_tick(app, now=datetime(2026, 6, 11, 9, 0),
                           reminder_service=_Exploding())

    assert result["sent"] is False
    rows = _runs(app, "briefing")
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    stored = rows[0]["error"] or ""
    for fragment in LEAKY_FRAGMENTS:
        assert fragment not in stored, f"{fragment!r} leaked into the cause"


def test_a_broken_recorder_does_not_stop_the_briefing(app, monkeypatch) -> None:
    import mycelos.scheduler.jobs as jobs
    from mycelos.scheduler.jobs import briefing_tick

    app._llm = _FakeBroker()
    app.memory.set("default", "system", "briefing_enabled", True)
    delivery = _FakeDelivery()
    monkeypatch.setattr(jobs, "RunRecorder", _BrokenRecorder)

    result = briefing_tick(
        app, now=datetime(2026, 6, 11, 8, 0), reminder_service=delivery)

    assert result["sent"] is True
    assert len(delivery.dispatched) == 1
