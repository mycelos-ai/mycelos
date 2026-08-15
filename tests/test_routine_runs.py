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
