# Foundation Implementation Plan (Milestone 1 + 2)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the core infrastructure that every other subsystem depends on — storage, CLI, LLM integration, security layer, and configuration rollback.

**Architecture:** Modular services with Python Protocol interfaces and SQLite+Filesystem storage. LiteLLM for multi-provider LLM access. Every service is testable in isolation via mock implementations. The foundation produces a working `mycelos init` + `mycelos chat` that talks to an LLM through a secure broker.

**Tech Stack:** Python 3.12+, SQLite (WAL mode), LiteLLM, age-compatible encryption (MYCELOS_MASTER_KEY), click (CLI), pytest, Rich (terminal output)

**Spec Reference:** `docs/superpowers/specs/2026-03-15-agentos-design.md`

**Naming:** CLI command is `mycelos` (Mycelos). Python package is `mycelos`.

---

## Chunk 1: Project Setup and Data Model (Tasks 1-4)

### Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/mycelos/__init__.py`
- Create: `src/mycelos/py.typed`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `.gitignore`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "mycelos"
version = "0.1.0"
description = "Mycelos — a security-first agent operating system"
requires-python = ">=3.12"
dependencies = [
    "click>=8.1",
    "litellm>=1.40",
    "rich>=13.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "hypothesis>=6.0",
]

[project.scripts]
mycelos = "mycelos.cli.main:cli"

[tool.hatch.build.targets.wheel]
packages = ["src/mycelos"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create package structure**

```
mkdir -p src/mycelos
touch src/mycelos/__init__.py
touch src/mycelos/py.typed
mkdir -p tests
touch tests/__init__.py
```

`src/mycelos/__init__.py`:
```python
"""Mycelos — a security-first agent operating system."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Create .gitignore**

```
__pycache__/
*.pyc
*.egg-info/
dist/
build/
.venv/
*.db
*.db-wal
*.db-shm
# No .env files — system config lives in SQLite config generations
# No secrets files — credentials are encrypted in SQLite
artifacts/
data/
```

- [ ] **Step 4: Create tests/conftest.py**

```python
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Temporary data directory for tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def db_path(tmp_data_dir: Path) -> Path:
    """Path to temporary SQLite database."""
    return tmp_data_dir / "mycelos.db"
```

- [ ] **Step 5: Install in dev mode and verify**

Run: `pip install -e ".[dev]"`
Expected: Installs successfully

Run: `mycelos --help`
Expected: Error (cli.main not yet created) — confirms entry point is wired

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/ tests/ .gitignore
git commit -m "feat: project scaffolding with pyproject.toml and package structure"
```

---

### Task 2: Service Protocol Definitions

**Files:**
- Create: `src/mycelos/protocols.py`
- Create: `tests/test_protocols.py`

- [ ] **Step 1: Write protocol verification test**

`tests/test_protocols.py`:
```python
from mycelos.protocols import (
    StorageBackend,
    MemoryService,
    LLMBroker,
    SandboxManager,
    AuditLogger,
    CredentialProxy,
)


def test_protocols_are_importable():
    """All core service protocols can be imported."""
    assert StorageBackend is not None
    assert MemoryService is not None
    assert LLMBroker is not None
    assert SandboxManager is not None
    assert AuditLogger is not None
    assert CredentialProxy is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_protocols.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement protocols**

`src/mycelos/protocols.py`:
```python
"""Core service protocols (interfaces) for Mycelos.

Every core component is designed as Protocol + Implementation.
The Protocol defines the contract; implementations can be swapped
without changing the rest of the system.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Interface for structured data storage."""

    def execute(self, sql: str, params: tuple = ()) -> Any: ...
    def fetchone(self, sql: str, params: tuple = ()) -> dict | None: ...
    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]: ...
    def executescript(self, sql: str) -> None: ...


@runtime_checkable
class MemoryService(Protocol):
    """Interface for agent memory management."""

    def get(self, user_id: str, scope: str, key: str, agent_id: str | None = None) -> Any: ...
    def set(self, user_id: str, scope: str, key: str, value: Any, agent_id: str | None = None, created_by: str = "system") -> None: ...
    def search(self, user_id: str, scope: str, query: str, agent_id: str | None = None) -> list[dict]: ...
    def delete(self, user_id: str, scope: str, key: str, agent_id: str | None = None) -> bool: ...


@runtime_checkable
class LLMBroker(Protocol):
    """Interface for LLM access and conversation management."""

    def complete(self, messages: list[dict], model: str | None = None, tools: list[dict] | None = None, stream: bool = False) -> Any: ...
    def count_tokens(self, messages: list[dict], model: str | None = None) -> int: ...


@runtime_checkable
class SandboxManager(Protocol):
    """Interface for isolated agent execution."""

    def create_sandbox(self, agent_id: str, read_paths: list[str] | None = None, write_paths: list[str] | None = None) -> Any: ...
    def execute(self, sandbox_id: str, command: str, timeout: int = 30) -> dict: ...
    def cleanup(self, sandbox_id: str) -> None: ...


@runtime_checkable
class AuditLogger(Protocol):
    """Interface for audit event logging."""

    def log(self, event_type: str, agent_id: str | None = None, task_id: str | None = None, details: dict | None = None) -> None: ...
    def query(self, event_type: str | None = None, agent_id: str | None = None, limit: int = 100) -> list[dict]: ...


@runtime_checkable
class CredentialProxy(Protocol):
    """Interface for credential management."""

    def get_credential(self, service: str) -> dict | None: ...
    def store_credential(self, service: str, credential: dict) -> None: ...
    def list_services(self) -> list[str]: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_protocols.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/protocols.py tests/test_protocols.py
git commit -m "feat: define core service protocols (interfaces)"
```

---

### Task 3: SQLite Storage Backend

**Files:**
- Create: `src/mycelos/storage/__init__.py`
- Create: `src/mycelos/storage/database.py`
- Create: `src/mycelos/storage/schema.sql`
- Create: `tests/test_storage.py`

- [ ] **Step 1: Write storage tests**

`tests/test_storage.py`:
```python
import pytest
from pathlib import Path

from mycelos.storage.database import SQLiteStorage
from mycelos.protocols import StorageBackend


def test_sqlite_implements_protocol():
    """SQLiteStorage satisfies the StorageBackend protocol."""
    assert isinstance(SQLiteStorage.__new__(SQLiteStorage), StorageBackend)


def test_create_database(db_path: Path):
    """Database is created and schema is applied."""
    storage = SQLiteStorage(db_path)
    storage.initialize()

    result = storage.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    table_names = [r["name"] for r in result]

    assert "audit_events" in table_names
    assert "config_generations" in table_names
    assert "active_generation" in table_names
    assert "memory_entries" in table_names
    assert "tasks" in table_names
    assert "agents" in table_names


def test_wal_mode_enabled(db_path: Path):
    """SQLite WAL mode is enabled for concurrent access."""
    storage = SQLiteStorage(db_path)
    storage.initialize()

    result = storage.fetchone("PRAGMA journal_mode")
    assert result["journal_mode"] == "wal"


def test_execute_and_fetch(db_path: Path):
    """Basic insert and fetch operations work."""
    storage = SQLiteStorage(db_path)
    storage.initialize()

    storage.execute(
        "INSERT INTO audit_events (event_type, details) VALUES (?, ?)",
        ("test_event", '{"key": "value"}'),
    )

    result = storage.fetchone(
        "SELECT event_type, details FROM audit_events WHERE event_type = ?",
        ("test_event",),
    )

    assert result is not None
    assert result["event_type"] == "test_event"
    assert result["details"] == '{"key": "value"}'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_storage.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create schema.sql**

`src/mycelos/storage/schema.sql`:
```sql
-- Mycelos Core Schema

-- Tasks
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    goal        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    inputs      TEXT,
    budget      REAL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT
);

-- Agents
CREATE TABLE IF NOT EXISTS agents (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    agent_type  TEXT NOT NULL DEFAULT 'deterministic',
    status      TEXT NOT NULL DEFAULT 'proposed',
    capabilities TEXT,
    policy      TEXT,
    reputation  REAL NOT NULL DEFAULT 0.5,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    created_by  TEXT NOT NULL DEFAULT 'system'
);

-- Plans
CREATE TABLE IF NOT EXISTS plans (
    id          TEXT PRIMARY KEY,
    task_id     TEXT REFERENCES tasks(id),
    steps       TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'new',
    success_rate REAL NOT NULL DEFAULT 0.0,
    use_count   INTEGER NOT NULL DEFAULT 0,
    estimated_cost REAL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Attempts
CREATE TABLE IF NOT EXISTS attempts (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id),
    plan_id         TEXT REFERENCES plans(id),
    agent_id        TEXT REFERENCES agents(id),
    model_used      TEXT,
    cost            REAL,
    duration_ms     INTEGER,
    result          TEXT,
    success         INTEGER NOT NULL DEFAULT 0,
    escalation_count INTEGER NOT NULL DEFAULT 0,
    generation_id   INTEGER,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Config Generations (NixOS-style)
CREATE TABLE IF NOT EXISTS config_generations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    parent_id       INTEGER REFERENCES config_generations(id),
    config_hash     TEXT NOT NULL,
    config_snapshot TEXT NOT NULL,
    description     TEXT,
    trigger         TEXT NOT NULL DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS active_generation (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    generation_id   INTEGER NOT NULL REFERENCES config_generations(id),
    activated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Memory
CREATE TABLE IF NOT EXISTS memory_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL DEFAULT 'default',
    scope       TEXT NOT NULL,
    agent_id    TEXT,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    created_by  TEXT NOT NULL DEFAULT 'system',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT,
    expires_at  TEXT,
    UNIQUE(user_id, scope, agent_id, key)
);

CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory_entries(user_id, scope, agent_id);

-- Audit Events
CREATE TABLE IF NOT EXISTS audit_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    agent_id    TEXT,
    task_id     TEXT,
    user_id     TEXT NOT NULL DEFAULT 'default',
    details     TEXT,
    generation_id INTEGER,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_events(event_type, created_at);

-- Workflow Events (Durable Execution)
CREATE TABLE IF NOT EXISTS workflow_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL,
    step_id     TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    checkpoint  TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_workflow ON workflow_events(workflow_id, created_at);
```

- [ ] **Step 4: Implement SQLiteStorage**

`src/mycelos/storage/__init__.py`:
```python
from mycelos.storage.database import SQLiteStorage

__all__ = ["SQLiteStorage"]
```

`src/mycelos/storage/database.py`:
```python
"""SQLite storage backend with WAL mode."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path
from typing import Any


class SQLiteStorage:
    """SQLite-based storage backend.

    Uses WAL mode for concurrent read access.
    Returns rows as dicts for easy field access.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

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
        conn.commit()
        return cursor

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
        if self._conn:
            self._conn.close()
            self._conn = None
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_storage.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/storage/ tests/test_storage.py
git commit -m "feat: SQLite storage backend with WAL mode and core schema"
```

---

### Task 4: Config Generation Manager with Blueprint Lifecycle

**Files:**
- Create: `src/mycelos/config/__init__.py`
- Create: `src/mycelos/config/generations.py`
- Create: `tests/test_config.py`

**Design:** NixOS-style immutable generations with a Blueprint Lifecycle (resolve → verify → plan → apply → status) inspired by NemoClaw's orchestration pattern. See spec Section 9 for full details. The Blueprint Lifecycle wraps every config change in a 5-phase pipeline that ensures integrity (SHA-256 verify), visibility (plan/diff with risk classification), and safety (guard period with auto-rollback). This is a pattern adoption, not a code dependency on NemoClaw.

- [ ] **Step 1: Write config generation tests**

`tests/test_config.py`:
```python
import json
import pytest
from pathlib import Path

from mycelos.storage.database import SQLiteStorage
from mycelos.config.generations import ConfigGenerationManager


@pytest.fixture
def config_mgr(db_path: Path) -> ConfigGenerationManager:
    storage = SQLiteStorage(db_path)
    storage.initialize()
    return ConfigGenerationManager(storage)


def test_apply_first_config(config_mgr: ConfigGenerationManager):
    """First config application creates generation 1."""
    config = {"agents": {}, "models": {"default": "haiku"}}
    gen_id = config_mgr.apply(config, description="initial config")

    assert gen_id == 1
    assert config_mgr.get_active_generation_id() == 1


def test_apply_creates_new_generation(config_mgr: ConfigGenerationManager):
    """Each apply creates a new generation."""
    config_mgr.apply({"version": 1}, description="v1")
    config_mgr.apply({"version": 2}, description="v2")

    assert config_mgr.get_active_generation_id() == 2


def test_rollback_to_previous(config_mgr: ConfigGenerationManager):
    """Rollback reverts to parent generation."""
    config_mgr.apply({"version": 1}, description="v1")
    config_mgr.apply({"version": 2}, description="v2")

    rolled_back = config_mgr.rollback()
    assert rolled_back == 1
    assert config_mgr.get_active_generation_id() == 1


def test_rollback_to_specific(config_mgr: ConfigGenerationManager):
    """Rollback to a specific generation ID."""
    config_mgr.apply({"version": 1})
    config_mgr.apply({"version": 2})
    config_mgr.apply({"version": 3})

    config_mgr.rollback(to_generation=1)
    assert config_mgr.get_active_generation_id() == 1


def test_get_config_snapshot(config_mgr: ConfigGenerationManager):
    """Active config snapshot is retrievable."""
    config = {"agents": {"email": {"model": "haiku"}}}
    config_mgr.apply(config)

    snapshot = config_mgr.get_active_config()
    assert snapshot == config


def test_list_generations(config_mgr: ConfigGenerationManager):
    """List returns all generations with active marker."""
    config_mgr.apply({"v": 1}, description="first")
    config_mgr.apply({"v": 2}, description="second")

    gens = config_mgr.list_generations()
    assert len(gens) == 2
    assert gens[0]["active"] is True  # most recent first
    assert gens[0]["description"] == "second"


def test_duplicate_config_deduplication(config_mgr: ConfigGenerationManager):
    """Identical configs reuse existing generation (content-addressed)."""
    config = {"same": "config"}
    gen1 = config_mgr.apply(config, description="first")
    gen2 = config_mgr.apply(config, description="duplicate")

    assert gen1 == gen2  # same config hash = same generation


def test_diff_generations(config_mgr: ConfigGenerationManager):
    """Diff shows changes between two generations."""
    config_mgr.apply({"model": "haiku", "timeout": 30})
    config_mgr.apply({"model": "sonnet", "timeout": 30})

    diff = config_mgr.diff(1, 2)
    assert "model" in diff
    assert diff["model"]["from"] == "haiku"
    assert diff["model"]["to"] == "sonnet"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL

- [ ] **Step 3: Implement ConfigGenerationManager**

`src/mycelos/config/__init__.py`:
```python
from mycelos.config.generations import ConfigGenerationManager

__all__ = ["ConfigGenerationManager"]
```

`src/mycelos/config/generations.py`:
```python
"""NixOS-style immutable config generations with atomic rollback."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from mycelos.protocols import StorageBackend


class ConfigGenerationManager:
    """Manages immutable configuration generations.

    Every config change creates a new numbered generation.
    Rollback is a pointer swap, not an undo operation.
    """

    def __init__(self, storage: StorageBackend):
        self._storage = storage

    def _canonical_json(self, config: dict) -> str:
        return json.dumps(config, sort_keys=True, separators=(",", ":"))

    def _hash_config(self, config: dict) -> str:
        canonical = self._canonical_json(config)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def apply(
        self,
        config: dict,
        description: str = "",
        trigger: str = "manual",
    ) -> int:
        """Apply a new config. Returns generation ID."""
        config_hash = self._hash_config(config)
        config_json = self._canonical_json(config)

        # Check for duplicate (content-addressed dedup)
        existing = self._storage.fetchone(
            "SELECT id FROM config_generations WHERE config_hash = ?",
            (config_hash,),
        )
        if existing:
            gen_id = existing["id"]
        else:
            parent = self._storage.fetchone(
                "SELECT generation_id FROM active_generation WHERE id = 1"
            )
            parent_id = parent["generation_id"] if parent else None

            cursor = self._storage.execute(
                """INSERT INTO config_generations
                   (parent_id, config_hash, config_snapshot, description, trigger)
                   VALUES (?, ?, ?, ?, ?)""",
                (parent_id, config_hash, config_json, description, trigger),
            )
            gen_id = cursor.lastrowid

        # Atomic swap
        now = datetime.now(timezone.utc).isoformat()
        self._storage.execute(
            """INSERT INTO active_generation (id, generation_id, activated_at)
               VALUES (1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 generation_id = excluded.generation_id,
                 activated_at = excluded.activated_at""",
            (gen_id, now),
        )
        return gen_id

    def rollback(self, to_generation: int | None = None) -> int:
        """Roll back to a specific or previous generation."""
        if to_generation is None:
            row = self._storage.fetchone(
                """SELECT g.parent_id FROM active_generation a
                   JOIN config_generations g ON a.generation_id = g.id
                   WHERE a.id = 1"""
            )
            if not row or row["parent_id"] is None:
                raise ValueError("No parent generation to roll back to")
            to_generation = row["parent_id"]

        target = self._storage.fetchone(
            "SELECT id FROM config_generations WHERE id = ?",
            (to_generation,),
        )
        if not target:
            raise ValueError(f"Generation {to_generation} does not exist")

        now = datetime.now(timezone.utc).isoformat()
        self._storage.execute(
            "UPDATE active_generation SET generation_id = ?, activated_at = ? WHERE id = 1",
            (to_generation, now),
        )
        return to_generation

    def get_active_generation_id(self) -> int | None:
        row = self._storage.fetchone(
            "SELECT generation_id FROM active_generation WHERE id = 1"
        )
        return row["generation_id"] if row else None

    def get_active_config(self) -> dict | None:
        row = self._storage.fetchone(
            """SELECT g.config_snapshot FROM active_generation a
               JOIN config_generations g ON a.generation_id = g.id
               WHERE a.id = 1"""
        )
        return json.loads(row["config_snapshot"]) if row else None

    def list_generations(self, limit: int = 20) -> list[dict]:
        active_id = self.get_active_generation_id()
        rows = self._storage.fetchall(
            """SELECT id, created_at, parent_id, config_hash,
                      description, trigger
               FROM config_generations
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
        return [
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "parent_id": r["parent_id"],
                "hash": r["config_hash"][:12],
                "description": r["description"],
                "trigger": r["trigger"],
                "active": r["id"] == active_id,
            }
            for r in rows
        ]

    def diff(self, gen_a: int, gen_b: int) -> dict:
        """Semantic diff between two generations."""
        a_row = self._storage.fetchone(
            "SELECT config_snapshot FROM config_generations WHERE id = ?",
            (gen_a,),
        )
        b_row = self._storage.fetchone(
            "SELECT config_snapshot FROM config_generations WHERE id = ?",
            (gen_b,),
        )
        if not a_row or not b_row:
            raise ValueError("Generation not found")

        a = json.loads(a_row["config_snapshot"])
        b = json.loads(b_row["config_snapshot"])
        return self._deep_diff(a, b)

    def _deep_diff(self, a: dict, b: dict, path: str = "") -> dict:
        changes = {}
        all_keys = set(list(a.keys()) + list(b.keys()))
        for key in sorted(all_keys):
            current_path = f"{path}.{key}" if path else key
            if key not in a:
                changes[current_path] = {"type": "added", "value": b[key]}
            elif key not in b:
                changes[current_path] = {"type": "removed", "value": a[key]}
            elif a[key] != b[key]:
                if isinstance(a[key], dict) and isinstance(b[key], dict):
                    changes.update(self._deep_diff(a[key], b[key], current_path))
                else:
                    changes[current_path] = {"type": "changed", "from": a[key], "to": b[key]}
        return changes
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_config.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/mycelos/config/ tests/test_config.py
git commit -m "feat: NixOS-style config generations with atomic rollback"
```

**Note on Blueprint Lifecycle implementation order:** The `apply()` method implemented here covers the core mechanics (resolve + verify + apply as a single call). The full Blueprint Lifecycle (explicit `apply_blueprint()` with separate phases, `ChangeSpec`, `BlueprintResult`, risk classification, guard period, and `blueprint_transitions` table) is specified in the design doc (Section 9) and will be implemented in Milestone 2 alongside the Security Layer. The existing `apply()` method remains the low-level primitive that the Blueprint Lifecycle orchestrates.

---

## Chunk 2: CLI, Audit, and LLM Broker (Tasks 5-8)

### Task 5: Audit Logger

**Files:**
- Create: `src/mycelos/audit.py`
- Create: `tests/test_audit.py`

- [ ] **Step 1: Write audit tests**

`tests/test_audit.py`:
```python
import pytest
from pathlib import Path

from mycelos.storage.database import SQLiteStorage
from mycelos.audit import SQLiteAuditLogger
from mycelos.protocols import AuditLogger


def test_implements_protocol():
    assert isinstance(SQLiteAuditLogger.__new__(SQLiteAuditLogger), AuditLogger)


@pytest.fixture
def logger(db_path: Path) -> SQLiteAuditLogger:
    storage = SQLiteStorage(db_path)
    storage.initialize()
    return SQLiteAuditLogger(storage)


def test_log_event(logger: SQLiteAuditLogger):
    logger.log("task.created", task_id="t1", details={"goal": "test"})

    events = logger.query(event_type="task.created")
    assert len(events) == 1
    assert events[0]["task_id"] == "t1"


def test_query_by_agent(logger: SQLiteAuditLogger):
    logger.log("agent.action", agent_id="a1")
    logger.log("agent.action", agent_id="a2")

    events = logger.query(agent_id="a1")
    assert len(events) == 1
    assert events[0]["agent_id"] == "a1"


def test_query_limit(logger: SQLiteAuditLogger):
    for i in range(5):
        logger.log("test.event", details={"i": i})

    events = logger.query(limit=3)
    assert len(events) == 3
```

- [ ] **Step 2: Run to verify failure, then implement**

`src/mycelos/audit.py`:
```python
"""Audit event logging."""

from __future__ import annotations

import json

from mycelos.protocols import StorageBackend


class SQLiteAuditLogger:
    """SQLite-based audit logger."""

    def __init__(self, storage: StorageBackend):
        self._storage = storage

    def log(
        self,
        event_type: str,
        agent_id: str | None = None,
        task_id: str | None = None,
        details: dict | None = None,
        user_id: str = "default",
        generation_id: int | None = None,
    ) -> None:
        self._storage.execute(
            """INSERT INTO audit_events
               (event_type, agent_id, task_id, user_id, details, generation_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                event_type,
                agent_id,
                task_id,
                user_id,
                json.dumps(details) if details else None,
                generation_id,
            ),
        )

    def query(
        self,
        event_type: str | None = None,
        agent_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        conditions = []
        params: list = []

        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        return self._storage.fetchall(
            f"SELECT * FROM audit_events {where} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
```

- [ ] **Step 3: Run tests, commit**

Run: `pytest tests/test_audit.py -v`
Expected: All PASS

```bash
git add src/mycelos/audit.py tests/test_audit.py
git commit -m "feat: SQLite audit logger with event type and agent filtering"
```

---

### Task 6: LLM Broker (LiteLLM-based Conversation Service)

**Files:**
- Create: `src/mycelos/llm/__init__.py`
- Create: `src/mycelos/llm/broker.py`
- Create: `tests/test_llm_broker.py`

- [ ] **Step 1: Write broker tests (with mock)**

`tests/test_llm_broker.py`:
```python
import pytest
from unittest.mock import patch, MagicMock

from mycelos.llm.broker import LiteLLMBroker
from mycelos.protocols import LLMBroker


def test_implements_protocol():
    assert isinstance(LiteLLMBroker.__new__(LiteLLMBroker), LLMBroker)


def test_complete_calls_litellm():
    broker = LiteLLMBroker(default_model="claude-haiku-4-5-20251001")

    with patch("mycelos.llm.broker.litellm") as mock_litellm:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello!"
        mock_response.usage.total_tokens = 50
        mock_litellm.completion.return_value = mock_response

        result = broker.complete(
            messages=[{"role": "user", "content": "Hi"}]
        )

        mock_litellm.completion.assert_called_once()
        assert result.content == "Hello!"
        assert result.total_tokens == 50


def test_default_model_used():
    broker = LiteLLMBroker(default_model="claude-haiku-4-5-20251001")

    with patch("mycelos.llm.broker.litellm") as mock_litellm:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "test"
        mock_response.usage.total_tokens = 10
        mock_litellm.completion.return_value = mock_response

        broker.complete(messages=[{"role": "user", "content": "test"}])

        call_kwargs = mock_litellm.completion.call_args
        assert call_kwargs.kwargs["model"] == "claude-haiku-4-5-20251001"


def test_model_override():
    broker = LiteLLMBroker(default_model="claude-haiku-4-5-20251001")

    with patch("mycelos.llm.broker.litellm") as mock_litellm:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "test"
        mock_response.usage.total_tokens = 10
        mock_litellm.completion.return_value = mock_response

        broker.complete(
            messages=[{"role": "user", "content": "test"}],
            model="claude-opus-4-20250514",
        )

        call_kwargs = mock_litellm.completion.call_args
        assert call_kwargs.kwargs["model"] == "claude-opus-4-20250514"


def test_count_tokens():
    broker = LiteLLMBroker(default_model="claude-haiku-4-5-20251001")

    with patch("mycelos.llm.broker.litellm") as mock_litellm:
        mock_litellm.token_counter.return_value = 42

        count = broker.count_tokens(
            messages=[{"role": "user", "content": "Hello world"}]
        )
        assert count == 42
```

- [ ] **Step 2: Implement LiteLLM broker**

`src/mycelos/llm/__init__.py`:
```python
from mycelos.llm.broker import LiteLLMBroker

__all__ = ["LiteLLMBroker"]
```

`src/mycelos/llm/broker.py`:
```python
"""LLM Broker / Conversation Service backed by LiteLLM."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import litellm


@dataclass
class LLMResponse:
    """Structured response from LLM."""
    content: str
    total_tokens: int
    model: str = ""
    tool_calls: list[dict] | None = None


class LiteLLMBroker:
    """LLM Broker using LiteLLM for multi-provider access.

    Provides: completion, token counting, cost tracking.
    All LLM access in the system goes through this broker.
    """

    def __init__(self, default_model: str = "claude-haiku-4-5-20251001"):
        self.default_model = default_model
        self.total_cost: float = 0.0
        self.total_tokens: int = 0

    def complete(
        self,
        messages: list[dict],
        model: str | None = None,
        tools: list[dict] | None = None,
        stream: bool = False,
    ) -> LLMResponse:
        """Send completion request through LiteLLM."""
        chosen_model = model or self.default_model

        kwargs: dict[str, Any] = {
            "model": chosen_model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        if stream:
            kwargs["stream"] = True

        response = litellm.completion(**kwargs)

        if stream:
            return response  # type: ignore — caller handles streaming

        choice = response.choices[0]
        usage = response.usage

        self.total_tokens += usage.total_tokens

        tool_calls = None
        if hasattr(choice.message, "tool_calls") and choice.message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in choice.message.tool_calls
            ]

        return LLMResponse(
            content=choice.message.content or "",
            total_tokens=usage.total_tokens,
            model=chosen_model,
            tool_calls=tool_calls,
        )

    def count_tokens(
        self,
        messages: list[dict],
        model: str | None = None,
    ) -> int:
        """Count tokens for messages without making an API call."""
        chosen_model = model or self.default_model
        return litellm.token_counter(model=chosen_model, messages=messages)
```

- [ ] **Step 3: Run tests, commit**

Run: `pytest tests/test_llm_broker.py -v`
Expected: All PASS

```bash
git add src/mycelos/llm/ tests/test_llm_broker.py
git commit -m "feat: LiteLLM-based LLM broker with token counting and cost tracking"
```

---

### Task 7: CLI Skeleton

**Files:**
- Create: `src/mycelos/cli/__init__.py`
- Create: `src/mycelos/cli/main.py`
- Create: `src/mycelos/cli/init_cmd.py`
- Create: `src/mycelos/cli/chat_cmd.py`
- Create: `src/mycelos/cli/config_cmd.py`
- Create: `src/mycelos/app.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write CLI tests**

`tests/test_cli.py`:
```python
from click.testing import CliRunner

from mycelos.cli.main import cli


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Mycelos" in result.output


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_init_creates_database(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--data-dir", str(tmp_path / "data")])
    assert result.exit_code == 0
    assert (tmp_path / "data" / "mycelos.db").exists()


def test_config_list_empty(tmp_path):
    runner = CliRunner()
    data_dir = str(tmp_path / "data")
    runner.invoke(cli, ["init", "--data-dir", data_dir])
    result = runner.invoke(cli, ["config", "list", "--data-dir", data_dir])
    assert result.exit_code == 0
```

- [ ] **Step 2: Implement App (service container)**

`src/mycelos/app.py`:
```python
"""Application container — wires all services together."""

from __future__ import annotations

from pathlib import Path

from mycelos.audit import SQLiteAuditLogger
from mycelos.config.generations import ConfigGenerationManager
from mycelos.llm.broker import LiteLLMBroker
from mycelos.storage.database import SQLiteStorage


class App:
    """Central application container.

    Creates and holds all service instances.
    Services are lazily initialized.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._storage: SQLiteStorage | None = None
        self._config_mgr: ConfigGenerationManager | None = None
        self._audit: SQLiteAuditLogger | None = None
        self._llm: LiteLLMBroker | None = None

    @property
    def storage(self) -> SQLiteStorage:
        if self._storage is None:
            self._storage = SQLiteStorage(self.data_dir / "mycelos.db")
        return self._storage

    @property
    def config(self) -> ConfigGenerationManager:
        if self._config_mgr is None:
            self._config_mgr = ConfigGenerationManager(self.storage)
        return self._config_mgr

    @property
    def audit(self) -> SQLiteAuditLogger:
        if self._audit is None:
            self._audit = SQLiteAuditLogger(self.storage)
        return self._audit

    @property
    def llm(self) -> LiteLLMBroker:
        if self._llm is None:
            config = self.config.get_active_config() or {}
            default_model = config.get("default_model", "claude-haiku-4-5-20251001")
            self._llm = LiteLLMBroker(default_model=default_model)
        return self._llm

    def initialize(self) -> None:
        """Initialize database and apply first config generation."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.storage.initialize()

        if self.config.get_active_generation_id() is None:
            self.config.apply(
                {"version": "0.1.0", "default_model": "claude-haiku-4-5-20251001"},
                description="initial config",
                trigger="init",
            )
            self.audit.log("system.initialized")
```

- [ ] **Step 3: Implement CLI commands**

`src/mycelos/cli/__init__.py`:
```python
```

`src/mycelos/cli/main.py`:
```python
"""Mycelos CLI entry point."""

import click

from mycelos import __version__
from mycelos.cli.init_cmd import init_cmd
from mycelos.cli.config_cmd import config_group


@click.group()
@click.version_option(version=__version__, prog_name="Mycelos")
def cli():
    """Mycelos — a security-first agent operating system."""
    pass


cli.add_command(init_cmd, "init")
cli.add_command(config_group, "config")
```

`src/mycelos/cli/init_cmd.py`:
```python
"""mycelos init command."""

from pathlib import Path

import click
from rich.console import Console

from mycelos.app import App

console = Console()


@click.command()
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=Path.home() / ".mycelos",
    help="Data directory for Mycelos",
)
def init_cmd(data_dir: Path):
    """Initialize Mycelos."""
    app = App(data_dir)
    app.initialize()
    console.print(f"[green]Mycelos initialized at {data_dir}[/green]")
    console.print(f"Database: {data_dir / 'mycelos.db'}")
    console.print("\nNext: run [bold]mycelos chat[/bold] to start.")
```

`src/mycelos/cli/config_cmd.py`:
```python
"""mycelos config commands."""

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from mycelos.app import App

console = Console()


@click.group()
def config_group():
    """Manage system configuration (NixOS-style generations)."""
    pass


@config_group.command("list")
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def config_list(data_dir: Path):
    """List all config generations."""
    app = App(data_dir)

    gens = app.config.list_generations()
    if not gens:
        console.print("No config generations found. Run [bold]mycelos init[/bold] first.")
        return

    table = Table(title="Config Generations")
    table.add_column("ID", style="cyan")
    table.add_column("Created", style="dim")
    table.add_column("Hash", style="dim")
    table.add_column("Description")
    table.add_column("Active", style="green")

    for gen in gens:
        table.add_row(
            str(gen["id"]),
            gen["created_at"][:19],
            gen["hash"],
            gen["description"] or "",
            "<<<" if gen["active"] else "",
        )

    console.print(table)


@config_group.command("rollback")
@click.argument("generation_id", type=int, required=False)
@click.option("--data-dir", type=click.Path(path_type=Path), default=Path.home() / ".mycelos")
def config_rollback(generation_id: int | None, data_dir: Path):
    """Roll back to a previous config generation."""
    app = App(data_dir)

    try:
        rolled_back = app.config.rollback(to_generation=generation_id)
        app.audit.log("config.rollback", details={"to_generation": rolled_back})
        console.print(f"[green]Rolled back to generation {rolled_back}[/green]")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_cli.py -v`
Expected: All PASS

- [ ] **Step 5: Manual verification**

Run: `pip install -e ".[dev]" && mycelos --help`
Expected: Shows help with init and config commands

Run: `mycelos init --data-dir /tmp/mycelos-test && mycelos config list --data-dir /tmp/mycelos-test`
Expected: Shows generation 1 as active

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/cli/ src/mycelos/app.py tests/test_cli.py
git commit -m "feat: CLI skeleton with init, config list, and config rollback"
```

---

### Task 8: Memory Service

**Files:**
- Create: `src/mycelos/memory/__init__.py`
- Create: `src/mycelos/memory/service.py`
- Create: `tests/test_memory.py`

- [ ] **Step 1: Write memory service tests**

`tests/test_memory.py`:
```python
import pytest
from pathlib import Path

from mycelos.storage.database import SQLiteStorage
from mycelos.memory.service import SQLiteMemoryService
from mycelos.protocols import MemoryService


def test_implements_protocol():
    assert isinstance(SQLiteMemoryService.__new__(SQLiteMemoryService), MemoryService)


@pytest.fixture
def memory(db_path: Path) -> SQLiteMemoryService:
    storage = SQLiteStorage(db_path)
    storage.initialize()
    return SQLiteMemoryService(storage)


def test_set_and_get_system_memory(memory: SQLiteMemoryService):
    memory.set("user1", "system", "user.name", "Stefan", created_by="system")
    result = memory.get("user1", "system", "user.name")
    assert result == "Stefan"


def test_agent_memory_isolation(memory: SQLiteMemoryService):
    memory.set("user1", "agent", "pref", "dark", agent_id="agent-a", created_by="agent-a")
    memory.set("user1", "agent", "pref", "light", agent_id="agent-b", created_by="agent-b")

    assert memory.get("user1", "agent", "pref", agent_id="agent-a") == "dark"
    assert memory.get("user1", "agent", "pref", agent_id="agent-b") == "light"


def test_get_nonexistent_returns_none(memory: SQLiteMemoryService):
    result = memory.get("user1", "system", "nonexistent")
    assert result is None


def test_search_memory(memory: SQLiteMemoryService):
    memory.set("user1", "shared", "project.alpha.deadline", "2026-04-15", created_by="planner")
    memory.set("user1", "shared", "project.beta.status", "active", created_by="planner")

    results = memory.search("user1", "shared", "project.alpha")
    assert len(results) == 1
    assert results[0]["key"] == "project.alpha.deadline"


def test_delete_memory(memory: SQLiteMemoryService):
    memory.set("user1", "agent", "temp", "data", agent_id="a1", created_by="a1")
    assert memory.delete("user1", "agent", "temp", agent_id="a1")
    assert memory.get("user1", "agent", "temp", agent_id="a1") is None


def test_user_scoping(memory: SQLiteMemoryService):
    memory.set("user1", "system", "name", "Stefan", created_by="system")
    memory.set("user2", "system", "name", "Max", created_by="system")

    assert memory.get("user1", "system", "name") == "Stefan"
    assert memory.get("user2", "system", "name") == "Max"


def test_update_existing_key(memory: SQLiteMemoryService):
    memory.set("user1", "system", "lang", "de", created_by="system")
    memory.set("user1", "system", "lang", "en", created_by="system")

    assert memory.get("user1", "system", "lang") == "en"
```

- [ ] **Step 2: Implement SQLiteMemoryService**

`src/mycelos/memory/__init__.py`:
```python
from mycelos.memory.service import SQLiteMemoryService

__all__ = ["SQLiteMemoryService"]
```

`src/mycelos/memory/service.py`:
```python
"""Memory Management Service with scoped access."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from mycelos.protocols import StorageBackend


class SQLiteMemoryService:
    """SQLite-based memory service with four scoped layers.

    Scopes: system, agent, shared, session.
    All entries are user-scoped via user_id.
    """

    def __init__(self, storage: StorageBackend):
        self._storage = storage

    def get(
        self,
        user_id: str,
        scope: str,
        key: str,
        agent_id: str | None = None,
    ) -> Any:
        if agent_id:
            row = self._storage.fetchone(
                """SELECT value FROM memory_entries
                   WHERE user_id = ? AND scope = ? AND key = ? AND agent_id = ?""",
                (user_id, scope, key, agent_id),
            )
        else:
            row = self._storage.fetchone(
                """SELECT value FROM memory_entries
                   WHERE user_id = ? AND scope = ? AND key = ? AND agent_id IS NULL""",
                (user_id, scope, key),
            )
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return row["value"]

    def set(
        self,
        user_id: str,
        scope: str,
        key: str,
        value: Any,
        agent_id: str | None = None,
        created_by: str = "system",
    ) -> None:
        serialized = json.dumps(value) if not isinstance(value, str) else value
        now = datetime.now(timezone.utc).isoformat()

        self._storage.execute(
            """INSERT INTO memory_entries (user_id, scope, agent_id, key, value, created_by)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, scope, agent_id, key) DO UPDATE SET
                 value = excluded.value,
                 updated_at = ?""",
            (user_id, scope, agent_id, key, serialized, created_by, now),
        )

    def search(
        self,
        user_id: str,
        scope: str,
        query: str,
        agent_id: str | None = None,
    ) -> list[dict]:
        if agent_id:
            rows = self._storage.fetchall(
                """SELECT key, value, created_by, created_at FROM memory_entries
                   WHERE user_id = ? AND scope = ? AND agent_id = ? AND key LIKE ?
                   ORDER BY created_at DESC""",
                (user_id, scope, agent_id, f"%{query}%"),
            )
        else:
            rows = self._storage.fetchall(
                """SELECT key, value, created_by, created_at FROM memory_entries
                   WHERE user_id = ? AND scope = ? AND key LIKE ?
                   ORDER BY created_at DESC""",
                (user_id, scope, f"%{query}%"),
            )
        return rows

    def delete(
        self,
        user_id: str,
        scope: str,
        key: str,
        agent_id: str | None = None,
    ) -> bool:
        if agent_id:
            cursor = self._storage.execute(
                """DELETE FROM memory_entries
                   WHERE user_id = ? AND scope = ? AND key = ? AND agent_id = ?""",
                (user_id, scope, key, agent_id),
            )
        else:
            cursor = self._storage.execute(
                """DELETE FROM memory_entries
                   WHERE user_id = ? AND scope = ? AND key = ? AND agent_id IS NULL""",
                (user_id, scope, key),
            )
        return cursor.rowcount > 0
```

- [ ] **Step 3: Run tests, commit**

Run: `pytest tests/test_memory.py -v`
Expected: All PASS

```bash
git add src/mycelos/memory/ tests/test_memory.py
git commit -m "feat: memory service with system/agent/shared/session scopes"
```

---

## Chunk 3: Integration — mycelos init + mycelos chat (Tasks 9-10)

### Task 9: Wire Memory Service into App

**Files:**
- Modify: `src/mycelos/app.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add memory property to App**

Add to `src/mycelos/app.py`:
```python
from mycelos.memory.service import SQLiteMemoryService

# In App class, add property:
@property
def memory(self) -> SQLiteMemoryService:
    if self._memory is None:
        self._memory = SQLiteMemoryService(self.storage)
    return self._memory
```

Also add `self._memory: SQLiteMemoryService | None = None` to `__init__`.

- [ ] **Step 2: Add test for integrated init**

Add to `tests/test_cli.py`:
```python
def test_init_creates_initial_config(tmp_path):
    runner = CliRunner()
    data_dir = str(tmp_path / "data")
    runner.invoke(cli, ["init", "--data-dir", data_dir])

    from mycelos.app import App
    app = App(Path(data_dir))
    config = app.config.get_active_config()
    assert config is not None
    assert config["version"] == "0.1.0"
```

- [ ] **Step 3: Run all tests**

Run: `pytest -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/mycelos/app.py tests/test_cli.py
git commit -m "feat: wire memory service into app container"
```

---

### Task 10: Basic Chat Command (End-to-End)

**Files:**
- Create: `src/mycelos/cli/chat_cmd.py`
- Modify: `src/mycelos/cli/main.py`
- Create: `tests/test_chat.py`

- [ ] **Step 1: Write chat test**

`tests/test_chat.py`:
```python
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from pathlib import Path

from mycelos.cli.main import cli


def test_chat_requires_init(tmp_path):
    """Chat fails gracefully if not initialized."""
    runner = CliRunner()
    result = runner.invoke(cli, ["chat", "--data-dir", str(tmp_path / "nodata")])
    assert result.exit_code != 0 or "not initialized" in result.output.lower()
```

- [ ] **Step 2: Implement chat command**

`src/mycelos/cli/chat_cmd.py`:
```python
"""mycelos chat command — interactive session with the Creator-Agent."""

from pathlib import Path

import click
from rich.console import Console
from rich.markdown import Markdown

from mycelos.app import App

console = Console()


@click.command()
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=Path.home() / ".mycelos",
)
def chat_cmd(data_dir: Path):
    """Start an interactive chat session."""
    db_path = data_dir / "mycelos.db"
    if not db_path.exists():
        console.print("[red]Mycelos not initialized. Run: mycelos init[/red]")
        raise SystemExit(1)

    app = App(data_dir)
    app.audit.log("session.started")

    # Load user name from memory
    user_name = app.memory.get("default", "system", "user.name")
    if user_name:
        console.print(f"\n[bold]Welcome back, {user_name}![/bold]\n")
    else:
        console.print("\n[bold]Welcome to Mycelos![/bold]\n")

    messages = [
        {
            "role": "system",
            "content": (
                "You are the Mycelos Creator-Agent. You help the user set up "
                "and manage their agent operating system. Be helpful and concise. "
                "If the user tells you their name, remember it."
            ),
        }
    ]

    console.print("[dim]Type 'exit' or 'quit' to end the session.[/dim]\n")

    while True:
        try:
            user_input = console.input("[bold cyan]You:[/bold cyan] ")
        except (EOFError, KeyboardInterrupt):
            break

        if user_input.strip().lower() in ("exit", "quit", "q"):
            break

        if not user_input.strip():
            continue

        messages.append({"role": "user", "content": user_input})

        try:
            response = app.llm.complete(messages=messages)
            messages.append({"role": "assistant", "content": response.content})

            console.print()
            console.print(Markdown(response.content))
            console.print()

            app.audit.log(
                "chat.exchange",
                details={
                    "tokens": response.total_tokens,
                    "model": response.model,
                },
            )
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]\n")

    app.audit.log("session.ended")
    console.print("\n[dim]Session ended.[/dim]")
```

- [ ] **Step 3: Add chat to CLI**

Add to `src/mycelos/cli/main.py`:
```python
from mycelos.cli.chat_cmd import chat_cmd

cli.add_command(chat_cmd, "chat")
```

- [ ] **Step 4: Run tests**

Run: `pytest -v`
Expected: All PASS

- [ ] **Step 5: Manual end-to-end test**

```bash
mycelos init --data-dir /tmp/mycelos-test
export ANTHROPIC_API_KEY=sk-...  # or any supported provider
mycelos chat --data-dir /tmp/mycelos-test
```

Expected: Interactive chat session with LLM, type messages, get responses, exit with 'quit'.

- [ ] **Step 6: Commit**

```bash
git add src/mycelos/cli/chat_cmd.py src/mycelos/cli/main.py tests/test_chat.py
git commit -m "feat: interactive chat command with LLM broker integration"
```

---

## Milestone 1 Complete

At this point you have a working system:

```bash
$ pip install -e ".[dev]"
$ mycelos init
$ mycelos chat          # Talk to the Creator-Agent
$ mycelos config list   # See config generations
$ mycelos config rollback  # Roll back to previous config
```

**What works:**
- SQLite storage with WAL mode
- Config generations with atomic rollback (low-level `apply()` primitive)
- Content-addressed deduplication (SHA-256)
- LLM Broker via LiteLLM (any provider)
- Memory service with scoped access
- Audit logging
- CLI with init, chat, config commands
- All services are Protocol-based (swappable)

**What's next:** Milestone 2 adds the Security Layer (Credential Proxy, Capability Tokens, Policy Engine, Sandbox) and the full Blueprint Lifecycle on top of the config generation primitives (explicit resolve → verify → plan → apply → status pipeline, risk classification, guard period with auto-rollback, `blueprint_transitions` tracking table).

---

## Summary

| Task | Component | Tests | Lines (est.) |
|------|-----------|-------|-------------|
| 1 | Project scaffolding | - | 50 |
| 2 | Service protocols | 1 | 80 |
| 3 | SQLite storage | 4 | 150 |
| 4 | Config generations | 8 | 200 |
| 5 | Audit logger | 4 | 80 |
| 6 | LLM Broker | 5 | 120 |
| 7 | CLI skeleton | 4 | 250 |
| 8 | Memory service | 8 | 130 |
| 9 | App integration | 1 | 20 |
| 10 | Chat command | 1 | 100 |
| **Total** | | **36 tests** | **~1180 lines** |
